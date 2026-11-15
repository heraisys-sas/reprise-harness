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

    def seed_event(self, event, chat, due):
        with self.db:
            self.db.execute('INSERT INTO claude_quota_resumes VALUES (?,?,?,?,?,?,?)',
                            (event, chat, 10, due, 'f' * 64, 'p' * 64, 'waiting'))

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
        self.seed_event('ev-future', 'chat-future', 5000)
        result = self.run_admission(now=1000, observe=lambda: {'resets_at': 5000})
        self.assertEqual(result['status'], 'waiting')
        self.assertEqual(result['resets_at'], 5000)

    def test_admits_at_deadline_once_with_the_transfer_brief(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True)
        self.seed_event('ev-5000', 'chat-5000', 5000)
        result = self.run_admission(now=6000, observe=lambda: {'resets_at': 5000})
        self.assertEqual(result['status'], 'admitted')
        self.assertEqual(result['request_key'], 'claude-continuation-ev-5000')
        store = TaskStore(self.quick_state, self.project)
        row = store.db.execute("SELECT prompt,chat_id FROM desktop_tasks WHERE request_key='claude-continuation-ev-5000'").fetchone()
        self.assertIn('Continuation après quota', row[0])
        self.assertIn('chat_id=chat-5000', row[0])
        self.assertEqual(row[1], 'chat-5000')
        store.close()
        again = self.run_admission(now=6000, observe=lambda: {'resets_at': 5000})
        self.assertEqual(again['status'], 'already_admitted')

    def test_snapshot_deadline_alone_never_invents_a_session(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True)
        result = self.run_admission(now=6000, snapshot={'resets_at': 5000})
        self.assertEqual(result['status'], 'no_verified_session')
        store = TaskStore(self.quick_state, self.project)
        try:
            self.assertEqual(store.list_tasks()['tasks'], [])
        finally:
            store.close()

    def test_two_chats_same_deadline_have_distinct_durable_tasks(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True)
        self.seed_event('ev-one', 'chat-one', 5000)
        self.seed_event('ev-two', 'chat-two', 5000)
        result = self.run_admission(now=5000, snapshot={'resets_at': 5000})
        self.assertEqual(result['count'], 2)
        self.assertEqual(set(result['request_keys']),
                         {'claude-continuation-ev-one', 'claude-continuation-ev-two'})
        self.assertEqual(self.run_admission(now=5001, snapshot={'resets_at': 5000})['status'],
                         'already_admitted')
        store = TaskStore(self.quick_state, self.project)
        try:
            self.assertEqual({t['request_key'] for t in store.list_tasks()['tasks']},
                             set(result['request_keys']))
        finally:
            store.close()

    def test_legacy_deadline_admission_stays_uncertain(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True,
                    claude_queue_admitted_for='5000.0')
        self.seed_event('ev-one', 'chat-one', 5000)
        self.seed_event('ev-two', 'chat-two', 5100)
        result = self.run_admission(now=6000, snapshot={'resets_at': 5100})
        self.assertEqual(result['status'], 'admitted')
        self.assertEqual(result['legacy_uncertain_events'], ['ev-one'])
        self.assertEqual(result['request_keys'], ['claude-continuation-ev-two'])
        later = self.run_admission(now=6000, snapshot={'resets_at': 5100})
        self.assertEqual(later['status'], 'legacy_uncertain')
        store = TaskStore(self.quick_state, self.project)
        try:
            self.assertNotIn('claude-continuation-ev-one',
                             {t['request_key'] for t in store.list_tasks()['tasks']})
        finally:
            store.close()

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
        self.assertEqual(result['request_key'], 'claude-continuation-ev')

    def test_uncertain_deliveries_are_skipped_not_redelivered(self):
        self.seeded(auto_enabled=True, claude_queue_channel=True)
        store = TaskStore(self.quick_state, self.project)
        store.admit('t-old', 'remise incertaine')
        handed = store.claim('t-old', 'claim-1')
        self.assertEqual(handed['delivery'], 'handed_over')
        store.close()
        self.seed_event('ev-uncertain', 'chat-uncertain', 5000)
        result = self.run_admission(now=6000, observe=lambda: {'resets_at': 5000})
        self.assertEqual(result['status'], 'admitted')
        self.assertEqual(result['request_key'], 'claude-continuation-ev-uncertain')
        store = TaskStore(self.quick_state, self.project)
        row = store.db.execute("SELECT claim_key,delivery FROM desktop_tasks WHERE request_key='t-old'").fetchone()
        self.assertEqual(row[0], 'claim-1')
        self.assertEqual(row[1], 'attempted')
        store.close()


if __name__ == '__main__':
    unittest.main()
