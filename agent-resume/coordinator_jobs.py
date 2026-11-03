"""Durable, encrypted drafts for host-authorized coordination; no dispatch."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

import orchestrator_memory
import vault

PURPOSE = 'coordinator-jobs-v1'
VERSION = 1
MAX_STORE = 8 * 1024 * 1024
MAX_PROPOSAL = 12 * 1024
MAX_JOBS = 5000
NAMESPACE = uuid.UUID('309d6c9e-dade-4d6b-8e07-cc7d293c0e03')


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError('Canonical UUID required')
    try:
        parsed = str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError('Canonical UUID required') from exc
    if value != parsed:
        raise ValueError('Canonical UUID required')
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _text(value, max_bytes=4096):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > max_bytes:
        raise ValueError('Bounded nonempty text required')
    return value


def _strings(value):
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError('Bounded list required')
    return [_text(item, 1024) for item in value]


def _proposal(value):
    if not isinstance(value, dict) or set(value) != {'objective', 'acceptance', 'constraints', 'questions'}:
        raise ValueError('Invalid proposal fields')
    proposal = {'objective': _text(value['objective']), 'acceptance': _strings(value['acceptance']),
                'constraints': _strings(value['constraints']), 'questions': _strings(value['questions'])}
    if not proposal['acceptance'] or len(_json(proposal).encode()) > MAX_PROPOSAL:
        raise ValueError('Acceptance criteria and bounded proposal required')
    return proposal


def _authority(value, capability):
    """Validate trusted host claims; neither this function nor a UUID authenticates an operator."""
    keys = {'project_id', 'work_id', 'routing_role', 'routing_actor', 'backend', 'mode', 'billing', 'billing_authorized',
            'permission_granted', 'workspace_write_authorized', 'max_requests', 'max_seconds',
            'max_cost_minor', 'currency'}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError('Explicit host authority required')
    project = _uuid(value['project_id'])
    _uuid(value['work_id'])
    # These are host-declared routing provenance, not evidence that a harness ran.
    for field in ('routing_role', 'routing_actor'):
        identifier = _text(value[field], 64)
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', identifier):
            raise ValueError('Invalid routing provenance')
    backend = _text(value['backend'], 64)
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', backend):
        raise ValueError('Invalid backend identifier')
    mode = value['mode']
    if mode not in ('expertise', 'implementation'):
        raise ValueError('Invalid mode')
    if value['permission_granted'] is not True or value['billing_authorized'] is not True:
        raise ValueError('Explicit host permission and billing authorization required')
    if type(value['workspace_write_authorized']) is not bool:
        raise ValueError('Explicit workspace permission required')
    if mode == 'implementation' and (backend != 'codex' or value['workspace_write_authorized'] is not True):
        raise ValueError('Implementation requires explicit Codex workspace grant')
    if not isinstance(capability, dict) or capability.get('id') != backend or capability.get('configured') is not True:
        raise ValueError('Requested backend not configured in capability snapshot')
    modes = capability.get('modes')
    if not isinstance(modes, list) or mode not in modes:
        raise ValueError('Requested mode absent from capability snapshot')
    if mode == 'implementation' and capability.get('workspace_write_authorized') is not True:
        raise ValueError('Workspace capability not granted')
    _text(value['billing'], 120)
    if type(value['max_requests']) is not int or not 1 <= value['max_requests'] <= 100:
        raise ValueError('Invalid request budget')
    if type(value['max_seconds']) is not int or not 1 <= value['max_seconds'] <= 86400:
        raise ValueError('Invalid time budget')
    if type(value['max_cost_minor']) is not int or not 0 <= value['max_cost_minor'] <= 10**12:
        raise ValueError('Invalid monetary budget')
    if not isinstance(value['currency'], str) or not re.fullmatch(r'[A-Z]{3}', value['currency']):
        raise ValueError('Explicit currency required')
    return dict(value, project_id=project)


def _directory(fd):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Private owned directory required')


def _file(fd):
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
        raise ValueError('Private owned regular file required')
    return info


class Jobs:
    """Project-isolated encrypted drafts. Caller is trusted and authenticates the operator."""
    def __init__(self, state_directory, *, crypt=None):
        self.state = Path(state_directory).expanduser().absolute()
        self.crypt = crypt or vault.crypt
        root = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            _directory(root)
            try:
                os.mkdir('coordinator-jobs.noindex', 0o700, dir_fd=root)
            except FileExistsError:
                pass
            folder = os.open('coordinator-jobs.noindex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            try:
                _directory(folder)
            finally:
                os.close(folder)
        finally:
            os.close(root)

    def _project(self, project_id):
        # Same read-only project lookup as Memory; project ID alone is not a grant.
        return orchestrator_memory.Memory._project(self, project_id)

    @contextlib.contextmanager
    def _store(self, *, write=False, durable=False):
        root = os.open(self.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        folder = lock = None
        try:
            _directory(root)
            folder = os.open('coordinator-jobs.noindex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
            _directory(folder)
            if write or durable:
                os.fsync(root)
            try:
                lock = os.open('jobs.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
            except FileExistsError:
                lock = os.open('jobs.lock', os.O_RDWR | os.O_NOFOLLOW, dir_fd=folder)
            _file(lock)
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Jobs lock busy')
                    time.sleep(.02)
            try:
                fd = os.open('jobs.enc', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
            except FileNotFoundError:
                fd = None
            if fd is None:
                data = {'version': VERSION, 'jobs': []}
            else:
                try:
                    info = _file(fd)
                    if info.st_size > MAX_STORE:
                        raise ValueError('Jobs store too large')
                    encrypted = bytearray()
                    while len(encrypted) <= MAX_STORE:
                        block = os.read(fd, min(65536, MAX_STORE + 1 - len(encrypted)))
                        if not block:
                            break
                        encrypted.extend(block)
                    if len(encrypted) > MAX_STORE:
                        raise ValueError('Jobs store too large')
                finally:
                    os.close(fd)
                plain = self.crypt(bytes(encrypted), PURPOSE, 'open')
                if not isinstance(plain, bytes) or len(plain) > MAX_STORE:
                    raise ValueError('Invalid jobs store')
                data = json.loads(plain)
                if (not isinstance(data, dict) or set(data) != {'version', 'jobs'} or data['version'] != VERSION
                        or not isinstance(data['jobs'], list) or len(data['jobs']) > MAX_JOBS):
                    raise ValueError('Unsupported or corrupt jobs store')
            before = _json(data)
            yield data
            if write and _json(data) != before:
                plain = _json(data).encode('utf-8')
                if len(plain) > MAX_STORE:
                    raise ValueError('Jobs store too large')
                encrypted = self.crypt(plain, PURPOSE, 'seal')
                if not isinstance(encrypted, bytes) or len(encrypted) > MAX_STORE:
                    raise ValueError('Invalid ciphertext')
                name = '.jobs-' + str(uuid.uuid4())
                tmp = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                try:
                    with os.fdopen(tmp, 'wb') as stream:
                        stream.write(encrypted); stream.flush(); os.fsync(stream.fileno())
                    os.replace(name, 'jobs.enc', src_dir_fd=folder, dst_dir_fd=folder)
                    os.fsync(folder)
                finally:
                    try:
                        os.unlink(name, dir_fd=folder)
                    except FileNotFoundError:
                        pass
            elif (write or durable) and fd is not None:
                # A previous replace may have succeeded before its fsync failed.
                os.fsync(folder)
        finally:
            if lock is not None: os.close(lock)
            if folder is not None: os.close(folder)
            os.close(root)

    def prepare(self, operation_id, proposal, *, authority, capability):
        """Persist an idempotent draft and key; never invoke any model, bridge or worker."""
        operation = _uuid(operation_id)
        authority = _authority(authority, capability)
        proposal = _proposal(proposal)
        project = authority['project_id']
        self._project(project)
        payload = {'proposal': proposal, 'authority': authority}
        fingerprint = hashlib.sha256(_json(payload).encode('utf-8')).hexdigest()
        # Operation IDs are scoped by project; the derived identifiers are not credentials.
        job_id = str(uuid.uuid5(NAMESPACE, 'job:' + project + ':' + operation))
        request_key = str(uuid.uuid5(NAMESPACE, 'request:' + project + ':' + operation))
        with self._store(write=True) as data:
            for item in data['jobs']:
                if item['project_id'] == project and item['operation_id'] == operation:
                    if item['fingerprint'] != fingerprint:
                        raise ValueError('Operation ID bound to different draft')
                    return self._public(item)
            if len(data['jobs']) >= MAX_JOBS:
                raise ValueError('Jobs capacity reached')
            record = {'project_id': project, 'operation_id': operation, 'job_id': job_id,
                      'request_key': request_key, 'fingerprint': fingerprint, 'created_at': time.time(),
                      'status': 'draft', 'proposal': proposal, 'authority': authority}
            data['jobs'].append(record)
            return self._public(record)

    @staticmethod
    def _public(item):
        # Legacy recorded rows were accepted using only a key and result hash;
        # neither proves the recorded request had this mission's payload.
        legacy_recorded = item['status'] == 'recorded'
        return {'job_id': item['job_id'], 'request_key': item['request_key'],
                'project_id': item['project_id'], 'status': 'indeterminate' if legacy_recorded else item['status'],
                'created_at': item['created_at'], 'proposal': item['proposal'],
                'authority': item['authority'], 'revision': item.get('revision', 0),
                'attempt_id': item.get('attempt_id'), 'result_receipt': None if legacy_recorded else item.get('result_receipt'),
                'authorization_evidence': item.get('authorization_evidence'),
                'dispatch_enabled': False}

    def reserve(self, project_id, job_id, *, expected_revision, attempt_id):
        """Persist an intent before handoff; it does not prove bridge admission."""
        self._project(_uuid(project_id)); _uuid(job_id); _uuid(attempt_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Invalid revision')
        with self._store(write=True) as data:
            for row in data['jobs']:
                if row['project_id'] == project_id and row['job_id'] == job_id:
                    if row['status'] != 'draft' or row.get('revision', 0) != expected_revision:
                        raise ValueError('Job already admitted or revision conflict')
                    row.update(status='dispatch_intent', revision=expected_revision + 1,
                               attempt_id=attempt_id)
                    return self._public(row)
            raise ValueError('Job not found')

    def confirm_authorization(self, project_id, job_id, *, attempt_id, evidence_id, proof_sha256):
        """Record the verifier's latest evidence before a bridge call, not its truth."""
        self._project(_uuid(project_id)); _uuid(job_id); _uuid(attempt_id)
        _text(evidence_id, 200)
        if not isinstance(proof_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', proof_sha256):
            raise ValueError('Invalid proof digest')
        with self._store(write=True) as data:
            for row in data['jobs']:
                if row['project_id'] == project_id and row['job_id'] == job_id:
                    if row['status'] != 'dispatch_intent' or row.get('attempt_id') != attempt_id:
                        raise ValueError('Attempt identity or state mismatch')
                    row['authorization_evidence'] = {'evidence_id': evidence_id, 'proof_sha256': proof_sha256}
                    row['status'] = 'dispatch_ready'
                    row['revision'] = row.get('revision', 0) + 1
                    return self._public(row)
            raise ValueError('Job not found')

    def record_outcome(self, project_id, job_id, *, attempt_id, status, receipt=None):
        """Record conservative outcome for exactly this admitted attempt."""
        self._project(_uuid(project_id)); _uuid(job_id); _uuid(attempt_id)
        if status not in ('not_admitted', 'indeterminate'):
            raise ValueError('Invalid outcome')
        with self._store(write=True) as data:
            for row in data['jobs']:
                if row['project_id'] != project_id or row['job_id'] != job_id:
                    continue
                if row['status'] not in ('dispatch_intent', 'dispatch_ready', 'indeterminate') or row.get('attempt_id') != attempt_id:
                    raise ValueError('Attempt identity or state mismatch')
                if row['status'] == 'indeterminate':
                    raise ValueError('Uncertain attempt cannot be reset')
                if receipt is not None:
                    raise ValueError('Unexpected receipt')
                row['status'] = status
                row['revision'] = row.get('revision', 0) + 1
                return self._public(row)
            raise ValueError('Job not found')

    def get(self, project_id, job_id):
        self._project(_uuid(project_id)); _uuid(job_id)
        with self._store(durable=True) as data:
            return next((self._public(row) for row in data['jobs'] if row['project_id'] == project_id
                         and row['job_id'] == job_id), None)

    def recover(self, project_id, operation_id):
        """Find a draft by its original operation ID without treating absence as stopped work."""
        project_id, operation_id = _uuid(project_id), _uuid(operation_id)
        job_id = str(uuid.uuid5(NAMESPACE, 'job:' + project_id + ':' + operation_id))
        return self.get(project_id, job_id)

    def list(self, project_id, *, limit=50, offset=0):
        self._project(_uuid(project_id))
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError('Invalid pagination')
        with self._store(durable=True) as data:
            selected = [self._public(row) for row in data['jobs'] if row['project_id'] == project_id]
            return selected[offset:offset + limit]
