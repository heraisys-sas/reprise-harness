#!/usr/bin/env python3
"""Local Quick Entry supervision server (start-at-login, in the Codex server spirit).

Accepts a validated Quick Apply payload and hands it to Claude Desktop's Quick
Entry submission contract. It binds a private UNIX socket, speaks newline
delimited JSON-RPC, and never opens or automates the Desktop UI: actually
submitting through Quick Entry's native overlay requires separate
operator-granted macOS permissions and is intentionally left to the supervised
driver. This server owns payload validation, target routing and the bounded
admission contract so the harness can treat it as an external-expert channel.
"""
from app_paths import state_location
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import stat
import sys
import time
import uuid

import claude_desktop
from claude_desktop import build_payload, validate_chat_id
from delegation_store import Store, file_evidence

SERVER_NAME = 'reprise-claude-desktop-quick'
VERSION = Path(__file__).with_name('VERSION').read_text().strip()


class Endpoint:
    """Bounded admission and payload hand-off for Quick Entry submissions."""

    def __init__(self, state, daily_limit=8, project_root=None):
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state = state
        self.db = sqlite3.connect(state/'quick.sqlite', timeout=2)
        self.db.execute('CREATE TABLE IF NOT EXISTS quick_submissions(scope TEXT,id TEXT,payload TEXT,day INTEGER,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS quick_calls(scope TEXT,day INTEGER,count INTEGER,PRIMARY KEY(scope,day))')
        self.db.commit()
        self.db.execute('CREATE TABLE IF NOT EXISTS quick_identity(scope TEXT NOT NULL)')
        identities = {row[0] for row in self.db.execute('SELECT scope FROM quick_identity')}
        identities.update(row[0] for row in self.db.execute('SELECT DISTINCT scope FROM quick_submissions'))
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='delegation_requests'").fetchone():
            identities.update(row[0] for row in self.db.execute('SELECT DISTINCT scope FROM delegation_requests')
                              if not row[0].startswith('quick-project-v1:'))
        if len(identities) > 1:
            self.db.close()
            raise ValueError('Identités Quick Apply multiples : réconciliation explicite requise.')
        self.scope = next(iter(identities), str(state.resolve()))
        if not self.db.execute('SELECT 1 FROM quick_identity').fetchone():
            self.db.execute('INSERT INTO quick_identity VALUES (?)', (self.scope,))
        self.db.commit()
        request_scope = self.scope
        if project_root is not None:
            project = str(Path(project_root).resolve(strict=True))
            request_scope = 'quick-project-v1:' + hashlib.sha256((self.scope + '\0' + project).encode()).hexdigest()
        self.requests = Store(self.db, request_scope)
        self.tasks = None
        if project_root is not None:
            from desktop_task_store import TaskStore
            self.tasks = TaskStore(state, project_root)

    def close(self):
        if self.tasks:
            self.tasks.close()
        self.db.close()

    def submit(self, args):
        """Validate, bound and record a submission; returns the routed payload."""
        if not isinstance(args, dict):
            raise ValueError('Objet de soumission requis')
        allowed = {'prompt', 'images', 'filePaths', 'chatId', 'request_key', 'parent_request_key', 'purpose'}
        if args.keys() - allowed:
            raise ValueError('Champs de soumission non pris en charge')
        purpose = args.get('purpose', 'delegation')
        if purpose not in ('delegation', 'continuation'):
            raise ValueError('Invalid submission purpose')
        payload = build_payload(args.get('prompt'), images=args.get('images'),
                                file_paths=args.get('filePaths'), chat_id=args.get('chatId'))
        if self.tasks:
            if payload['images'] or payload['filePaths']:
                raise ValueError('Scoped Desktop inbox supports text snapshots only; attachments require an explicit adapter')
            from desktop_task_store import bounded
            bounded(payload['text'], 'prompt', 12000)
        identity = dict(payload, file_evidence=file_evidence(self.state, file_paths=payload['filePaths']))
        if purpose == 'continuation':
            identity['purpose'] = purpose
        existing = self.requests.begin(args.get('request_key'), identity, args.get('parent_request_key'))
        if existing:
            return existing
        delivery = payload
        if self.tasks and purpose == 'delegation':
            delivery = dict(payload, text=(
                'A Reprise task is waiting in the configured project. Persist a claim_key, then call '
                'get_task with request_key=' + json.dumps(args['request_key']) +
                '. Follow your session instructions, perform that scoped task, and call submit_result '
                'with the returned attempt_id. If already completed, retrieve the existing result instead.'))
        day = int(time.time() // 86400)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            ident = str(uuid.uuid4())
            self.db.execute('INSERT INTO quick_submissions VALUES (?,?,?,?)',
                            (self.scope, ident, json.dumps(delivery, ensure_ascii=False), day))
        if self.tasks and purpose == 'delegation':
            self.tasks.admit(args['request_key'], payload['text'], payload['chatId'])
        return self.requests.finish(args['request_key'], {'status': 'finished', 'submission_id': ident, 'chatId': payload['chatId'],
                'images': len(payload['images']), 'files': len(payload['filePaths']),
                'dispatch': 'dispatchOnQuickEntrySubmit',
                'reason': 'Payload validé et routé ; la soumission réelle dépend du pilote Desktop autorisé.',
                'automatic_fallback': False, 'admission_only': True, 'dispatch_attempted': False,
                'task_available': self.tasks is not None and purpose == 'delegation', 'purpose': purpose})

    def call(self, name, args):
        if not isinstance(args, dict):
            raise ValueError('Objet JSON-RPC requis')
        if name == 'initialize':
            return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                    'serverInfo': {'name': SERVER_NAME, 'version': VERSION}}
        if name == 'quick_entry/capabilities':
            if args:
                raise ValueError('No capability arguments expected')
            return {'version': VERSION, 'admission_supported': True, 'dispatch_supported': False, 'resume_validation_supported': False,
                    'task_return_supported': self.tasks is not None,
                    'project_root': self.tasks.scope if self.tasks else None,
                    'state_sha256': hashlib.sha256(str(self.state.resolve()).encode()).hexdigest(),
                    'reason': 'Native Quick Apply dispatch driver not connected; admission alone does not send a turn.'}
        if name == 'quick_entry/get_result':
            if not self.tasks or args.keys() - {'request_key', 'acknowledge', 'view', 'offset', 'max_chars', 'result_sha256'} or 'request_key' not in args:
                raise ValueError('Project-scoped result lookup required')
            return self.tasks.result(**args)
        if name == 'ping':
            return {}
        if name == 'quick_entry/requests':
            return self.requests.lookup(args)
        if name == 'quick_entry/submit':
            return self.submit(args)
        raise ValueError('Méthode Quick Apply non prise en charge')


def _serve(conn, endpoint):
    stream = conn.makefile('rwb')
    for raw in stream:
        if len(raw) > 1024*1024:
            break
        ident = None
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError('Objet requis')
            ident = message.get('id')
            if 'id' not in message:
                continue
            result = endpoint.call(message.get('method'), message.get('params') or {})
            reply = {'jsonrpc': '2.0', 'id': ident, 'result': result}
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
            reply = {'jsonrpc': '2.0', 'id': ident,
                     'error': {'code': -32602, 'message': str(error) or 'Requête refusée ; aucune relance automatique.'}}
        stream.write((json.dumps(reply, ensure_ascii=False)+'\n').encode())
        stream.flush()
    stream.close()


def serve(path, endpoint):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with Path(str(path) + '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Socket already owned by another Quick Apply daemon')
        return _serve_owned(path, endpoint)


def _serve_owned(path, endpoint):
    path = Path(path)
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise SystemExit('Le chemin de socket existe déjà sans être un socket.')
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(1)
            probe.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError):
            pass
        else:
            raise SystemExit('Socket is accepting connections; refusing replacement')
        finally:
            probe.close()
        try:
            path.unlink()
        except OSError as error:
            # An orphan socket we are not allowed to remove must fail loudly,
            # not crash silently: the caller (daemon) reports the real cause.
            raise SystemExit(f'Impossible de supprimer le socket orphelin {path} : {error}')
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as error:
        raise SystemExit(f'Impossible de créer le dossier de socket {path.parent} : {error}')
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    os.umask(0o077)
    sock.bind(str(path))
    os.chmod(path, 0o600)
    sock.listen(8)
    try:
        while True:
            conn, _ = sock.accept()
            try:
                conn.settimeout(5)
                _serve(conn, endpoint)
            except (OSError, TimeoutError):
                pass
            finally:
                conn.close()
    finally:
        sock.close()
        endpoint.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default=str(claude_desktop.DEFAULT_SOCKET))
    parser.add_argument('--state', default=str(state_location(Path(__file__).parent, 'quick-state')))
    parser.add_argument('--project-root', help='Bind the inbox/result channel to this project; legacy admission remains unbound')
    parser.add_argument('--daily-limit', type=int, help='Deprecated compatibility option; ignored')
    args = parser.parse_args()
    endpoint = Endpoint(args.state, args.daily_limit, args.project_root)
    def terminate(signum, frame):
        raise SystemExit(128 + signum)
    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        serve(args.socket, endpoint)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    main()
