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
            return {'model':params.get('model'), 'thread': {'id': ID, 'cwd': Fake.root, 'modelProvider': 'openai', 'status': {'type': 'active' if self.active else 'idle'}}}
        if method == 'turn/start':
            if self.approval:
                self.notifications.append({'id': 12, 'method': 'item/commandExecution/requestApproval', 'params': {'threadId': ID, 'turnId': 'own'}})
            else:
                self.notifications.extend([
                    {'method': 'item/completed', 'params': {'threadId': ID, 'turnId': 'own', 'item': {'type': 'agentMessage', 'phase':self.phase, 'text': 'Finding: unchecked input'}}},
                    {'method': 'turn/completed', 'params': {'threadId': ID, 'turn': {'id': 'own', 'status': 'completed'}}}])
            return {'turn': {'id': 'own'}}
        return {}

class ExpertTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        Fake.root, Fake.used, Fake.auth, Fake.active, Fake.approval = self.temp.name, 30, 'chatgpt', False, False
        Fake.phase, Fake.mcp_status = 'final_answer', 'disabled'
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
    def test_scope_cannot_escape(self):
        for paths in [['../'], ['/etc/passwd']]:
            with self.assertRaises(ValueError): self.run_review(paths=paths)

if __name__ == '__main__': unittest.main()
