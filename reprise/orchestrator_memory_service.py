"""Operator-facing memory operations, scoped by a trusted host, without AFM tools.

The JSONL host owns request framing, permissions, and an immutable project
selection. Operation IDs identify mutations and are separate from transport IDs.
"""
import fcntl
import copy
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
import sqlite3
import uuid

import orchestrator_memory

MAX_INPUT_BYTES = 65535
MAX_OUTPUT_BYTES = 32768
LIST_BUDGET_BYTES = 16384
INTENT_PURPOSE = 'orchestrator-intent-v1'

MUTATIONS = frozenset(('create_fact', 'update_fact', 'delete_fact', 'create_checkpoint'))
FIELDS = {
    'search_facts': {'op', 'query', 'offset'},
    'list_facts': {'op', 'offset'},
    'get_fact': {'op', 'fact_id'},
    'get_fact_chunk': {'op', 'fact_id', 'expected_revision', 'offset'},
    'create_fact': {'op', 'operation_id', 'text', 'source'},
    'update_fact': {'op', 'operation_id', 'fact_id', 'expected_revision', 'text', 'source'},
    'delete_fact': {'op', 'operation_id', 'fact_id', 'expected_revision'},
    'list_checkpoints': {'op', 'work_id', 'offset'},
    'get_checkpoint': {'op', 'work_id', 'checkpoint_id'},
    'create_checkpoint': {'op', 'operation_id', 'work_id', 'expected_version', 'fact_ids',
                          'decisions', 'constraints', 'next_action', 'uncertain_effects', 'source'},
    'operation_status': {'op', 'operation_id'},
    'list_intents': {'op', 'offset'},
}


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError('invalid UUID')
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError('invalid UUID')
    except (ValueError, AttributeError):
        raise ValueError('invalid UUID') from None
    return value


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8'))


def _mutation_payload(request):
    """Use the same normalized semantic payload as Memory._mutate."""
    op = request['op']
    if op == 'create_fact':
        return {'action': 'fact_create', 'text': orchestrator_memory._text(request['text']),
                'source': orchestrator_memory._source(request['source'])}
    if op in ('update_fact', 'delete_fact'):
        ident = _uuid(request['fact_id'])
        revision = request['expected_revision']
        orchestrator_memory.Memory._revision(revision)
        payload = {'action': 'fact_update' if op == 'update_fact' else 'fact_delete',
                   'id': ident, 'revision': revision}
        if op == 'update_fact':
            payload.update(text=orchestrator_memory._text(request['text']),
                           source=orchestrator_memory._source(request['source']))
        elif request.get('_recorded_by') is not None:
            payload['recorded_by'] = request['_recorded_by']
        return payload
    if op == 'create_checkpoint':
        work = _uuid(request['work_id'])
        version = request['expected_version']
        orchestrator_memory.Memory._revision(version, allow_zero=True)
        facts = request['fact_ids']
        if not isinstance(facts, list) or len(facts) > orchestrator_memory.MAX_LIST:
            raise ValueError('Invalid fact references')
        for ident in facts:
            _uuid(ident)
        if len(set(facts)) != len(facts):
            raise ValueError('Duplicate fact references')
        return {'action': 'checkpoint_create', 'work_id': work, 'expected_version': version,
                'fact_ids': facts, 'decisions': orchestrator_memory._strings(request['decisions']),
                'constraints': orchestrator_memory._strings(request['constraints']),
                'next_action': orchestrator_memory._text(request['next_action'], limit=2048),
                'uncertain_effects': orchestrator_memory._strings(request['uncertain_effects']),
                'source': orchestrator_memory._source(request['source'])}
    raise ValueError('invalid mutation')


def _classify(error):
    # These codes are generated locally; do not relay exception text to the UI.
    if isinstance(error, TimeoutError):
        return 'memory_busy'
    if isinstance(error, ValueError):
        text = str(error)
        if text.startswith('Coffre Reprise indisponible'):
            return 'vault_unavailable'
        if text == 'Operation ID bound to different input':
            return 'operation_conflict'
        if text in ('Checkpoint version conflict', 'Missing fact or revision conflict'):
            return 'version_conflict'
        if text in ('Unknown project', 'Existing readable project registry required', 'Project registry validation failed', 'Malformed project registry schema'):
            return 'project_unavailable'
        if text in ('Unsupported memory schema version', 'Invalid memory store', 'Invalid encrypted memory store', 'Memory store too large', 'Memory file must be private, owned and regular', 'Memory directory must be private and owned', 'invalid intent'):
            return 'memory_unavailable'
        return 'invalid_request'
    return 'memory_unavailable'


class MemoryService:
    """Bind a project chosen by the host, never by a model-supplied tool call.

    This object is not an IPC authenticator. The caller must authorize its host
    and project, limit concurrency and protect the pipe.
    """
    def __init__(self, state, project_id, *, memory_factory=orchestrator_memory.Memory, attribution=None):
        path = Path(state)
        if not path.is_absolute():
            raise ValueError('absolute state path required')
        self.state = path
        self.project_id = _uuid(project_id)
        self.attribution = (orchestrator_memory._source({'kind': 'host', 'reference': 'profile',
            'producer': 'host', 'observed_at': 'host', 'recorded_by': attribution})['recorded_by']
            if attribution is not None else None)
        self.memory_factory = memory_factory

    def _memory(self):
        return self.memory_factory(self.state)

    @staticmethod
    def _intent_dir(mem):
        """Open an owned private directory anchored at the checked memory directory."""
        statefd = os.open(mem.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            orchestrator_memory._private_dir(statefd)
            parentfd = os.open('orchestrator-memory.noindex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=statefd)
        finally:
            os.close(statefd)
        try:
            orchestrator_memory._private_dir(parentfd)
            try:
                os.mkdir('intents', 0o700, dir_fd=parentfd)
            except FileExistsError:
                pass
            fd = os.open('intents', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parentfd)
            try:
                orchestrator_memory._private_dir(fd)
            except BaseException:
                os.close(fd)
                raise
            return fd
        finally:
            os.close(parentfd)

    @staticmethod
    def _sync_intent_parents(mem, intent_dirfd):
        # Sync both parent links on every admission, including a retry after
        # a previous fsync failed after mkdir or atomic replacement.
        statefd = os.open(mem.state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parentfd = None
        try:
            orchestrator_memory._private_dir(statefd)
            parentfd = os.open('orchestrator-memory.noindex', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=statefd)
            orchestrator_memory._private_dir(parentfd)
            current = os.stat('intents', dir_fd=parentfd, follow_symlinks=False)
            opened = os.fstat(intent_dirfd)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise ValueError('invalid intent')
            os.fsync(statefd)
            os.fsync(parentfd)
        finally:
            if parentfd is not None:
                os.close(parentfd)
            os.close(statefd)

    @staticmethod
    def _read_intent(mem, dirfd, filename):
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
        except FileNotFoundError:
            return None
        try:
            info = orchestrator_memory._private_file(fd)
            if info.st_size > 2048:
                raise ValueError('invalid intent')
            raw = os.read(fd, 2049)
            if len(raw) > 2048:
                raise ValueError('invalid intent')
            return json.loads(mem.crypt(raw, INTENT_PURPOSE, 'open'))
        finally:
            os.close(fd)

    def _reserve_intent(self, mem, request):
        # Validate the selected project before creating durable operation metadata.
        mem._project(self.project_id)
        operation_id = _uuid(request['operation_id'])
        canonical = orchestrator_memory._json(_mutation_payload(request))
        intent = {'format': 1, 'project_id': self.project_id, 'operation_id': operation_id,
                  'operation': request['op'], 'request_sha256': hashlib.sha256(canonical.encode('utf-8')).hexdigest()}
        known = mem.get_operation(self.project_id, operation_id)
        if known['state'] == 'recorded' and not hmac.compare_digest(known['signature'], intent['request_sha256']):
            raise ValueError('Operation ID bound to different input')
        payload = mem.crypt(json.dumps(intent, sort_keys=True, separators=(',', ':')).encode('ascii'),
                            INTENT_PURPOSE, 'seal')
        if not isinstance(payload, bytes) or len(payload) > 2048:
            raise ValueError('invalid intent')
        name = self.project_id + '-' + operation_id + '.enc'
        dirfd = self._intent_dir(mem)
        temporary = '.intent-' + str(uuid.uuid4())
        lockfd = None
        try:
            self._sync_intent_parents(mem, dirfd)
            try:
                lockfd = os.open('intents.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            except FileExistsError:
                lockfd = os.open('intents.lock', os.O_RDWR | os.O_NOFOLLOW, dir_fd=dirfd)
            orchestrator_memory._private_file(lockfd)
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('intent lock busy')
                    time.sleep(.02)
            prior = self._read_intent(mem, dirfd, name)
            if prior is not None:
                if prior != intent:
                    raise ValueError('Operation ID bound to different input')
                # A prior replace may have succeeded while its directory fsync failed.
                # Never admit the mutation until the existing name is durable.
                os.fsync(dirfd)
                return
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(payload); stream.flush(); os.fsync(stream.fileno())
                os.replace(temporary, name, src_dir_fd=dirfd, dst_dir_fd=dirfd)
                os.fsync(dirfd)
            finally:
                try:
                    os.unlink(temporary, dir_fd=dirfd)
                except FileNotFoundError:
                    pass
        finally:
            if lockfd is not None:
                os.close(lockfd)
            os.close(dirfd)

    def _operation_status(self, mem, operation_id):
        operation_id = _uuid(operation_id)
        receipt = mem.get_operation(self.project_id, operation_id)
        dirfd = self._intent_dir(mem)
        try:
            self._sync_intent_parents(mem, dirfd)
            intent = self._read_intent(mem, dirfd, self.project_id + '-' + operation_id + '.enc')
            if intent is not None:
                # Visibility after a failed directory fsync is not durability.
                os.fsync(dirfd)
        finally:
            os.close(dirfd)
        if intent is None:
            state = 'recorded_unattributed' if receipt['state'] == 'recorded' else 'not_recorded'
            return {'status': 'finished', 'state': state, 'intent': 'absent'}
        if (not isinstance(intent, dict) or set(intent) != {'format', 'project_id', 'operation_id', 'operation', 'request_sha256'}
                or intent['format'] != 1 or intent['project_id'] != self.project_id or intent['operation_id'] != operation_id
                or intent['operation'] not in MUTATIONS or not isinstance(intent['request_sha256'], str)
                or len(intent['request_sha256']) != 64 or any(c not in '0123456789abcdef' for c in intent['request_sha256'])):
            raise ValueError('invalid intent')
        state = receipt['state']
        if state == 'recorded' and not hmac.compare_digest(receipt['signature'], intent['request_sha256']):
            state = 'identity_mismatch'
        return {'status': 'finished', 'state': state, 'intent': 'present',
                'operation': intent['operation'], 'request_sha256': intent['request_sha256'],
                **({'result': receipt['result']} if state == 'recorded' else {})}

    def _list_intents(self, mem, offset):
        mem._project(self.project_id)
        orchestrator_memory.Memory._page(1, offset)
        dirfd = self._intent_dir(mem)
        try:
            prefix = self.project_id + '-'
            names = []
            with os.scandir(dirfd) as entries:
                for entry in entries:
                    if not entry.name.startswith(prefix) or not entry.name.endswith('.enc'):
                        continue
                    operation_id = entry.name[len(prefix):-4]
                    try:
                        _uuid(operation_id)
                    except ValueError:
                        continue
                    names.append((entry.name, operation_id))
                    if len(names) > 10100:
                        return {'status': 'intent_limit_reached'}
            names.sort()
            items = []
            for name, operation_id in names[offset:offset + orchestrator_memory.MAX_LIST]:
                # Filenames are candidates only; operation_status authenticates the encrypted intent.
                item = {'operation_id': operation_id, 'intent': 'unverified_candidate'}
                candidate = {'status': 'finished', 'items': items + [item], 'next_offset': offset + len(items) + 1,
                             'pagination_limit_reached': False}
                if _size(candidate) > LIST_BUDGET_BYTES:
                    break
                items.append(item)

            next_offset = offset + len(items) if offset + len(items) < len(names) else None
            limited = next_offset is not None and next_offset > 10000
            return {'status': 'finished', 'items': items, 'next_offset': None if limited else next_offset,
                    'pagination_limit_reached': limited}
        finally:
            os.close(dirfd)

    @staticmethod
    def _page(rows, offset, *, facts=False):
        result = []
        for row in rows:
            if facts:
                row = {key: row[key] for key in ('id', 'revision', 'status', 'source', 'created_at', 'updated_at')} | {
                    'recorded_by': row.get('recorded_by', {'status': 'unknown'})}
            candidate = {'status': 'finished', 'items': result + [row], 'next_offset': offset + len(result) + 1, 'pagination_limit_reached': False}
            if _size(candidate) > LIST_BUDGET_BYTES:
                break
            result.append(row)
        next_offset = offset + len(result) if len(result) < len(rows) or len(rows) == orchestrator_memory.MAX_LIST else None
        if rows and not result:
            return {'status': 'item_too_large'}
        limited = next_offset is not None and next_offset > 10000
        return {'status': 'finished', 'items': result, 'next_offset': None if limited else next_offset,
                'pagination_limit_reached': limited}

    def handle(self, request):
        """Handle one operator request, returning only stable non-sensitive errors."""
        mutation_dispatched = False
        op = None
        try:
            if not isinstance(request, dict) or _size(request) > MAX_INPUT_BYTES:
                return {'status': 'invalid_request'}
            op = request.get('op')
            if op not in FIELDS or set(request) != FIELDS[op]:
                return {'status': 'invalid_request'}
            request = copy.deepcopy(request)
            if op == 'delete_fact' and self.attribution is not None:
                request['_recorded_by'] = self.attribution
            if 'source' in request:
                if not isinstance(request['source'], dict) or 'recorded_by' in request['source']:
                    return {'status': 'invalid_request'}
                if self.attribution is not None:
                    request['source']['recorded_by'] = self.attribution
            if op in MUTATIONS or op == 'operation_status':
                _uuid(request['operation_id'])
            mem = self._memory()
            project = self.project_id
            if op in MUTATIONS:
                self._reserve_intent(mem, request)
                mutation_dispatched = True
            if op == 'operation_status':
                return self._operation_status(mem, request['operation_id'])
            if op == 'list_intents':
                return self._list_intents(mem, request['offset'])
            if op == 'list_facts':
                offset = request['offset']
                rows = mem.list_facts(project, limit=orchestrator_memory.MAX_LIST, offset=offset)
                return self._page(rows, offset, facts=True)
            if op == 'search_facts':
                rows = mem.search_facts(project, request['query'], limit=orchestrator_memory.MAX_LIST,
                                        offset=request['offset'])
                # Large valid facts must not block pagination. Full text is
                # obtained by explicit get_fact, never silently truncated there.
                for row in rows:
                    text = row.pop('text')
                    row['excerpt'] = text[:256]
                    row['excerpt_truncated'] = len(text) > 256
                return self._page(rows, request['offset'])
            if op in ('get_fact', 'get_fact_chunk'):
                item = mem.get_fact(project, request['fact_id'])
                if item is None:
                    return {'status': 'not_found'}
                if op == 'get_fact_chunk':
                    mem._revision(request['expected_revision'])
                    if item['revision'] != request['expected_revision']:
                        return {'status': 'version_conflict'}
                    offset = request['offset']
                    text = item.pop('text')
                    if type(offset) is not int or not 0 <= offset <= len(text):
                        return {'status': 'invalid_request'}
                    end = min(len(text), offset + 1024)
                    return {'status': 'finished', 'item': item, 'text_chunk': text[offset:end],
                            'offset': offset, 'next_offset': end if end < len(text) else None,
                            'total_characters': len(text)}
                result = {'status': 'finished', 'item': item}
                if _size(result) > MAX_OUTPUT_BYTES:
                    return {'status': 'item_too_large', 'fact_id': item['id'],
                            'revision': item['revision'], 'retrieval': 'reprise_memory_get_chunk'}
            elif op == 'create_fact':
                result = {'status': 'finished', 'result': mem.create_fact(project, request['operation_id'], request['text'], request['source'])}
            elif op == 'update_fact':
                result = {'status': 'finished', 'result': mem.update_fact(project, request['fact_id'], request['operation_id'],
                          request['expected_revision'], request['text'], request['source'])}
            elif op == 'delete_fact':
                result = {'status': 'finished', 'result': mem.delete_fact(project, request['fact_id'], request['operation_id'], request['expected_revision'], recorded_by=self.attribution)}
            elif op == 'list_checkpoints':
                offset = request['offset']
                rows = mem.list_checkpoints(project, request['work_id'], limit=orchestrator_memory.MAX_LIST, offset=offset)
                return self._page(rows, offset)
            elif op == 'get_checkpoint':
                _uuid(request['work_id'])
                item = mem.get_checkpoint(project, request['checkpoint_id'])
                if item is None or item['work_id'] != request['work_id']:
                    return {'status': 'not_found'}
                result = {'status': 'finished', 'item': item}
            else:
                result = {'status': 'finished', 'result': mem.create_checkpoint(
                    project, request['work_id'], request['operation_id'], request['expected_version'],
                    fact_ids=request['fact_ids'], decisions=request['decisions'], constraints=request['constraints'],
                    next_action=request['next_action'], uncertain_effects=request['uncertain_effects'], source=request['source'])}
            if op in MUTATIONS:
                result['effect'] = 'recorded'
            return result if _size(result) <= MAX_OUTPUT_BYTES else {'status': 'item_too_large'}
        except (OSError, ValueError, TypeError, KeyError, OverflowError, sqlite3.Error, TimeoutError) as error:
            result = {'status': _classify(error)}
            if op in MUTATIONS:
                result['effect'] = 'indeterminate' if mutation_dispatched else 'not_admitted'
            return result
