from collections import deque
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
import codex_expert as expert
from support_clock import FakeClock, ScriptedFactory, review_replies

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
    def test_permission_records_awaiting_approval_without_interrupt(self):
        Fake.approval = True
        result = self.run_review()
        self.assertEqual((result['status'], result['phase']), ('awaiting_approval', 'awaiting_approval'))
        self.assertEqual(result['pending_request']['method'], 'item/commandExecution/requestApproval')
        self.assertNotIn('turn/interrupt', [m for m,p in Fake.last.calls])
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
    def test_awaiting_approval_closes_client_without_interrupt(self):
        Fake.approval=True
        with patch.object(Fake,'close') as close:
            result=self.run_review()
        self.assertEqual(result['status'],'awaiting_approval')
        self.assertEqual(result['thread_id'],ID)
        self.assertNotIn('turn/interrupt',[m for m,p in Fake.last.calls])
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
        self.assertEqual(result['status'], 'needs_review')
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
    def test_wait_expiry_detaches_without_interrupt(self):
        (Path(self.temp.name)/'rollout.jsonl').touch()
        clock = FakeClock()
        factory = ScriptedFactory(clock, replies=review_replies(self.temp.name, ID))
        with patch('codex_expert.Client', factory):
            result = expert.review('codex', {}, self.temp.name, 'Review', clock=clock, wait_timeout=30)
        self.assertEqual((result['status'], result['phase'], result['wait_expired']),
                         ('running', 'active', True))
        self.assertTrue(result['resumable'])
        self.assertEqual(factory.last.methods().count('turn/start'), 1)
        self.assertNotIn('turn/interrupt', factory.last.methods())
    def test_transport_timeout_before_wait_expiry_stays_uncertain(self):
        (Path(self.temp.name)/'rollout.jsonl').touch()
        clock = FakeClock()
        factory = ScriptedFactory(clock, replies=review_replies(self.temp.name, ID), idle_step=5)
        with patch('codex_expert.Client', factory):
            result = expert.review('codex', {}, self.temp.name, 'Review', clock=clock, wait_timeout=30)
        self.assertEqual(result['status'], 'needs_review')
        self.assertIn('uncertain', result['reason'])
        self.assertNotIn('turn/interrupt', factory.last.methods())
    def test_inactivity_is_diagnostic_without_interrupt(self):
        (Path(self.temp.name)/'rollout.jsonl').touch()
        clock = FakeClock()
        factory = ScriptedFactory(clock, replies=review_replies(self.temp.name, ID), idle_step=12)
        with patch('codex_expert.Client', factory):
            result = expert.review('codex', {}, self.temp.name, 'Review', clock=clock,
                                   wait_timeout=600, inactivity=10)
        self.assertEqual((result['status'], result['phase']), ('running', 'active'))
        self.assertTrue(result['inactivity_observed'])
        self.assertFalse(result['wait_expired'])
        self.assertEqual(result['idle_seconds'], 10.0)
        self.assertNotIn('turn/interrupt', factory.last.methods())
    def test_authorized_execution_deadline_interrupts_once(self):
        (Path(self.temp.name)/'rollout.jsonl').touch()
        clock = FakeClock()
        factory = ScriptedFactory(clock, replies=review_replies(self.temp.name, ID), idle_step=7)
        with patch('codex_expert.Client', factory):
            result = expert.review('codex', {}, self.temp.name, 'Review', clock=clock,
                                   wait_timeout=600, execution_deadline=5, stop_on_deadline=True)
        self.assertEqual((result['status'], result['phase'], result['work_completed']),
                         ('interrupted', 'interrupted', False))
        self.assertEqual(result['interrupt']['reason'], 'execution_deadline')
        self.assertEqual(factory.last.methods().count('turn/interrupt'), 1)
    def test_native_identity_is_persisted_before_client_wait(self):
        (Path(self.temp.name)/'rollout.jsonl').touch()
        clock, events = FakeClock(), []
        factory = ScriptedFactory(clock, replies=review_replies(self.temp.name, ID))
        with patch('codex_expert.Client', factory):
            result = expert.review('codex', {}, self.temp.name, 'Review', clock=clock,
                                   wait_timeout=30, execution_deadline=60, on_turn=events.append)
        self.assertEqual([event['phase'] for event in events], ['starting', 'active'])
        self.assertEqual((events[0]['thread_id'], events[0]['turn_id']), (ID, None))
        self.assertEqual((events[1]['thread_id'], events[1]['turn_id']), (ID, 'own'))
        self.assertIsNotNone(events[1]['deadline_at'])
        self.assertEqual(result['status'], 'running')
    def test_scope_cannot_escape(self):
        for paths in [['../'], ['/etc/passwd']]:
            with self.assertRaises(ValueError): self.run_review(paths=paths)

if __name__ == '__main__': unittest.main()
