import sys
import unittest
import fcntl
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import desktop_mode as dm


class StatusTests(unittest.TestCase):
    def test_status_reports_app_and_server(self):
        s = dm.status()
        # Application liée : ReClaude.app (copie patchée #130) sinon Claude.app.
        self.assertIn(s['app'], ('ReClaude.app', 'Claude.app'))
        self.assertIn('app_running', s)
        self.assertIn('server_running', s)
        self.assertIn('socket', s)

    def test_app_version_read_from_plist(self):
        # On this machine Claude is installed; version may or may not be present.
        self.assertIn('app_version', dm.status())


class LaunchTests(unittest.TestCase):
    def test_launch_when_already_running_does_not_reopen(self):
        with patch.object(dm, 'app_state', return_value='running'):
            result = dm.launch()
        self.assertTrue(result['ok'])
        self.assertFalse(result['launched'])

    def test_launch_when_not_running_opens_app(self):
        with patch.object(dm, 'app_state', return_value='absent'), patch('subprocess.run') as run:
            run.return_value.returncode = 0
            result = dm.launch()
        self.assertTrue(result['ok'])
        self.assertTrue(result['launched'])

    def test_launch_with_unknown_process_state_does_not_open_app(self):
        with patch.object(dm, 'app_state', return_value='unknown'), patch('subprocess.run') as run:
            result = dm.launch()
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'unknown')
        run.assert_not_called()

    def test_start_reports_failure_with_cause(self):
        # start() must report failure with a cause, not claim success.
        with patch.object(dm, 'server_running', return_value=False), \
             patch('subprocess.Popen') as popen, \
             patch('time.sleep'):
            popen.return_value.pid = 999999
            result = dm.start(socket='/nonexistent-dir/quick.sock', state='/nonexistent-state')
        self.assertFalse(result['ok'])
        self.assertFalse(result['started'])
        self.assertIn('reason', result)


if __name__ == '__main__':
    unittest.main()


class QuickApplyDaemonTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.state = tempfile.mkdtemp()

    def test_new_daemon_reads_managed_connector_project(self):
        with patch.object(dm, 'DEFAULT_SOCKET', Path(self.state) / 'quick.sock'), \
             patch.object(dm, 'server_running', side_effect=[False, True]), \
             patch('claude_integration.inspect', return_value={'managed': True, 'project': self.state}), \
             patch('subprocess.Popen') as popen, patch('time.sleep'), \
             patch.object(dm, 'app_state', return_value='running'):
            popen.return_value.pid = 999999
            result = dm.start(state=self.state)
        self.assertTrue(result['ok'])
        argv = popen.call_args.args[0]
        self.assertEqual(argv[argv.index('--project-root') + 1], self.state)
        # A packaged daemon must not create bytecode in the immutable release,
        # or the next integration verification rejects its integrity.
        self.assertEqual(argv[1:4], ['-E', '-s', '-B'])

    def test_daemon_status_shape(self):
        s = dm.daemon_status(self.state)
        self.assertEqual(s['daemon'], 'reprise-quick-apply')
        self.assertIn('running', s)
        self.assertIn('listening', s)
        self.assertIn('app_running', s)

    def test_daemon_start_reports_failure_with_cause(self):
        # With an unremovable orphan socket, start() must report the failure.
        with patch.object(dm, 'server_running', return_value=False), patch('subprocess.Popen'), patch('time.sleep'):
            result = dm.daemon_start(socket='/nonexistent-dir/x.sock', state=self.state)
        self.assertIn('ok', result)
        self.assertIn('reason', result)
        if not result['ok']:
            self.assertIn('reason', result)

    def test_daemon_stop_without_daemon_is_noop(self):
        with patch.object(dm, 'socket_state', return_value='stopped'):
            result = dm.daemon_stop(self.state)
        self.assertTrue(result['ok'])
        self.assertFalse(result['stopped'])

    def test_daemon_start_is_idempotent_when_running(self):
        with patch.object(dm, 'DEFAULT_SOCKET', Path(self.state) / 'quick.sock'), \
             patch.object(dm, 'server_running', return_value=True):
            result = dm.daemon_start(state=self.state)
        self.assertTrue(result['ok'])
        self.assertFalse(result['started'])

    def test_startup_starts_only_an_absent_socket_and_verifies_greeting(self):
        with patch.object(dm, 'socket_state', return_value='stopped'), \
             patch.object(dm, 'DEFAULT_SOCKET', Path(self.state) / 'quick.sock') as socket_path, \
             patch.object(dm, 'daemon_start', return_value={'ok': True, 'started': True}), \
             patch.object(dm, 'app_state', return_value='absent'), \
             patch('claude_desktop_rpc.probe_dispatch_socket', return_value=True) as probe:
            result = dm.daemon_startup(self.state)
        self.assertEqual(result['status'], 'started')
        self.assertTrue(result['daemon_ready'])
        self.assertFalse(result['app_running'])
        self.assertIsNone(result['relay_ready'])
        probe.assert_called_once()

    def test_startup_does_not_duplicate_a_ready_daemon(self):
        with patch.object(dm, 'socket_state', return_value='running'), \
             patch.object(dm, 'DEFAULT_SOCKET', Path(self.state) / 'quick.sock'), \
             patch.object(dm, 'daemon_start') as start, \
             patch.object(dm, 'app_state', return_value='running'), \
             patch('claude_desktop_rpc.probe_dispatch_socket', return_value=True):
            result = dm.daemon_startup(self.state)
        self.assertEqual(result['status'], 'already_ready')
        self.assertTrue(result['daemon_ready'])
        start.assert_not_called()

    def test_startup_does_not_replace_orphan_or_unverified_socket(self):
        with patch.object(dm, 'socket_state', return_value='stopped'), \
             patch.object(dm, 'DEFAULT_SOCKET', Path(self.state) / 'quick.sock') as socket_path, \
             patch.object(dm, 'daemon_start') as start, \
             patch.object(dm, 'app_state', return_value='absent'):
            socket_path.write_text('synthetic orphan')
            orphan = dm.daemon_startup(self.state)
        self.assertEqual(orphan['status'], 'orphan_socket')
        start.assert_not_called()
        with patch.object(dm, 'socket_state', return_value='running'), \
             patch.object(dm, 'DEFAULT_SOCKET', Path(self.state) / 'quick.sock'), \
             patch.object(dm, 'daemon_start') as start, \
             patch.object(dm, 'app_state', return_value='absent'), \
             patch('claude_desktop_rpc.probe_dispatch_socket', return_value=False):
            unverified = dm.daemon_startup(self.state)
        self.assertEqual(unverified['status'], 'unverified_socket')
        start.assert_not_called()

    def test_startup_leaves_unknown_socket_state_untouched(self):
        with patch.object(dm, 'socket_state', return_value='unknown'), \
             patch.object(dm, 'DEFAULT_SOCKET', Path(self.state) / 'quick.sock'), \
             patch.object(dm, 'daemon_start') as start, \
             patch.object(dm, 'app_state', return_value='unknown'):
            result = dm.daemon_startup(self.state)
        self.assertEqual(result['status'], 'unknown')
        start.assert_not_called()

    def test_startup_serializes_concurrent_launch_attempts(self):
        with patch.object(dm, 'DEFAULT_SOCKET', Path(self.state) / 'quick.sock'):
            self._assert_startup_busy_with_state_lock()

    def _assert_startup_busy_with_state_lock(self):
        lock_path = Path(self.state) / 'daemon-startup.lock'
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(dm, 'daemon_start') as start:
                result = dm.daemon_startup(self.state)
        self.assertEqual(result['status'], 'startup_busy')
        start.assert_not_called()

    def test_manual_start_uses_same_startup_lock(self):
        lock_path = Path(self.state) / 'daemon-startup.lock'
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(dm, 'DEFAULT_SOCKET', Path(self.state) / 'quick.sock'), \
                 patch('subprocess.Popen') as popen:
                result = dm.daemon_start(state=self.state)
        self.assertEqual(result['status'], 'startup_busy')
        popen.assert_not_called()

    def test_different_states_share_socket_startup_lock(self):
        socket_path = Path(self.state) / 'shared.sock'
        socket_lock = socket_path.with_name(socket_path.name + '.reprise-startup.lock')
        other_state = Path(self.state) / 'other-state'
        other_state.mkdir()
        with socket_lock.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch('subprocess.Popen') as popen:
                result = dm.daemon_start(socket=str(socket_path), state=str(other_state))
        self.assertEqual(result['status'], 'startup_busy')
        popen.assert_not_called()

class QuickApplyShutdownTests(unittest.TestCase):
    """Socket-local protocol tests; never open or stop the user's daemon."""

    def setUp(self):
        import tempfile
        self._temp = tempfile.TemporaryDirectory(prefix='reprise-qa-', dir='/tmp')
        self.addCleanup(self._temp.cleanup)
        self.state = Path(self._temp.name)
        self.socket_path = self.state / 'quick.sock'
        self.token = 'ab' * 32
        (self.state / 'quick-apply-shutdown.token').write_text(self.token)
        setting = patch.object(dm, 'DEFAULT_SOCKET', self.socket_path)
        setting.start()
        self.addCleanup(setting.stop)

    def _serve_once(self, reply):
        import json
        import os
        import socket
        import threading

        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)  # engines.socket_path enforces owner-only sockets
        listener.listen(2)
        messages, errors = [], []

        def worker():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(3)
                    conn.sendall(b'{"hello":true}\n')
                    raw = bytearray()
                    while b'\n' not in raw:
                        chunk = conn.recv(4096)
                        if not chunk:
                            raise ValueError('Client disconnected before shutdown')
                        raw.extend(chunk)
                    message = json.loads(raw.split(b'\n', 1)[0])
                    messages.append(message)
                    if reply is not None:
                        conn.sendall((json.dumps({'jsonrpc': '2.0', 'id': message['id'],
                                                  'result': reply}) + '\n').encode())
            except Exception as error:
                errors.append(error)
            finally:
                listener.close()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        self.addCleanup(lambda: (listener.close(), thread.join(timeout=3)))
        return messages, errors, thread

    def test_authenticated_shutdown_waits_for_socket_closure(self):
        messages, errors, thread = self._serve_once({'ok': True})
        result = dm.daemon_stop(self.state)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result['status'], 'stopped')
        self.assertTrue(result['stopped'])
        self.assertTrue(result['ok'])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['method'], 'shutdown')
        self.assertEqual(messages[0]['params'], {'token': self.token})
        self.assertNotIn(self.token, str(result))

    def test_shutdown_rejected_by_daemon_never_reports_stopped(self):
        messages, errors, thread = self._serve_once({'ok': False, 'status': 'unauthenticated'})
        result = dm.daemon_stop(self.state)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(messages), 1)
        self.assertEqual(result['status'], 'unauthenticated')
        self.assertFalse(result['stopped'])
        self.assertNotIn(self.token, str(result))

    def test_shutdown_lost_reply_is_indeterminate_and_not_retried(self):
        messages, errors, thread = self._serve_once(None)
        result = dm.daemon_stop(self.state)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(messages), 1)
        self.assertEqual(result['status'], 'indeterminate')
        self.assertFalse(result['ok'])
        self.assertFalse(result['stopped'])
        self.assertNotIn(self.token, str(result))

    def test_orphan_socket_does_not_claim_shutdown(self):
        import os
        import socket

        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        listener.close()
        result = dm.daemon_stop(self.state)
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(result['stopped'])
        self.assertFalse(result['ok'])

    def test_invalid_local_token_does_not_contact_daemon(self):
        (self.state / 'quick-apply-shutdown.token').write_text('invalid-token')
        with patch('claude_desktop_rpc.Client') as client:
            result = dm.daemon_stop(self.state)
        self.assertEqual(result['status'], 'unauthenticated')
        self.assertFalse(result['stopped'])
        client.assert_not_called()

    def test_shutdown_ack_without_socket_closure_is_indeterminate(self):
        with patch('claude_desktop_rpc.Client') as client, \
             patch.object(dm, 'socket_state', return_value='running'), \
             patch('time.monotonic', side_effect=[0.0, 10.0]):
            client.return_value.call.return_value = {'ok': True}
            result = dm.daemon_stop(self.state)
        client.assert_called_once_with(
            'claude-desktop', {'socket': str(self.socket_path)}, timeout=3)
        self.assertEqual(result['status'], 'indeterminate')
        self.assertFalse(result['stopped'])
        self.assertFalse(result['ok'])

    def test_status_queries_real_private_socket_with_correct_client_signature(self):
        messages, errors, thread = self._serve_once({'running': False, 'targets': {}})
        with patch.object(dm, 'socket_state', return_value='running'), \
             patch.object(dm, 'app_running', return_value=False), \
             patch.object(dm, 'app_version', return_value=None):
            result = dm.daemon_status(self.state)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['method'], 'cdp/status')
        self.assertEqual(result['cdp'], {'running': False, 'targets': {}})

    def test_reprise_cli_shutdown_on_isolated_home(self):
        import json
        import os
        import subprocess

        home = self.state / 'home'
        home.mkdir()
        socket_dir = home / '.claude-desktop'
        socket_dir.mkdir(mode=0o700)
        state = self.state / 'cli-state'
        (state / 'quick-state').mkdir(parents=True)
        (state / 'quick-state' / 'quick-apply-shutdown.token').write_text(self.token)
        self.socket_path = socket_dir / 'quick-entry.sock'
        messages, errors, thread = self._serve_once({'ok': True})
        env = os.environ.copy()
        env.update({'HOME': str(home), 'AGENT_RESUME_STATE': str(state),
                    'PYTHONDONTWRITEBYTECODE': '1'})
        result = subprocess.run(
            [sys.executable, '-B', str(Path(__file__).parents[1] / 'reprise'),
             'quick-apply-daemon-stop'],
            capture_output=True, text=True, timeout=15, env=env)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'stopped')
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['method'], 'shutdown')

    def test_connections_stop_wrapper_uses_authenticated_path(self):
        result = {'ok': True, 'stopped': True, 'status': 'stopped'}
        with patch.object(dm, 'daemon_stop', return_value=result) as stop:
            self.assertEqual(dm.stop(), result)
        stop.assert_called_once_with()
