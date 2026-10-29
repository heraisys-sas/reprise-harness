"""Refusal kinds (issue #53) and awaiting_human reconciliation (blocked_permission)."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import error_payload
from delegation_store import Store


class RefusalKindTests(unittest.TestCase):
    def test_refused_for_caller_mistake(self):
        payload = error_payload(ValueError('chatId and filePaths are only valid with backend claude-desktop'))
        self.assertEqual(payload['data']['kind'], 'refused')
        self.assertEqual(payload['code'], -32602)

    def test_busy_for_locked_database(self):
        payload = error_payload(sqlite3.OperationalError('database is locked'))
        self.assertEqual(payload['data']['kind'], 'busy')
        payload = error_payload(sqlite3.OperationalError('database table is busy'))
        self.assertEqual(payload['data']['kind'], 'busy')

    def test_storage_error_for_other_sqlite_and_os_failures(self):
        self.assertEqual(error_payload(sqlite3.OperationalError('no such table: x'))['data']['kind'], 'storage_error')
        self.assertEqual(error_payload(sqlite3.DatabaseError('malformed'))['data']['kind'], 'storage_error')
        self.assertEqual(error_payload(OSError('disk full'))['data']['kind'], 'storage_error')

    def test_message_unchanged(self):
        for exc in (ValueError('x'), sqlite3.OperationalError('database is locked'), OSError('y')):
            self.assertEqual(error_payload(exc)['message'], 'Request refused or unavailable; no automatic retry.')


class AwaitingHumanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = sqlite3.connect(Path(self.temp.name) / 'expert.sqlite')
        self.store = Store(self.db, 'scope')

    def test_blocked_permission_is_awaiting_human(self):
        self.store.begin('k', {'tool': 'reprise_delegate', 'question': 'Q'})
        self.store.finish('k', {'status': 'blocked_permission', 'reason': 'Human approval required.'})
        state, = self.db.execute("SELECT state FROM delegation_requests WHERE request_key='k'").fetchone()
        self.assertEqual(state, 'awaiting_human')
        receipt = self.store.lookup({'request_key': 'k'})
        self.assertEqual(receipt['state'], 'awaiting_human')
        self.assertEqual(receipt['result']['status'], 'blocked_permission')

    def test_awaiting_human_blocks_begin_until_acknowledged(self):
        self.store.begin('parent', {'tool': 'reprise_delegate', 'question': 'Q'})
        self.store.finish('parent', {'status': 'blocked_permission', 'reason': 'Approval pending.'})
        for payload in ({'tool': 'reprise_delegate', 'question': 'Q'},
                        {'tool': 'reprise_delegate', 'question': 'Other'}):
            blocked = self.store.begin('child', payload, parent='parent')
            self.assertEqual(blocked['status'], 'indeterminate')
        self.db.commit()
        self.db.execute("UPDATE delegation_requests SET state='awaiting_human' WHERE request_key='parent'")
        self.db.commit()
        equivalent = self.store.begin('twin', {'tool': 'reprise_delegate', 'question': 'Q'})
        self.assertEqual(equivalent['status'], 'indeterminate')
        receipt = self.store.lookup({'request_key': 'parent', 'view': 'receipt', 'acknowledge': True})
        self.assertEqual(receipt['state'], 'recorded')
        self.assertTrue(receipt['acknowledged'])
        state, = self.db.execute("SELECT state FROM delegation_requests WHERE request_key='parent'").fetchone()
        self.assertEqual(state, 'recorded')
        # Acknowledgement reconciles the receipt: a recorded parent admits new delegations.
        # A distinct payload under the reconciled parent is admitted again.
        stored = self.store.begin('child', {'tool': 'reprise_delegate', 'question': 'Follow-up'}, parent='parent')
        self.assertIsNone(stored)  # fresh attempt admitted (new dispatch)
        self.store.finish('child', {'status': 'finished'})
        admitted = self.store.begin('child', {'tool': 'reprise_delegate', 'question': 'Follow-up'}, parent='parent')
        self.assertEqual(admitted['status'], 'finished')

    def test_backfill_is_idempotent(self):
        # Legacy receipts persisted as indeterminate with a blocked_permission result
        # are migrated to awaiting_human; replaying the migration changes nothing.
        self.db.execute("INSERT INTO delegation_requests(scope,request_key,identity,signature,parent,root,depth,attempt,state,result)"
                        " VALUES ('scope','legacy','i','s',NULL,'legacy',0,'a','indeterminate',?)",
                        (json.dumps({'status': 'blocked_permission', 'reason': 'r'}),))
        self.db.execute("INSERT INTO delegation_requests(scope,request_key,identity,signature,parent,root,depth,attempt,state,result)"
                        " VALUES ('scope','other','i2','s2',NULL,'other',0,'b','indeterminate',?)",
                        (json.dumps({'status': 'timed_out'}),))
        for _ in range(2):
            self.store.reconcile_awaiting_human()
        legacy, = self.db.execute("SELECT state FROM delegation_requests WHERE request_key='legacy'").fetchone()
        other, = self.db.execute("SELECT state FROM delegation_requests WHERE request_key='other'").fetchone()
        self.assertEqual(legacy, 'awaiting_human')
        self.assertEqual(other, 'indeterminate')


if __name__ == '__main__':
    unittest.main()
