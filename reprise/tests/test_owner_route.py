import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import owner_route
import run_status
from engines import Unavailable


CHILD = '01a1418b-f417-7be0-b62b-25c63629a6f2'
PARENT = '01a1418b-f417-7be0-b62b-25c63629a6f3'
TASK = {'id': 'request-1', 'session': CHILD, 'cwd': '/tmp', 'prompt': 'Continue the pending step.'}


class FakeClient:
    def __init__(self):
        self.calls, self.closed = [], False
        self.parent_state, self.parent_cwd, self.parent_relation = 'idle', '/tmp', PARENT
        self.quota, self.account, self.provider = True, 'chatgpt', 'openai'
        self.queued, self.busy_after_queue, self.start_error = [], False, False

    def send(self, message):
        self.calls.append(('notification', message))

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == 'thread/read':
            child = params['threadId'] == CHILD
            return {'thread': {'id': params['threadId'], 'parentThreadId': self.parent_relation if child else None,
                    'cwd': '/tmp' if child else self.parent_cwd,
                    'status': {'type': 'active' if not child and self.busy_after_queue and self.queued else self.parent_state},
                    'modelProvider': self.provider}}
        if method == 'account/read':
            return {'account': {'type': self.account}}
        if method == 'account/rateLimits/read':
            return {'ordinaryUsageAllowed': self.quota}
        if method == 'thread/queue/list':
            return {'data': self.queued, 'nextCursor': None}
        if method == 'thread/queue/add':
            item = dict(params, id='submission-1')
            self.queued.append(item)
            return {'queuedSubmission': item}
        if method == 'thread/queue/start':
            if self.start_error:
                raise TimeoutError()
            return {'turn': {'id': 'parent-turn-1'}}
        return {}

    def close(self):
        self.closed = True


class OwnerRouteTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        run_status.setup(self.db)
        self.temp = tempfile.TemporaryDirectory()
        self.log = Path(self.temp.name) / 'request.log'
        self.client = FakeClient()

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def execute(self, automatic=False):
        with patch.object(owner_route, 'Client', return_value=self.client):
            result = owner_route.execute(self.db, TASK, 'codex', {'parent': PARENT}, self.log, automatic=automatic)
        self.assertEqual(result, 'needs_review')
        self.assertTrue(self.client.closed)
        return self.db.execute('SELECT state FROM run_details').fetchone()[0]

    def mutations(self):
        return [(method, params) for method, params in self.client.calls if method in ('thread/queue/add', 'thread/queue/start', 'turn/start', 'thread/resume')]

    def test_confirmed_parent_receives_native_queue_without_claiming_child_success(self):
        self.assertEqual(self.execute(), 'delegated_parent')
        self.assertEqual([method for method, _ in self.mutations()], ['thread/queue/add', 'thread/queue/start'])
        receipt = json.loads(self.log.read_text())
        self.assertEqual(receipt['child'], CHILD)
        self.assertEqual(receipt['parent_turn_id'], 'parent-turn-1')
        self.assertNotIn(TASK['prompt'], self.log.read_text())
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)
        self.assertTrue(self.client.calls[0][1]['capabilities']['experimentalApi'])

    def test_unconfirmed_relationship_never_sends(self):
        self.client.parent_relation = None
        self.assertEqual(self.execute(), 'blocked_session')
        self.assertEqual(self.mutations(), [])

    def test_other_directory_never_sends(self):
        self.client.parent_cwd = '/'
        self.assertEqual(self.execute(), 'blocked_session')
        self.assertEqual(self.mutations(), [])

    def test_busy_parent_never_steered(self):
        self.client.parent_state = 'active'
        self.assertEqual(self.execute(), 'blocked_owner')
        self.assertEqual(self.mutations(), [])

    def test_parent_becomes_busy_after_enqueue_no_start(self):
        self.client.busy_after_queue = True
        self.assertEqual(self.execute(), 'queued_parent')
        self.assertEqual([method for method, _ in self.mutations()], ['thread/queue/add'])

    def test_existing_request_never_duplicated_or_started_again(self):
        self.client.queued = [{'id': 'existing', 'clientUserMessageId': TASK['id']}]
        self.assertEqual(self.execute(), 'queued_parent')
        self.assertEqual(self.mutations(), [])

    def test_start_timeout_retains_queued_receipt(self):
        self.client.start_error = True
        self.assertEqual(self.execute(), 'queued_parent')
        self.assertEqual(json.loads(self.log.read_text())['queued_submission_id'], 'submission-1')

    def test_automatic_requires_explicit_quota_permission(self):
        self.client.quota = None
        self.assertEqual(self.execute(automatic=True), 'quota_blocked')
        self.assertEqual(self.mutations(), [])

    def test_automatic_rejects_api_account(self):
        self.client.account = 'apikey'
        self.assertEqual(self.execute(automatic=True), 'blocked_auth')
        self.assertEqual(self.mutations(), [])

    def test_pagination_cycle_refuses_mutation(self):
        with patch.object(self.client, 'call', return_value={'data': [], 'nextCursor': 'repeat'}):
            with self.assertRaises(Unavailable):
                owner_route.queue_once(self.client, PARENT, TASK['id'], 'text')


if __name__ == '__main__':
    unittest.main()
