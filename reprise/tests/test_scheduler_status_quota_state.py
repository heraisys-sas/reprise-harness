"""Payload scheduler-status enrichi : champ claude_quota_state exposé (#134)."""
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import engines
import run_status
import scheduler

TASKS_SCHEMA = ("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, session TEXT, cwd TEXT, "
                "due REAL, status TEXT, attempts INTEGER)")


class SchedulerStatusQuotaStateTests(unittest.TestCase):
    def _payload(self, folder):
        with closing(sqlite3.connect(Path(folder) / 'tasks.sqlite')) as db:
            scheduler.setup(db)
            run_status.setup(db)
            engines.setup(db)
            db.execute(TASKS_SCHEMA)
            yield db

    def test_status_includes_claude_quota_state(self):
        state = {'status': 'blocked', 'reason': 'quota', 'resume_at': 1780000000.0, 'checked_at': 1779990000.0}
        with tempfile.TemporaryDirectory() as folder:
            for db in self._payload(folder):
                scheduler.put(db, 'claude_quota_state', state)
                payload = scheduler.status(db, Path(folder))
                self.assertEqual(payload['claude_quota_state'], state)

    def test_status_omits_claude_quota_state_when_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            for db in self._payload(folder):
                payload = scheduler.status(db, Path(folder))
                self.assertIsNone(payload['claude_quota_state'])


if __name__ == '__main__':
    unittest.main()
