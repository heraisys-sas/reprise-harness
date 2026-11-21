"""Issue #117 — surface des approbations Codex natives.

Chaîne testée : boucle d'attente (`codex_expert.review`) émet un événement
`awaiting_approval` portant l'approbation ; le pont la persiste de façon durable
(`desktop_approvals`) ; `approval_list` la restitue. Depuis #117 T2a,
`approval_respond` est désactivé applicativement : toute invocation est refusée
avant reattach, sans consommer ni résoudre l'approbation (voie opérateur en T2b/T2c).
"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import codex_expert as expert
import codex_expert_mcp

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
        self.patch_client = patch('codex_expert.Client', ReviewFake)
        self.patch_client.start()
        self.addCleanup(self.patch_client.stop)

    def make_bridge(self):
        bridge = codex_expert_mcp.Bridge(str(self.project), {}, self.state)
        self.addCleanup(bridge.close)
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

    def test_3_approval_respond_is_disabled_before_reattach(self):
        bridge = self.make_bridge()
        self.record(bridge)

        def no_client(*args, **kwargs):
            raise AssertionError('approval_respond ne doit pas réattacher le thread (#117 T2a)')
        for args in ({'approval_id': APPROVAL_EVENT['approval']['approval_id'], 'decision': 'approved'},
                     {'approval_id': APPROVAL_EVENT['approval']['approval_id'], 'decision': 'denied'},
                     {}):
            with self.subTest(args=args):
                with patch('codex_expert_mcp.Client', no_client):
                    with self.assertRaises(codex_expert_mcp.Unavailable) as caught:
                        bridge.call('approval_respond', args)
                self.assertEqual(caught.exception.state, 'approval_disabled')
                self.assertIn('#117 T2b', caught.exception.reason)
                # Refus sans effet : l'approbation reste pending, jamais résolue.
                pending = bridge.desktop_tasks.pending_approval(APPROVAL_EVENT['approval']['approval_id'])
                self.assertIsNotNone(pending)
                self.assertEqual(pending['rpc_id'], '12')

    def test_4_approval_list_unchanged_after_respond_refusal(self):
        bridge = self.make_bridge()
        self.record(bridge)
        with self.assertRaises(codex_expert_mcp.Unavailable):
            bridge.call('approval_respond', {'approval_id': APPROVAL_EVENT['approval']['approval_id'],
                                             'decision': 'approved'})
        result = bridge.call('approval_list', {})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(len(result['approvals']), 1)
        self.assertIsNone(result['approvals'][0].get('decision'))
        self.assertEqual(result['approvals'][0]['method'], 'item/commandExecution/requestApproval')

    def test_5_respond_refusal_does_not_resolve_any_approval(self):
        bridge = self.make_bridge()
        self.record(bridge)
        with patch.object(bridge.desktop_tasks, 'resolve_approval',
                          side_effect=AssertionError('resolve_approval ne doit pas être appelé par MCP (#117 T2a)')):
            with self.assertRaises(codex_expert_mcp.Unavailable):
                bridge.call('approval_respond', {'approval_id': APPROVAL_EVENT['approval']['approval_id'],
                                                 'decision': 'approved'})
        self.assertIsNotNone(bridge.desktop_tasks.pending_approval(APPROVAL_EVENT['approval']['approval_id']))


if __name__ == '__main__':
    unittest.main()
