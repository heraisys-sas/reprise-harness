"""#117 T2b: explicit, project-bound approval delegation grants; never send an approval.

Grant records do not authorize MCP clients. A grant becomes effective only after
a separate, operator-controlled native response path has been implemented and
independently reviewed. No model-facing API reads or consumes these tokens.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import time
import uuid

from desktop_task_store import TaskStore

DURATIONS = {'1h': 3600, '24h': 86400, '7d': 604800}
HARNESS_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')
SCHEMA = """CREATE TABLE IF NOT EXISTS approval_delegations(
    scope TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    harness TEXT NOT NULL,
    workspace TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    token_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    max_decisions INTEGER NOT NULL CHECK(max_decisions BETWEEN 1 AND 100),
    used_decisions INTEGER NOT NULL DEFAULT 0 CHECK(used_decisions >= 0),
    revoked_at REAL,
    PRIMARY KEY(scope, grant_id)
)"""


def _directory(path, name):
    if not isinstance(path, (str, os.PathLike)):
        raise ValueError(f'{name} doit être un chemin absolu')
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f'{name} doit être un chemin absolu')
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f'{name} doit être un dossier')
    return resolved


class ApprovalDelegations:
    """Durable grants only; no token redemption or Codex RPC is exposed."""

    def __init__(self, project, state):
        self.project = _directory(project, 'project')
        self.state = _directory(state, 'state')
        self.store = TaskStore(self.state, self.project)
        try:
            with self.store.db:
                self.store.db.execute(SCHEMA)
        except BaseException:
            self.store.close()
            raise

    def close(self):
        self.store.close()

    def grant(self, harness, workspace, thread_id, duration, max_decisions, token_file, now=None):
        if not isinstance(harness, str) or HARNESS_RE.fullmatch(harness) is None:
            raise ValueError('Identifiant harness invalide')
        workspace = _directory(workspace, 'workspace')
        if duration not in DURATIONS:
            raise ValueError('Durée autorisée : 1h, 24h, 7d')
        if type(max_decisions) is not int or not 1 <= max_decisions <= 100:
            raise ValueError('max_decisions doit être compris entre 1 et 100')
        try:
            canonical_thread = str(uuid.UUID(thread_id)) if isinstance(thread_id, str) else None
        except ValueError:
            canonical_thread = None
        if canonical_thread != thread_id or canonical_thread is None:
            raise ValueError('thread_id doit être un UUID canonique')
        if not isinstance(token_file, (str, os.PathLike)):
            raise ValueError('token_file requis')
        destination = Path(token_file).expanduser()
        if not destination.is_absolute():
            raise ValueError('token_file doit être absolu')
        parent = destination.parent.resolve(strict=True)
        if not parent.is_dir() or stat.S_IMODE(parent.stat().st_mode) & 0o077:
            raise ValueError('Le dossier des jetons doit être privé (0700)')
        if parent == self.project or parent.is_relative_to(self.project):
            raise ValueError('Ne jamais placer de jeton dans le dépôt du projet')
        if destination.name in ('', '.', '..'):
            raise ValueError('Nom de jeton invalide')
        now = time.time() if now is None else float(now)
        if not 0 < now < 1e12:
            raise ValueError('Horloge invalide')
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode('ascii')).hexdigest()
        grant_id = str(uuid.uuid4())
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        descriptor = os.open(destination, flags, 0o600)
        try:
            with os.fdopen(descriptor, 'w', encoding='ascii') as file:
                file.write(token + '\n')
                file.flush()
                os.fsync(file.fileno())
            with self.store.db:
                self.store.db.execute('BEGIN IMMEDIATE')
                self.store.db.execute("""INSERT INTO approval_delegations
                    (scope,grant_id,harness,workspace,thread_id,token_sha256,
                     created_at,expires_at,max_decisions)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (self.store.scope, grant_id, harness, str(workspace), thread_id,
                     digest, now, now + DURATIONS[duration], max_decisions))
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return {'status': 'created_inactive', 'grant_id': grant_id,
                'harness': harness, 'workspace': str(workspace), 'thread_id': thread_id,
                'expires_at': now + DURATIONS[duration], 'max_decisions': max_decisions,
                'token_file': str(destination),
                'note': 'Jeton privé créé ; aucune capacité d\'approbation native ni MCP activée.'}

    def list_grants(self, now=None):
        now = time.time() if now is None else float(now)
        rows = self.store.db.execute("""SELECT grant_id,harness,workspace,thread_id,
            created_at,expires_at,max_decisions,used_decisions,revoked_at
            FROM approval_delegations WHERE scope=? ORDER BY created_at DESC,grant_id""",
            (self.store.scope,)).fetchall()
        names = ('grant_id', 'harness', 'workspace', 'thread_id', 'created_at',
                 'expires_at', 'max_decisions', 'used_decisions', 'revoked_at')
        return [{'status': ('revoked' if row[8] is not None else
                            'expired' if now >= row[5] else 'inactive_pending_native_path'),
                 **dict(zip(names, row))} for row in rows]

    def revoke(self, grant_id, now=None):
        try:
            canonical_grant = str(uuid.UUID(grant_id)) if isinstance(grant_id, str) else None
        except ValueError:
            canonical_grant = None
        if canonical_grant != grant_id or canonical_grant is None:
            raise ValueError('grant_id invalide')
        now = time.time() if now is None else float(now)
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            row = self.store.db.execute("""SELECT revoked_at FROM approval_delegations
                WHERE scope=? AND grant_id=?""", (self.store.scope, grant_id)).fetchone()
            if row is None:
                raise ValueError('Délégation introuvable dans ce projet')
            if row[0] is None:
                self.store.db.execute("""UPDATE approval_delegations
                    SET revoked_at=? WHERE scope=? AND grant_id=? AND revoked_at IS NULL""",
                    (now, self.store.scope, grant_id))
        return {'status': 'revoked', 'grant_id': grant_id}


def main(root=None, argv=None):
    parser = argparse.ArgumentParser(description='Gestion locale des délégations d’approbation (#117).')
    parser.add_argument('operation', choices=('grant', 'list', 'revoke'))
    parser.add_argument('--project', required=True)
    parser.add_argument('--state', required=True, help='Dossier d’état local existant, hors dépôt')
    parser.add_argument('--harness')
    parser.add_argument('--workspace')
    parser.add_argument('--thread-id')
    parser.add_argument('--duration', choices=tuple(DURATIONS), default='24h')
    parser.add_argument('--max-decisions', type=int, default=1)
    parser.add_argument('--token-file')
    parser.add_argument('--grant-id')
    args = parser.parse_args(argv)
    manager = None
    try:
        manager = ApprovalDelegations(args.project, args.state)
        if args.operation == 'grant':
            result = manager.grant(args.harness, args.workspace, args.thread_id,
                                   args.duration, args.max_decisions, args.token_file)
        elif args.operation == 'list':
            result = {'status': 'ok', 'grants': manager.list_grants()}
        else:
            result = manager.revoke(args.grant_id)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        print(json.dumps({'status': 'refused', 'message': str(error)}, ensure_ascii=False))
        return 1
    finally:
        if manager is not None:
            manager.close()


if __name__ == '__main__':
    sys.exit(main())
