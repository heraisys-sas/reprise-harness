"""Boucle complète quota en conditions simulées (#132) : pause → register → run_native quick-apply mocké."""
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_quota as quota
import scheduler

ZONE = ZoneInfo('Europe/Paris')
TEXT = 'En pause : votre utilisation gratuite sera de nouveau disponible 24 oct. à 01:50. Vous pourrez continuer à ce moment-là.'
CHAT = '12345678-1234-1234-1234-123456789abc'
OTHER = '12345678-1234-1234-1234-123456789abd'
NOW = datetime(2026, 10, 23, 23, 0, tzinfo=ZONE).timestamp()
DUE = datetime(2026, 10, 24, 1, 50, tzinfo=ZONE).timestamp()
OBSERVATION = dict(status='observed', chat_id=CHAT, pause_text=TEXT,
                   evidence_sha256='a' * 64, busy=False, continue_enabled=False, composer_empty=True)


class ClaudeQuotaEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE scheduler_meta(key TEXT PRIMARY KEY,value TEXT)')
        self.db.execute("INSERT INTO scheduler_meta VALUES ('auto_enabled','true')")
        self.db.execute("INSERT INTO scheduler_meta VALUES ('claude_desktop_auto_enabled','true')")
        self.db.commit()
        quota.initialize(self.db)
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)

    # Étape 1 : pause représentative → ligne waiting avec échéance correcte.
    def test_parse_register_then_run_native_submits_once(self):
        due = quota.parse_pause(TEXT, NOW, ZONE)
        self.assertEqual(due, DUE)
        key = quota.register(self.db, OBSERVATION, NOW, ZONE)
        self.assertIsNotNone(key)
        rows = quota.status(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['status'], rows[0]['due']), ('waiting', DUE))
        # Variante notice opérateur sans observation native.
        quota.register_notice(self.db, OTHER, TEXT, NOW, ZONE)
        notice = next(r for r in quota.status(self.db) if r['chat_id'] == OTHER)
        self.assertEqual(notice['status'], 'awaiting_observation')

        receipts = []

        class Client:
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def call(self, name, args):
                receipts.append((name, args))
                if name == 'quick_entry/capabilities':
                    return {'dispatch_supported': True, 'resume_validation_supported': True}
                if name == 'quick_entry/submit':
                    return {'status': 'finished', 'admission_only': False, 'dispatch_attempted': True}
                return {}

        # Le mock remplace claude_desktop_rpc.Client (aucun socket réel).
        with mock.patch('claude_desktop_rpc.Client', Client):
            # Avant l'échéance : aucune soumission.
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE - 1)
            self.assertEqual(result['submitted'], 0)
            self.assertFalse(any(name == 'quick_entry/submit' for name, _ in receipts))
            # À l'échéance : seule la ligne waiting part en dispatch (la notice
            # awaiting_observation reste locale, sans clic).
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 1)
            self.assertEqual(result['status'], 'observed')
            self.assertEqual(result['transport'], 'quick-apply')
            self.assertEqual(result['submitted'], 1)
            statuses = {r['chat_id']: r['status'] for r in quota.status(self.db)}
            self.assertEqual(statuses[CHAT], 'submitted')
            self.assertEqual(statuses[OTHER], 'awaiting_observation')
            messages = [args for name, args in receipts if name == 'quick_entry/submit']
            self.assertEqual([m['chatId'] for m in messages], [CHAT])
            self.assertTrue(all(m['request_key'].startswith('resume-') for m in messages))

            # Une seule soumission par interruption : rejeu refusé.
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 2)
            self.assertEqual(result['submitted'], 0)
            self.assertEqual(sum(name == 'quick_entry/submit' for name, _ in receipts), 1)

    def test_manual_action_between_ticks_supersedes_and_blocks_dispatch(self):
        quota.register(self.db, OBSERVATION, NOW, ZONE)
        key = quota.status(self.db)[0]['event_key']
        # Action manuelle : l'utilisateur a poursuivi lui-même (fingerprint changé) → superseded.
        self.db.execute("UPDATE claude_quota_resumes SET status='superseded' WHERE event_key=?", (key,))
        self.db.commit()

        class Client:
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def call(self, name, args):
                if name == 'quick_entry/submit':
                    raise AssertionError('superseded must never be dispatched')
                return {'dispatch_supported': True, 'resume_validation_supported': True}

        with mock.patch('claude_desktop_rpc.Client', Client):
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 1)
        self.assertEqual(result['submitted'], 0)
        self.assertEqual(quota.status(self.db)[0]['status'], 'superseded')

    def test_unknown_transport_fails_closed_without_writes(self):
        quota.register(self.db, OBSERVATION, NOW, ZONE)
        scheduler.put(self.db, 'claude_desktop_transport', 'accessibility')
        with mock.patch('claude_desktop_rpc.Client') as mock_client_cls:
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 1)
            self.assertEqual(result, {'status': 'unknown_transport', 'submitted': 0,
                                      'transport': 'accessibility'})
            mock_client_cls.assert_not_called()
        self.assertEqual(quota.status(self.db)[0]['status'], 'waiting')
        self.assertFalse(list(Path(self.state.name).glob('claude-quota.lock')))

    # Règle permanente : aucun test ne doit atteindre ~/.claude-desktop/quick-entry.sock.
    DEAD_SOCKET = '/nonexistent/reprise-test-dead.sock'

    def _guard_real_socket(self):
        import socket as socket_module
        real_connect = socket_module.socket.connect
        self.addCleanup(setattr, socket_module.socket, 'connect', real_connect)

        def forbidden(self_socket, address):
            raise AssertionError('real socket touched: %r' % (address,))
        socket_module.socket.connect = forbidden
        return mock.patch('claude_desktop_rpc.REPRISE_DISPATCH_SOCKET', Path(self.DEAD_SOCKET)), \
               mock.patch('claude_quota.REPRISE_DISPATCH_SOCKET', Path(self.DEAD_SOCKET), create=True), \
               mock.patch('claude_desktop.DEFAULT_SOCKET', Path(self.DEAD_SOCKET))

    def test_crash_during_dispatch_marks_uncertain_without_retry(self):
        quota.register(self.db, OBSERVATION, NOW, ZONE)
        calls = []

        class Client:
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def call(self, name, args):
                calls.append(name)
                if name == 'quick_entry/capabilities':
                    return {'dispatch_supported': True, 'resume_validation_supported': True}
                if name == 'quick_entry/submit':
                    raise RuntimeError('dispatch crashed')
                return {}

        with mock.patch('claude_desktop_rpc.Client', Client):
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 1)
            self.assertEqual(result['submitted'], 0)
            self.assertEqual(quota.status(self.db)[0]['status'], 'uncertain')
            # Aucune nouvelle tentative au tick suivant.
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 2)
            self.assertEqual(result['submitted'], 0)
            self.assertEqual(calls.count('quick_entry/submit'), 1)

    def test_bad_capabilities_fail_clean_without_dispatch(self):
        quota.register(self.db, OBSERVATION, NOW, ZONE)

        class Client:
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def call(self, name, args):
                if name == 'quick_entry/submit':
                    raise AssertionError('unavailable driver must not dispatch')
                return {'dispatch_supported': False, 'resume_validation_supported': False}

        with mock.patch('claude_desktop_rpc.Client', Client):
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 1)
        self.assertEqual(result['status'], 'dispatch_driver_unavailable')
        self.assertEqual(result['submitted'], 0)
        self.assertEqual(quota.status(self.db)[0]['status'], 'waiting')

    def test_global_pause_blocks_dispatch(self):
        quota.register(self.db, OBSERVATION, NOW, ZONE)
        scheduler.put(self.db, 'claude_desktop_auto_enabled', 'false')

        class Client:
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def call(self, name, args):
                raise AssertionError('paused scheduler must not dispatch')

        with mock.patch('claude_desktop_rpc.Client', Client):
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 1)
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(result['submitted'], 0)
        self.assertEqual(quota.status(self.db)[0]['status'], 'waiting')

    def test_run_native_never_touches_real_socket(self):
        quota.register(self.db, OBSERVATION, NOW, ZONE)

        class Client:
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def call(self, name, args):
                if name == 'quick_entry/capabilities':
                    return {'dispatch_supported': True, 'resume_validation_supported': True}
                if name == 'quick_entry/submit':
                    return {'status': 'finished', 'admission_only': False, 'dispatch_attempted': True}
                return {}

        patches = self._guard_real_socket()
        with mock.patch('claude_desktop_rpc.Client', Client), patches[0], patches[1], patches[2]:
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 1)
        self.assertEqual(result['status'], 'observed')
        self.assertEqual(result['submitted'], 1)


if __name__ == '__main__':
    unittest.main()
