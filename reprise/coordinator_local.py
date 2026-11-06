"""Explicit, one-shot local Foundation Models execution; no cloud or fallback.

Only a fresh Store.guarded_handoff permits a spawn. Jobs is a conservative
projection; an uncertain or historical intent never recreates an execution.
"""
import contextlib
import base64
import hashlib
import json
import os
from pathlib import Path
import selectors
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time

import coordinator_jobs as jobs_module
from delegation_store import Store, digest
from apple_pilot_transport import _stop

MAX_OUTPUT = 65536
_OPEN_LOCK = threading.Lock()


def default_helper():
    source = Path(__file__).resolve().parent
    if source.name == 'helpers' and source.parent.name == 'Resources':
        return source.parent.parent / 'MacOS/FoundationExplain'
    return source / 'Reprise.app/Contents/MacOS/FoundationExplain'


def _helper(path):
    path = Path(path).absolute()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        import stat
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022
                or not info.st_mode & 0o111 or not 0 < info.st_size <= 30*1024*1024):
            raise ValueError('Local helper unavailable')
        content = bytearray()
        while len(content) <= 30*1024*1024:
            block = os.read(fd, 65536)
            if not block: break
            content.extend(block)
        if len(content) != info.st_size: raise ValueError('Local helper changed')
        return str(path), bytes(content), hashlib.sha256(content).hexdigest()
    finally:
        os.close(fd)


def _spawn(*args, **kwargs):
    return subprocess.Popen(*args, **kwargs)


SUPERVISION_CONTRACT = {'contract': 'foundation-explain-v1-self-deadline', 'max_seconds': 120,
                        'on_device': True, 'tools': False, 'automatic_fallback': False}


def _probe(path):
    """A static capability query, never a generation or availability probe."""
    process = _spawn([path, '--supervision-contract'], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={'PATH':'/usr/bin:/bin'}, start_new_session=True, close_fds=True)
    try:
        output, _ = process.communicate(timeout=2)
        if process.returncode != 0 or len(output) > 4096 or json.loads(output) != SUPERVISION_CONTRACT:
            raise ValueError('Self-supervised local helper required')
    except (subprocess.TimeoutExpired, ValueError, UnicodeError) as exc:
        _stop(process)
        raise ValueError('Self-supervised local helper required') from exc
    finally:
        if process.stdout: process.stdout.close()


class Control:
    def __init__(self):
        self.lock = threading.Lock()
        self.cancelled = threading.Event()


class LocalExecution:
    def __init__(self, jobs, *, helper=None):
        self.jobs = jobs
        # No path from an AFM tool or a JSONL request is accepted here.
        self.helper = Path(helper) if helper is not None else default_helper()

    def plan(self, project, operation, max_seconds):
        if type(max_seconds) is not int or not 1 <= max_seconds <= 120:
            raise ValueError('Bounded local deadline required')
        row = self.jobs.recover(project, operation)
        if row is None: raise ValueError('Job not found')
        consent = row.get('operator_authorization') or {}
        if row['status'] != 'consented' or consent.get('backend') != 'foundation-models':
            raise ValueError('Explicit local consent required')
        context = self.jobs.consent_context(project, consent['host_context']['work_id'])
        if context['context_sha256'] != consent['context_sha256']:
            raise ValueError('Host context changed')
        path, _, sha = _helper(self.helper)
        _probe(path)
        manifest = {'version': 1, 'prompt_format': 'operator-question-v2', 'backend': 'foundation-models', 'adapter': 'native-foundation-explain',
            'mode': 'expertise', 'project_id': project, 'work_id': context['context']['work_id'],
            'project_root': context['context']['project_root'], 'job_id': row['job_id'],
            'request_key': row['request_key'], 'consent_id': consent['authorization_id'],
            'context_sha256': consent['context_sha256'], 'proposal_sha256': row['proposal_sha256'],
            'helper_path': path, 'helper_sha256': sha, 'max_requests': 1,
            'max_seconds': max_seconds, 'stop_on_deadline': True, 'automatic_fallback': False,
            'tools': False, 'supervision_contract': SUPERVISION_CONTRACT['contract'], 'availability': 'not_verified', 'cost_policy': 'local_helper_only_no_provider_route'}
        if 'clarifications' in row:
            manifest['clarifications_sha256'] = digest(row['clarifications'])
            if consent.get('clarifications_sha256') != manifest['clarifications_sha256']:
                raise ValueError('Clarification identity changed')
        return {'manifest': manifest, 'manifest_sha256': digest(manifest)}

    @contextlib.contextmanager
    def _store(self, root):
        directory = self.jobs.state / 'coordinator-jobs.noindex'
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        connection = None
        try:
            # SQLite owns every descriptor of an existing DB. Closing a raw fd
            # of that inode could release another thread's POSIX SQLite locks.
            # Serialize initial creation so its one raw fd is closed before any
            # connection in this process can open this dedicated registry.
            with _OPEN_LOCK:
                jobs_module._directory(fd)
                try:
                    before = os.stat('local-admissions.sqlite', dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    created = os.open('local-admissions.sqlite', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                      0o600, dir_fd=fd)
                    try: os.fsync(created)
                    finally: os.close(created)
                    before = os.stat('local-admissions.sqlite', dir_fd=fd, follow_symlinks=False)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                        or before.st_nlink != 1 or before.st_mode & 0o077):
                    raise ValueError('Private admission registry required')
                path = directory / 'local-admissions.sqlite'
                connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=2)
                after = os.stat('local-admissions.sqlite', dir_fd=fd, follow_symlinks=False)
                current_directory = os.stat(directory, follow_symlinks=False)
                if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
                        or (os.fstat(fd).st_dev, os.fstat(fd).st_ino) !=
                           (current_directory.st_dev, current_directory.st_ino)):
                    raise ValueError('Admission registry changed')
                connection.execute('PRAGMA synchronous=FULL')
                store = Store(connection, root)
                store.setup_guarded_authority()
                os.fsync(fd)
            yield store
        finally:
            if connection is not None: connection.close()
            os.close(fd)

    def _parts(self, row):
        execution = row['local_execution']; manifest = execution['manifest']
        binding = {key: manifest[key] for key in ('project_id', 'work_id', 'job_id', 'request_key', 'project_root', 'backend', 'mode')}
        binding.update(socket='owned-local-process', workspace_write=False)
        # Missing format is the historical byte representation, including its question.
        # Recovery must reconstruct that identity, never reinterpret or reissue it.
        version = manifest.get('prompt_format')
        if version is None:
            model_input = {'facts': jobs_module._json(row['proposal']),
                           'question': 'Réponds à l’objectif décrit dans ces données et précise les limites de ta réponse.'}
        elif version == 'operator-question-v2':
            model_input = {'prompt_format': version,
                           'facts': jobs_module._json({key: value for key, value in row['proposal'].items() if key != 'objective'}),
                           'question': row['proposal']['objective']}
        else:
            raise ValueError('Unsupported local prompt format')
        if 'clarifications_sha256' in manifest:
            if digest(row.get('clarifications')) != manifest['clarifications_sha256']:
                raise ValueError('Clarification identity changed')
            facts = json.loads(model_input['facts'])
            facts['clarifications'] = row['clarifications']
            model_input['facts'] = jobs_module._json(facts)
        payload = {'backend': 'foundation-models', 'mode': 'expertise', 'manifest': manifest,
                   'input': model_input}
        return execution['run_id'], manifest, binding, payload

    def recover(self, project, operation, *, active=False):
        row = self.jobs.recover(project, operation)
        if row is None or not row.get('local_execution'): return row
        run, manifest, binding, payload = self._parts(row)
        try:
            with self._store(manifest['project_root']) as store:
                correlated = store.guarded_receipt(run, binding, row['job_id'], row['request_key'], digest({'payload':payload,'parent':None}))
            receipt = correlated['receipt']
            result = receipt.get('result') or {}
            if receipt.get('state') == 'recorded' and result.get('run_id') == run:
                if result.get('response_ciphertext'):
                    answer = self.jobs.crypt(base64.b64decode(result['response_ciphertext'], validate=True), jobs_module.PURPOSE, 'open').decode('utf-8')
                    if hashlib.sha256(answer.encode()).hexdigest() != result.get('response_sha256'):
                        raise ValueError('Result identity mismatch')
                    result = dict(result, response=answer, answer_available=True)
                    result.pop('response_ciphertext', None)
                current = row['local_execution'].get('receipt')
                if current is None:
                    # The answer may be lost after a crash, but the attributable terminal descriptor is durable.
                    result = dict(result, admission_attempt_id=receipt['attempt_id'],
                                  answer_available=bool(result.get('answer_available')))
                    row = self.jobs.local_update(project, operation, run, stage='recorded', receipt=result)
                elif current.get('response_sha256') != result.get('response_sha256'):
                    raise ValueError('Result identity mismatch')
            elif not active and row['local_execution']['stage'] != 'recorded':
                row = self.jobs.local_update(project, operation, run, stage='indeterminate')
        except (ValueError, OSError, sqlite3.Error):
            if row['local_execution']['stage'] != 'recorded' and not active:
                row = self.jobs.local_update(project, operation, run, stage='indeterminate')
        return row

    def run(self, project, operation, run_id, control):
        """Called only for a newly reserved intent, in its owning execution thread."""
        row = self.jobs.recover(project, operation)
        if not row or not row.get('local_execution') or row['local_execution']['run_id'] != run_id:
            raise ValueError('Execution identity conflict')
        if row['local_execution']['stage'] != 'intent':
            return
        run, manifest, binding, payload = self._parts(row)
        key = row['request_key']; process = None
        with control.lock:
            if control.cancelled.is_set() or row['local_execution']['stop_requested']:
                self.jobs.local_update(project, operation, run, stage='indeterminate')
                return
        try:
            path, binary, sha = _helper(self.helper)
            if path != manifest['helper_path'] or sha != manifest['helper_sha256']:
                raise ValueError('Local helper changed')
            context = self.jobs.consent_context(project, manifest['work_id'])
            if context['context_sha256'] != manifest['context_sha256']:
                raise ValueError('Host context changed')
            with tempfile.TemporaryDirectory(prefix='.local-run-', dir=self.jobs.state/'coordinator-jobs.noindex') as temp:
                snapshot = Path(temp)/'FoundationExplain'
                with snapshot.open('xb') as stream:
                    stream.write(binary); stream.flush(); os.fsync(stream.fileno())
                snapshot.chmod(0o500)
                _probe(str(snapshot))
                with self._store(manifest['project_root']) as store:
                    # Rights, one-request budget and immutable payload are committed atomically.
                    with control.lock:
                        if control.cancelled.is_set() or row['local_execution']['stop_requested']:
                            self.jobs.local_update(project, operation, run, stage='indeterminate')
                            return
                        store.create_guarded_grant(run, binding, 1, payload=payload)
                        handoff = store.guarded_handoff(run, binding, row['job_id'], key, payload)
                    if handoff is None: raise ValueError('No fresh admission')
                    admitted = json.loads(handoff.snapshot_json)
                    if admitted != {'binding':binding,'grant_id':run,'job_id':row['job_id'],
                                    'request_key':key,'payload':payload,'parent':None}:
                        raise ValueError('Admission snapshot mismatch')
                    self.jobs.local_update(project, operation, run, stage='admitted')
                    # Use only the newly admitted snapshot, never mutable caller input.
                    frozen = admitted['payload']; frozen_manifest = frozen['manifest']
                    if hashlib.sha256(snapshot.read_bytes()).hexdigest() != frozen_manifest['helper_sha256']:
                        raise ValueError('Executable snapshot changed')
                    with control.lock:
                        if control.cancelled.is_set():
                            outcome = {'status':'interrupted','process_started':False,'process_reaped':False,
                                       'work_completed':False,'stop_reason':'user_stop'}
                        else:
                            env = {k:os.environ[k] for k in ('HOME','USER','LOGNAME','LANG','LC_ALL') if k in os.environ}
                            env.update(PATH='/usr/bin:/bin', TMPDIR=temp)
                            process = _spawn([str(snapshot), '--deadline-seconds', str(frozen_manifest['max_seconds'])], cwd=frozen_manifest['project_root'],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=env, start_new_session=True, close_fds=True)
                    if process is not None:
                        self.jobs.local_update(project, operation, run, stage='running')
                        outcome = self._wait(process, frozen, control)
                    answer = outcome.pop('response', None)
                    descriptor = dict(outcome, backend='foundation-models', adapter='native-foundation-explain',
                        run_id=run, job_id=row['job_id'], request_key=key, payload_identity=digest({'payload':frozen,'parent':None}),
                        manifest_sha256=digest(frozen_manifest), helper_sha256=frozen_manifest['helper_sha256'])
                    if answer is not None:
                        descriptor['response_sha256'] = hashlib.sha256(answer.encode()).hexdigest()
                        descriptor['response_ciphertext'] = base64.b64encode(self.jobs.crypt(answer.encode(), jobs_module.PURPOSE, 'seal')).decode('ascii')
                    store.finish(key, descriptor)
                    checked = store.guarded_receipt(run, binding, row['job_id'], key, descriptor['payload_identity'])
                    if checked['receipt']['state'] != 'recorded': raise ValueError('Nonterminal receipt')
                    result = dict(descriptor, admission_attempt_id=checked['receipt']['attempt_id'],
                                  answer_available=answer is not None)
                    result.pop('response_ciphertext', None)
                    if answer is not None: result['response'] = answer
                    self.jobs.local_update(project, operation, run, stage='recorded', receipt=result)
        except Exception:
            if process is not None:
                try: _stop(process)
                except Exception: pass
            # Never retry or claim no effect after an exception crossing these boundaries.
            self.jobs.local_update(project, operation, run, stage='indeterminate')
        finally:
            if process is not None:
                if process.stdin: process.stdin.close()
                if process.stdout: process.stdout.close()

    def _wait(self, process, payload, control):
        body = jobs_module._json(payload['input']).encode()
        deadline = time.monotonic() + payload['manifest']['max_seconds']
        output = bytearray(); sent = 0; eof = False
        os.set_blocking(process.stdin.fileno(), False); os.set_blocking(process.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE)
            selector.register(process.stdout, selectors.EVENT_READ)
            while not (eof and process.poll() is not None):
                reason = ('user_stop' if control.cancelled.is_set() else
                          'execution_deadline' if time.monotonic() >= deadline else None)
                if reason:
                    _stop(process)
                    return {'status':'interrupted','process_started':True,'process_reaped':True,
                            'work_completed':False,'exit_code':process.returncode,'stop_reason':reason}
                for event, _ in selector.select(timeout=.05):
                    if event.fileobj is process.stdin:
                        try: sent += os.write(process.stdin.fileno(), body[sent:])
                        except BrokenPipeError: sent = len(body)
                        if sent == len(body):
                            selector.unregister(process.stdin); process.stdin.close()
                    else:
                        chunk = os.read(process.stdout.fileno(), 8192)
                        if not chunk:
                            eof = True; selector.unregister(process.stdout)
                        else:
                            output.extend(chunk)
                            if len(output) > MAX_OUTPUT:
                                _stop(process)
                                return {'status':'apple_process_failed','process_started':True,'process_reaped':True,
                                        'work_completed':False,'reason':'output_limit'}
        code = process.wait()
        base = {'process_started':True,'process_reaped':True,'exit_code':code,'work_completed':False}
        if code == 124:
            return base | {'status':'interrupted','stop_reason':'execution_deadline','terminal_evidence':'owned_helper_self_deadline'}
        try:
            value = json.loads(output)
            if code != 0 or not isinstance(value, dict) or value.get('available') is not True:
                raise ValueError('Local model unavailable')
            answer = value.get('answer')
            if not isinstance(answer, str) or not answer.strip(): raise ValueError('Empty response')
            return base | {'status':'finished','response':answer,'work_completed':True}
        except (ValueError, UnicodeError):
            return base | {'status':'apple_process_failed','reason':'local_generation_unavailable_or_invalid'}
