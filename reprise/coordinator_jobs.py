"""Durable, encrypted drafts for host-authorized coordination; no dispatch."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

import orchestrator_memory
import vault

PURPOSE = 'coordinator-jobs-v1'
VERSION = 1
MAX_STORE = 8 * 1024 * 1024
MAX_PROPOSAL = 12 * 1024
MAX_JOBS = 5000
MAX_CLARIFICATIONS = 24 * 1024
NAMESPACE = uuid.UUID('309d6c9e-dade-4d6b-8e07-cc7d293c0e03')


class WriteUncertainError(OSError):
    """Atomic replacement happened but durability could not be confirmed."""


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


def _fingerprint(proposal, authority):
    return hashlib.sha256(_json({'proposal': proposal, 'authority': authority}).encode('utf-8')).hexdigest()


def _request_key(project, operation, fingerprint):
    # Every proposal revision gets its own request identity; operation_id stays stable.
    return str(uuid.uuid5(NAMESPACE, 'request:' + project + ':' + operation + ':' + fingerprint))


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
    def _store(self, *, write=False, durable=False, commit_guard=None):
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
                    # Source guard is entered only after crypto and temp-file sync.
                    with commit_guard() if commit_guard else contextlib.nullcontext():
                        os.replace(name, 'jobs.enc', src_dir_fd=folder, dst_dir_fd=folder)
                        try:
                            os.fsync(folder)
                        except OSError as exc:
                            raise WriteUncertainError('Jobs replacement durability uncertain') from exc
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

    def consent_context(self, project_id, work_id):
        """Read the existing registry only; work is the explicit operator selection.

        This binding is not evidence that a model, billing or execution is available.
        The probe lives in a separate process so SQLite lock ownership is preserved.
        """
        project, work = _uuid(project_id), _uuid(work_id)
        try:
            response = subprocess.run([sys.executable, '-I', '-c', orchestrator_memory._REGISTRY_PROBE],
                input=_json({'state': str(self.state), 'project_id': project, 'include_root': True}),
                text=True, capture_output=True, timeout=5, check=False)
            found = json.loads(response.stdout)
            if (response.returncode != 0 or not isinstance(found, dict)
                    or set(found) != {'status', 'root_path', 'temp_store'}
                    or found['status'] != 'ok' or found['temp_store'] != 2):
                raise ValueError('Host context unavailable')
            root = Path(found['root_path']).resolve(strict=True)
            if not root.is_dir(): raise ValueError('Host context unavailable')
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError) as exc:
            raise ValueError('Host context unavailable') from exc
        context = {'schema': 1, 'source': 'operator_gui', 'project_id': project,
                   'work_id': work, 'project_root': str(root), 'execution_verification': 'not_performed'}
        return {'context': context, 'context_sha256': hashlib.sha256(_json(context).encode()).hexdigest()}

    def consent(self, project_id, operation_id, *, authorization_id, expected_revision,
                expected_proposal_sha256, host_context, expected_context_sha256, backend="codex"):
        """Record operator consent only, atomically and without an execution authority."""
        if backend not in ("codex", "foundation-models"):
            raise ValueError("Explicit supported consent backend required")
        project, operation, authorization = _uuid(project_id), _uuid(operation_id), _uuid(authorization_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Invalid expected revision')
        for digest in (expected_proposal_sha256, expected_context_sha256):
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
                raise ValueError('Invalid consent identity')
        if (not isinstance(host_context, dict) or set(host_context) !=
                {'schema', 'source', 'project_id', 'work_id', 'project_root', 'execution_verification'}):
            raise ValueError('Invalid host context')
        current = self.consent_context(project, host_context['work_id'])
        if current['context'] != host_context or current['context_sha256'] != expected_context_sha256:
            raise ValueError('Host context changed')
        identity = {'authorization_id': authorization, 'preparation_revision': expected_revision,
                    'proposal_sha256': expected_proposal_sha256, 'context_sha256': expected_context_sha256,
                    'host_context': current['context'], 'backend': backend, 'mode': 'expertise',
                    'billing_verification': 'not_performed', 'execution_authorized': False}
        with self._store(write=True) as data:
            for row in data['jobs']:
                if row['project_id'] != project or row.get('operation_id') != operation: continue
                if 'clarifications' in row:
                    identity['clarifications_sha256'] = hashlib.sha256(_json(row['clarifications']).encode()).hexdigest()
                digest = hashlib.sha256(_json(identity).encode()).hexdigest()
                previous = row.get('operator_authorization')
                if previous and previous['authorization_id'] == authorization:
                    if previous['authorization_sha256'] != digest:
                        raise ValueError('Authorization conflict')
                    return self._public(row)
                if row['status'] != 'preparation':
                    raise ValueError('Mission locked')
                if row.get('candidate_import') and row['candidate_import']['origin']['work_id'] != host_context['work_id']:
                    raise ValueError('Host context changed')
                if row.get('revision', 0) != expected_revision:
                    raise ValueError('Revision conflict')
                proposal_digest = hashlib.sha256(_json(row['proposal']).encode()).hexdigest()
                if proposal_digest != expected_proposal_sha256:
                    raise ValueError('Proposal identity changed')
                if row['proposal']['questions'] or any(not item['resolved'] for item in row.get('clarifications', [])):
                    raise ValueError('Clarification required')
                authorization_record = dict(identity, authorization_sha256=digest, authorized_at=time.time())
                fingerprint = hashlib.sha256(_json({'proposal': row['proposal'],
                    'operator_authorization': identity}).encode()).hexdigest()
                row.update(status='consented', authority=None, authorization_kind='operator_consent_v1',
                    operator_authorization=authorization_record, fingerprint=fingerprint,
                    request_key=_request_key(project, operation, fingerprint), revision=expected_revision + 1)
                return self._public(row)
            raise ValueError('Job not found')

    def reserve_local(self, project_id, operation_id, *, run_id, expected_revision, manifest):
        """Persist one execution intent; only Store.guarded_handoff admits it."""
        project, operation, run = _uuid(project_id), _uuid(operation_id), _uuid(run_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Invalid expected revision')
        snapshot = json.loads(_json(manifest))
        identity = hashlib.sha256(_json(snapshot).encode()).hexdigest()
        self._project(project)
        with self._store(write=True) as data:
            for row in data['jobs']:
                if row['project_id'] != project or row.get('operation_id') != operation: continue
                prior = row.get('local_execution')
                if prior:
                    if prior['run_id'] != run or prior['manifest_sha256'] != identity:
                        raise ValueError('Execution identity conflict')
                    return self._public(row), False
                consent = row.get('operator_authorization') or {}
                if (row['status'] != 'consented' or consent.get('backend') != 'foundation-models'
                        or consent.get('execution_authorized') is not False):
                    raise ValueError('Explicit local consent required')
                if row.get('revision') != expected_revision: raise ValueError('Revision conflict')
                host = consent['host_context']
                if 'clarifications' in row:
                    clarifications_sha = hashlib.sha256(_json(row['clarifications']).encode()).hexdigest()
                    if (snapshot.get('clarifications_sha256') != clarifications_sha
                            or consent.get('clarifications_sha256') != clarifications_sha):
                        raise ValueError('Execution manifest mismatch')
                if (snapshot.get('project_id') != project or snapshot.get('work_id') != host['work_id']
                        or snapshot.get('project_root') != host['project_root']
                        or snapshot.get('adapter') != 'native-foundation-explain'
                        or snapshot.get('mode') != 'expertise' or snapshot.get('tools') is not False
                        or snapshot.get('automatic_fallback') is not False
                        or snapshot.get('stop_on_deadline') is not True
                        or type(snapshot.get('max_requests')) is not int or snapshot['max_requests'] != 1
                        or type(snapshot.get('max_seconds')) is not int or not 1 <= snapshot['max_seconds'] <= 120
                        or snapshot.get('supervision_contract') != 'foundation-explain-v1-self-deadline'
                        or snapshot.get('cost_policy') != 'local_helper_only_no_provider_route'):
                    raise ValueError('Execution manifest mismatch')
                if (snapshot.get('job_id') != row['job_id'] or snapshot.get('request_key') != row['request_key']
                        or snapshot.get('consent_id') != consent['authorization_id']
                        or snapshot.get('context_sha256') != consent['context_sha256']
                        or snapshot.get('proposal_sha256') != hashlib.sha256(_json(row['proposal']).encode()).hexdigest()
                        or snapshot.get('backend') != 'foundation-models'):
                    raise ValueError('Execution manifest mismatch')
                row['local_execution'] = {'run_id': run, 'manifest_sha256': identity,
                    'manifest': snapshot, 'stage': 'intent', 'stop_requested': False,
                    'created_at': time.time(), 'receipt': None}
                row.update(status='local_pending', revision=expected_revision + 1)
                return self._public(row), True
            raise ValueError('Job not found')

    def local_update(self, project_id, operation_id, run_id, *, stage=None, receipt=None, stop=False):
        """Host projection only; never establishes an admission or repeats work."""
        project, operation, run = _uuid(project_id), _uuid(operation_id), _uuid(run_id)
        if stage not in (None, 'admitted', 'running', 'recorded', 'indeterminate'):
            raise ValueError('Invalid execution stage')
        with self._store(write=True) as data:
            for row in data['jobs']:
                if row['project_id'] != project or row.get('operation_id') != operation: continue
                execution = row.get('local_execution')
                if not execution or execution['run_id'] != run: raise ValueError('Execution identity conflict')
                if execution['stage'] == 'recorded': return self._public(row)
                if stop: execution['stop_requested'] = True
                if stage:
                    execution['stage'] = stage
                    row['status'] = 'local_' + stage
                if receipt is not None:
                    if stage != 'recorded': raise ValueError('Terminal receipt required')
                    if receipt.get('run_id') != run or receipt.get('request_key') != row['request_key']:
                        raise ValueError('Receipt identity mismatch')
                    execution['receipt'] = json.loads(_json(receipt))
                row['revision'] = row.get('revision', 0) + 1
                return self._public(row)
            raise ValueError('Job not found')

    def prepare(self, operation_id, proposal, *, authority, capability):
        """Persist an idempotent draft and key; never invoke any model, bridge or worker."""
        operation = _uuid(operation_id)
        authority = _authority(authority, capability)
        proposal = _proposal(proposal)
        project = authority['project_id']
        self._project(project)
        fingerprint = _fingerprint(proposal, authority)
        # Operation IDs are scoped by project; the derived identifiers are not credentials.
        job_id = str(uuid.uuid5(NAMESPACE, 'job:' + project + ':' + operation))
        request_key = _request_key(project, operation, fingerprint)
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
                      'status': 'draft', 'proposal': proposal, 'authority': authority, 'revision': 0}
            data['jobs'].append(record)
            return self._public(record)

    def prepare_proposal(self, project_id, operation_id, proposal):
        """Persist operator-authored preparation without authority or request identity."""
        project, operation = _uuid(project_id), _uuid(operation_id)
        proposal = _proposal(proposal)
        self._project(project)
        job_id = str(uuid.uuid5(NAMESPACE, 'job:' + project + ':' + operation))
        fingerprint = _fingerprint(proposal, None)
        with self._store(write=True) as data:
            for row in data['jobs']:
                if row['project_id'] == project and row['operation_id'] == operation:
                    if row['status'] != 'preparation' or row['fingerprint'] != fingerprint:
                        raise ValueError('Operation ID bound to another preparation')
                    return self._public(row)
            if len(data['jobs']) >= MAX_JOBS:
                raise ValueError('Jobs capacity reached')
            row = {'project_id': project, 'operation_id': operation, 'job_id': job_id,
                   'request_key': None, 'fingerprint': fingerprint, 'created_at': time.time(),
                   'status': 'preparation', 'proposal': proposal, 'authority': None, 'revision': 0}
            data['jobs'].append(row)
            return self._public(row)

    def import_candidate(self, project_id, operation_id, extracted, *, import_id, work_id,
                         expected_revision, expected_proposal_sha256, operator_confirmed):
        """Explicit host import into a different preparation, never authority or dispatch."""
        import coordinator_candidate as candidates
        from delegation_store import digest
        project, operation, work, mutation = map(_uuid, (project_id, operation_id, work_id, import_id))
        extracted = json.loads(_json(extracted))
        if operator_confirmed is not True:
            raise ValueError('Explicit candidate confirmation required')
        candidate = candidates.parse(_json(extracted['candidate']))
        if digest(candidate) != extracted['candidate_sha256']:
            raise ValueError('Candidate identity mismatch')
        origin = json.loads(_json(extracted['origin']))
        if (origin['project_id'] != project or origin['work_id'] != work
                or origin['source_operation_id'] == operation):
            raise ValueError('Candidate source identity mismatch')
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise ValueError('Invalid expected revision')
        if (expected_revision is None) != (expected_proposal_sha256 is None):
            raise ValueError('Expected preparation identity required')
        self._project(project)
        record = {'schema': candidates.SCHEMA, 'import_id': mutation, 'origin': origin,
                  'candidate': candidate, 'candidate_sha256': extracted['candidate_sha256'],
                  'expected_revision': expected_revision, 'expected_proposal_sha256': expected_proposal_sha256,
                  'operator_confirmed': True, 'source_evidence': json.loads(_json(extracted['evidence']))}
        with self._store(write=True, commit_guard=lambda: candidates.revalidate(self.state, extracted)) as data:
            source = next((row for row in data['jobs'] if row['project_id'] == project
                           and row.get('operation_id') == origin['source_operation_id']), None)
            if (source is None or candidates.origin_for(self._public(source)) != origin
                    or candidates.parse(source['local_execution']['receipt']['response']) != candidate):
                raise ValueError('Candidate source changed')
            row = next((row for row in data['jobs'] if row['project_id'] == project
                        and row.get('operation_id') == operation), None)
            if row and row.get('candidate_import'):
                if row['candidate_import'] == record:
                    return self._public(row)
                raise ValueError('Candidate import identity conflict')
            if row is None:
                if expected_revision is not None:
                    raise ValueError('Revision conflict')
                if len(data['jobs']) >= MAX_JOBS:
                    raise ValueError('Jobs capacity reached')
                row = {'project_id': project, 'operation_id': operation,
                       'job_id': str(uuid.uuid5(NAMESPACE, 'job:' + project + ':' + operation)),
                       'request_key': None, 'created_at': time.time(), 'status': 'preparation',
                       'authority': None, 'revision': 0}
                data['jobs'].append(row)
            else:
                if row['status'] != 'preparation':
                    raise ValueError('Mission locked')
                if (row.get('revision', 0) != expected_revision
                        or hashlib.sha256(_json(row['proposal']).encode()).hexdigest() != expected_proposal_sha256):
                    raise ValueError('Revision conflict')
                row['revision'] += 1
            proposal = candidate['proposal']
            clarifications = [{'id': str(uuid.uuid5(NAMESPACE, 'candidate-question:' + extracted['candidate_sha256'] + ':' + str(index))),
                               'question': question, 'answer': None, 'resolved': False, 'history': []}
                              for index, question in enumerate(proposal['questions'])]
            row.update(proposal=proposal, candidate_import=record, clarifications=clarifications,
                       fingerprint=_fingerprint(proposal, None))
            return self._public(row)

    def clarify_candidate(self, project_id, operation_id, *, question_id, answer, resolved,
                          resolution_id, expected_revision):
        """Answer and resolve explicitly; retain original question and every accepted answer."""
        project, operation, question, mutation = map(_uuid, (project_id, operation_id, question_id, resolution_id))
        if type(resolved) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Invalid clarification revision')
        answer = _text(answer, 1024)
        self._project(project)
        identity = {'resolution_id': mutation, 'answer': answer, 'resolved': resolved,
                    'expected_revision': expected_revision}
        with self._store(write=True) as data:
            row = next((row for row in data['jobs'] if row['project_id'] == project
                        and row.get('operation_id') == operation), None)
            if row is None:
                raise ValueError('Job not found')
            questions = row.get('clarifications') or []
            item = next((item for item in questions if item['id'] == question), None)
            if item is None:
                raise ValueError('Clarification not found')
            for other in questions:
                for previous in other['history']:
                    if previous['resolution_id'] == mutation:
                        if other['id'] == question and previous == identity:
                            return self._public(row)
                        raise ValueError('Clarification identity conflict')
            if row['status'] != 'preparation':
                raise ValueError('Mission locked')
            if row['revision'] != expected_revision:
                raise ValueError('Revision conflict')
            item['history'].append(identity)
            item.update(answer=answer, resolved=resolved)
            if 'disposition' in item:
                item['disposition'] = 'answered' if resolved else 'open'
            if len(_json(questions).encode()) > MAX_CLARIFICATIONS:
                raise ValueError('Clarification history capacity reached')
            current = row['proposal']['questions']
            if resolved:
                current[:] = [value for value in current if value != item['question']]
            elif item['question'] not in current:
                current.append(item['question'])
            row['proposal'] = _proposal(row['proposal'])
            row.update(revision=row['revision'] + 1, revised_at=time.time(),
                       fingerprint=_fingerprint(row['proposal'], None))
            return self._public(row)

    def review_clarification(self, project_id, operation_id, *, question_id, action, reason,
                             resolution_id, expected_revision):
        """An operator decision is not a fabricated answer or permission."""
        project, operation, question, mutation = map(_uuid, (project_id, operation_id, question_id, resolution_id))
        if action not in ('dismiss', 'reopen') or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Invalid clarification decision')
        reason = _text(reason, 1024)
        self._project(project)
        identity = {'resolution_id': mutation, 'action': action, 'reason': reason,
                    'expected_revision': expected_revision}
        with self._store(write=True) as data:
            row = next((row for row in data['jobs'] if row['project_id'] == project
                        and row.get('operation_id') == operation), None)
            if row is None:
                raise ValueError('Job not found')
            questions = row.get('clarifications') or []
            item = next((item for item in questions if item['id'] == question), None)
            if item is None:
                raise ValueError('Clarification not found')
            for other in questions:
                for previous in other['history']:
                    if previous['resolution_id'] == mutation:
                        if other['id'] == question and previous == identity:
                            return self._public(row)
                        raise ValueError('Clarification identity conflict')
            if row['status'] != 'preparation':
                raise ValueError('Mission locked')
            if row['revision'] != expected_revision:
                raise ValueError('Revision conflict')
            item['history'].append(identity)
            item.update(disposition='dismissed' if action == 'dismiss' else 'open',
                        resolved=action == 'dismiss')
            if len(_json(questions).encode()) > MAX_CLARIFICATIONS:
                raise ValueError('Clarification history capacity reached')
            current = row['proposal']['questions']
            if action == 'dismiss':
                current[:] = [value for value in current if value != item['question']]
            elif item['question'] not in current:
                current.append(item['question'])
            row['proposal'] = _proposal(row['proposal'])
            row.update(revision=row['revision'] + 1, revised_at=time.time(),
                       fingerprint=_fingerprint(row['proposal'], None))
            return self._public(row)

    def revise(self, project_id, operation_id, proposal, *, expected_revision):
        """Durably revise an operator-owned draft with CAS; never dispatch or alter admitted work."""
        project, operation = _uuid(project_id), _uuid(operation_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Invalid expected revision')
        proposal = _proposal(proposal)
        self._project(project)
        job_id = str(uuid.uuid5(NAMESPACE, 'job:' + project + ':' + operation))
        with self._store(write=True) as data:
            for row in data['jobs']:
                if row['project_id'] != project or row['job_id'] != job_id:
                    continue
                if row.get('operation_id') not in (None, operation):
                    raise ValueError('Operation identity mismatch')
                if row['status'] not in ('draft', 'preparation'):
                    raise ValueError('Draft only; admitted missions cannot be revised')
                if any(not item['resolved'] and item['question'] not in proposal['questions'] for item in row.get('clarifications', [])):
                    raise ValueError('Unresolved clarification cannot be removed')
                fingerprint = _fingerprint(proposal, row.get('authority'))
                # An exact replay is a no-op even when its original CAS token is now stale.
                if row.get('fingerprint') == fingerprint and row['proposal'] == proposal:
                    return self._public(row)
                revision = row.get('revision', 0)
                if type(revision) is not int or revision != expected_revision:
                    raise ValueError('Revision conflict')
                row.update(operation_id=operation, proposal=proposal, fingerprint=fingerprint,
                           request_key=(_request_key(project, operation, fingerprint)
                                        if row['status'] == 'draft' else None), revision=revision + 1,
                           revised_at=time.time())
                return self._public(row)
            raise ValueError('Job not found')

    @staticmethod
    def _public(item):
        # Legacy recorded rows were accepted using only a key and result hash;
        # neither proves the recorded request had this mission's payload.
        legacy_recorded = item['status'] == 'recorded'
        result = {'job_id': item['job_id'], 'request_key': item.get('request_key'),
                'project_id': item['project_id'], 'status': 'indeterminate' if legacy_recorded else item['status'],
                'created_at': item['created_at'], 'proposal': item['proposal'],
                'authority': item.get('authority'), 'revision': item.get('revision', 0),
                'attempt_id': item.get('attempt_id'), 'result_receipt': None if legacy_recorded else item.get('result_receipt'),
                'authorization_evidence': item.get('authorization_evidence'),
                'dispatch_enabled': False,
                'proposal_sha256': hashlib.sha256(_json(item['proposal']).encode()).hexdigest(),
                'authorization_kind': item.get('authorization_kind'),
                'operator_authorization': item.get('operator_authorization'),
                'local_execution': item.get('local_execution')}
        for field in ('candidate_import', 'clarifications'):
            if field in item:
                result[field] = item[field]
        if item.get('operation_id') is not None:
            result['operation_id'] = item['operation_id']
        return result

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

    @staticmethod
    def _work_association(row):
        bindings = []
        for record, key in ((row.get('candidate_import'), 'origin'),
                            (row.get('operator_authorization'), 'host_context'),
                            (row.get('local_execution'), 'manifest')):
            if record is None:
                continue
            context = record.get(key) if isinstance(record, dict) else None
            if not isinstance(context, dict) or context.get('project_id') != row['project_id']:
                return 'inconsistent', None
            try:
                bindings.append(_uuid(context.get('work_id')))
            except ValueError:
                return 'inconsistent', None
        if len(set(bindings)) > 1:
            return 'inconsistent', None
        return ('bound', bindings[0]) if bindings else ('unbound', None)

    def browse(self, project_id, *, work_id=None, limit=20, offset=0):
        """Minimal project summaries, no reconciliation or inferred work binding."""
        project = _uuid(project_id)
        self._project(project)
        if work_id is not None:
            work_id = _uuid(work_id)
        if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or not 0 <= offset <= MAX_JOBS:
            raise ValueError('Invalid pagination')
        with self._store() as data:
            selected = []
            for row in data['jobs']:
                if row['project_id'] != project:
                    continue
                association, work = self._work_association(row)
                if work_id is not None and (association != 'bound' or work != work_id):
                    continue
                selected.append((row, association, work))
            selected.sort(key=lambda item: (item[0]['created_at'], item[0]['job_id']), reverse=True)
            summaries = []
            for row, association, work in selected[offset:offset + limit]:
                operation = row.get('operation_id')
                try:
                    _uuid(operation)
                    selectable = True
                except ValueError:
                    operation, selectable = None, False
                # Recognition uses the host's exact versioned template, excluding
                # imported candidates; prose alone never creates an association.
                import coordinator_local
                try:
                    kind = ('analysis' if coordinator_local.analysis_request(row) is not None
                            else 'mission' if row.get('candidate_import') else 'preparation')
                except (ValueError, KeyError, TypeError):
                    kind = 'unknown'
                summaries.append({'project_id': project, 'operation_id': operation, 'job_id': row['job_id'],
                    'objective': row['proposal']['objective'][:160],
                    'status': 'indeterminate' if row['status'] == 'recorded' else row['status'],
                    'revision': row.get('revision', 0), 'created_at': row['created_at'],
                    'updated_at': row.get('revised_at', row['created_at']), 'kind': kind,
                    'work_association': association, 'work_id': work,
                    'selectable': selectable and association != 'inconsistent'})
            return {'project_id': project, 'work_id': work_id, 'offset': offset, 'limit': limit,
                    'next_offset': offset + limit if offset + limit < len(selected) else None,
                    'operations': summaries}

    def list(self, project_id, *, limit=50, offset=0):
        self._project(_uuid(project_id))
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 10000:
            raise ValueError('Invalid pagination')
        with self._store(durable=True) as data:
            selected = [self._public(row) for row in data['jobs'] if row['project_id'] == project_id]
            return selected[offset:offset + limit]
