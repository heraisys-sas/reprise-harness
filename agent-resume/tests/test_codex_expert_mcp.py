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
        self.assertEqual({item['name'] for item in self.bridge.dispatch({'method': 'tools/list'})['tools']}, {'codex_review','codex_followup','reprise_delegate','reprise_backends','reprise_followup'})
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
        with patch('codex_expert_mcp.review', return_value={'status': 'finished', 'resumable': True, 'thread_id': ID, 'response': 'review'}) as review:
            self.bridge.call('codex_review', {'question': 'review'})
            result = self.bridge.dispatch({'method': 'tools/call', 'params': {'name': 'codex_followup', 'arguments': {'thread_id': ID, 'question': 'explain'}}})
            self.assertFalse(result['isError'])
            self.assertEqual(review.call_args.kwargs['thread_id'], ID)
    def test_arbitrary_parameters_refused(self):
        with self.assertRaises(ValueError):
            self.bridge.call('codex_review', {'question': 'review', 'cwd': '/'})
    def test_levels_route_and_followup_preserves_model(self):
        for kind,level,model in [('expertise','standard','gpt-6.1-sol'),('review','deep','gpt-6-astra'),('batch','standard','gpt-6-luna')]:
            with patch('codex_expert_mcp.review', return_value={'status':'finished','resumable':True,'thread_id':ID}) as review:
                self.bridge.limit=20
                self.bridge.call('reprise_delegate',{'kind':kind,'level':level,'question':'Check this contract'})
                self.assertEqual(review.call_args.kwargs['model'],model)
                self.bridge.call('codex_followup',{'thread_id':ID,'question':'Explain'})
                self.assertEqual(review.call_args.kwargs['model'],model)
    def test_backends_no_inference_or_budget_consumption(self):
        with patch('codex_expert_mcp.review') as review:
            result=self.bridge.call('reprise_backends',{})
            review.assert_not_called()
        self.assertFalse(result['automatic_fallback'])
        self.assertFalse(result['backends'][1]['configured'])
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_calls').fetchone()[0],0)
    def test_invalid_delegation_rejected_before_budget(self):
        for args in [{'kind':'write','question':'x'},{'kind':'review','level':'ultra','question':'x'}]:
            with self.assertRaises(ValueError):self.bridge.call('reprise_delegate',args)
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_calls').fetchone()[0],0)

    def test_nonpersistent_thread_is_not_registered(self):
        with patch('codex_expert_mcp.review', return_value={'status':'blocked_permission','thread_id':ID,'resumable':False}):
            self.bridge.call('codex_review', {'question':'review'})
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_threads').fetchone()[0], 0)

    def test_claude_explicit_route_followup_and_backend_isolation(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        with patch('codex_expert_mcp.claude_review', return_value={
                'status':'finished','resumable':True,'thread_id':ID,'model':'claude-sonnet-test'}) as review:
            self.bridge.call('reprise_delegate', {'backend':'claude-code','kind':'expertise','question':'Review'})
            self.assertEqual(review.call_args.kwargs['model'], 'sonnet')
            self.bridge.call('reprise_followup', {'backend':'claude-code','thread_id':ID,'question':'Continue'})
            self.assertEqual(review.call_args.kwargs['model'], 'claude-sonnet-test')
            with self.assertRaises(ValueError):
                self.bridge.call('codex_followup', {'thread_id':ID,'question':'Continue'})
            self.bridge.claude_program = '/other-claude'
            with self.assertRaises(ValueError):
                self.bridge.call('reprise_followup', {'backend':'claude-code','thread_id':ID,'question':'Continue'})

    def test_claude_disabled_never_falls_back_or_consumes_budget(self):
        with patch('codex_expert_mcp.review') as codex, patch('codex_expert_mcp.claude_review') as claude:
            result = self.bridge.call('reprise_delegate', {'backend':'claude-code','kind':'expertise','question':'Review'})
            self.assertEqual(result['status'],'engine_unavailable')
            self.bridge.claude_program = '/claude'
            result = self.bridge.call('reprise_delegate', {'backend':'claude-code','kind':'expertise','question':'Review'})
            self.assertEqual(result['status'],'billing_unverified')
            codex.assert_not_called()
            claude.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_calls').fetchone()[0],0)

    def test_claude_budget_shared_with_codex_and_failed_session_not_owned(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        with patch('codex_expert_mcp.review', return_value={'status':'quota_blocked'}):
            self.bridge.call('codex_review', {'question':'Review'})
        with patch('codex_expert_mcp.claude_review', return_value={'status':'timed_out','resumable':False}) as review:
            self.bridge.call('reprise_delegate', {'backend':'claude-code','kind':'review','question':'Review'})
            self.assertEqual(self.bridge.call('reprise_delegate', {'backend':'claude-code','kind':'review','question':'Review'})['status'],'budget_blocked')
            self.assertEqual(review.call_count,1)
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM claude_expert_routes').fetchone()[0],0)
