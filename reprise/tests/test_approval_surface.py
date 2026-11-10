"""Issue #117 — surface des approbations Codex natives.

Chaîne testée : boucle d'attente (`codex_expert.review`) émet un événement
`awaiting_approval` portant l'approbation ; le pont la persiste de façon durable
(`desktop_approvals`) ; `approval_list` la restitue ; `approval_respond`
réattache le client, envoie la réponse JSON-RPC et marque la résolution.
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import codex_expert as expert
import codex_expert_mcp
from codex_rpc import Unavailable
from desktop_task_store import TaskStore

ID = '12345678-1234-1234-1234-123456789abc'

APPROVAL_EVENT = {'phase': 'awaiting_approval', 'thread_id': ID, 'approval': {
    'approval_id': '11111111-2222-3333-4444-555555555555', 'rpc_id': 12,
    'method': 'item/commandExecution/requestApproval', 'thread_id': ID,
    'turn_id': 'own', 'params': {'command': 'rm -rf build'}, 'requested_at': 100.0,
    'sandbox': 'workspace-write'}}


class ReviewFake:
    """Fake native review whose Fake client injects an approval request."""
    used, auth, active, approval = 30, 'chatgpt', False, False
    responses = ['Finding: unchecked input']
    last = None

    def __init__(self, *args):
        self.calls = []
        ReviewFake.last = self
        from collections import deque
        self.notifications = deque()

    def send(self, msg): pass

    def close(self): pass

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == 'account/read': return {'account': {'type': self.auth}}
        if method == 'config/read': return {'config': {'mcp_servers': {'external': {'enabled': True}}}}
        if method == 'mcpServerStatus/list': return {'data': [{'name': 'external', 'runtimeStatus': 'disabled'}]}
        if method == 'account/rateLimits/read': return {'ordinaryUsageAllowed': True, 'rateLimits': {'primary': {'usedPercent': self.used}}}
        if method == 'model/list': return {'data': [{'model': 'gpt-6-astra', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}]}
        if method.startswith('thread/'):
            return {'model': params.get('model'), 'thread': {'id': ID, 'cwd': ReviewFake.root,
                    'modelProvider': 'openai', 'path': str(Path(ReviewFake.root) / 'rollout.jsonl'),
                    'turns': [], 'status': {'type': 'active' if self.active else 'idle'}}}
        if method == 'turn/start':
            if self.approval:
                self.notifications.append({'id': 12, 'method': 'item/commandExecution/requestApproval',
                                           'params': {'threadId': ID, 'turnId': 'own'}})
            else:
                self.notifications.extend([
                    *[{'method': 'item/completed', 'params': {'threadId': ID, 'turnId': 'own',
                       'item': {'type': 'agentMessage', 'phase': 'final_answer', 'text': text}}}
                      for text in self.responses],
                    {'method': 'turn/completed', 'params': {'threadId': ID, 'turn': {'id': 'own', 'status': 'completed'}}}])
            return {'turn': {'id': 'own'}}
        return {}


class RespondFake:
    """Fake reattach client for approval_respond."""
    fail = None
    last = None

    def __init__(self, *args):
        self.sent = []
        self.closed = False
        RespondFake.last = self

    def call(self, method, params=None):
        if RespondFake.fail == 'reattach':
            raise Unavailable('owner_unavailable', 'codex unreachable')
        if method == 'config/read':
            return {'config': {'mcp_servers': {}}}
        if method == 'thread/resume':
            self.resume_params = params
            return {'thread': {'id': params.get('threadId'), 'status': {'type': 'idle'}}}
        return {}

    def send(self, message):
        if RespondFake.fail == 'send':
            raise Unavailable('owner_unavailable', 'peer closed')
        self.sent.append(message)

    def close(self):
        self.closed = True


class ApprovalSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'rollout.jsonl').touch()
        self.state = self.root / 'expert'
        ReviewFake.root = str(self.project)
        ReviewFake.approval = False
        RespondFake.fail = None
        self.patch_client = patch('codex_expert.Client', ReviewFake)
        self.patch_client.start()
        self.addCleanup(self.patch_client.stop)

    def make_bridge(self):
        bridge = codex_expert_mcp.Bridge(str(self.project), {}, self.state)
        self.addCleanup(bridge.db.close)
        return bridge

    def record(self, bridge, event=APPROVAL_EVENT):
        approval = event['approval']
        bridge.desktop_tasks.record_approval(
            approval['approval_id'], approval['method'], rpc_id=approval.get('rpc_id'),
            thread_id=approval.get('thread_id'), turn_id=approval.get('turn_id'),
            params=approval.get('params'), requested_at=approval.get('requested_at'),
            sandbox=approval.get('sandbox'))

    def test_1_wait_loop_records_approval_before_returning(self):
        ReviewFake.approval = True
        events = []
        result = expert.review('codex', {}, str(self.project), 'Review input checks',
                               on_turn=events.append, implementation=True)
        self.assertEqual(result['status'], 'awaiting_approval')
        approval = events[-1].get('approval')
        self.assertTrue(approval)
        self.assertEqual(events[-1]['phase'], 'awaiting_approval')
        self.assertEqual(approval['method'], 'item/commandExecution/requestApproval')
        self.assertEqual(approval['rpc_id'], 12)
        self.assertEqual(approval['thread_id'], ID)
        self.assertTrue(approval['params'])
        self.assertGreater(approval['requested_at'], 0)
        self.assertEqual(result['pending_request']['approval']['approval_id'], approval['approval_id'])
        # Durable record: a second store opened on the same state sees the row.
        bridge = self.make_bridge()
        # CI runners have no Codex executable; the bridge would short-circuit with
        # engine_unavailable before reaching the (patched) wait loop.
        bridge.program = 'codex-fixture'
        with patch.object(codex_expert_mcp, 'review',
                          side_effect=lambda *a, **k: (k['on_turn'] or events.append)(APPROVAL_EVENT) or {'status': 'awaiting_approval'}):
            bridge.call('codex_review', {'question': 'Review input checks', 'request_key': 'appr-1'})
        self.assertEqual(len(bridge.desktop_tasks.list_approvals()), 1)
        self.assertEqual(bridge.desktop_tasks.pending_approval(APPROVAL_EVENT['approval']['approval_id'])['rpc_id'], '12')

    def test_2_approval_list_returns_pending_rows(self):
        bridge = self.make_bridge()
        self.record(bridge)
        result = bridge.call('approval_list', {})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(len(result['approvals']), 1)
        row = result['approvals'][0]
        self.assertEqual(row['method'], 'item/commandExecution/requestApproval')
        self.assertEqual(row['params'], {'command': 'rm -rf build'})
        self.assertEqual(row['rpc_id'], '12')

    def test_3_approval_respond_happy_path_sends_and_resolves(self):
        bridge = self.make_bridge()
        self.record(bridge)
        with patch('codex_expert_mcp.Client', RespondFake):
            result = bridge.call('approval_respond', {'approval_id': APPROVAL_EVENT['approval']['approval_id'],
                                                      'decision': 'approved'})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['decision'], 'approved')
        self.assertIn({'id': 12, 'result': {'decision': 'accept'}}, RespondFake.last.sent)
        # The resume replays the session policy captured at detection time.
        self.assertEqual(RespondFake.last.resume_params['sandbox'], 'workspace-write')
        self.assertEqual(RespondFake.last.resume_params['approvalPolicy'], 'on-request')
        self.assertTrue(RespondFake.last.resume_params['config'])
        self.assertTrue(RespondFake.last.closed)
        self.assertIsNone(bridge.desktop_tasks.pending_approval(APPROVAL_EVENT['approval']['approval_id']))

    def test_4_approval_respond_refuses_missing_or_invalid_decision(self):
        bridge = self.make_bridge()
        self.record(bridge)
        for args in ({'approval_id': APPROVAL_EVENT['approval']['approval_id']},
                     {'approval_id': APPROVAL_EVENT['approval']['approval_id'], 'decision': 'auto-yes'}):
            with self.assertRaises(ValueError):
                bridge.call('approval_respond', args)
        self.assertEqual(bridge.desktop_tasks.pending_approval(APPROVAL_EVENT['approval']['approval_id'])['rpc_id'], '12')

    def test_5_approval_respond_reattach_failure_keeps_request_pending(self):
        bridge = self.make_bridge()
        self.record(bridge)
        RespondFake.fail = 'reattach'
        with patch('codex_expert_mcp.Client', RespondFake):
            with self.assertRaises(codex_expert_mcp.Bridge.ApprovalReattachFailed) as caught:
                bridge.call('approval_respond', {'approval_id': APPROVAL_EVENT['approval']['approval_id'],
                                                 'decision': 'denied'})
        self.assertTrue(issubclass(type(caught.exception), Unavailable))
        self.assertIn('stays pending', caught.exception.args[-1])
        self.assertIsNotNone(bridge.desktop_tasks.pending_approval(APPROVAL_EVENT['approval']['approval_id']))
        # Same guarantee when the raw send fails after a successful reattach.
        RespondFake.fail = 'send'
        with patch('codex_expert_mcp.Client', RespondFake):
            with self.assertRaises(codex_expert_mcp.Bridge.ApprovalReattachFailed):
                bridge.call('approval_respond', {'approval_id': APPROVAL_EVENT['approval']['approval_id'],
                                                 'decision': 'denied'})
        self.assertIsNotNone(bridge.desktop_tasks.pending_approval(APPROVAL_EVENT['approval']['approval_id']))

    def test_6_approval_respond_legacy_v1_uses_legacy_vocabulary(self):
        bridge = self.make_bridge()
        legacy = {'phase': 'awaiting_approval', 'thread_id': ID, 'approval': {
            'approval_id': '66666666-2222-3333-4444-555555555555', 'rpc_id': 42,
            'method': 'execCommandApproval', 'thread_id': ID,
            'turn_id': 'own', 'params': {'command': 'ls'}, 'requested_at': 200.0}}
        self.record(bridge, event=legacy)
        with patch('codex_expert_mcp.Client', RespondFake):
            bridge.call('approval_respond', {'approval_id': legacy['approval']['approval_id'],
                                             'decision': 'denied'})
        self.assertIn({'id': 42, 'result': {'decision': {'denied': {'rejection': 'Denied by operator'}}}},
                      RespondFake.last.sent)

    def test_7_approval_respond_rejects_non_idle_or_foreign_thread(self):
        bridge = self.make_bridge()
        self.record(bridge)
        original_call = RespondFake.call

        def foreign(self, method, params=None):
            if method == 'thread/resume':
                return {'thread': {'id': 'other-thread', 'status': {'type': 'idle'}}}
            return original_call(self, method, params)
        RespondFake.call = foreign
        try:
            with patch('codex_expert_mcp.Client', RespondFake):
                with self.assertRaises(codex_expert_mcp.Bridge.ApprovalReattachFailed):
                    bridge.call('approval_respond', {'approval_id': APPROVAL_EVENT['approval']['approval_id'],
                                                     'decision': 'approved'})
        finally:
            RespondFake.call = original_call
        self.assertIsNotNone(bridge.desktop_tasks.pending_approval(APPROVAL_EVENT['approval']['approval_id']))


if __name__ == '__main__':
    unittest.main()
