"""Explicit local project memory; no model, tools, authority or external effects.

A selected project UUID scopes every operation. This API does not authenticate
its caller; an eventual IPC/MCP adapter must bind identity and permissions itself.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import time
import uuid

import vault

PURPOSE = 'orchestrator-memory-v1'
SCHEMA_VERSION = 1
MAX_STORE_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024
MAX_LIST = 100

# Isolated to prevent closing registry file descriptors from releasing SQLite
# POSIX locks held by another connection in the calling process. Uses SQLite's
# actual path so journal and locking semantics remain native.
_REGISTRY_PROBE = r"""
import json, os, sqlite3, stat, sys
from pathlib import Path

def run():
    request = json.loads(sys.stdin.read(4096))
    state = Path(request['state'])
    statefd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    filefd = None
    try:
        directory = os.fstat(statefd)
        if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.getuid() or directory.st_mode & 0o077:
            return {'status': 'refused'}
        filefd = os.open('projects.sqlite', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=statefd)
        before = os.fstat(filefd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_mode & 0o077:
            return {'status': 'refused'}
        header = os.pread(filefd, 100, 0)
        if len(header) < 100 or header[:16] != b'SQLite format 3\x00' or header[18:20] != b'\x01\x01':
            return {'status': 'refused'}
        os.close(filefd)
        filefd = None
        try:
            os.stat('projects.sqlite-journal', dir_fd=statefd, follow_symlinks=False)
            return {'status': 'refused'}
        except FileNotFoundError:
            pass
        connection = sqlite3.connect((state / 'projects.sqlite').as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA temp_store=MEMORY')
            temp_store = connection.execute('PRAGMA temp_store').fetchone()[0]
            connection.execute('BEGIN')
            if connection.execute('PRAGMA journal_mode').fetchone()[0].lower() == 'wal':
                return {'status': 'refused'}
            schema = connection.execute("SELECT type FROM sqlite_master WHERE name='projects'").fetchone()
            columns = {row[1]: row for row in connection.execute('PRAGMA table_info(projects)')}
            required = {'id', 'name', 'name_key', 'root_path', 'source_repository', 'source_common_dir', 'created_at'}
            if (not schema or schema[0] != 'table' or not required <= columns.keys()
                    or columns['id'][2].upper() != 'TEXT' or columns['id'][5] != 1):
                return {'status': 'malformed'}
            found = connection.execute('SELECT id FROM projects WHERE id=?', (request['project_id'],)).fetchall()
            current = os.stat('projects.sqlite', dir_fd=statefd, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
                return {'status': 'refused'}
            if len(found) != 1 or found[0][0] != request['project_id']:
                return {'status': 'unknown'}
            return {'status': 'ok', 'temp_store': temp_store}
        finally:
            connection.close()
    finally:
        if filefd is not None: os.close(filefd)
        os.close(statefd)

try:
    print(json.dumps(run()))
except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
    print(json.dumps({'status': 'refused'}))
"""


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError('Expected a canonical UUID')
    try:
        parsed = str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError('Expected a canonical UUID') from exc
    if value != parsed:
        raise ValueError('Expected a canonical UUID')
    return value


def _text(value, *, limit=MAX_TEXT_BYTES, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or len(value.encode('utf-8')) > limit:
        raise ValueError('Invalid or oversized text')
    return value


def _source(value):
    allowed = {'kind', 'reference', 'producer', 'observed_at', 'sha256', 'recorded_by'}
    if not isinstance(value, dict) or set(value) - allowed or not {'kind', 'reference', 'producer', 'observed_at'} <= set(value):
        raise ValueError('Explicit provenance is required')
    result = {key: _text(value[key], limit=512) for key in ('kind', 'reference', 'producer', 'observed_at')}
    if 'sha256' in value:
        digest = value['sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('Invalid asserted source fingerprint')
        result['sha256'] = digest
    if 'recorded_by' in value:
        attribution = value['recorded_by']
        if (not isinstance(attribution, dict) or set(attribution) != {'harness_id', 'trust'}
                or attribution.get('trust') != 'local_host_profile'):
            raise ValueError('Invalid host attribution')
        result['recorded_by'] = {'harness_id': _text(attribution['harness_id'], limit=128),
                                 'trust': 'local_host_profile'}
    return result


def _strings(value):
    if not isinstance(value, list) or len(value) > MAX_LIST:
        raise ValueError('Invalid or oversized list')
    return [_text(x, limit=2048) for x in value]


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _private_file(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        raise ValueError('Memory file must be private, owned and regular')
    return info


def _private_dir(fd):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Memory directory must be private and owned')


class Memory:
    """In-process API; provide crypt(data, purpose, operation) for test isolation."""
    def __init__(self, state_directory, *, crypt=None):
        self.state = Path(state_directory).expanduser().absolute()
        self.crypt = crypt or vault.crypt
        if self.state.is_symlink() or not self.state.is_dir():
            raise ValueError('An existing private state directory is required')
        fd = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _private_dir(fd)
            try:
                os.mkdir('orchestrator-memory.noindex', 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            directory = os.open('orchestrator-memory.noindex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                _private_dir(directory)
            finally:
                os.close(directory)
        finally:
            os.close(fd)
        self.directory = self.state / 'orchestrator-memory.noindex'

    def _project(self, project_id):
        """Query the registry in another process, preserving this process's SQLite locks."""
        _uuid(project_id)
        try:
            response = subprocess.run([sys.executable, '-I', '-c', _REGISTRY_PROBE],
                input=_json({'state': str(self.state), 'project_id': project_id}),
                text=True, capture_output=True, timeout=5, check=False)
            if response.returncode != 0:
                raise ValueError('Project registry validation failed')
            result = json.loads(response.stdout)
            if result == {'status': 'ok', 'temp_store': 2}:
                return
            if isinstance(result, dict) and result.get('status') == 'malformed':
                raise ValueError('Malformed project registry schema')
            if isinstance(result, dict) and result.get('status') == 'unknown':
                raise ValueError('Unknown project')
            raise ValueError('Existing readable project registry required')
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            raise ValueError('Project registry validation failed') from exc

    @contextlib.contextmanager
    def _store(self, *, write=False, durable_receipt=False):
        rootfd = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        dirfd = None
        lockfd = None
        db = None
        try:
            _private_dir(rootfd)
            dirfd = os.open('orchestrator-memory.noindex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=rootfd)
            _private_dir(dirfd)
            if write or durable_receipt:
                # Persist the state-directory entry for the memory folder even
                # when a previous creator failed to sync it.
                os.fsync(rootfd)
            try:
                lockfd = os.open('memory.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            except FileExistsError:
                lockfd = os.open('memory.lock', os.O_RDWR | os.O_NOFOLLOW, dir_fd=dirfd)
            _private_file(lockfd)
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Memory lock busy')
                    time.sleep(0.02)
            db = sqlite3.connect(':memory:')
            db.execute('PRAGMA temp_store=MEMORY')
            db.row_factory = sqlite3.Row
            try:
                filefd = os.open('memory.enc', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
            except FileNotFoundError:
                filefd = None
            if filefd is not None:
                try:
                    info = _private_file(filefd)
                    if info.st_size > MAX_STORE_BYTES:
                        raise ValueError('Memory store too large')
                    chunks = bytearray()
                    while len(chunks) <= MAX_STORE_BYTES:
                        chunk = os.read(filefd, min(65536, MAX_STORE_BYTES + 1 - len(chunks)))
                        if not chunk:
                            break
                        chunks.extend(chunk)
                    if len(chunks) > MAX_STORE_BYTES:
                        raise ValueError('Memory store too large')
                finally:
                    os.close(filefd)
                plain = self.crypt(bytes(chunks), PURPOSE, 'open')
                if not isinstance(plain, bytes) or len(plain) > MAX_STORE_BYTES:
                    raise ValueError('Invalid memory store')
                db.deserialize(plain)
                version = db.execute('PRAGMA user_version').fetchone()[0]
                if version != SCHEMA_VERSION:
                    raise ValueError('Unsupported memory schema version')
            else:
                db.executescript('''
                    CREATE TABLE facts(project_id TEXT, id TEXT, revision INTEGER, text TEXT,
                        source TEXT, created_at REAL, updated_at REAL, PRIMARY KEY(project_id,id));
                    CREATE TABLE checkpoints(project_id TEXT, work_id TEXT, id TEXT, version INTEGER,
                        payload TEXT, created_at REAL, PRIMARY KEY(project_id,id),
                        UNIQUE(project_id,work_id,version));
                    CREATE TABLE operations(project_id TEXT, id TEXT, signature TEXT, result TEXT,
                        PRIMARY KEY(project_id,id));
                    PRAGMA user_version=1;
                ''')
            yield db
            if write and db.total_changes:
                db.commit()
                plain = db.serialize()
                if len(plain) > MAX_STORE_BYTES:
                    raise ValueError('Memory store too large')
                sealed = self.crypt(plain, PURPOSE, 'seal')
                if not isinstance(sealed, bytes) or len(sealed) > MAX_STORE_BYTES:
                    raise ValueError('Invalid encrypted memory store')
                # Same-directory replace: a crash yields either previous or new ciphertext.
                tmpname = '.memory-' + str(uuid.uuid4())
                tmpfd = os.open(tmpname, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=dirfd)
                try:
                    with os.fdopen(tmpfd, 'wb') as stream:
                        stream.write(sealed)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(tmpname, 'memory.enc', src_dir_fd=dirfd, dst_dir_fd=dirfd)
                    os.fsync(dirfd)
                finally:
                    try:
                        os.unlink(tmpname, dir_fd=dirfd)
                    except FileNotFoundError:
                        pass
            elif (write or durable_receipt) and filefd is not None:
                # A previous atomic replace may be visible after its directory
                # sync failed; do not confirm its receipt before retrying.
                os.fsync(dirfd)
        finally:
            if db is not None:
                db.close()
            if lockfd is not None:
                os.close(lockfd)
            if dirfd is not None:
                os.close(dirfd)
            os.close(rootfd)

    def _mutate(self, project_id, operation_id, payload, action):
        self._project(project_id)
        _uuid(operation_id)
        signature = hashlib.sha256(_json(payload).encode()).hexdigest()
        with self._store(write=True) as db:
            previous = db.execute('SELECT signature,result FROM operations WHERE project_id=? AND id=?',
                                  (project_id, operation_id)).fetchone()
            if previous:
                if previous['signature'] != signature:
                    raise ValueError('Operation ID bound to different input')
                return json.loads(previous['result'])
            result = action(db)
            db.execute('INSERT INTO operations VALUES (?,?,?,?)', (project_id, operation_id, signature, _json(result)))
            return result

    def get_operation(self, project_id, operation_id):
        """Return a durable mutation receipt; absence does not prove a worker stopped."""
        self._project(project_id); _uuid(operation_id)
        with self._store(durable_receipt=True) as db:
            row = db.execute('SELECT signature,result FROM operations WHERE project_id=? AND id=?',
                             (project_id, operation_id)).fetchone()
            if row is None:
                return {'state': 'not_recorded'}
            return {'state': 'recorded', 'signature': row['signature'], 'result': json.loads(row['result'])}

    def create_fact(self, project_id, operation_id, text, source):
        text, source = _text(text), _source(source)
        payload = {'action': 'fact_create', 'text': text, 'source': source}
        def action(db):
            ident, now = str(uuid.uuid4()), time.time()
            db.execute('INSERT INTO facts VALUES (?,?,?,?,?,?,?)', (project_id, ident, 1, text, _json(source), now, now))
            return {'id': ident, 'revision': 1}
        return self._mutate(project_id, operation_id, payload, action)

    def get_fact(self, project_id, fact_id):
        self._project(project_id); _uuid(fact_id)
        with self._store() as db:
            row = db.execute('SELECT id,revision,text,source,created_at,updated_at FROM facts WHERE project_id=? AND id=?',
                             (project_id, fact_id)).fetchone()
            return self._fact(row)

    @staticmethod
    def _fact(row):
        if row is None:
            return None
        result = dict(row)
        result['source'] = json.loads(result['source'])
        result['recorded_by'] = result['source'].pop('recorded_by', {'status': 'unknown'})
        result['status'] = 'asserted'
        return result

    def search_facts(self, project_id, query, *, limit=50, offset=0):
        """Literal casefolded substring search; no cross-project index or inference."""
        self._project(project_id)
        query = _text(query, limit=1024).casefold()
        self._page(limit, offset)
        with self._store() as db:
            db.create_function('memory_contains', 1, lambda text: int(query in text.casefold()))
            rows = db.execute('SELECT id,revision,text,source,created_at,updated_at FROM facts '
                              'WHERE project_id=? AND memory_contains(text) '
                              'ORDER BY created_at,id LIMIT ? OFFSET ?', (project_id, limit, offset)).fetchall()
            return [self._fact(row) for row in rows]

    def list_facts(self, project_id, *, limit=50, offset=0):
        self._project(project_id)
        self._page(limit, offset)
        with self._store() as db:
            rows = db.execute('SELECT id,revision,text,source,created_at,updated_at FROM facts WHERE project_id=? ORDER BY created_at,id LIMIT ? OFFSET ?',
                              (project_id, limit, offset)).fetchall()
            return [self._fact(row) for row in rows]

    @staticmethod
    def _page(limit, offset):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= MAX_LIST or not 0 <= offset <= 10000:
            raise ValueError('Invalid pagination')

    def update_fact(self, project_id, fact_id, operation_id, expected_revision, text, source):
        _uuid(fact_id); self._revision(expected_revision)
        text, source = _text(text), _source(source)
        payload = {'action': 'fact_update', 'id': fact_id, 'revision': expected_revision, 'text': text, 'source': source}
        def action(db):
            cursor = db.execute('UPDATE facts SET text=?,source=?,revision=revision+1,updated_at=? WHERE project_id=? AND id=? AND revision=?',
                                (text, _json(source), time.time(), project_id, fact_id, expected_revision))
            if cursor.rowcount != 1:
                raise ValueError('Missing fact or revision conflict')
            return {'id': fact_id, 'revision': expected_revision + 1}
        return self._mutate(project_id, operation_id, payload, action)

    @staticmethod
    def _revision(revision, *, allow_zero=False):
        if type(revision) is not int or not (0 if allow_zero else 1) <= revision <= 1_000_000:
            raise ValueError('Invalid revision')

    def delete_fact(self, project_id, fact_id, operation_id, expected_revision, *, recorded_by=None):
        _uuid(fact_id); self._revision(expected_revision)
        payload = {'action': 'fact_delete', 'id': fact_id, 'revision': expected_revision}
        if recorded_by is not None:
            payload['recorded_by'] = _source({'kind': 'host', 'reference': 'profile',
                'producer': 'host', 'observed_at': 'host', 'recorded_by': recorded_by})['recorded_by']
        def action(db):
            cursor = db.execute('DELETE FROM facts WHERE project_id=? AND id=? AND revision=?',
                                (project_id, fact_id, expected_revision))
            if cursor.rowcount != 1:
                raise ValueError('Missing fact or revision conflict')
            result = {'id': fact_id, 'deleted': True}
            if recorded_by is not None:
                result['recorded_by'] = payload['recorded_by']
            return result
        return self._mutate(project_id, operation_id, payload, action)

    def create_checkpoint(self, project_id, work_id, operation_id, expected_version, *, fact_ids,
                          decisions, constraints, next_action, uncertain_effects, source):
        _uuid(work_id); self._revision(expected_version, allow_zero=True)
        if not isinstance(fact_ids, list) or len(fact_ids) > MAX_LIST:
            raise ValueError('Invalid fact references')
        for fact_id in fact_ids:
            _uuid(fact_id)
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError('Duplicate fact references')
        decisions, constraints, uncertain_effects = map(_strings, (decisions, constraints, uncertain_effects))
        next_action, source = _text(next_action, limit=2048), _source(source)
        payload = {'action': 'checkpoint_create', 'work_id': work_id, 'expected_version': expected_version,
                   'fact_ids': fact_ids, 'decisions': decisions, 'constraints': constraints,
                   'next_action': next_action, 'uncertain_effects': uncertain_effects, 'source': source}
        def action(db):
            latest = db.execute('SELECT COALESCE(MAX(version),0) FROM checkpoints WHERE project_id=? AND work_id=?',
                                (project_id, work_id)).fetchone()[0]
            if latest != expected_version:
                raise ValueError('Checkpoint version conflict')
            facts = []
            for ident in fact_ids:
                row = db.execute('SELECT id,revision,text,source,created_at,updated_at FROM facts WHERE project_id=? AND id=?',
                                 (project_id, ident)).fetchone()
                if row is None:
                    raise ValueError('Fact not found in project')
                facts.append(self._fact(row))
            checkpoint = {'facts': facts, 'decisions': decisions, 'constraints': constraints,
                          'next_action': next_action, 'uncertain_effects': uncertain_effects, 'source': source}
            ident = str(uuid.uuid4()); version = latest + 1
            db.execute('INSERT INTO checkpoints VALUES (?,?,?,?,?,?)',
                       (project_id, work_id, ident, version, _json(checkpoint), time.time()))
            return {'id': ident, 'work_id': work_id, 'version': version}
        return self._mutate(project_id, operation_id, payload, action)

    def get_checkpoint(self, project_id, checkpoint_id):
        self._project(project_id); _uuid(checkpoint_id)
        with self._store() as db:
            row = db.execute('SELECT id,work_id,version,payload,created_at FROM checkpoints WHERE project_id=? AND id=?',
                             (project_id, checkpoint_id)).fetchone()
            if row is None: return None
            result = dict(row); result['payload'] = json.loads(result['payload'])
            return result

    def list_checkpoints(self, project_id, work_id, *, limit=50, offset=0):
        self._project(project_id); _uuid(work_id); self._page(limit, offset)
        with self._store() as db:
            rows = db.execute('SELECT id,work_id,version,created_at FROM checkpoints WHERE project_id=? AND work_id=? ORDER BY version DESC LIMIT ? OFFSET ?',
                              (project_id, work_id, limit, offset)).fetchall()
            return [dict(row) for row in rows]
