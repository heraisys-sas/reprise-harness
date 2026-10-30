import json
import os
import sqlite3
import socket
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
from claude_desktop_quick import Endpoint


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix='quick-test-')
        self.endpoint = Endpoint(self.state, daily_limit=2)
        self.addCleanup(self.endpoint.close)

    def submit(self, args):
        return self.endpoint.submit({'request_key': str(uuid.uuid4()), **args})

    def test_submit_routes_target_chat_and_records(self):
        result = self.submit({'prompt': 'Explique le routage Quick Entry',
                                       'chatId': '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3'})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['chatId'], '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3')
        self.assertEqual(result['dispatch'], 'dispatchOnQuickEntrySubmit')
        self.assertTrue(result['submission_id'])

    def test_legacy_daily_limit_does_not_block(self):
        self.submit({'prompt': 'Un message valide'})
        self.submit({'prompt': 'Deuxième message valide'})
        result = self.submit({'prompt': 'Troisième message valide'})
        self.assertEqual(result['status'], 'finished')

    def test_short_prompt_rejected_by_guard(self):
        with self.assertRaises(ValueError):
            self.submit({'prompt': 'ab'})

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.submit({'prompt': 'Un message valide', 'hack': 'x'})

    def test_scoped_daemon_and_mcp_share_task_result(self):
        from desktop_task_store import TaskStore
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Endpoint(directory, project_root=directory)
            self.addCleanup(endpoint.close)
            key = 'shared-job'
            admitted = endpoint.submit({'request_key': key, 'prompt': 'Analyze this task'})
            self.assertFalse(admitted['dispatch_attempted'])
            self.assertTrue(admitted['task_available'])
            store = TaskStore(directory, directory)
            self.addCleanup(store.close)
            claimed = store.claim(key, 'saved-claim')
            store.submit(key, claimed['attempt_id'], 'Verified result')
            result = endpoint.call('quick_entry/get_result', {'request_key': key, 'view': 'full'})
            self.assertEqual(result['answer'], 'Verified result')
            self.assertFalse(endpoint.call('quick_entry/capabilities', {})['dispatch_supported'])

    def test_project_scoped_listing_is_metadata_only_and_does_not_acknowledge(self):
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Endpoint(directory, project_root=directory)
            self.addCleanup(endpoint.close)
            endpoint.tasks.admit('inbox-key', 'private prompt')
            claimed = endpoint.tasks.claim('inbox-key', 'private-claim')
            listed = endpoint.call('quick_entry/list_tasks', {'limit': 10})
            self.assertEqual(listed['tasks'], [{'request_key': 'inbox-key', 'state': 'attempted',
                'result_available': False, 'acknowledged': False}])
            self.assertNotIn('private prompt', json.dumps(listed))
            endpoint.tasks.submit('inbox-key', claimed['attempt_id'], 'private answer')
            listed = endpoint.call('quick_entry/list_tasks', {})
            self.assertEqual(listed['tasks'][0]['state'], 'recorded')
            self.assertFalse(listed['tasks'][0]['acknowledged'])
            with self.assertRaises(ValueError):
                endpoint.call('quick_entry/list_tasks', {'scope': directory})

    def test_non_object_list_params_get_error_and_same_socket_still_serves_ping(self):
        from claude_desktop_quick import _serve
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Endpoint(directory, project_root=directory)
            server, client = socket.socketpair()
            worker = threading.Thread(target=_serve, args=(server, endpoint))
            worker.start()
            try:
                invalid_params = [[], None, False, 0, '']
                messages = [{'jsonrpc': '2.0', 'id': index + 1,
                    'method': 'quick_entry/list_tasks', 'params': params}
                    for index, params in enumerate(invalid_params)]
                messages.append({'jsonrpc': '2.0', 'id': len(messages) + 1,
                    'method': 'ping', 'params': {}})
                client.sendall((''.join(json.dumps(message) + '\n' for message in messages)).encode())
                client.shutdown(socket.SHUT_WR)
                reader = client.makefile('rb')
                replies = [json.loads(reader.readline()) for _ in messages]
                reader.close()
                self.assertEqual([reply['id'] for reply in replies], list(range(1, len(messages) + 1)))
                self.assertTrue(all(reply['error']['code'] == -32602 for reply in replies[:-1]))
                self.assertEqual(replies[-1]['result'], {})
            finally:
                client.close()
                worker.join(timeout=5)
                server.close()
                endpoint.close()
            self.assertFalse(worker.is_alive())

    def test_changed_project_cannot_replay_other_project_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'shared'
            projects = [Path(directory) / 'a', Path(directory) / 'b']
            for project in projects:
                project.mkdir()
                endpoint = Endpoint(state, project_root=project)
                try:
                    receipt = endpoint.submit({'request_key': 'same', 'prompt': 'Scoped task'})
                    self.assertTrue(receipt['task_available'])
                    self.assertEqual(endpoint.tasks.claim('same', 'claim')['prompt'], 'Scoped task')
                finally:
                    endpoint.close()
            endpoint = Endpoint(state, project_root=projects[0])
            endpoint.close()

    def test_first_launch_from_bundle_without_preexisting_quick_state(self):
        import subprocess
        import time
        from unittest.mock import patch
        from package_helpers import package
        from claude_desktop_rpc import Client
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            helpers = package(Path(__file__).parents[1], base / 'Reprise.app')
            state, socket = base / 'new-state', base / 'quick.sock'
            env = dict(os.environ, HOME=str(base))
            env.pop('AGENT_RESUME_STATE', None)
            process = subprocess.Popen([sys.executable, str(helpers / 'claude_desktop_quick.py'),
                '--state', str(state), '--socket', str(socket), '--project-root', str(base)],
                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            client = None
            try:
                deadline = time.monotonic() + 15
                while not socket.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertIsNone(process.poll(), 'First-launch daemon exited')
                client = Client(None, {'socket': str(socket)}, timeout=2)
                capability = client.call('quick_entry/capabilities', {})
                self.assertEqual(capability['project_root'], str(base))
                self.assertTrue(capability['task_return_supported'])
                self.assertFalse(capability['dispatch_supported'])
            finally:
                if client: client.close()
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_initialize_and_ping(self):
        info = self.endpoint.call('initialize', {})
        self.assertEqual(info['serverInfo']['name'], 'reprise-claude-desktop-quick')
        self.assertEqual(self.endpoint.call('ping', {}), {})
        with self.assertRaises(ValueError):
            self.endpoint.call('unknown/method', {})

    def test_submission_persisted_in_sqlite(self):
        self.submit({'prompt': 'Un message valide'})
        row = sqlite3.connect(os.path.join(self.state, 'quick.sqlite')).execute(
            'SELECT COUNT(*) FROM quick_submissions').fetchone()
        self.assertEqual(row[0], 1)

    def test_cdp_resume_supported_in_capabilities(self):
        caps = self.endpoint.call('quick_entry/capabilities', {})
        self.assertTrue(caps['cdp_resume_supported'])

    @mock.patch('claude_desktop_quick._cdp_pipe')
    def test_cdp_status_idle(self, mock_cdp):
        """cdp/status retourne idle quand pas de verrou."""
        mock_inst = mock.MagicMock()
        mock_cdp.return_value = mock_inst
        profile = Path(tempfile.mkdtemp(prefix='cdp-status-'))
        mock_inst._profile_dir.return_value = profile
        mock_inst._hostname_pid_lock.return_value = (profile / 'lock-pid', profile / 'lock-pid')
        mock_inst.check_profile_busy.return_value = None
        mock_inst._chrome_bin.return_value = '/usr/bin/chrome'
        with mock.patch('pathlib.Path.exists', return_value=True):
            result = self.endpoint.call('cdp/status', {})
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'idle')

    @mock.patch('claude_desktop_quick._cdp_pipe')
    def test_cdp_status_profile_busy(self, mock_cdp):
        """cdp/status retourne profile_busy quand verrou actif."""
        mock_inst = mock.MagicMock()
        mock_cdp.return_value = mock_inst
        profile = Path(tempfile.mkdtemp(prefix='cdp-busy-'))
        mock_inst._profile_dir.return_value = profile
        mock_inst._hostname_pid_lock.return_value = (profile / 'lock-pid', profile / 'lock-pid')
        mock_inst.check_profile_busy.return_value = {'pid': 12345, 'lock_mtime': 1234567890.0}
        result = self.endpoint.call('cdp/status', {})
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'profile_busy')
        self.assertEqual(result['pid'], 12345)

    @mock.patch('claude_desktop_quick._cdp_pipe')
    def test_cdp_status_chrome_missing(self, mock_cdp):
        """cdp/status retourne chrome_missing si binaire absent."""
        mock_inst = mock.MagicMock()
        mock_cdp.return_value = mock_inst
        profile = Path(tempfile.mkdtemp(prefix='cdp-missing-'))
        mock_inst._profile_dir.return_value = profile
        mock_inst._hostname_pid_lock.return_value = (profile / 'lock-pid', profile / 'lock-pid')
        mock_inst.check_profile_busy.return_value = None
        mock_inst._chrome_bin.return_value = '/nonexistent/chrome'
        with mock.patch('pathlib.Path.exists', return_value=False):
            result = self.endpoint.call('cdp/status', {})
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'chrome_missing')

    def test_cdp_resume_requires_chat_id(self):
        """cdp/resume rejette sans chat_id."""
        result = self.endpoint.call('cdp/resume', {'prompt': 'hello'})
        self.assertFalse(result.get('ok'))
        self.assertEqual(result['status'], 'target_missing')

    def test_cdp_resume_rejects_short_prompt(self):
        """cdp/resume rejette prompt < 2 caractères."""
        result = self.endpoint.call('cdp/resume', {'chat_id': str(uuid.uuid4()), 'prompt': 'x'})
        self.assertFalse(result.get('ok'))
        self.assertEqual(result['status'], 'paramètres')

    def test_shutdown_unauthenticated(self):
        """shutdown avec token invalide retourne unauthenticated."""
        result = self.endpoint.call('shutdown', {'token': 'invalid'})
        self.assertFalse(result.get('ok'))
        self.assertEqual(result['status'], 'unauthenticated')


class _FakeStream:
    """Double de makefile('rwb') : lit les lignes fournies, capture les écritures à part."""

    def __init__(self, lines):
        self._lines = lines
        self.written = []

    def __iter__(self):
        return iter(self._lines)

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        pass


class ServeCatchAllTests(unittest.TestCase):
    """_serve répond -32603 à une panne inattendue sans tuer la boucle mono-tâche."""

    def test_runtime_error_replies_internal_error_and_loop_survives(self):
        from claude_desktop_quick import _serve
        requests = [
            {'jsonrpc': '2.0', 'id': 7, 'method': 'cdp/resume',
             'params': {'chat_id': 'x', 'prompt': 'continuer'}},
            {'jsonrpc': '2.0', 'id': 8, 'method': 'cdp/resume',
             'params': {'chat_id': 'x', 'prompt': 'continuer'}},
        ]
        stream = _FakeStream([(json.dumps(request) + '\n').encode() for request in requests])
        conn = mock.MagicMock()
        conn.makefile.return_value = stream
        endpoint = mock.MagicMock()
        endpoint.call.side_effect = [RuntimeError('CDP boom'), ValueError('paramètre')]

        _serve(conn, endpoint)

        replies = [json.loads(line) for line in b''.join(stream.written).splitlines()]
        self.assertEqual([reply['id'] for reply in replies], [7, 8])
        self.assertEqual(replies[0]['error']['code'], -32603)
        self.assertIn('CDP boom', replies[0]['error']['message'])
        self.assertEqual(replies[1]['error']['code'], -32602)


if __name__ == '__main__':
    unittest.main()
