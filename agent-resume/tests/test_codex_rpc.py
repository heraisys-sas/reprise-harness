import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from collections import deque
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import codex_rpc
from engines import Unavailable


def frame(message, opcode=1, final=True):
    payload = message if isinstance(message, bytes) else json.dumps(message).encode()
    size = len(payload)
    header = bytes([(128 if final else 0) | opcode, size]) if size < 126 else bytes([(128 if final else 0) | opcode, 126]) + struct.pack('!H', size)
    return header + payload


class Frames(unittest.TestCase):
    def client(self, data):
        client = codex_rpc.Client.__new__(codex_rpc.Client)
        client.buffer, client.timeout = bytearray(data), 1
        client.sent = []
        client._write = client.sent.append
        return client

    def test_json_extended_payload(self):
        message = {'result': 'a' * 200}
        self.assertEqual(self.client(frame(message)).receive(), message)

    def test_ping_between_fragments(self):
        client = self.client(frame(b'{"result":', final=False) + frame(b'ping', 9) + frame(b'42}', 0))
        self.assertEqual(client.receive(), {'result': 42})
        self.assertEqual(client.sent[0][0], 138)
        self.assertTrue(client.sent[0][1] & 128)

    def test_invalid_or_oversized_frames(self):
        for data in [b'\x81\xff' + struct.pack('!Q', 2**40), b'\x81\x7f' + struct.pack('!Q', 2**40), frame(b'{}', 0), frame(b'{}', 2), frame(b'[]')]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                self.client(data).receive()

    def test_closed_is_unknown_not_success(self):
        with self.assertRaises(Unavailable):
            self.client(frame(b'', 8)).receive()

    def test_requests_masked_and_roundtrip(self):
        client = self.client(b'')
        client.send({'id': 1})
        raw = client.sent[0]
        mask, payload = raw[2:6], raw[6:]
        self.assertEqual(json.loads(bytes(v ^ mask[i % 4] for i, v in enumerate(payload))), {'id': 1})


class FakeClient:
    status = 'idle'
    cwd = '/tmp'
    notifications_template = None
    limits = True
    closed = False

    def __init__(self, *args):
        self.calls, self.notifications = [], deque(self.notifications_template or [])
        self.__class__.last = self

    def send(self, value):
        pass

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == 'initialize':
            return {}
        if method.startswith('thread/'):
            return {'thread': {'id': 'session', 'cwd': self.cwd, 'status': {'type': self.status}, 'modelProvider': 'openai'}}
        if method == 'account/read':
            return {'account': {'type': 'chatgpt'}}
        if method == 'account/rateLimits/read':
            return {'ordinaryUsageAllowed': self.limits}
        if method == 'turn/start':
            return {'turn': {'id': 'own'}}
        return {}

    def receive(self, timeout=None):
        raise Unavailable('needs_review', 'Connection lost')

    def close(self):
        self.closed = True


def event(status='completed', turn='own', thread='session', **extra):
    return {'method': 'turn/completed', 'params': {'threadId': thread, 'turn': {'id': turn, 'status': status}}, **extra}


class Execution(unittest.TestCase):
    def setUp(self):
        FakeClient.status, FakeClient.cwd, FakeClient.limits = 'idle', '/tmp', True
        FakeClient.notifications_template = [event()]
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.states = []
        self.task = {'id': 'task', 'session': 'session', 'cwd': '/tmp', 'prompt': 'continue'}

    def execute(self, **kw):
        with patch('codex_rpc.Client', FakeClient), patch('run_status.save', side_effect=lambda db, task, state, *a, **k: self.states.append(state)):
            return codex_rpc.execute(None, self.task, 'codex', {}, Path(self.temp.name)/'log', **kw)

    def test_success_preserves_session_parameters(self):
        self.assertEqual(self.execute(), 'finished')
        start = next(params for method, params in FakeClient.last.calls if method == 'turn/start')
        self.assertEqual(set(start), {'threadId', 'clientUserMessageId', 'input'})
        self.assertTrue(FakeClient.last.closed)
        self.assertNotIn('turn/interrupt', [m for m, _ in FakeClient.last.calls])

    def test_busy_or_unloaded_never_starts(self):
        for status in ['active', 'notLoaded', 'systemError']:
            FakeClient.status = status
            self.assertEqual(self.execute(), 'needs_review')
            self.assertEqual(self.states[-1], 'blocked_owner')
            self.assertNotIn('turn/start', [m for m, _ in FakeClient.last.calls])

    def test_wrong_workspace_never_starts(self):
        FakeClient.cwd = '/'
        self.execute()
        self.assertEqual(self.states[-1], 'blocked_session')

    def test_quota_unconfirmed_never_starts(self):
        FakeClient.limits = None
        self.execute(automatic=True)
        self.assertEqual(self.states[-1], 'quota_blocked')
        self.assertNotIn('turn/start', [m for m, _ in FakeClient.last.calls])

    def test_approval_interrupts_only_own_turn(self):
        FakeClient.notifications_template = [{'id': 42, 'method': 'item/commandExecution/requestApproval', 'params': {'threadId': 'session', 'turnId': 'own'}}]
        self.execute()
        self.assertEqual(self.states[-1], 'blocked_permission')
        self.assertIn(('turn/interrupt', {'threadId': 'session', 'turnId': 'own'}), FakeClient.last.calls)

    def test_unrelated_completion_cannot_finish(self):
        FakeClient.notifications_template = [event(turn='other'), event(thread='other')]
        self.execute()
        self.assertEqual(self.states[-1], 'needs_review')
        self.assertIn(('turn/interrupt', {'threadId': 'session', 'turnId': 'own'}), FakeClient.last.calls)

    def test_failed_or_interrupted_terminal_not_success(self):
        for terminal, expected in [('failed', 'failed'), ('interrupted', 'stopped')]:
            FakeClient.notifications_template = [event(terminal)]
            self.execute()
            self.assertEqual(self.states[-1], expected)
            self.assertNotIn('turn/interrupt', [m for m, _ in FakeClient.last.calls])

    def test_timeout_interrupts_own_turn(self):
        self.execute(timeout=0)
        self.assertEqual(self.states[-1], 'timed_out')
        self.assertIn(('turn/interrupt', {'threadId': 'session', 'turnId': 'own'}), FakeClient.last.calls)


class InstalledServer(unittest.TestCase):
    @unittest.skipUnless(__import__('os').environ.get('REPRISE_CODEX_RPC_SMOKE') == '1', 'Opt-in isolated installed Codex handshake')
    def test_initialize_over_private_unix_socket(self):
        import os
        import shutil
        import subprocess
        import time
        program = shutil.which('codex')
        if not program:
            self.skipTest('Codex absent')
        with tempfile.TemporaryDirectory(prefix='reprise-rpc-') as home:
            endpoint = Path(home)/'rpc.sock'
            environment = codex_rpc.codex_environment() | {'CODEX_HOME': home}
            process = subprocess.Popen([program, 'app-server', '--listen', 'unix://' + str(endpoint)],
                                       env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, umask=0o077)
            client = None
            try:
                for _ in range(100):
                    if endpoint.exists():
                        break
                    if process.poll() is not None:
                        self.fail('Isolated server exited before socket creation')
                    time.sleep(.05)
                with patch.dict(os.environ, {'CODEX_HOME': home}):
                    client = codex_rpc.Client(program, {'socket': str(endpoint)})
                    result = client.call('initialize', {'clientInfo': {'name': 'reprise-test', 'version': '0.1'}, 'capabilities': None})
                    self.assertIn('userAgent', result)
                    client.send({'method': 'initialized'})
            finally:
                if client:
                    client.close()
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
