import ast
import errno
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_desktop
import desktop_mode as dm

ROOT = Path(__file__).parents[1]


class AuthenticatedStopTests(unittest.TestCase):
    def test_reused_pid_is_never_signalled(self):
        with tempfile.TemporaryDirectory() as td, \
                patch.object(dm, 'DEFAULT_STATE', Path(td)), \
                patch.object(dm, 'socket_state', return_value='running'), patch('os.kill') as kill:
            self.assertIsNone(dm.daemon_pid())
            for stop in (dm.stop, dm.daemon_stop):
                result = stop()
                self.assertFalse(result['ok'])
                self.assertFalse(result['stopped'])
                self.assertEqual(result['status'], 'unsupported')
            kill.assert_not_called()

    def test_unknown_is_not_success_and_orphan_is_stopped_without_action(self):
        for state, ok in (('unknown', False), ('stopped', True)):
            with self.subTest(state=state), \
                    tempfile.TemporaryDirectory() as td, \
                    patch.object(dm, 'DEFAULT_STATE', Path(td)), \
                    patch.object(dm, 'socket_state', return_value=state):
                result = dm.daemon_stop()
                self.assertEqual(result['ok'], ok)
                self.assertFalse(result['stopped'])

    def test_socket_connection_distinguishes_live_orphan_and_uncertainty(self):
        for error, expected in (
                (None, 'running'), (FileNotFoundError(errno.ENOENT, 'missing'), 'stopped'),
                (ConnectionRefusedError(errno.ECONNREFUSED, 'orphan'), 'stopped'),
                (PermissionError(errno.EACCES, 'denied'), 'unknown'), (TimeoutError(), 'unknown')):
            with self.subTest(expected=expected), patch('socket.socket') as factory:
                factory.return_value.connect.side_effect = error
                self.assertEqual(dm.socket_state(), expected)
                factory.return_value.connect.assert_called_once_with(str(dm.DEFAULT_SOCKET))
                factory.return_value.close.assert_called_once()

    def test_status_uses_socket_not_pid_file(self):
        with patch.object(dm, 'socket_state', return_value='stopped'), \
                patch.object(dm, 'daemon_pid', return_value=123), \
                patch.object(dm, 'app_running', return_value=False), \
                patch.object(dm, 'app_version', return_value=None):
            self.assertFalse(dm.daemon_status()['running'])


class ContextBudgetTests(unittest.TestCase):
    def read_context(self, chunks, paths, budget):
        handle = Mock()
        handle.read.side_effect = chunks
        with patch.object(Path, 'resolve', return_value=Path('/project/file')), \
                patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'open') as opened:
            opened.return_value.__enter__.return_value = handle
            try:
                return claude_desktop.context_files(Path('/project'), paths, max_bytes=budget)
            finally:
                self.reads = handle.read.call_args_list

    def test_first_file_over_budget_is_rejected_with_bounded_read(self):
        with self.assertRaisesRegex(ValueError, 'budget'):
            self.read_context([b'12345'], ['large', 'later'], 4)
        self.assertEqual(self.reads, [call(5)])

    def test_later_overflow_never_returns_partial_context(self):
        with self.assertRaisesRegex(ValueError, 'budget'):
            self.read_context([b'123', b'45'], ['first', 'second'], 4)
        self.assertEqual(self.reads, [call(5), call(2)])

    def test_exact_utf8_budget_and_invalid_utf8(self):
        self.assertIn('é', self.read_context(['é'.encode()], ['file'], 2))
        self.assertEqual(self.reads, [call(3)])
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            self.read_context([b'\xff'], ['file'], 1)

    def test_missing_or_excess_context_is_explicitly_refused(self):
        with patch.object(Path, 'is_file', return_value=False), self.assertRaises(ValueError):
            claude_desktop.context_files(ROOT, ['missing'])
        with self.assertRaises(ValueError):
            claude_desktop.context_files(ROOT, ['x', 'y'], max_files=1)


class SourceExportTests(unittest.TestCase):
    def test_export_closes_build_and_local_import_dependencies(self):
        tree = ast.parse((ROOT / 'resume').read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'share_files')
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'share_files', 'exec'), namespace)
        names = set(namespace['share_files']())
        from package_helpers import ASSETS
        self.assertTrue(set(ASSETS) <= names)
        self.assertTrue({'native_contract.py', 'package_runtime.py', 'StateSetup.swift',
                         'Integrations.swift', 'SchedulerService.swift', 'IronClawConnection.swift',
                         'state_setup.py', 'state_install.py', 'ux_review_setup.py',
                         'install-ux-review.py'} <= names)
        required = set(re.findall(r'\$ROOT/([A-Za-z0-9_.-]+\.(?:py|swift))',
                                  (ROOT / 'native-install').read_text()))
        self.assertTrue(required <= names, required - names)
        for name in names:
            self.assertTrue((ROOT / name).is_file(), name)
            if name.endswith('.py') or name == 'resume':
                for node in ast.walk(ast.parse((ROOT / name).read_text())):
                    imports = ([a.name for a in node.names] if isinstance(node, ast.Import)
                               else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                    for module in imports:
                        dependency = module.split('.')[0] + '.py'
                        if (ROOT / dependency).is_file():
                            self.assertIn(dependency, names, (name, dependency))
