import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import Bridge
from test_codex_expert import ID

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state', daily_limit=2)
        self.bridge.program = 'codex'
        self.addCleanup(self.bridge.db.close)
    def test_protocol_lists_only_bounded_tools(self):
        self.assertIn('tools', self.bridge.dispatch({'method': 'initialize'})['capabilities'])
        self.assertEqual(len(self.bridge.dispatch({'method': 'tools/list'})['tools']), 2)
    def test_followup_requires_own_thread(self):
        with self.assertRaises(ValueError):
            self.bridge.call('codex_followup', {'thread_id': ID, 'question': 'continue'})
    def test_limit_survives_restart_and_failures(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'quota_blocked'}) as review:
            for _ in range(2): self.bridge.call('codex_review', {'question': 'review'})
            other = Bridge(self.temp.name, {}, Path(self.temp.name)/'state', daily_limit=2)
            other.program = 'codex'
            try:
                self.assertEqual(other.call('codex_review', {'question': 'review'})['status'], 'budget_blocked')
            finally: other.db.close()
            self.assertEqual(review.call_count, 2)
    def test_followup_preserves_thread(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'finished', 'thread_id': ID, 'response': 'review'}) as review:
            self.bridge.call('codex_review', {'question': 'review'})
            result = self.bridge.dispatch({'method': 'tools/call', 'params': {'name': 'codex_followup', 'arguments': {'thread_id': ID, 'question': 'explain'}}})
            self.assertFalse(result['isError'])
            self.assertEqual(review.call_args.kwargs['thread_id'], ID)
    def test_arbitrary_parameters_refused(self):
        with self.assertRaises(ValueError):
            self.bridge.call('codex_review', {'question': 'review', 'cwd': '/'})
