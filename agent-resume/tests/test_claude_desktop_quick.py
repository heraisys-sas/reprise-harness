import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from claude_desktop_quick import Endpoint


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix='quick-test-')
        self.endpoint = Endpoint(self.state, daily_limit=2)
        self.addCleanup(self.endpoint.close)

    def test_submit_routes_target_chat_and_records(self):
        result = self.endpoint.submit({'prompt': 'Explique le routage Quick Entry',
                                       'chatId': '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3'})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['chatId'], '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3')
        self.assertEqual(result['dispatch'], 'dispatchOnQuickEntrySubmit')
        self.assertTrue(result['submission_id'])

    def test_daily_cap_blocks_after_limit(self):
        self.endpoint.submit({'prompt': 'Un message valide'})
        self.endpoint.submit({'prompt': 'Deuxième message valide'})
        result = self.endpoint.submit({'prompt': 'Troisième message valide'})
        self.assertEqual(result['status'], 'budget_blocked')

    def test_short_prompt_rejected_by_guard(self):
        with self.assertRaises(ValueError):
            self.endpoint.submit({'prompt': 'ab'})

    def test_unknown_fields_rejected(self):
        with self.assertRaises(ValueError):
            self.endpoint.submit({'prompt': 'Un message valide', 'hack': 'x'})

    def test_initialize_and_ping(self):
        info = self.endpoint.call('initialize', {})
        self.assertEqual(info['serverInfo']['name'], 'reprise-claude-desktop-quick')
        self.assertEqual(self.endpoint.call('ping', {}), {})
        with self.assertRaises(ValueError):
            self.endpoint.call('unknown/method', {})

    def test_submission_persisted_in_sqlite(self):
        self.endpoint.submit({'prompt': 'Un message valide'})
        row = sqlite3.connect(os.path.join(self.state, 'quick.sqlite')).execute(
            'SELECT COUNT(*) FROM quick_submissions').fetchone()
        self.assertEqual(row[0], 1)


if __name__ == '__main__':
    unittest.main()
