import io
import json
from pathlib import Path
import sys
import subprocess
import threading
import time
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).parents[1]))
import apple_pilot_service as service
import apple_pilot_transport


def uid():
    return str(uuid.uuid4())


class Memory:
    def __init__(self, project, work, checkpoint):
        self.project, self.work, self.checkpoint = project, work, checkpoint
        self.calls = []

    def list_checkpoints(self, project, work, *, limit, offset):
        self.calls.append(('list', project, work))
        if project != self.project:
            raise ValueError('unregistered')
        return [{'id': self.checkpoint, 'work_id': self.work, 'version': 1, 'created_at': 1.0}] if work == self.work else []

    def get_checkpoint(self, project, checkpoint):
        self.calls.append(('get', project, checkpoint))
        if project != self.project or checkpoint != self.checkpoint:
            return None
        return {'id': checkpoint, 'work_id': self.work, 'version': 1, 'payload': {}}


class Backend:
    def __init__(self):
        self.calls = 0
        self.started = threading.Event()
        self.block = False
        self.cleanup_error = False

    def respond(self, messages, tools, deadline, cancelled):
        self.calls += 1
        self.started.set()
        if self.cleanup_error:
            raise apple_pilot_transport.TransportError('cleanup_unconfirmed')
        if self.block:
            while not cancelled():
                time.sleep(.005)
            raise apple_pilot_transport.TransportError('cancelled')
        return {'role': 'assistant', 'content': 'Réponse locale'}


class PilotServiceTests(unittest.TestCase):
    def setUp(self):
        self.project, self.work, self.checkpoint = uid(), uid(), uid()
        self.memory = Memory(self.project, self.work, self.checkpoint)
        self.backend = Backend()
        self.output = []
        self.signal = threading.Event()
        self.factories = []
        def emit(value):
            self.output.append(value)
            self.signal.set()
        def memory_factory(state):
            self.factories.append('memory')
            return self.memory
        def backend_factory():
            self.factories.append('backend')
            return self.backend
        self.host = service.Service('/private/tmp', emit, memory_factory=memory_factory, backend_factory=backend_factory)
        self.addCleanup(self.host.shutdown)

    def send(self, op, **fields):
        ident = uid()
        self.host.handle({'id': ident, 'op': op, **fields})
        return ident

    def await_response(self, ident):
        end = time.monotonic() + 3
        while time.monotonic() < end:
            matching = [x for x in self.output if x['id'] == ident]
            if matching:
                return matching[-1]
            self.signal.wait(.05)
            self.signal.clear()
        self.fail('response timeout')

    def open(self, **overrides):
        values = dict(enabled=True, project_id=self.project, work_id=self.work,
                      checkpoint_id=self.checkpoint, skill='checkpoint-review')
        values.update(overrides)
        return self.await_response(self.send('open', **values))

    def test_catalog_and_disabled_open_are_inert(self):
        self.assertEqual(self.await_response(self.send('catalog'))['status'], 'finished')
        self.assertEqual(self.open(enabled=False)['status'], 'not_enabled')
        self.assertEqual(self.factories, [])
        self.assertEqual(self.backend.calls, 0)

    def test_scope_and_multi_turn(self):
        self.assertEqual(self.open(work_id=uid())['status'], 'invalid_checkpoint')
        self.assertEqual(self.open()['status'], 'opened')
        for prompt in ('Bonjour', 'Encore'):
            answer = self.await_response(self.send('turn', text=prompt))
            self.assertEqual(answer['status'], 'finished')
            self.assertEqual(answer['response'], 'Réponse locale')
        self.assertEqual(self.backend.calls, 2)
        self.assertEqual(self.await_response(self.send('close'))['status'], 'closed')
        self.assertEqual(self.await_response(self.send('turn', text='Bonjour'))['status'], 'not_open')

    def test_cancel_during_turn_and_only_ack_after_worker_finishes(self):
        self.assertEqual(self.open()['status'], 'opened')
        self.backend.block = True
        turn_id = self.send('turn', text='Bonjour')
        self.assertTrue(self.backend.started.wait(1))
        self.assertEqual(self.await_response(self.send('turn', text='En parallèle'))['status'], 'busy')
        self.assertEqual(self.await_response(self.send('cancel'))['status'], 'cancelling')
        self.assertEqual(self.await_response(turn_id)['status'], 'cancelled')
        self.backend.block = False
        self.assertEqual(self.await_response(self.send('turn', text='Reprendre'))['status'], 'finished')

    def test_cleanup_unconfirmed_disables_new_work(self):
        self.assertEqual(self.open()['status'], 'opened')
        self.backend.cleanup_error = True
        self.assertEqual(self.await_response(self.send('turn', text='Bonjour'))['status'], 'cleanup_unconfirmed')
        self.assertEqual(self.await_response(self.send('turn', text='Encore'))['status'], 'cleanup_unconfirmed')
        self.assertEqual(self.await_response(self.send('close'))['status'], 'cleanup_unconfirmed')

    def test_duplicate_id_rejected_without_second_call(self):
        ident = self.send('catalog')
        with self.assertRaises(ValueError):
            self.host.handle({'id': ident, 'op': 'catalog'})
        self.assertEqual(len(self.output), 1)

    def test_bounded_input_rejects_oversized_frame_without_backend(self):
        output = io.BytesIO()
        self.assertEqual(service.serve(io.BytesIO(b'x' * (service.MAX_LINE + 1)), output, '/private/tmp',
                                       memory_factory=lambda _: self.memory, backend_factory=lambda: self.backend), 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'invalid_frame')
        self.assertEqual(self.backend.calls, 0)

    def test_close_cancels_active_turn_and_discards_session(self):
        self.assertEqual(self.open()['status'], 'opened')
        self.backend.block = True
        turn_id = self.send('turn', text='Bonjour')
        self.assertTrue(self.backend.started.wait(1))
        self.assertEqual(self.await_response(self.send('close'))['status'], 'cancelling')
        self.assertEqual(self.await_response(turn_id)['status'], 'cancelled')
        self.assertEqual(self.await_response(self.send('turn', text='Encore'))['status'], 'not_open')

    def test_memory_worker_is_independent_of_pilot_cancel_and_shutdown(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingMemoryService:
            def handle(self, request):
                entered.set()
                release.wait(5)
                return {'status': 'finished'}
        self.assertEqual(self.await_response(self.send('memory_bind', enabled=False, project_id=self.project))['status'], 'not_enabled')
        self.assertEqual(self.await_response(self.send('memory_bind', enabled=True, project_id=self.project))['status'], 'bound')
        self.host.memory_service = BlockingMemoryService()
        memory_id = self.send('memory_call', request={'op':'list_facts', 'offset':0})
        self.assertTrue(entered.wait(1))
        self.assertEqual(self.await_response(self.send('memory_call', request={'op':'list_facts', 'offset':0}))['status'], 'memory_busy')
        self.assertEqual(self.await_response(self.send('memory_bind', enabled=True, project_id=uid()))['status'], 'memory_busy')
        self.assertEqual(self.open()['status'], 'opened')
        self.backend.block = True
        turn_id = self.send('turn', text='Bonjour')
        self.assertTrue(self.backend.started.wait(1))
        self.assertEqual(self.await_response(self.send('cancel'))['status'], 'cancelling')
        self.assertEqual(self.await_response(turn_id)['status'], 'cancelled')
        self.assertFalse(self.host.shutdown(timeout=.05))
        release.set()
        self.assertEqual(self.await_response(memory_id)['status'], 'finished')
        self.assertTrue(self.host.shutdown(timeout=1))

    def test_memory_scope_binding_and_legacy_pilot_are_distinct(self):
        self.assertEqual(self.await_response(self.send('memory_call', request={'op':'list_facts','offset':0}))['status'], 'not_bound')
        self.assertEqual(self.await_response(self.send('memory_bind', enabled=True, project_id=self.project))['status'], 'bound')
        self.assertEqual(self.open(project_id=uid())['status'], 'scope_mismatch')
        self.assertEqual(self.open()['status'], 'opened')
        self.assertEqual(self.await_response(self.send('memory_bind', enabled=True, project_id=uid()))['status'], 'scope_mismatch')

    def test_process_catalog_only_without_inference(self):
        program = Path(service.__file__)
        request = json.dumps({'id': uid(), 'op': 'catalog'}) + '\n'
        result = subprocess.run([sys.executable, str(program), '--state', '/private/tmp'],
                                input=request, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)['status'], 'finished')
        self.assertEqual(result.stderr, '')

    def test_shutdown_waits_until_terminal_response_is_enqueued(self):
        self.assertEqual(self.open()['status'], 'opened')
        emitting = threading.Event()
        release = threading.Event()
        original = self.host.emit
        def slow_emit(value):
            if value.get('status') == 'finished' and 'response' in value:
                emitting.set()
                release.wait(2)
            original(value)
        self.host.emit = slow_emit
        ident = self.send('turn', text='Bonjour')
        self.assertTrue(emitting.wait(1))
        self.assertFalse(self.host.shutdown(timeout=.05))
        release.set()
        self.assertEqual(self.await_response(ident)['status'], 'finished')
        self.assertTrue(self.host.shutdown(timeout=1))

    def test_noncooperative_worker_does_not_keep_process_alive(self):
        script = r"""
import io, json, sys, threading, time, uuid
sys.path.insert(0, sys.argv[1])
import apple_pilot_service as svc
started = threading.Event()
project, work, checkpoint = (str(uuid.uuid4()) for _ in range(3))
class Memory:
    def __init__(self, state): pass
    def get_checkpoint(self, project_id, checkpoint_id):
        return {'id': checkpoint, 'work_id': work, 'version': 1, 'payload': {}}
class Backend:
    def respond(self, *args):
        started.set()
        time.sleep(60)
class Input:
    def __init__(self):
        self.lines = [
            {'id': str(uuid.uuid4()), 'op': 'open', 'enabled': True, 'project_id': project,
             'work_id': work, 'checkpoint_id': checkpoint, 'skill': 'checkpoint-review'},
            {'id': str(uuid.uuid4()), 'op': 'turn', 'text': 'hello'}]
    def readline(self, limit):
        if self.lines: return (json.dumps(self.lines.pop(0)) + '\n').encode()
        assert started.wait(1)
        return b''
original = svc.Service.shutdown
svc.Service.shutdown = lambda self: original(self, timeout=.05)
sys.exit(svc.serve(Input(), io.BytesIO(), '/private/tmp', memory_factory=Memory, backend_factory=Backend))
"""
        result = subprocess.run([sys.executable, '-c', script, str(Path(service.__file__).parent)],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_blocked_output_does_not_keep_process_alive(self):
        script = r"""
import io, json, sys, time, uuid
sys.path.insert(0, sys.argv[1])
import apple_pilot_service as svc
class Output:
    def write(self, value): time.sleep(60)
    def flush(self): pass
request = (json.dumps({'id': str(uuid.uuid4()), 'op': 'catalog'}) + '\n').encode()
sys.exit(svc.serve(io.BytesIO(request), Output(), '/private/tmp'))
"""
        result = subprocess.run([sys.executable, '-c', script, str(Path(service.__file__).parent)],
                                capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_duplicate_json_key_rejected(self):
        output = io.BytesIO()
        inp = io.BytesIO(b'{"op":"catalog","op":"turn"}\n')
        service.serve(inp, output, '/private/tmp', memory_factory=lambda _: self.memory,
                      backend_factory=lambda: self.backend)
        self.assertEqual(json.loads(output.getvalue())['status'], 'invalid_request')
        self.assertEqual(self.backend.calls, 0)


if __name__ == '__main__':
    unittest.main()
