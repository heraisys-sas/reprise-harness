import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import Bridge
from support_clock import FakeClock, ScriptedFactory, thread_reply, turn_payload

THREAD = '12345678-1234-1234-1234-123456789abc'
TURN = 'turn-1'


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'project'
        self.root.mkdir()
        (self.root/'rollout.jsonl').touch()
        self.state = Path(self.temp.name)/'state'

    def bridge(self):
        bridge = Bridge(self.root, {}, self.state)
        bridge.program = 'codex'
        self.addCleanup(bridge.db.close)
        return bridge

    def prepare(self, bridge, owner=None, deadline_at=None, status='running'):
        bridge.requests.begin('key', {'question': 'Review'})
        for phase, turn in (('starting', None), ('active', TURN)):
            bridge.requests.attach('key', {'phase': phase, 'thread_id': THREAD, 'turn_id': turn,
                                           'started_at': 1000.0, 'deadline_at': deadline_at,
                                           'owner': owner})
        bridge.requests.finish('key', {'status': status, 'phase': 'active', 'work_completed': False,
                                       'thread_id': THREAD, 'turn_id': TURN, 'next_action': 'reconcile'})

    def factory(self, *, status='inProgress', items=None, thread_status='idle', turns=None):
        if turns is None:
            turns = [turn_payload(TURN, status=status, items=items if items is not None else [])]
        reply = thread_reply(self.root, THREAD, status=thread_status, turns=turns)
        return ScriptedFactory(FakeClock(), replies={'thread/read': reply, 'thread/resume': reply,
                                                     'turn/interrupt': {}})

    def reconcile(self, bridge, factory, **args):
        with patch('delegation_reconcile.Client', factory):
            return bridge.call('reprise_reconcile', {'request_key': 'key', **args})

    def manifest(self, bridge, **args):
        return bridge.call('reprise_requests', {'request_key': 'key', 'view': 'receipt', **args})

    def test_late_result_captured_after_detach_without_new_turn(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory(status='completed',
                               items=[{'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Late deliverable'}])
        result = self.reconcile(bridge, factory)
        self.assertEqual((result['status'], result['work_completed'], result['response']),
                         ('finished', True, 'Late deliverable'))
        self.assertNotIn('turn/start', factory.last.methods())
        manifest = self.manifest(bridge)
        self.assertEqual((manifest['state'], manifest['phase'], manifest['next_action']),
                         ('recorded', 'finished', 'collect'))
        self.assertTrue(manifest['available'])

    def test_reconnect_after_restart_observes_without_second_turn(self):
        first = self.bridge()
        self.prepare(first, owner='local:999999')
        second = self.bridge()
        factory = self.factory(thread_status='active')
        result = self.reconcile(second, factory)
        self.assertEqual((result['status'], result['phase'], result['claim']),
                         ('running', 'active', 'taken_over'))
        self.assertEqual(factory.last.methods().count('turn/start'), 0)
        self.assertEqual(factory.last.methods().count('turn/resume'), 0)
        events = second.db.execute('SELECT events FROM delegation_turns WHERE request_key=?',
                                   ('key',)).fetchone()[0]
        self.assertIn('takeover_from', events)
        self.assertEqual(second.requests.phase('key')['owner'], second.owner)

    def test_authorized_stop_interrupts_once_and_is_recorded(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory()
        result = self.reconcile(bridge, factory, action='stop', reason='user_stop')
        self.assertEqual((result['status'], result['work_completed'], result['interrupt']['confirmed']),
                         ('interrupted', False, True))
        self.assertEqual(factory.last.methods().count('turn/interrupt'), 1)
        self.assertNotIn('turn/start', factory.last.methods())
        manifest = self.manifest(bridge)
        self.assertEqual((manifest['state'], manifest['phase'], manifest['work_completed']),
                         ('recorded', 'interrupted', False))
        self.assertEqual(manifest['next_action'], 'collect')

    def test_stop_never_interrupts_an_already_terminal_turn(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory(status='completed',
                               items=[{'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Done'}])
        result = self.reconcile(bridge, factory, action='stop', reason='user_stop')
        self.assertEqual((result['status'], result['work_completed']), ('finished', True))
        self.assertNotIn('turn/interrupt', factory.last.methods())
        self.assertEqual(self.manifest(bridge)['state'], 'recorded')

    def test_stop_requires_explicit_authorized_reason(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory()
        with patch('delegation_reconcile.Client', factory):
            for args in ({'action': 'stop'}, {'action': 'stop', 'reason': 'because'}):
                with self.subTest(args=args), self.assertRaises(ValueError):
                    bridge.call('reprise_reconcile', {'request_key': 'key', **args})
        self.assertEqual(factory.instances, [])

    def test_missing_final_text_is_owner_unavailable_and_never_relaunches(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory(status='completed', items=[])
        result = self.reconcile(bridge, factory)
        self.assertEqual((result['status'], result['next_action']), ('owner_unavailable', 'collect'))
        self.assertNotIn('turn/start', factory.last.methods())
        manifest = self.manifest(bridge)
        self.assertEqual(manifest['phase'], 'owner_unavailable')
        self.assertEqual(manifest['state'], 'running')
        self.assertEqual(factory.last.methods().count('turn/start'), 0)

    def test_pending_approval_is_reported_without_interrupt(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory(items=[{'type': 'commandExecution/requestApproval'}])
        result = self.reconcile(bridge, factory)
        self.assertEqual((result['status'], result['phase']), ('awaiting_approval', 'awaiting_approval'))
        self.assertEqual(result['pending_request']['method'], 'commandExecution/requestApproval')
        self.assertNotIn('turn/interrupt', factory.last.methods())

    def test_unreachable_native_endpoint_preserves_uncertainty(self):
        bridge = self.bridge()
        self.prepare(bridge)
        def down(*args, **kwargs):
            raise OSError('endpoint down')
        result = self.reconcile(bridge, down)
        self.assertEqual(result['status'], 'owner_unavailable')
        self.assertEqual(self.manifest(bridge)['state'], 'running')
        self.assertEqual(bridge.requests.phase('key')['phase'], 'owner_unavailable')

    def test_reconcile_rejects_unknown_key_and_bad_arguments(self):
        bridge = self.bridge()
        with self.assertRaises(ValueError):
            bridge.call('reprise_reconcile', {'request_key': 'missing'})
        with self.assertRaises(ValueError):
            bridge.call('reprise_reconcile', {'request_key': 'key', 'action': 'dispatch'})
        with self.assertRaises(ValueError):
            bridge.call('reprise_reconcile', {'request_key': 'key', 'unknown': True})


if __name__ == '__main__':
    unittest.main()
