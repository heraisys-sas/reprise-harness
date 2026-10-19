import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import importlib.machinery
import importlib.util

_loader = importlib.machinery.SourceFileLoader(
    'install_quick_entry', str(Path(__file__).parents[1] / 'install-quick-entry.py'))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
installer = importlib.util.module_from_spec(_spec)
_loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.socket = self.home / '.claude-desktop' / 'quick-entry.sock'
        self.state = self.home / 'quick-state'

    def install(self, **kw):
        kw.setdefault('home', self.home)
        kw.setdefault('socket_path', self.socket)
        kw.setdefault('state', self.state)
        return installer.install(**kw)

    def read_plist(self):
        path = self.home / 'Library' / 'LaunchAgents' / installer.PLIST_NAME
        return plistlib.loads(path.read_bytes())

    def test_plist_uses_explicit_repo_and_user_paths(self):
        result = self.install()
        settings = self.read_plist()
        self.assertEqual(settings['Label'], installer.LABEL)
        args = settings['ProgramArguments']
        self.assertEqual(args[1], str(installer.SERVER))
        self.assertIn(str(self.socket), args)
        self.assertIn(str(self.state), args)
        self.assertIn('--daily-limit', args)
        self.assertNotIn('RunAtLoad', settings)
        self.assertNotIn('KeepAlive', settings)
        self.assertEqual(result['plist'],
                         str(self.home / 'Library' / 'LaunchAgents' / installer.PLIST_NAME))

    def test_existing_plist_refused_without_force(self):
        self.install()
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertTrue((self.home / 'Library' / 'LaunchAgents' / installer.PLIST_NAME).exists())

    def test_force_replaces_existing_plist(self):
        self.install()
        self.install(force=True, daily_limit=3)
        settings = self.read_plist()
        self.assertIn('--daily-limit', settings['ProgramArguments'])
        self.assertIn('3', settings['ProgramArguments'])

    def test_paths_outside_home_refused(self):
        with self.assertRaises(ValueError):
            installer.install(home=self.home, socket_path=Path('/etc/passwd'), state=self.state)

    def test_server_outside_repo_refused(self):
        with self.assertRaises(ValueError):
            installer.plist_settings(server=Path('/tmp/other.py'), socket_path=self.socket,
                                     state=self.state, daily_limit=8, home=self.home)

    def test_launchctl_never_executed(self):
        with patch.object(installer, 'plist_settings', wraps=installer.plist_settings) as wrapped:
            self.install()
        wrapped.assert_called_once()
        self.assertFalse(hasattr(installer, 'subprocess'))


if __name__ == '__main__':
    unittest.main()
