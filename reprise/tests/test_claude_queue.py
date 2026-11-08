import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scheduler
import claude_quota
from desktop_task_store import TaskStore


class QueueAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = str(root)
        self.quick_state = str(root / 'quick-state')
        self.project = str(root / 'project')
        (root / 'project').mkdir()
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        scheduler.setup(self.db)
        claude_quota.initialize(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def seeded(self, **meta):
        for key, value in meta.items():
            scheduler.put(self.db, key, value)

    def run_admission(self, now=1000, observe=None, snapshot=None):
        return claude_quota.admit_queue_continuation(
            self.db, self.root, self.quick_state, self.project,
            now=now, snapshot=snapshot, observe=observe)

    def test_queue_disabled_by_default(self):
        self.seeded(auto_enabled=True)
        result = self.run_admission(observe=lambda: {'resets_at': 500})
        self.assertEqual(result['status'], 'queue_disabled')

    def test_paused_scheduler_blocks_admission(self):
        self.seeded(auto_enabled=False, claude_queue_channel=True)
        result = self.run_admission(observe=lambda: {'resets_at': 500})
        self.assertEqual(result['status'], 'paused')

    def test_deadline_in_future_waits(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True)
        result = self.run_admission(now=1000, observe=lambda: {'resets_at': 5000})
        self.assertEqual(result['status'], 'waiting')
        self.assertEqual(result['resets_at'], 5000)

    def test_admits_at_deadline_once_with_the_transfer_brief(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True)
        result = self.run_admission(now=6000, observe=lambda: {'resets_at': 5000})
        self.assertEqual(result['status'], 'admitted')
        self.assertEqual(result['request_key'], 'claude-continuation-5000')
        store = TaskStore(self.quick_state, self.project)
        row = store.db.execute("SELECT prompt FROM desktop_tasks WHERE request_key='claude-continuation-5000'").fetchone()
        self.assertIn('Continuation après quota', row[0])
        self.assertIn('/workspace', row[0])
        store.close()
        again = self.run_admission(now=6000, observe=lambda: {'resets_at': 5000})
        self.assertEqual(again['status'], 'already_admitted')

    def test_snapshot_without_deadline_reports_no_deadline(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True)
        result = self.run_admission(observe=lambda: {'notice': None})
        self.assertEqual(result['status'], 'no_deadline')

    def test_waiting_row_of_the_native_tick_feeds_the_deadline(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True)
        with self.db:
            self.db.execute("INSERT INTO claude_quota_resumes VALUES ('ev','chat',10,10,'fp','hash','waiting')")
        result = self.run_admission(now=6000, observe=lambda: {'notice': None})
        self.assertEqual(result['status'], 'admitted')
        self.assertEqual(result['request_key'], 'claude-continuation-10')

    def test_uncertain_deliveries_are_skipped_not_redelivered(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True)
        store = TaskStore(self.quick_state, self.project)
        store.admit('t-old', 'remise incertaine')
        handed = store.claim('t-old', 'claim-1')
        self.assertEqual(handed['delivery'], 'handed_over')
        store.close()
        result = self.run_admission(now=6000, observe=lambda: {'resets_at': 5000})
        self.assertEqual(result['status'], 'admitted')
        self.assertEqual(result['request_key'], 'claude-continuation-5000')
        store = TaskStore(self.quick_state, self.project)
        row = store.db.execute("SELECT claim_key,delivery FROM desktop_tasks WHERE request_key='t-old'").fetchone()
        self.assertEqual(row[0], 'claim-1')
        self.assertEqual(row[1], 'attempted')
        store.close()


if __name__ == '__main__':
    unittest.main()
