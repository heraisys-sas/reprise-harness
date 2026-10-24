import json
import os
import sqlite3
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from claude_desktop_quick import Endpoint


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix='quick-test-')
        self.endpoint = Endpoint(self.state, daily_limit=2)
        self.addCleanup(self.endpoint.close)

    def submit(self, args):
        return self.endpoint.submit({'request_key': str(uuid.uuid4()), **args})

    def test_submit_routes_target_chat_and_records(self):
        result = self.submit({'prompt': 'Explique le routage Quick Entry',
                                       'chatId': '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3'})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['chatId'], '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3')
        self.assertEqual(result['dispatch'], 'dispatchOnQuickEntrySubmit')
        self.assertTrue(result['submission_id'])

    def test_legacy_daily_limit_does_not_block(self):
        self.submit({'prompt': 'Un message valide'})
        self.submit({'prompt': 'Deuxième message valide'})
        result = self.submit({'prompt': 'Troisième message valide'})
        self.assertEqual(result['status'], 'finished')

    def test_short_prompt_rejected_by_guard(self):
        with self.assertRaises(ValueError):
            self.submit({'prompt': 'ab'})

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.submit({'prompt': 'Un message valide', 'hack': 'x'})

    def test_scoped_daemon_and_mcp_share_task_result(self):
        from desktop_task_store import TaskStore
        with tempfile.TemporaryDirectory() as directory:
            endpoint = Endpoint(directory, project_root=directory)
            self.addCleanup(endpoint.close)
            key = 'shared-job'
            admitted = endpoint.submit({'request_key': key, 'prompt': 'Analyze this task'})
            self.assertFalse(admitted['dispatch_attempted'])
            self.assertTrue(admitted['task_available'])
            store = TaskStore(directory, directory)
            self.addCleanup(store.close)
            claimed = store.claim(key, 'saved-claim')
            store.submit(key, claimed['attempt_id'], 'Verified result')
            result = endpoint.call('quick_entry/get_result', {'request_key': key, 'view': 'full'})
            self.assertEqual(result['answer'], 'Verified result')
            self.assertFalse(endpoint.call('quick_entry/capabilities', {})['dispatch_supported'])

    def test_changed_project_cannot_replay_other_project_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / 'shared'
            projects = [Path(directory) / 'a', Path(directory) / 'b']
            for project in projects:
                project.mkdir()
                endpoint = Endpoint(state, project_root=project)
                try:
                    receipt = endpoint.submit({'request_key': 'same', 'prompt': 'Scoped task'})
                    self.assertTrue(receipt['task_available'])
                    self.assertEqual(endpoint.tasks.claim('same', 'claim')['prompt'], 'Scoped task')
                finally:
                    endpoint.close()
            endpoint = Endpoint(state, project_root=projects[0])
            endpoint.close()

    def test_initialize_and_ping(self):
        info = self.endpoint.call('initialize', {})
        self.assertEqual(info['serverInfo']['name'], 'reprise-claude-desktop-quick')
        self.assertEqual(self.endpoint.call('ping', {}), {})
        with self.assertRaises(ValueError):
            self.endpoint.call('unknown/method', {})

    def test_submission_persisted_in_sqlite(self):
        self.submit({'prompt': 'Un message valide'})
        row = sqlite3.connect(os.path.join(self.state, 'quick.sqlite')).execute(
            'SELECT COUNT(*) FROM quick_submissions').fetchone()
        self.assertEqual(row[0], 1)


if __name__ == '__main__':
    unittest.main()
