import json
import socket
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_desktop
import claude_desktop_quick
import claude_desktop_rpc
import engines
from claude_desktop_quick import Endpoint
from claude_desktop_rpc import dispatch_turn, probe_dispatch_socket


class FakeRepriseSocket:
    """Serveur de test imitant le socket injecté : greeting, une ligne, une réponse."""

    def __init__(self, directory, reply='{"ok":true}', greet=True, hold_reply=False):
        self.path = Path(directory) / 'quick-entry-reprise.sock'
        self.requests = []
        self.reply = reply
        self.greet = greet
        self.hold_reply = hold_reply
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.settimeout(0.2)
        self._server.bind(str(self.path))
        self._server.listen(4)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except (socket.timeout, OSError):
                continue
            with conn:
                if self.greet:
                    conn.sendall(b'{"hello":true}\n')
                buffer = bytearray()
                while b'\n' not in buffer:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buffer.extend(chunk)
                line, _, _ = buffer.partition(b'\n')
                self.requests.append(json.loads(line.decode()))
                if not self.hold_reply:
                    conn.sendall((self.reply + '\n').encode())

    def close(self, unlink=True):
        self._stop.set()
        self._thread.join(timeout=5)
        self._server.close()
        if unlink:
            self.path.unlink(missing_ok=True)


class DispatchClientTests(unittest.TestCase):
    def test_dispatch_turn_sends_payload_and_reads_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeRepriseSocket(directory)
            self.addCleanup(fake.close)
            reply = dispatch_turn(fake.path, 'Explique le routage', chat_id='178dcceb-fcd3-4f2d-ac4f-116a8a153bc3')
            self.assertEqual(reply, {'ok': True})
            self.assertEqual(fake.requests, [{'prompt': 'Explique le routage', 'images': [], 'filePaths': [],
                                              'chatId': '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3'}])

    def test_dispatch_turn_timeout_keeps_connection_open_but_gives_up(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeRepriseSocket(directory, hold_reply=True)
            self.addCleanup(fake.close)
            with self.assertRaises((OSError, engines.Unavailable)):
                dispatch_turn(fake.path, 'Prompt sans réponse', timeout=0.3)
            self.assertEqual(len(fake.requests), 1)

    def test_dispatch_turn_refuses_empty_prompt(self):
        with self.assertRaises(ValueError):
            dispatch_turn(Path('/unused.sock'), '  ')

    def test_probe_detects_live_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeRepriseSocket(directory)
            self.addCleanup(fake.close)
            self.assertTrue(probe_dispatch_socket(fake.path))

    def test_probe_orphan_socket_file_is_stopped(self):
        with tempfile.TemporaryDirectory() as directory:
            orphan = FakeRepriseSocket(directory)
            orphan.close(unlink=False)  # fichier présent, personne n'écoute
            self.assertFalse(probe_dispatch_socket(orphan.path))
            self.assertTrue(orphan.path.exists())

    def test_probe_missing_path_is_stopped(self):
        self.assertFalse(probe_dispatch_socket(Path(tempfile.mkdtemp()) / 'absent.sock'))


class DaemonDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fake = FakeRepriseSocket(self.directory.name)
        self.addCleanup(self.fake.close)
        self.socket_patch = mock.patch.object(claude_desktop_quick, 'REPRISE_DISPATCH_SOCKET', str(self.fake.path))
        self.socket_patch.start()
        self.addCleanup(self.socket_patch.stop)

    def endpoint(self):
        endpoint = Endpoint(self.directory.name, project_root=self.directory.name)
        self.addCleanup(endpoint.close)
        return endpoint

    def test_capabilities_report_dispatch_socket(self):
        endpoint = self.endpoint()
        caps = endpoint.call('quick_entry/capabilities', {})
        self.assertTrue(caps['dispatch_supported'])
        self.assertEqual(caps['dispatch_socket'], str(self.fake.path))

    def test_capabilities_false_when_socket_stopped(self):
        self.fake.close(unlink=False)  # socket orphelin
        endpoint = self.endpoint()
        caps = endpoint.call('quick_entry/capabilities', {})
        self.assertFalse(caps['dispatch_supported'])
        self.assertIsNone(caps['dispatch_socket'])
        self.assertIn('not connected', caps['reason'])

    def test_dispatch_admitted_request_key(self):
        endpoint = self.endpoint()
        key = str(uuid.uuid4())
        endpoint.submit({'request_key': key, 'prompt': 'Tâche orchestrée pour dispatch'})
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertTrue(result['dispatch_attempted'])
        self.assertTrue(result['dispatch_ok'])
        self.assertEqual(self.fake.requests[0]['prompt'], 'Tâche orchestrée pour dispatch')

    def test_dispatch_unknown_request_key_refused(self):
        endpoint = self.endpoint()
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'inconnu'})
        self.assertEqual(self.fake.requests, [])

    def test_dispatch_refuses_extra_fields(self):
        endpoint = self.endpoint()
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'k', 'images': ['a']})
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'k', 'filePaths': ['file:///x']})
        self.assertEqual(self.fake.requests, [])

    def test_dispatch_error_returned_without_retry(self):
        endpoint = self.endpoint()
        key = str(uuid.uuid4())
        endpoint.submit({'request_key': key, 'prompt': 'Une requête qui échouera'})
        self.fake.close(unlink=False)  # connect refusé au moment du dispatch
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertTrue(result['dispatch_attempted'])
        self.assertFalse(result['dispatch_ok'])
        self.assertIn('dispatch_error', result)


class _FakeRpcClient:
    def __init__(self, *args, **kw):
        self.calls = []
        _FakeRpcClient.last = self

    def call(self, method, params):
        self.calls.append(method)
        if method == 'quick_entry/submit':
            return {'status': 'finished', 'submission_id': 'sub-1'}
        if method == 'quick_entry/capabilities':
            return _FakeRpcClient.capabilities
        if method == 'quick_entry/dispatch':
            return _FakeRpcClient.dispatch
        return {}

    def close(self):
        pass


class ReviewDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        _FakeRpcClient.capabilities = {'dispatch_supported': True, 'dispatch_socket': '/reprise.sock'}
        _FakeRpcClient.dispatch = {'dispatch_ok': True}

    def review(self):
        from desktop_task_store import TaskStore
        tasks = TaskStore(self.directory.name, self.directory.name)
        self.addCleanup(tasks.close)
        with mock.patch.object(claude_desktop_rpc, 'Client', _FakeRpcClient):
            return claude_desktop.review(self.directory.name, 'Question orchestrée', kind='expertise',
                                         level='standard', request_key='rk-1', tasks=tasks)

    def test_receipt_reflects_dispatch_attempted(self):
        result = self.review()
        self.assertEqual(result['status'], 'finished')
        self.assertTrue(result['dispatch_attempted'])
        self.assertTrue(result['dispatch_ok'])
        self.assertTrue(result['admission_only'])
        self.assertIsNone(result['response'])

    def test_failed_dispatch_keeps_admission_only(self):
        _FakeRpcClient.dispatch = {'dispatch_ok': False, 'dispatch_error': 'refusé'}
        result = self.review()
        self.assertEqual(result['status'], 'finished')
        self.assertTrue(result['dispatch_attempted'])
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['dispatch_error'], 'refusé')
        self.assertTrue(result['admission_only'])

    def test_no_dispatch_without_capability(self):
        _FakeRpcClient.capabilities = {'dispatch_supported': False}
        result = self.review()
        self.assertEqual(result['status'], 'finished')
        self.assertNotIn('dispatch_attempted', result)
        self.assertTrue(result['admission_only'])


if __name__ == '__main__':
    unittest.main()
