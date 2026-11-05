"""Opt-in JSONL host for the experimental local Apple pilot; no implicit inference."""
import argparse
import copy
import json
import queue
import sys
import threading
import time
import uuid
from pathlib import Path

import apple_pilot
import apple_pilot_transport
import coordinator_jobs
import coordinator_local
import orchestrator_memory
import orchestrator_memory_service

MAX_LINE = 65536
MAX_REQUESTS = 1000


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _uuid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('invalid UUID')
    return value


class _Backend:
    def __init__(self, backend):
        self.backend = backend
        self.cleanup_unconfirmed = False

    def respond(self, messages, tools, deadline, cancelled):
        try:
            return self.backend.respond(messages, tools, deadline, cancelled)
        except apple_pilot_transport.TransportError as exc:
            if exc.code == 'cleanup_unconfirmed':
                self.cleanup_unconfirmed = True
            raise


class Service:
    """Single scoped session; the writer callback must be thread-safe."""
    def __init__(self, state, emit, *, memory_factory=orchestrator_memory.Memory,
                 backend_factory=apple_pilot_transport.LocalFM, jobs_factory=coordinator_jobs.Jobs, local_factory=coordinator_local.LocalExecution):
        self.state = Path(state)
        if not self.state.is_absolute():
            raise ValueError('absolute state path required')
        self.emit = emit
        self.memory_factory = memory_factory
        self.backend_factory = backend_factory
        self.jobs_factory = jobs_factory
        self.local_factory = local_factory
        self.local_plans = {}
        self.local_runs = {}
        self.lock = threading.Lock()
        self.ids = set()
        self.pilot = None
        self.worker = None
        self.cancel_event = None
        self.poisoned = False
        self.memory_project = None
        self.memory_service = None
        self.memory_worker = None
        self.memory_closing = False
        self.job_worker = None
        self.job_closing = False
        self.consent_binding = None

    def _reply(self, ident, status, **values):
        self.emit({'id': ident, 'status': status, **values})

    def handle(self, request):
        if not isinstance(request, dict):
            raise ValueError('invalid request')
        ident = _uuid(request.get('id'))
        with self.lock:
            if ident in self.ids or len(self.ids) >= MAX_REQUESTS:
                raise ValueError('duplicate id or request limit')
            self.ids.add(ident)
        op = request.get('op')
        keys = {
            'catalog': {'id', 'op'},
            'list_checkpoints': {'id', 'op', 'enabled', 'project_id', 'work_id'},
            'open': {'id', 'op', 'enabled', 'project_id', 'work_id', 'checkpoint_id', 'skill'},
            'turn': {'id', 'op', 'text'},
            'cancel': {'id', 'op'},
            'close': {'id', 'op'},
            'memory_bind': {'id', 'op', 'enabled', 'project_id'},
            'memory_call': {'id', 'op', 'request'},
            'job_consent_context': {'id', 'op', 'enabled', 'project_id', 'work_id'},
            'job_authorize_local': {'id', 'op', 'enabled', 'project_id', 'work_id', 'operation_id',
                'authorization_id', 'expected_revision', 'expected_proposal_sha256', 'expected_context_sha256'},
            'job_local_plan': {'id', 'op', 'enabled', 'project_id', 'operation_id', 'max_seconds'},
            'job_local_start': {'id', 'op', 'enabled', 'project_id', 'operation_id', 'run_id', 'expected_revision', 'expected_manifest_sha256'},
            'job_local_stop': {'id', 'op', 'enabled', 'project_id', 'operation_id', 'run_id'},
            'job_authorize': {'id', 'op', 'enabled', 'project_id', 'work_id', 'operation_id',
                'authorization_id', 'expected_revision', 'expected_proposal_sha256', 'expected_context_sha256'},
            'job_prepare': {'id', 'op', 'enabled', 'project_id', 'operation_id', 'proposal'},
            'job_recover': {'id', 'op', 'enabled', 'project_id', 'operation_id'},
            'job_revise': {'id', 'op', 'enabled', 'project_id', 'operation_id', 'proposal', 'expected_revision'},
        }
        if op not in keys or set(request) != keys[op]:
            self._reply(ident, 'invalid_request')
            return
        try:
            self._dispatch(ident, op, request)
        except Exception:
            self._reply(ident, 'error')

    def _dispatch(self, ident, op, request):
        if op in ('job_prepare', 'job_recover', 'job_revise', 'job_consent_context', 'job_authorize', 'job_authorize_local', 'job_local_plan', 'job_local_start', 'job_local_stop'):
            if request.get('enabled') is not True:
                self._reply(ident, 'not_enabled')
                return
            project = _uuid(request['project_id'])
            operation = _uuid(request['operation_id']) if op != 'job_consent_context' else None
            if op == 'job_revise' and (type(request['expected_revision']) is not int or request['expected_revision'] < 0):
                self._reply(ident, 'invalid_request')
                return
            if op in ('job_prepare', 'job_revise') and not isinstance(request.get('proposal'), dict):
                self._reply(ident, 'invalid_request')
                return
            with self.lock:
                if self.job_closing:
                    status = 'job_closing'
                elif self.job_worker is not None:
                    status = 'job_busy'
                else:
                    worker = threading.Thread(target=self._job_call,
                        args=(ident, op, project, operation, copy.deepcopy(request)), daemon=True)
                    self.job_worker = worker
                    status = None
            if status:
                self._reply(ident, status)
            else:
                worker.start()
            return
        if op == 'catalog':
            self._reply(ident, 'finished', skills=apple_pilot.catalog(), experimental=True)
            return
        if op == 'memory_bind':
            if request['enabled'] is not True:
                self._reply(ident, 'not_enabled')
                return
            project = _uuid(request['project_id'])
            with self.lock:
                if self.memory_closing:
                    status = 'memory_closing'
                elif self.memory_worker is not None:
                    status = 'memory_busy'
                elif self.pilot is not None and self.pilot.project_id != project:
                    status = 'scope_mismatch'
                else:
                    self.memory_service = orchestrator_memory_service.MemoryService(
                        self.state, project, memory_factory=self.memory_factory)
                    self.memory_project = project
                    status = 'bound'
            self._reply(ident, status)
            return
        if op == 'memory_call':
            if not isinstance(request['request'], dict):
                self._reply(ident, 'invalid_request')
                return
            with self.lock:
                if self.memory_closing:
                    status = 'memory_closing'
                elif self.memory_service is None:
                    status = 'not_bound'
                elif self.memory_worker is not None:
                    status = 'memory_busy'
                else:
                    worker = threading.Thread(target=self._memory_call,
                        args=(ident, self.memory_service, copy.deepcopy(request['request'])), daemon=True)
                    self.memory_worker = worker
                    status = None
            if status:
                self._reply(ident, status)
            else:
                worker.start()
            return
        if op in ('cancel', 'close'):
            with self.lock:
                running = self.worker is not None
                if self.cancel_event:
                    self.cancel_event.set()
                if self.pilot:
                    self.pilot.cancel()
                if op == 'close' and self.pilot:
                    self.pilot.close()
                    self.pilot = None
                poisoned = self.poisoned
            self._reply(ident, 'cleanup_unconfirmed' if poisoned else ('cancelling' if running else ('closed' if op == 'close' else 'idle')))
            return
        with self.lock:
            busy = self.worker is not None
            poisoned = self.poisoned
            has_session = self.pilot is not None
        if poisoned:
            self._reply(ident, 'cleanup_unconfirmed')
            return
        if busy:
            self._reply(ident, 'busy')
            return
        if op in ('open', 'list_checkpoints'):
            if request['enabled'] is not True:
                self._reply(ident, 'not_enabled')
                return
            project_id = _uuid(request['project_id'])
            work_id = _uuid(request['work_id'])
            if op == 'open' and self.memory_project is not None and self.memory_project != project_id:
                self._reply(ident, 'scope_mismatch')
                return
            if op == 'open' and request['skill'] not in apple_pilot.SKILLS:
                self._reply(ident, 'invalid_request')
                return
            if op == 'open' and has_session:
                self._reply(ident, 'session_open')
                return
            memory = self.memory_factory(self.state)
            if op == 'list_checkpoints':
                checkpoints = memory.list_checkpoints(project_id, work_id, limit=50, offset=0)
                self._reply(ident, 'finished', checkpoints=checkpoints)
                return
            checkpoint_id = request['checkpoint_id']
            if checkpoint_id is not None:
                checkpoint_id = _uuid(checkpoint_id)
                row = memory.get_checkpoint(project_id, checkpoint_id)
                if not isinstance(row, dict) or row.get('id') != checkpoint_id or row.get('work_id') != work_id:
                    self._reply(ident, 'invalid_checkpoint')
                    return
            else:
                # Confirm registered project even if no checkpoint is selected.
                memory.list_checkpoints(project_id, work_id, limit=1, offset=0)
            event = threading.Event()
            backend = _Backend(self.backend_factory())
            pilot = apple_pilot.Pilot(backend=backend, memory=memory, project_id=project_id,
                                      work_id=work_id, checkpoint_id=checkpoint_id,
                                      skill=request['skill'], cancelled=event.is_set)
            with self.lock:
                self.pilot, self.cancel_event = pilot, event
            self._reply(ident, 'opened', experimental=True)
            return
        if op == 'turn':
            with self.lock:
                if not self.pilot:
                    self._reply(ident, 'not_open')
                    return
                event = self.cancel_event
                event.clear()
                pilot = self.pilot
                pilot.reset_cancel()
                worker = threading.Thread(target=self._turn, args=(ident, pilot, request['text']), daemon=True)
                self.worker = worker
            worker.start()
            return

    def _turn(self, ident, pilot, text):
        try:
            result = pilot.turn(text)
        except Exception:
            result = {'status': 'error'}
        if pilot.backend.cleanup_unconfirmed:
            result = {'status': 'cleanup_unconfirmed'}
            with self.lock:
                self.poisoned = True
        try:
            self.emit({'id': ident, **result})
        except OSError:
            pass  # The host detects the broken output channel.
        finally:
            with self.lock:
                self.worker = None

    def _memory_call(self, ident, memory_service, request):
        try:
            result = memory_service.handle(request)
        except Exception:
            result = {'status': 'memory_unavailable'}
            if request.get('op') in orchestrator_memory_service.MUTATIONS:
                result['effect'] = 'indeterminate'
        try:
            self.emit({'id': ident, **result})
        except OSError:
            pass
        finally:
            with self.lock:
                self.memory_worker = None

    def _job_call(self, ident, op, project, operation, request):
        validation_only = op in ('job_prepare', 'job_revise')
        try:
            if validation_only:
                coordinator_jobs._proposal(request['proposal'])
                if op == 'job_revise' and (type(request['expected_revision']) is not int or request['expected_revision'] < 0):
                    raise ValueError('Invalid expected revision')
            validation_only = False
            jobs = self.jobs_factory(self.state)
            if op == 'job_consent_context':
                result = jobs.consent_context(project, _uuid(request['work_id']))
                with self.lock:
                    self.consent_binding = result
                response = {'status': 'finished', **result}
            elif op in ('job_authorize', 'job_authorize_local'):
                with self.lock:
                    binding = copy.deepcopy(self.consent_binding)
                if (not binding or binding['context']['project_id'] != project
                        or binding['context']['work_id'] != _uuid(request['work_id'])
                        or binding['context_sha256'] != request['expected_context_sha256']):
                    raise ValueError('Host context changed')
                result = jobs.consent(project, operation, authorization_id=request['authorization_id'],
                    expected_revision=request['expected_revision'],
                    expected_proposal_sha256=request['expected_proposal_sha256'],
                    expected_context_sha256=request['expected_context_sha256'], host_context=binding['context'],
                    **({'backend': 'foundation-models'} if op == 'job_authorize_local' else {}))
            elif op == 'job_local_plan':
                plan = self.local_factory(jobs).plan(project, operation, request['max_seconds'])
                with self.lock: self.local_plans[(project, operation)] = plan
                response = {'status': 'finished', 'local_plan': plan}
            elif op == 'job_local_start':
                run = _uuid(request['run_id'])
                existing = jobs.recover(project, operation)
                if existing and existing.get('local_execution'):
                    execution = existing['local_execution']
                    if execution['run_id'] != run or execution['manifest_sha256'] != request['expected_manifest_sha256']:
                        raise ValueError('Execution identity conflict')
                    with self.lock: active = self.local_runs.get((project, operation))
                    result = self.local_factory(jobs).recover(project, operation, active=bool(active and active[2].is_alive()))
                    self.emit({'id': ident, 'status': 'finished', 'job': result})
                    return
                with self.lock: plan = copy.deepcopy(self.local_plans.get((project, operation)))
                if not plan or plan['manifest_sha256'] != request['expected_manifest_sha256']:
                    raise ValueError('Execution manifest mismatch')
                # Re-read helper/context without invoking it; never accept a client-supplied manifest.
                fresh = self.local_factory(jobs).plan(project, operation, plan['manifest']['max_seconds'])
                if fresh != plan: raise ValueError('Execution manifest mismatch')
                result, created = jobs.reserve_local(project, operation, run_id=run,
                    expected_revision=request['expected_revision'], manifest=plan['manifest'])
                if created:
                    control = coordinator_local.Control()
                    thread = threading.Thread(target=self._local_run, args=(project, operation, run, control), daemon=True)
                    with self.lock:
                        # Registration and start are atomic with closing. A start
                        # already preparing may not escape shutdown's snapshot.
                        if self.job_closing:
                            result = jobs.local_update(project, operation, run, stop=True, stage='indeterminate')
                        else:
                            self.local_runs[(project, operation)] = (run, control, thread)
                            thread.start()
            elif op == 'job_local_stop':
                run = _uuid(request['run_id'])
                with self.lock: active = self.local_runs.get((project, operation))
                if active and active[0] == run and active[2].is_alive():
                    with active[1].lock:
                        result = jobs.local_update(project, operation, run, stop=True)
                        active[1].cancelled.set()
                else:
                    result = jobs.local_update(project, operation, run, stop=True)
                    result = self.local_factory(jobs).recover(project, operation)
            elif op == 'job_prepare':
                result = jobs.prepare_proposal(project, operation, request['proposal'])
            elif op == 'job_recover':
                result = jobs.recover(project, operation)
                if result and result.get('local_execution'):
                    with self.lock: active = self.local_runs.get((project, operation))
                    result = self.local_factory(jobs).recover(project, operation,
                        active=bool(active and active[2].is_alive()))
                if result is None:
                    response = {'status': 'not_found'}
                    result = None
                else:
                    response = {'status': 'finished', 'job': result}
            else:
                result = jobs.revise(project, operation, request['proposal'],
                                     expected_revision=request['expected_revision'])
            if op not in ('job_consent_context', 'job_local_plan') and (op != 'job_recover' or result is not None):
                response = {'status': 'finished', 'job': result}
        except coordinator_jobs.WriteUncertainError:
            response = {'status': 'write_uncertain', 'effect': 'indeterminate'}
        except ValueError as exc:
            message = str(exc)
            if op in ('job_authorize', 'job_authorize_local', 'job_consent_context', 'job_local_plan', 'job_local_start', 'job_local_stop'):
                statuses = {'Revision conflict': 'revision_conflict', 'Mission locked': 'mission_locked',
                    'Authorization conflict': 'authorization_conflict', 'Clarification required': 'clarification_required',
                    'Host context changed': 'host_context_changed', 'Host context unavailable': 'host_context_unavailable',
                    'Proposal identity changed': 'proposal_identity_changed', 'Job not found': 'not_found'}
                status = statuses.get(message, 'invalid_authorization')
            elif 'Revision conflict' in message:
                status = 'revision_conflict'
            elif 'admitted' in message or 'Draft only' in message:
                status = 'mission_locked'
            elif 'not found' in message:
                status = 'not_found'
            else:
                status = 'invalid_proposal'
            response = {'status': status}
            if validation_only or op in ('job_authorize', 'job_authorize_local', 'job_consent_context', 'job_local_plan'):
                response['effect'] = 'not_admitted'
        except Exception:
            response = {'status': 'job_unavailable'}
        try:
            self.emit({'id': ident, **response})
        except OSError:
            pass
        finally:
            with self.lock:
                self.job_worker = None

    def _local_run(self, project, operation, run, control):
        try:
            self.local_factory(self.jobs_factory(self.state)).run(project, operation, run, control)
        except Exception:
            # A failed owner is observable by recovery; never replace it with a new run.
            pass

    def shutdown(self, timeout=70):
        deadline = time.monotonic() + timeout
        with self.lock:
            self.memory_closing = True
            self.job_closing = True
            if self.cancel_event:
                self.cancel_event.set()
            if self.pilot:
                self.pilot.close()
                self.pilot = None
            worker = self.worker
            memory_worker = self.memory_worker
            job_worker = self.job_worker
            local_runs = list(self.local_runs.values())
        for _, control, _ in local_runs:
            with control.lock: control.cancelled.set()
        for pending in (worker, memory_worker, job_worker, *(x[2] for x in local_runs)):
            if pending:
                pending.join(timeout=max(0, deadline - time.monotonic()))
        return (worker is None or not worker.is_alive()) and (
            memory_worker is None or not memory_worker.is_alive()) and (
            job_worker is None or not job_worker.is_alive()) and all(not x[2].is_alive() for x in local_runs) and not self.poisoned


def serve(input_stream, output_stream, state, *, memory_factory=orchestrator_memory.Memory,
          backend_factory=apple_pilot_transport.LocalFM):
    output_queue = queue.Queue(maxsize=64)
    output_failed = threading.Event()

    def write_output():
        try:
            while True:
                data = output_queue.get()
                if data is None:
                    return
                output_stream.write(data)
                output_stream.flush()
        except Exception:
            output_failed.set()

    writer = threading.Thread(target=write_output, daemon=True)
    writer.start()

    def emit(value):
        data = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8') + b'\n'
        if len(data) > 131072 or output_failed.is_set():
            output_failed.set()
            raise OSError('output unavailable')
        try:
            output_queue.put_nowait(data)
        except queue.Full:
            output_failed.set()
            raise OSError('output unavailable') from None

    service = Service(state, emit, memory_factory=memory_factory, backend_factory=backend_factory)
    try:
        while True:
            line = input_stream.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE or not line.endswith(b'\n'):
                emit({'id': None, 'status': 'invalid_frame'})
                break
            try:
                request = json.loads(line, object_pairs_hook=_unique)
                service.handle(request)
            except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
                emit({'id': None, 'status': 'invalid_request'})
    finally:
        stopped = service.shutdown()
    try:
        output_queue.put_nowait(None)
    except queue.Full:
        output_failed.set()
    writer.join(timeout=1)
    return 0 if stopped and not output_failed.is_set() and not writer.is_alive() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, help='Répertoire d’état Reprise absolu et existant')
    args = parser.parse_args()
    if not Path(args.state).is_absolute() or not Path(args.state).is_dir():
        print('Répertoire d’état invalide.', file=sys.stderr)
        return 2
    try:
        return serve(sys.stdin.buffer, sys.stdout.buffer, args.state)
    except (OSError, ValueError):
        print('Erreur du service expérimental.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
