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
