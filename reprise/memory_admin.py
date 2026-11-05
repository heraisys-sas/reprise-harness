"""Explicit local memory administration. Plans do not create files or authorize Vault.

Fingerprints detect changes; they are not signatures or same-UID authentication.
The host/operator owns this interface. It is deliberately absent from MCP tools.
"""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from orchestrator_memory import Memory, MAX_STORE_BYTES, PURPOSE, _private_dir, _private_file, _uuid
import vault

MAX_PLAN_BYTES = 32768


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _identity(info):
    values = [info.st_dev, info.st_ino, info.st_uid, info.st_mode]
    return values if stat.S_ISDIR(info.st_mode) else values + [info.st_nlink]


def _file(directory, name):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except FileNotFoundError:
        return None, None
    try:
        info = _private_file(fd)
        if info.st_size > MAX_STORE_BYTES:
            raise ValueError('Administrative input too large')
        data = bytearray()
        while len(data) <= MAX_STORE_BYTES:
            chunk = os.read(fd, min(65536, MAX_STORE_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > MAX_STORE_BYTES:
            raise ValueError('Administrative input too large')
        after = os.fstat(fd)
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (_identity(info) != _identity(current) or info.st_size != after.st_size
                or info.st_mtime_ns != after.st_mtime_ns):
            raise ValueError('Administrative input changed during inspection')
        return {'identity': _identity(info), 'sha256': hashlib.sha256(data).hexdigest()}, bytes(data)
    finally:
        os.close(fd)


def _database(data):
    db = sqlite3.connect(':memory:')
    try:
        db.execute('PRAGMA temp_store=MEMORY')
        db.deserialize(data)
        db.execute('PRAGMA query_only=ON')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Invalid administrative database')
        return db
    except BaseException:
        db.close()
        raise


def _observe(state, *, crypt=vault.crypt):
    """Read descriptor-anchored ciphertext/registry; never construct Memory."""
    state = Path(state)
    if not state.is_absolute() or state != state.resolve(strict=True):
        raise ValueError('Canonical absolute private state required')
    root = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    memoryfd = None
    try:
        _private_dir(root)
        baseline = {'state_identity': _identity(os.fstat(root))}
        for suffix in ('-journal', '-wal', '-shm'):
            try:
                os.stat('projects.sqlite'+suffix, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('Active registry transaction refused')
        baseline['registry'], registry = _file(root, 'projects.sqlite')
        if registry is None or registry[:16] != b'SQLite format 3\x00' or registry[18:20] != b'\x01\x01':
            raise ValueError('Existing rollback-mode registry required')
        db = _database(registry)
        try:
            projects = [row[0] for row in db.execute('SELECT id FROM projects')]
            for ident in projects:
                _uuid(ident)
        finally:
            db.close()
        try:
            memoryfd = os.open('orchestrator-memory.noindex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        except FileNotFoundError:
            baseline.update(memory_directory=None, store=None, backup=None)
            ciphertext = None
        else:
            _private_dir(memoryfd)
            baseline['memory_directory'] = _identity(os.fstat(memoryfd))
            baseline['store'], ciphertext = _file(memoryfd, 'memory.enc')
            baseline['backup'], _ = _file(memoryfd, 'memory.v1-backup.enc')
        version, scopes = 1, []
        if ciphertext is not None:
            plain = crypt(ciphertext, PURPOSE, 'open')
            if not isinstance(plain, bytes) or len(plain) > MAX_STORE_BYTES:
                raise ValueError('Invalid memory store')
            db = _database(plain)
            try:
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version not in (1, 2):
                    raise ValueError('Unsupported memory schema version')
                for table in ('facts', 'checkpoints', 'operations'):
                    db.execute('SELECT project_id FROM '+table+' LIMIT 0')
                if version == 2:
                    db.row_factory = sqlite3.Row
                    scopes = [dict(row) for row in db.execute('SELECT id,kind,subject_id,project_id,work_id FROM scopes ORDER BY kind,id')]
            finally:
                db.close()
        baseline['schema'] = version
        # Detect replacement of the directory itself while observing its files.
        if _identity(os.stat(state, follow_symlinks=False)) != baseline['state_identity']:
            raise ValueError('State identity changed during inspection')
        return {'state': str(state), 'schema': version, 'store_present': ciphertext is not None,
                'scopes': scopes, 'projects': projects, 'baseline': baseline}
    finally:
        if memoryfd is not None:
            os.close(memoryfd)
        os.close(root)


def inspect(state, *, crypt=vault.crypt):
    observed = _observe(state, crypt=crypt)
    return {'status': 'inspected', 'state': observed['state'], 'schema': observed['schema'],
            'store_present': observed['store_present'], 'scope_count': len(observed['scopes']),
            'baseline': observed['baseline'], 'client_loaded': 'unknown',
            'exchange_confirmed': 'unknown', 'inference_executed': False}


def list_scopes(state, *, crypt=vault.crypt):
    observed = _observe(state, crypt=crypt)
    return {'status': 'listed', 'schema': observed['schema'], 'items': observed['scopes'],
            'scope_registration_required': observed['schema'] == 1}


def _request(request, observed):
    if not isinstance(request, dict):
        raise ValueError('Invalid scope request')
    kind = request.get('kind')
    keys = {'user': {'kind', 'subject_id'}, 'project': {'kind', 'project_id'},
            'work': {'kind', 'project_id', 'work_id'}}.get(kind)
    if keys is None or set(request) != keys:
        raise ValueError('Invalid scope request')
    for key in keys - {'kind'}:
        _uuid(request[key])
    if 'project_id' in request and request['project_id'] not in observed['projects']:
        raise ValueError('Unknown project')
    if observed['schema'] != 2:
        raise ValueError('Explicit scope migration required')
    return dict(request)


def plan(state, action, *, request=None, crypt=vault.crypt):
    observed = _observe(state, crypt=crypt)
    if action == 'scope':
        request = _request(request, observed)
    elif action == 'migration' and request is None:
        request = {}
    else:
        raise ValueError('Invalid administrative action')
    result = {'format': 1, 'state': observed['state'], 'action': action,
              'request': request, 'baseline': observed['baseline'],
              'rollback': 'v1_release_refuses_v2; restoring_v1_backup_loses_later_writes',
              'physical_erasure_verified': False}
    result['fingerprint'] = hashlib.sha256(_json(result).encode()).hexdigest()
    return result


class _StalePlan(ValueError):
    pass


class _BoundMemory(Memory):
    def __init__(self, state, baseline, *, crypt):
        super().__init__(state, crypt=crypt)
        self.baseline = baseline
        # A fresh apply may have created the previously absent private folder.
        # Capture its identity, while preserving all other planned preconditions.
        current = _observe(state, crypt=crypt)['baseline']
        expected = dict(baseline)
        if expected['memory_directory'] is None:
            expected['memory_directory'] = current['memory_directory']
        if current != expected:
            raise _StalePlan('Administrative plan changed')
        self.baseline = current

    @contextlib.contextmanager
    def _store(self, **kwargs):
        # The core owns the sole memory lock. Validate AFTER acquiring that lock,
        # before yielding the database to migration/registration code.
        with super()._store(**kwargs) as db:
            if _observe(self.state, crypt=self.crypt)['baseline'] != self.baseline:
                raise _StalePlan('Administrative plan changed')
            yield db


def apply(selected, *, crypt=vault.crypt):
    if not isinstance(selected, dict) or len(_json(selected).encode()) > MAX_PLAN_BYTES:
        raise ValueError('Invalid administrative plan')
    if set(selected) != {'format', 'state', 'action', 'request', 'baseline', 'rollback',
                         'physical_erasure_verified', 'fingerprint'}:
        raise ValueError('Invalid administrative plan')
    request = selected['request'] if selected['action'] == 'scope' else None
    # Check availability of an EXISTING key; no authorize, interactive operation
    # or seal may run if this fails. Empty storage must not create a global key.
    crypt(b'', PURPOSE, 'check')
    current = plan(selected['state'], selected['action'], request=request, crypt=crypt)
    if current != selected:
        raise _StalePlan('Administrative plan changed')
    def existing_key_crypt(data, purpose, operation):
        if operation == 'seal':
            crypt(b'', purpose, 'check')
        return crypt(data, purpose, operation)
    memory = _BoundMemory(selected['state'], selected['baseline'], crypt=existing_key_crypt)
    try:
        if selected['action'] == 'migration':
            result = memory.migrate_scopes_v2()
        else:
            args = dict(selected['request'])
            kind = args.pop('kind')
            result = {'scope_id': memory.register_scope(kind, **args)}
    except _StalePlan:
        raise
    except (OSError, ValueError, sqlite3.Error) as error:
        raise RuntimeError('Administrative apply uncertain; inspect storage before retry') from error
    return {'status': 'applied', 'effect': 'recorded', 'action': selected['action'],
            'result': result, 'physical_erasure_verified': False}


def main(root, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('memory-inspect','memory-migration-plan','memory-scope-plan','memory-scope-list'):
        item = sub.add_parser(command)
        item.add_argument('--state', required=True, help='Canonical absolute existing private state')
        if command == 'memory-scope-plan':
            item.add_argument('--kind', choices=('user','project','work'), required=True)
            for key in ('subject-id','project-id','work-id'):
                item.add_argument('--'+key)
    for command in ('memory-migration-apply','memory-scope-apply'):
        sub.add_parser(command)
    args = parser.parse_args(argv)
    try:
        if args.command.endswith('-apply'):
            raw = sys.stdin.buffer.read(MAX_PLAN_BYTES + 1)
            if len(raw) > MAX_PLAN_BYTES:
                raise ValueError('Plan too large')
            selected = json.loads(raw)
            action = 'migration' if args.command == 'memory-migration-apply' else 'scope'
            if not isinstance(selected, dict) or selected.get('action') != action:
                raise ValueError('Wrong plan action')
            result = apply(selected)
        elif args.command == 'memory-inspect':
            result = inspect(args.state)
        elif args.command == 'memory-scope-list':
            result = list_scopes(args.state)
        elif args.command == 'memory-migration-plan':
            result = plan(args.state, 'migration')
        else:
            request = {'kind': args.kind}
            request.update({key: getattr(args,key) for key in ('subject_id','project_id','work_id') if getattr(args,key) is not None})
            result = plan(args.state,'scope',request=request)
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        return 0
    except RuntimeError:
        print(json.dumps({'status':'indeterminate','effect':'indeterminate',
                          'message':'Inspect storage before retry; no automatic replay.'}))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        print(json.dumps({'status':'refused','effect':'not_admitted',
                          'message':'Verify private state, existing Vault access and current plan.'}))
    return 1
