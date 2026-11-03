#!/usr/bin/env python3
"""Local Quick Entry supervision server (start-at-login, in the Codex server spirit).

Accepts a validated Quick Apply payload and hands it to Claude Desktop's Quick
Apply submission contract. It binds a private UNIX socket, speaks newline
delimited JSON-RPC. Orchestrated dispatch launches Chrome over CDP pipes owned
by this daemon, attached to the admitted claude.ai chat: one prepare, one
fingerprint check, one click — never a retry after a lost response, never a
response read. This server owns payload validation, target routing and the
bounded admission contract so the harness can treat it as an external-expert
channel.
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
import time
import uuid

import claude_desktop
import claude_desktop_apply
from claude_desktop import build_payload, validate_chat_id
from delegation_store import Store, file_evidence

SERVER_NAME = 'reprise-claude-desktop-quick'
VERSION = Path(__file__).with_name('VERSION').read_text().strip()


def _cdp_pipe():
    import claude_cdp_pipe as mod
    return mod


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
        allowed = {'prompt', 'images', 'filePaths', 'chatId', 'request_key', 'parent_request_key', 'purpose', 'orchestrated'}
        if args.keys() - allowed:
            raise ValueError('Champs de soumission non pris en charge')
        orchestrated = bool(args.get('orchestrated'))
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
        if self.tasks and purpose == 'delegation' and not orchestrated:
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
                'task_pre_registered': bool(orchestrated and self.tasks),
                'task_available': self.tasks is not None and purpose == 'delegation', 'purpose': purpose})

    def _dispatch(self, request_key):
        """Dispatch once over CDP pipes after durable admission and reservation.

        Unknown request_key never dispatches. The failure is logged
        (dispatch_attempted + ok/error/stage) and returned as-is; no retry, no
        fallback, and admission-only remains the recorded outcome.
        """
        row = self.tasks.db.execute(
            'SELECT prompt,chat_id FROM desktop_tasks WHERE scope=? AND request_key=?',
            (self.tasks.scope, request_key)).fetchone()
        if not row:
            raise ValueError('Unknown request_key; no dispatch')
        prompt, chat_id = row
        result = {'request_key': request_key, 'dispatch_attempted': True,
                  'dispatch_channel': 'cdp-pipe', 'admission_only': True}
        reservation = self.tasks.reserve_socket_dispatch(request_key)
        if reservation['state'] == 'recorded':
            return reservation['receipt']
        if reservation['state'] != 'reserved':
            return result | {'dispatch_ok': False, 'stage': 'incertain',
                             'dispatch_error': 'Dispatch antérieur ou historique incertain ; aucune relance.'}
        outcome = self._dispatch_reserved(result, prompt, chat_id)
        self.tasks.record_socket_dispatch(request_key, outcome)
        return outcome

    def _dispatch_reserved(self, result, prompt, chat_id):
        """Dispatch once over the daemon-owned Chrome CDP pipes, no retry.

        One prepare, one fingerprint check, one click. Chrome is terminated
        only on a certain failure that predates the click; a possible click
        (envoyé without click_observed, incertain) preserves the Chrome
        process so the outcome can be observed.
        """
        if not chat_id or not validate_chat_id(chat_id):
            return result | {'dispatch_ok': False, 'dispatch_error': 'chat_id invalide ou absent',
                             'stage': 'cible'}
        cdp_mod = _cdp_pipe()
        profile = cdp_mod._profile_dir()
        lock_path, _ = cdp_mod._hostname_pid_lock(profile)
        busy = cdp_mod.check_profile_busy(lock_path)
        if busy is not None:
            return result | {'dispatch_attempted': False, 'dispatch_ok': False,
                             'stage': 'profile_busy',
                             'dispatch_error': 'Profil Chrome occupé par un autre propriétaire ; aucune tentative.'}
        if not Path(cdp_mod._chrome_bin()).exists():
            return result | {'dispatch_ok': False, 'stage': 'incertain',
                             'dispatch_error': 'Binaire Chrome introuvable'}
        try:
            transport, proc = cdp_mod.launch_chrome(profile)
        except RuntimeError as exc:
            return result | {'dispatch_ok': False, 'stage': 'incertain',
                             'dispatch_error': str(exc) or 'launch_chrome échoué'}
        path = '/chat/' + chat_id
        try:
            try:
                transport.pick_and_attach(chat_id)
            except RuntimeError:
                # Cold start: launch_chrome opens only https://claude.ai; the
                # admitted chat page may not exist yet. Create the target
                # explicitly, then pick once again (still exactly one attach).
                transport.call('Target.createTarget',
                               {'url': 'https://claude.ai' + path})
                transport.pick_and_attach(chat_id)
            if not claude_desktop_apply.wait_composer(transport):
                cdp_mod.terminate_chrome(proc)
                return result | {'dispatch_ok': False, 'stage': 'composeur',
                                 'dispatch_error': 'Composeur indisponible'}
            info = claude_desktop_apply.state(transport)
            check = claude_desktop_apply._check(info, path)
            if check:
                cdp_mod.terminate_chrome(proc)
                return result | {'dispatch_ok': False, 'stage': check,
                                 'dispatch_error': check}
            if not claude_desktop_apply._prepare(transport, path):
                cdp_mod.terminate_chrome(proc)
                return result | {'dispatch_ok': False, 'stage': 'composeur',
                                 'dispatch_error': 'Préparation du composeur échouée'}
            fingerprint = hashlib.sha256(prompt.strip().encode()).hexdigest()
            transport.insert_text(prompt.strip())
            try:
                outcome = claude_desktop_apply._dispatch(transport, path, fingerprint)
            except (OSError, ValueError, claude_desktop_apply.WSError, TimeoutError):
                # A lost response may conceal a click: preserve Chrome, never retry.
                return result | {'dispatch_ok': False, 'stage': 'incertain',
                                 'dispatch_error': 'Réponse CDP perdue au clic'}
            if outcome.get('stage') == 'envoyé' and outcome.get('click_observed') is True:
                return result | {'dispatch_ok': True, 'stage': 'envoyé',
                                 'click_observed': True}
            if outcome.get('stage') == 'incertain' or outcome.get('click_observed') is not False:
                return result | {'dispatch_ok': False, 'stage': 'incertain',
                                 'dispatch_error': 'Résultat de clic incertain ; Chrome préservé'}
            stage = outcome.get('stage') or 'incertain'
            # Certain failure before the click: terminate the daemon-owned Chrome.
            cdp_mod.terminate_chrome(proc)
            return result | {'dispatch_ok': False, 'stage': stage,
                             'dispatch_error': outcome.get('dispatch_error') or stage}
        except Exception as exc:  # a daemon crash must never replace the receipt
            return result | {'dispatch_ok': False, 'stage': 'incertain',
                             'dispatch_error': str(exc) or type(exc).__name__}

    def call(self, name, args):
        if not isinstance(args, dict):
            raise ValueError('Objet JSON-RPC requis')
        if name == 'initialize':
            return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                    'serverInfo': {'name': SERVER_NAME, 'version': VERSION}}
        if name == 'quick_entry/capabilities':
            if args:
                raise ValueError('No capability arguments expected')
            # Dispatch is daemon-owned CDP over pipes; capability is not proof
            # of a connected browser or a successful click.
            chrome_present = Path(_cdp_pipe()._chrome_bin()).is_file()
            supported = self.tasks is not None and chrome_present
            if self.tasks is None:
                reason = 'Project-scoped task store required for dispatch.'
            elif chrome_present:
                reason = 'Chrome launches at dispatch; session and target are not verified.'
            else:
                reason = 'Chrome binary absent.'
            return {'version': VERSION, 'admission_supported': True,
                    'dispatch_supported': supported,
                    'dispatch_channel': 'cdp-pipe',
                    'resume_validation_supported': False,
                    'task_return_supported': self.tasks is not None,
                    'project_root': self.tasks.scope if self.tasks else None,
                    'state_sha256': hashlib.sha256(str(self.state.resolve()).encode()).hexdigest(),
                    'reason': reason,
                    'cdp_resume_supported': chrome_present}
        if name == 'quick_entry/dispatch':
            if not self.tasks:
                raise ValueError('Project-scoped dispatch required; unknown request_key gets no dispatch')
            if args.keys() - {'request_key'} or not isinstance(args.get('request_key'), str):
                raise ValueError('request_key only; images and filePaths are refused on this channel')
            return self._dispatch(args['request_key'])
        if name == 'quick_entry/get_result':
            if not self.tasks or args.keys() - {'request_key', 'acknowledge', 'view', 'offset', 'max_chars', 'result_sha256'} or 'request_key' not in args:
                raise ValueError('Project-scoped result lookup required')
            return self.tasks.result(**args)
        if name == 'quick_entry/list_tasks':
            if not self.tasks or args.keys() - {'limit', 'cursor'}:
                raise ValueError('Project-scoped task listing required')
            return self.tasks.list_tasks(**args)
        if name == 'ping':
            return {}
        if name == 'quick_entry/requests':
            return self.requests.lookup(args)
        if name == 'quick_entry/submit':
            return self.submit(args)
        if name == 'cdp/status':
            return self._cdp_status()
        if name == 'cdp/resume':
            return self._cdp_resume(args)
        if name == 'shutdown':
            return self._shutdown(args)
        raise ValueError('Méthode Quick Apply non prise en charge')

    # -- CDP methods ---------------------------------------------------------

    def _cdp_status(self):
        """Passive CDP status: returns running:false if no Chrome process."""
        cdp = _cdp_pipe()
        profile = cdp._profile_dir()
        lock_path, _ = cdp._hostname_pid_lock(profile)
        busy = cdp.check_profile_busy(lock_path)
        if busy is not None:
            return {'ok': False, 'status': 'profile_busy', 'pid': busy['pid'],
                    'lock_mtime': busy['lock_mtime']}
        if not Path(cdp._chrome_bin()).exists():
            return {'ok': False, 'status': 'chrome_missing'}
        return {'ok': True, 'status': 'idle', 'cdp_resume_supported': Path(_cdp_pipe()._chrome_bin()).exists()}

    def _cdp_resume(self, args):
        """Resume via CDP pipe: spawn lazy, target unique page, deposit+click."""
        if not isinstance(args, dict):
            raise ValueError('Objet de soumission CDP requis')
        chat_id = args.get('chat_id') or args.get('chatId')
        prompt = args.get('prompt')
        try:
            chat_uuid = validate_chat_id(chat_id)
        except ValueError:
            chat_uuid = None
        if not chat_id or not chat_uuid:
            return {'ok': False, 'status': 'target_missing', 'reason': 'chat_id invalide ou absent'}
        if not prompt or len(prompt.strip()) < 2:
            return {'ok': False, 'status': 'paramètres', 'reason': 'prompt requis (≥2 caractères)'}
        if len(prompt) > 12000:
            return {'ok': False, 'status': 'paramètres', 'reason': 'prompt trop long'}

        cdp_mod = _cdp_pipe()
        profile = cdp_mod._profile_dir()
        lock_path, _ = cdp_mod._hostname_pid_lock(profile)

        busy = cdp_mod.check_profile_busy(lock_path)
        if busy is not None:
            return {'ok': False, 'status': 'profile_busy', 'pid': busy['pid'],
                    'lock_mtime': busy['lock_mtime']}

        if not Path(cdp_mod._chrome_bin()).exists():
            return {'ok': False, 'status': 'chrome_missing'}

        try:
            transport, proc = cdp_mod.launch_chrome(profile)
        except RuntimeError as exc:
            return {'ok': False, 'status': 'session', 'reason': str(exc)}

        try:
            # Single pick-and-attach: getTargets → select /chat/<uuid> → attachToTarget flatten
            transport.pick_and_attach(chat_uuid)
        except Exception as exc:
            cdp_mod.terminate_chrome(proc)
            return {'ok': False, 'status': 'session', 'reason': str(exc)}

        try:
            compose_check = transport.evaluate(
                "(function(){const el=document.querySelector('[data-testid=compose-textarea]');return el?{hasText:!!el.innerText,textLength:el.innerText.length}:null})()")
            if compose_check and compose_check.get("textLength", 0) > 0:
                stage = 'brouillon'
                cdp_mod.terminate_chrome(proc)
                return {'ok': False, 'status': 'brouillon', 'stage': 'brouillon',
                        'detail': 'Contenu préexistant dans le composeur'}

            # Focus the composer BEFORE inserting text (proven sequence from claude_desktop_apply.py ~447)
            transport.evaluate(
                "(function(){const el=document.querySelector('[data-testid=compose-textarea]');if(el){el.focus();if(el.isContentEditable)el.dispatchEvent(new Event('input',{bubbles:true}));else el.focus()}return true})()")

            transport.insert_text(prompt)
            stage = 'envoyé'
        except Exception as exc:
            cdp_mod.terminate_chrome(proc)
            return {'ok': False, 'status': 'send_failed', 'reason': str(exc)}

        try:
            click_result = transport.evaluate(
                "(function(){const btn=document.querySelector('[data-testid=chat-input-send]');if(!btn)return null;btn.click();return{clicked:true}})()")
            if not click_result or not click_result.get("clicked"):
                stage = 'incertain'
        except Exception:
            stage = 'incertain'
        finally:
            cdp_mod.terminate_chrome(proc)

        return {
            'ok': True, 'status': 'submitted', 'stage': stage,
            'chatId': chat_uuid, 'submitted': 1,
            'transport': 'cdp-pipe',
            'detail': 'Envoyé' if stage == 'envoyé' else stage}

    def _shutdown(self, args):
        """Authenticate shutdown without terminating an unowned Chrome process."""
        if not isinstance(args, dict):
            raise ValueError('Objet de soumission requis')
        token = args.get('token', '')
        if not isinstance(token, str) or len(token) != 64:
            return {'ok': False, 'status': 'unauthenticated'}

        shutdown_token_path = self.state / 'quick-apply-shutdown.token'
        try:
            stored = shutdown_token_path.read_text().strip()
        except OSError:
            return {'ok': False, 'status': 'unauthenticated'}

        if hashlib.sha256(token.encode()).hexdigest() != hashlib.sha256(stored.encode()).hexdigest():
            return {'ok': False, 'status': 'unauthenticated'}

        try:
            shutdown_token_path.unlink(missing_ok=True)
        except OSError:
            pass

        return {'ok': True}


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
            method = message.get('method')
            params = message.get('params', {})
            if method == 'quick_entry/list_tasks' and not isinstance(params, dict):
                raise ValueError('Project-scoped task listing requires object params')
            result = endpoint.call(method, params or {})
            reply = {'jsonrpc': '2.0', 'id': ident, 'result': result}
            stream.write((json.dumps(reply, ensure_ascii=False)+'\n').encode())
            stream.flush()
            # After a successful shutdown response, exit the loop to close socket and endpoint
            if isinstance(result, dict) and result.get('ok') and message.get('method') == 'shutdown':
                raise SystemExit(0)
        except SystemExit:
            raise
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
            reply = {'jsonrpc': '2.0', 'id': ident,
                     'error': {'code': -32602, 'message': str(error) or 'Requête refusée ; aucune relance automatique.'}}
            stream.write((json.dumps(reply, ensure_ascii=False)+'\n').encode())
            stream.flush()
        except Exception as error:
            # Unexpected endpoint failure (CDP RuntimeError, id mismatch...):
            # reply as internal error instead of killing the single-threaded loop.
            reply = {'jsonrpc': '2.0', 'id': ident,
                     'error': {'code': -32603, 'message': str(error) or 'Erreur interne ; aucune relance automatique.'}}
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
        # A profile lock does not establish ownership of a Chrome process.
        raise SystemExit(128 + signum)
    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        serve(args.socket, endpoint)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    main()
