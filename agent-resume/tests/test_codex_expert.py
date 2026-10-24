from collections import deque
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
import codex_expert as expert

ID = '12345678-1234-1234-1234-123456789abc'

class Fake:
    used = 30
    auth = 'chatgpt'
    active = False
    approval = False
    phase = 'final_answer'
    mcp_status = 'disabled'
    responses = ['Finding: unchecked input']
    def __init__(self, *args):
        self.calls = []
        Fake.last = self
        self.notifications = deque()
    def send(self, msg): pass
    def close(self): pass
    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == 'account/read': return {'account': {'type': self.auth}}
        if method == 'mcpServerStatus/list':return {'data':[{'name':'external','runtimeStatus':self.mcp_status}]}
        if method == 'config/read': return {'config': {'mcp_servers': {'external': {'enabled': True}}}}
        if method == 'account/rateLimits/read': return {'ordinaryUsageAllowed': True, 'rateLimits': {'primary': {'usedPercent': self.used}}}
        if method == 'model/list':return {'data':[{'model':'gpt-6-astra','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]}
        if method.startswith('thread/'):
            return {'model':params.get('model'), 'thread': {'id': ID, 'cwd': Fake.root, 'modelProvider': 'openai', 'path': str(Path(Fake.root)/'rollout.jsonl'), 'turns': [], 'status': {'type': 'active' if self.active else 'idle'}}}
        if method == 'turn/start':
            if self.approval:
                self.notifications.append({'id': 12, 'method': 'item/commandExecution/requestApproval', 'params': {'threadId': ID, 'turnId': 'own'}})
            else:
                self.notifications.extend([
                    *[{'method': 'item/completed', 'params': {'threadId': ID, 'turnId': 'own', 'item': {'type': 'agentMessage', 'phase':self.phase, 'text': text}}} for text in self.responses],
                    {'method': 'turn/completed', 'params': {'threadId': ID, 'turn': {'id': 'own', 'status': 'completed'}}}])
            return {'turn': {'id': 'own'}}
        return {}

class ExpertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        Fake.root, Fake.used, Fake.auth, Fake.active, Fake.approval = self.temp.name, 30, 'chatgpt', False, False
        Fake.phase, Fake.mcp_status = 'final_answer', 'disabled'
        Fake.responses = ['Finding: unchecked input']
        (Path(self.temp.name)/'rollout.jsonl').touch()
        self.patch = patch('codex_expert.Client', Fake)
        self.patch.start(); self.addCleanup(self.patch.stop)
    def run_review(self, **kwargs):
        return expert.review('codex', {}, self.temp.name, 'Review input checks', **kwargs)
    def test_answer_and_readonly_policy(self):
        result = self.run_review()
        self.assertEqual(result['status'], 'finished')
        self.assertIn('unchecked input', result['response'])
        self.assertNotIn('thread/resume', [m for m,p in Fake.last.calls])
        params = next(p for m,p in Fake.last.calls if m == 'turn/start')
        self.assertEqual(params['sandboxPolicy'], {'type': 'readOnly', 'networkAccess': False})
        config = next(p for m,p in Fake.last.calls if m == 'thread/start')['config']
        self.assertFalse(config['features.apps'])
        self.assertFalse(config['features.plugins'])
        self.assertEqual(config['mcp_servers'], {'external':{'enabled':False}})
    def test_implementation_sets_native_workspace_policy_and_does_not_hide_thread(self):
        result = self.run_review(implementation=True)
        self.assertEqual(result['mode'], 'implementation')
        start = next(p for m,p in Fake.last.calls if m == 'thread/start')
        turn = next(p for m,p in Fake.last.calls if m == 'turn/start')
        self.assertEqual(start['sandbox'], 'workspace-write')
        self.assertNotIn('threadSource', start)
        self.assertEqual(turn['sandboxPolicy'], {'type': 'workspaceWrite', 'writableRoots': [str(Path(self.temp.name).resolve())], 'networkAccess': False})
        self.assertEqual(turn['approvalPolicy'], 'on-request')
        self.assertIn('Implement', start['developerInstructions'])
        self.assertEqual(result['status'], 'finished')

    def test_implementation_approval_never_autoapproves_and_keeps_thread(self):
        Fake.approval = True
        result = self.run_review(implementation=True)
        self.assertEqual(result['status'], 'blocked_permission')
        self.assertIn('pending_request', result)
        self.assertNotIn('thread/delete', [m for m,p in Fake.last.calls])

    def test_long_unicode_response_is_preserved_for_initial_and_followup(self):
        Fake.responses = ['é🙂\\"' * 20_000, 'second message']
        expected = '\n'.join(Fake.responses)
        for kwargs in ({}, {'thread_id': ID}):
            with self.subTest(kwargs=kwargs):
                result = self.run_review(**kwargs)
                self.assertEqual(result['status'], 'finished')
                self.assertEqual(result['response'], expected)
                self.assertNotIn('output_truncated', result)

    def test_capture_limit_reports_incomplete_without_replaying_turn(self):
        Fake.responses = ['🙂' * expert.MAX_RESPONSE_CHARACTERS, 'overflow']
        result = self.run_review()
        self.assertEqual(result['status'], 'needs_review')
        self.assertTrue(result['output_truncated'])
        self.assertFalse(result['output_complete'])
        self.assertIn('incomplete', result['reason'])
        self.assertEqual(result['capture_limit_characters'], expert.MAX_RESPONSE_CHARACTERS)
        self.assertEqual(result['response'], Fake.responses[0])
        self.assertEqual(sum(m == 'turn/start' for m, _ in Fake.last.calls), 1)
        self.assertNotIn('turn/interrupt', [m for m, _ in Fake.last.calls])

    def test_capture_limit_includes_message_separators(self):
        Fake.responses = ['first', 'second']
        with patch.object(expert, 'MAX_RESPONSE_CHARACTERS', 8):
            result = self.run_review()
        self.assertEqual(result['response'], 'first\nse')
        self.assertEqual(result['status'], 'needs_review')
        self.assertFalse(result['output_complete'])

    def test_quota_reserve_prevents_thread_creation(self):
        for used in (95, 99, None, float('nan'), True):
            Fake.used = used
            self.assertEqual(self.run_review()['status'], 'quota_blocked')
            self.assertNotIn('thread/start', [m for m,p in Fake.last.calls])
    def test_api_account_never_used(self):
        Fake.auth = 'apiKey'
        self.assertEqual(self.run_review()['status'], 'blocked_auth')
    def test_busy_followup_not_steered(self):
        Fake.active = True
        self.assertEqual(self.run_review(thread_id=ID)['status'], 'blocked_owner')
        self.assertNotIn('turn/start', [m for m,p in Fake.last.calls])
    def test_permission_blocks_and_interrupts_own_turn(self):
        Fake.approval = True
        result = self.run_review()
        self.assertEqual(result['status'], 'blocked_permission')
        self.assertIn(('turn/interrupt', {'threadId': ID, 'turnId': 'own'}), Fake.last.calls)
    def test_commentary_is_not_a_completed_answer(self):
        Fake.phase='commentary'
        result=self.run_review()
        self.assertEqual(result['status'],'needs_review')
        self.assertEqual(result['response'],'')
    def test_active_mcp_prevents_model_turn(self):
        Fake.mcp_status='ready'
        self.assertEqual(self.run_review()['status'],'blocked_permission')
        self.assertNotIn('turn/start',[m for m,p in Fake.last.calls])
    def test_model_catalog_and_effort_checked_before_inference(self):
        self.assertEqual(self.run_review(model='gpt-6-astra',effort='high')['status'],'finished')
        params=next(p for m,p in Fake.last.calls if m=='turn/start')
        self.assertEqual((params['model'],params['effort']),('gpt-6-astra','high'))
        self.assertEqual(self.run_review(model='unknown',effort='high')['status'],'engine_unavailable')
        self.assertNotIn('turn/start',[m for m,p in Fake.last.calls])
    def test_interrupt_rejection_preserves_result_and_closes_client(self):
        Fake.approval=True
        original=Fake.call
        def call(client,method,params=None):
            if method=='turn/interrupt':raise expert.Unavailable('needs_review','Transport closed')
            return original(client,method,params)
        with patch.object(Fake,'call',call),patch.object(Fake,'close') as close:
            result=self.run_review()
            self.assertEqual(result['status'],'blocked_permission')
            self.assertEqual(result['thread_id'],ID)
            close.assert_called_once()
    def test_empty_failed_start_is_deleted(self):
        (Path(self.temp.name)/'rollout.jsonl').unlink()
        Fake.mcp_status = 'ready'
        result = self.run_review()
        self.assertTrue(result['empty_thread_removed'])
        self.assertIsNone(result['thread_id'])
        self.assertFalse(result['resumable'])
        self.assertIn(('thread/delete', {'threadId': ID}), Fake.last.calls)
    def test_ambiguous_start_is_never_deleted(self):
        (Path(self.temp.name)/'rollout.jsonl').unlink()
        original = Fake.call
        def call(client, method, params=None):
            if method == 'turn/start':
                raise TimeoutError()
            return original(client, method, params)
        with patch.object(Fake, 'call', call):
            result = self.run_review()
        self.assertEqual(result['status'], 'timed_out')
        self.assertFalse(result['resumable'])
        self.assertNotIn('thread/delete', [m for m,p in Fake.last.calls])
    def test_cleanup_failure_preserves_original_error(self):
        (Path(self.temp.name)/'rollout.jsonl').unlink()
        Fake.mcp_status = 'ready'
        original = Fake.call
        def call(client, method, params=None):
            if method == 'thread/delete':
                raise expert.Unavailable('failed', 'Cleanup rejected')
            return original(client, method, params)
        with patch.object(Fake, 'call', call), patch.object(Fake, 'close') as close:
            result = self.run_review()
        self.assertEqual(result['status'], 'blocked_permission')
        self.assertFalse(result['resumable'])
        close.assert_called_once()
    def test_model_mismatch_cleans_up_new_empty_thread(self):
        (Path(self.temp.name)/'rollout.jsonl').unlink()
        original = Fake.call
        def call(client, method, params=None):
            result = original(client, method, params)
            if method == 'thread/start':
                result['model'] = 'unexpected'
            return result
        with patch.object(Fake, 'call', call):
            result = self.run_review(model='gpt-6-astra', effort='high')
        self.assertEqual(result['status'], 'blocked_session')
        self.assertTrue(result['empty_thread_removed'])
    def test_missing_history_followup_is_not_resumed(self):
        (Path(self.temp.name)/'rollout.jsonl').unlink()
        self.assertEqual(self.run_review(thread_id=ID)['status'], 'blocked_session')
        self.assertNotIn('thread/resume', [m for m,p in Fake.last.calls])
        self.assertNotIn('thread/delete', [m for m,p in Fake.last.calls])
    def test_existing_history_is_preserved_on_preflight_failure(self):
        Fake.mcp_status = 'ready'
        result = self.run_review()
        self.assertTrue(result['resumable'])
        self.assertNotIn('thread/delete', [m for m,p in Fake.last.calls])
    def test_scope_cannot_escape(self):
        for paths in [['../'], ['/etc/passwd']]:
            with self.assertRaises(ValueError): self.run_review(paths=paths)

if __name__ == '__main__': unittest.main()
