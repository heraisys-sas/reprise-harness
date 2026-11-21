import importlib.machinery
import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import contextlib
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_quota
import engines
import private_search
import reprise_health
import scheduler

loader = importlib.machinery.SourceFileLoader('reprise', str(Path(__file__).parents[1] / 'reprise'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

ZONE = ZoneInfo('Europe/Paris')
PAUSE_TEXT = ('En pause : votre utilisation gratuite sera de nouveau disponible '
              '12 nov. à 14:00. Vous pourrez continuer à ce moment-là.')


class QuotaStateTests(unittest.TestCase):
    def setUp(self):
        transport = patch('engines.select', return_value='codex-cli')
        transport.start()
        self.addCleanup(transport.stop)

    def _tick(self, db, run_native_result):
        folder = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
        db.close()
        with patch.object(module, 'database', return_value=module.database()), \
             patch.object(module, 'codex_executable', return_value=None), \
             patch.object(module, 'run_due') as run_due, \
             patch.object(scheduler, 'refresh_external'), \
             patch.object(claude_quota, 'run_native', return_value=run_native_result), \
             patch.object(private_search, 'cleanup'), \
             patch.object(reprise_health, 'watch'), \
             patch.object(sys, 'argv', ['reprise', 'tick']):
            module.main()
        run_due.assert_called_once()
        self.assertEqual(run_due.call_args[0][1], None)
        with contextlib.closing(sqlite3.connect(folder / 'tasks.sqlite')) as reopened:
            return scheduler.get(reopened, 'claude_quota_state')

    def test_waiting_resume_reports_blocked_with_resume_at(self):
        now = datetime(2026, 11, 11, 10, 0, tzinfo=ZONE).timestamp()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(module, 'STATE', Path(folder)), \
             patch.object(time, 'time', return_value=now):
            db = module.database()
            key = claude_quota.register_notice(db, '12345678-1234-1234-1234-123456789abc',
                                               PAUSE_TEXT, now, ZONE)
            self.assertIsNotNone(key)
            state = claude_quota.quota_state(db, now=now)
            self.assertEqual(state['status'], 'blocked')
            self.assertEqual(state['reason'], 'quota')
            self.assertEqual(state['resume_at'],
                             datetime(2026, 11, 12, 14, 0, tzinfo=ZONE).timestamp())
            db.close()

    def test_expired_resume_reports_ok(self):
        now = datetime(2026, 11, 11, 10, 0, tzinfo=ZONE).timestamp()
        after = datetime(2026, 11, 12, 14, 1, tzinfo=ZONE).timestamp()
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(module, 'STATE', Path(folder)):
            db = module.database()
            claude_quota.register_notice(db, '12345678-1234-1234-1234-123456789abc',
                                         PAUSE_TEXT, now, ZONE)
            state = claude_quota.quota_state(db, now=after)
            self.assertEqual(state, {'status': 'ok', 'reason': None, 'resume_at': None})
            db.close()

    def test_tick_writes_blocked_state_after_quota_refusal(self):
        now = datetime(2026, 11, 11, 10, 0, tzinfo=ZONE).timestamp()
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                fixed = datetime.fromtimestamp(now, ZONE)
                return fixed.astimezone(tz) if tz else fixed.replace(tzinfo=None)

        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(module, 'STATE', Path(folder)), \
             patch.object(module, 'dt', SimpleNamespace(datetime=FixedDateTime)), \
             patch.object(time, 'time', return_value=now):
            db = module.database()
            claude_quota.register_notice(db, '12345678-1234-1234-1234-123456789abc',
                                         PAUSE_TEXT, now, ZONE)
            state = self._tick(db, {'status': 'observed', 'submitted': 0})
            self.assertEqual(state['status'], 'blocked')
            self.assertEqual(state['reason'], 'quota')
            self.assertIsInstance(state['resume_at'], float)
            self.assertIsInstance(state['checked_at'], float)

    def test_tick_writes_ok_state_without_quota_block(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(module, 'STATE', Path(folder)):
            db = module.database()
            state = self._tick(db, {'status': 'observed', 'submitted': 0})
            self.assertEqual(state['status'], 'ok')
            self.assertIsNone(state['resume_at'])
            self.assertIsInstance(state['checked_at'], float)


if __name__ == '__main__':
    unittest.main()
