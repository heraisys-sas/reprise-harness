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
            # À l'échéance : run_native soumet les deux lignes (waiting + awaiting_observation).
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 1)
            self.assertEqual(result['status'], 'observed')
            self.assertEqual(result['transport'], 'quick-apply')
            self.assertEqual(result['submitted'], 2)
            statuses = {r['chat_id']: r['status'] for r in quota.status(self.db)}
            self.assertEqual(statuses[CHAT], 'submitted')
            self.assertEqual(statuses[OTHER], 'submitted')
            messages = [args for name, args in receipts if name == 'quick_entry/submit']
            self.assertEqual(sorted(m['chatId'] for m in messages), sorted([CHAT, OTHER]))
            self.assertTrue(all(m['request_key'].startswith('resume-') for m in messages))

            # Une seule soumission par interruption : rejeu refusé.
            result = quota.run_native(self.db, str(Path(self.state.name)), str(Path(self.state.name)), now=DUE + 2)
            self.assertEqual(result['submitted'], 0)
            self.assertEqual(sum(name == 'quick_entry/submit' for name, _ in receipts), 2)

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


if __name__ == '__main__':
    unittest.main()
