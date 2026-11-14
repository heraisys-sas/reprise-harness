"""Desktop shared-socket mode is retired: fail closed without touching codexd.

Mocks guard external side effects. No app, socket, model turn or daemon is used.
"""
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import engines
import scheduler
import shared_mode as shared


class SharedModeFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        scheduler.setup(self.db)
        engines.setup(self.db)
        with self.db:
            scheduler.put(self.db, 'auto_enabled', False)

    def _legacy(self, **changes):
        saved = {
            'prepared': True, 'launched': True, 'launch_version': 'v1',
            'verified_version': 'v1', 'verified_session': 'legacy-thread',
            'restore_auto_after_check': True,
        } | changes
        with self.db:
            scheduler.put(self.db, 'shared_mode', saved)
        return saved

    def test_legacy_success_is_never_startup_eligible_or_route_verified(self):
        history = self._legacy()
        with patch.object(shared, 'desktop_version', return_value='v1'):
            state = shared.state(self.db)
        self.assertEqual(state['status'], 'unsupported')
        self.assertEqual(state['desktop_version'], 'v1')
        self.assertTrue(state['legacy_verification_present'])
        self.assertFalse(state['startup_eligible'])
        self.assertFalse(state['shared_mode_supported'])
        self.assertFalse(state['desktop_socket_binding_verified'])
        self.assertFalse(state['prepared'])
        self.assertFalse(state['launched'])
        self.assertFalse(state['restore_auto_after_check'])
        self.assertIsNone(state['verified_version'])
        self.assertIsNone(state['verified_session'])
        self.assertEqual(scheduler.get(self.db, 'shared_mode'), history)

    def test_unvalidated_historical_state_remains_unvalidated(self):
        with self.db:
            scheduler.put(self.db, 'shared_mode', {'launched': True, 'launch_version': 'v1'})
        with patch.object(shared, 'desktop_version', return_value='v1'):
            state = shared.state(self.db)
        self.assertFalse(state['legacy_verification_present'])
        self.assertFalse(state['startup_eligible'])
        self.assertFalse(state['desktop_socket_binding_verified'])

    def test_state_does_not_rewrite_global_scheduler_mode(self):
        self._legacy()
        with self.db:
            scheduler.put(self.db, 'auto_enabled', True)
        with patch.object(shared, 'desktop_version', return_value='v1'):
            state = shared.state(self.db)
        self.assertFalse(state['startup_eligible'])
        # Read-only observation must neither enable nor disable unrelated work.
        self.assertTrue(scheduler.enabled(self.db))

    def test_all_actions_refuse_without_process_socket_or_scheduler_effects(self):
        history = self._legacy()
        actions = [
            ('prepare', lambda: shared.prepare(self.db, 'codex', '/tmp', startup=True)),
            ('launch', lambda: shared.launch(self.db, 'codex', '/tmp', startup=True)),
            ('check', lambda: shared.check(self.db, 'codex', 'legacy-thread')),
            ('stop', lambda: shared.stop(self.db, 'codex', '/tmp', startup=True)),
        ]
        # If the retired path ever attempts to contact a daemon, quit the UI,
        # change auto_enabled or acquire any RPC writer, the test must fail.
        with patch.object(shared.subprocess, 'run', side_effect=AssertionError('process')), \
             patch.object(shared, 'desktop_running', side_effect=AssertionError('desktop probe')), \
             patch.object(scheduler, 'put', side_effect=AssertionError('state write')), \
             patch('connection.probe', side_effect=AssertionError('socket probe')), \
             patch('codex_rpc.Client', side_effect=AssertionError('RPC client')) as client:
            for operation, invoke in actions:
                with self.subTest(operation=operation):
                    result = invoke()
                    self.assertFalse(result['ok'])
                    self.assertEqual(result['status'], 'unsupported')
                    self.assertEqual(result['operation'], operation)
                    self.assertFalse(result['shared_mode_supported'])
                    self.assertFalse(result['desktop_socket_binding_verified'])
                    self.assertFalse(result['auto_restored'])
                    self.assertIn('stdio', result['reason'])
            client.assert_not_called()
        self.assertEqual(scheduler.get(self.db, 'shared_mode'), history)
        self.assertFalse(scheduler.enabled(self.db))

    def test_check_cannot_restore_auto_after_readable_thread(self):
        self._legacy(restore_auto_after_check=True)
        self.assertFalse(scheduler.enabled(self.db))
        with patch('connection.probe', return_value={
            'connected': True, 'thread_loaded': True, 'thread_status': 'idle'
        }) as connection_probe:
            result = shared.check(self.db, 'codex', 'legacy-thread')
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'unsupported')
        connection_probe.assert_not_called()
        self.assertFalse(scheduler.enabled(self.db))
        self.assertFalse(shared.state(self.db)['startup_eligible'])

    def test_no_implicit_codexd_daemon_start_even_if_missing(self):
        with patch.object(shared.subprocess, 'run', side_effect=AssertionError('start codexd')) as run:
            self.assertFalse(shared.prepare(self.db, None, '/missing')['ok'])
            self.assertFalse(shared.launch(self.db, 'codex', '/missing')['ok'])
            run.assert_not_called()

    def test_no_implicit_codexd_stop_even_if_desktop_closed(self):
        with patch.object(shared.subprocess, 'run', side_effect=AssertionError('stop codexd')) as run:
            result = shared.stop(self.db, 'codex', '/tmp')
        self.assertEqual(result['status'], 'unsupported')
        run.assert_not_called()

    def test_server_rpc_clients_remain_separate_and_unmodified(self):
        # This module's refusal must not alter existing explicit engine routing.
        self.assertNotIn('CODEX_APP_SERVER_WS_URL', Path(shared.__file__).read_text())
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO engine_settings VALUES (?,?)',
                            ('codex-server', '{"socket": "/private/a.sock"}'))
        with patch.object(engines, 'socket_path', return_value=Path('/private/a.sock')):
            self.assertEqual(engines.select('codex-server', {'socket': '/private/a.sock'}),
                             'codex-server')


if __name__ == '__main__':
    unittest.main()
