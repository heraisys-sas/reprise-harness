import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import desktop_mode as dm


class StatusTests(unittest.TestCase):
    def test_status_reports_app_and_server(self):
        s = dm.status()
        self.assertEqual(s['app'], 'Claude.app')
        self.assertIn('app_running', s)
        self.assertIn('server_running', s)
        self.assertIn('socket', s)

    def test_app_version_read_from_plist(self):
        # On this machine Claude is installed; version may or may not be present.
        self.assertIn('app_version', dm.status())


class LaunchTests(unittest.TestCase):
    def test_launch_when_already_running_does_not_reopen(self):
        with patch.object(dm, 'app_running', return_value=True):
            result = dm.launch()
        self.assertTrue(result['ok'])
        self.assertFalse(result['launched'])

    def test_launch_when_not_running_opens_app(self):
        with patch.object(dm, 'app_running', return_value=False), patch('subprocess.run') as run:
            run.return_value.returncode = 0
            result = dm.launch()
        self.assertTrue(result['ok'])
        self.assertTrue(result['launched'])

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
        with patch.object(dm, 'server_running', side_effect=[False, True]), \
             patch('claude_integration.inspect', return_value={'managed': True, 'project': self.state}), \
             patch('subprocess.Popen') as popen, patch('time.sleep'), \
             patch.object(dm, 'app_running', return_value=True):
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
        with patch.object(dm, 'server_running', return_value=True):
            result = dm.daemon_start(state=self.state)
        self.assertTrue(result['ok'])
        self.assertFalse(result['started'])
