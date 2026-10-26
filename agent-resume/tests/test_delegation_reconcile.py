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
                                           'owner': owner, 'context': {'scope': bridge.scope, 'backend': 'codex'}})
        bridge.requests.finish('key', {'status': status, 'phase': 'active', 'work_completed': False,
                                       'thread_id': THREAD, 'turn_id': TURN, 'next_action': 'reconcile'})

    def factory(self, *, status='inProgress', items=None, thread_status='idle', turns=None):
        if turns is None:
            turns = [turn_payload(TURN, status=status, items=items if items is not None else [])]
        reply = thread_reply(self.root, THREAD, status=thread_status, turns=turns)
        def stop(params):
            for turn in reply['thread']['turns']:
                if turn['id'] == params['turnId']:
                    turn['status'] = 'interrupted'
            return {}
        return ScriptedFactory(FakeClock(), replies={'thread/read': reply, 'thread/resume': reply,
                                                     'turn/interrupt': stop})

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
        self.assertIsNone(second.requests.phase('key')['owner'])

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

    def test_stop_intent_is_durable_before_native_call_even_if_client_exits(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory()
        def exit_during_stop(params):
            self.assertEqual(bridge.requests.phase('key')['last_event'], 'stop_requested:user_stop')
            raise SystemExit(1)
        factory.kwargs['replies']['turn/interrupt'] = exit_during_stop
        with self.assertRaises(SystemExit):
            self.reconcile(bridge, factory, action='stop', reason='user_stop')
        self.assertEqual(self.manifest(bridge)['state'], 'running')
        self.assertEqual(bridge.requests.phase('key')['last_event'], 'stop_requested:user_stop')

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

    def test_absent_native_approval_fields_do_not_clear_persisted_permission(self):
        bridge = self.bridge()
        self.prepare(bridge)
        bridge.requests.finish('key', {'status': 'awaiting_approval', 'phase': 'awaiting_approval',
                                      'pending_request': {'method': 'item/commandExecution/requestApproval'},
                                      'thread_id': THREAD, 'turn_id': TURN})
        result = self.reconcile(bridge, self.factory(items=[]))
        self.assertEqual(result['status'], 'awaiting_approval')
        self.assertEqual(result['pending_request']['method'], 'item/commandExecution/requestApproval')
        self.assertEqual(bridge.requests.phase('key')['pending_method'], 'item/commandExecution/requestApproval')

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

    def test_initialize_precedes_observation(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory()
        self.reconcile(bridge, factory)
        self.assertEqual(factory.last.methods()[:2], ['initialize', 'thread/read'])
        self.assertEqual(factory.last.sent, [{'method': 'initialized'}])

    def test_missing_exact_turn_never_adopts_or_stops_another(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory(turns=[turn_payload('other', status='completed', items=[
            {'type': 'agentMessage', 'text': 'Unrelated answer'}])])
        result = self.reconcile(bridge, factory, action='stop', reason='user_stop')
        self.assertEqual(result['status'], 'owner_unavailable')
        self.assertEqual(bridge.requests.phase('key')['turn_id'], TURN)
        self.assertNotIn('turn/interrupt', factory.last.methods())
        self.assertEqual(self.manifest(bridge)['state'], 'running')

    def test_missing_ack_cannot_adopt_last_turn(self):
        bridge = self.bridge()
        bridge.requests.begin('key', {'question': 'lost acknowledgement'})
        bridge.requests.attach('key', {'phase': 'starting', 'thread_id': THREAD,
                                      'context': {'scope': bridge.scope}})
        factory = self.factory()
        result = self.reconcile(bridge, factory)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(factory.instances, [])
        self.assertEqual(self.manifest(bridge)['state'], 'indeterminate')

    def test_failed_stop_and_ack_without_terminal_proof_remain_uncertain(self):
        for fails in (True, False):
            with self.subTest(fails=fails):
                bridge = self.bridge()
                key = 'failed-stop-' + str(fails)
                bridge.requests.begin(key, {'question': key})
                bridge.requests.attach(key, {'phase': 'active', 'thread_id': THREAD, 'turn_id': TURN,
                                            'context': {'scope': bridge.scope}})
                factory = self.factory()
                def stop(params):
                    if fails:
                        raise TimeoutError('lost acknowledgement')
                    return {}
                factory.kwargs['replies']['turn/interrupt'] = stop
                with patch('delegation_reconcile.Client', factory):
                    result = bridge.call('reprise_reconcile', {'request_key': key, 'action': 'stop', 'reason': 'budget'})
                self.assertEqual(result['status'], 'needs_review')
                self.assertFalse(result['interrupt']['confirmed'])
                self.assertEqual(bridge.requests.lookup({'request_key': key})['state'], 'indeterminate')
                blocked = bridge.requests.begin(key + '-retry', {'question': key})
                self.assertEqual(blocked['status'], 'indeterminate')

    def test_legacy_receipt_without_native_table_recovers_exact_interrupted_turn(self):
        bridge = self.bridge()
        bridge.requests.begin('key', {'question': 'legacy'})
        bridge.requests.finish('key', {'status': 'needs_review', 'backend': 'codex',
                                      'thread_id': THREAD, 'turn_id': TURN, 'input_changed': True,
                                      'reason': 'Input files changed during delegation'})
        with bridge.db:
            bridge.db.execute('INSERT INTO expert_threads VALUES (?,?)', (bridge.scope, THREAD))
        self.assertIsNone(bridge.requests.phase('key'))
        result = self.reconcile(bridge, self.factory(status='interrupted'))
        self.assertEqual(result['status'], 'interrupted')
        stored = bridge.requests.lookup({'request_key': 'key'})
        self.assertEqual(stored['state'], 'recorded')
        self.assertTrue(stored['result']['input_changed'])
        self.assertEqual(stored['result']['reason'], 'Input files changed during delegation')
        self.assertEqual(stored['result']['terminal_evidence'], 'native:interrupted')

    def test_legacy_receipt_requires_existing_owned_route(self):
        bridge = self.bridge()
        bridge.requests.begin('key', {'question': 'legacy unowned'})
        bridge.requests.finish('key', {'status': 'needs_review', 'thread_id': THREAD, 'turn_id': TURN})
        with self.assertRaisesRegex(ValueError, 'not owned'):
            self.reconcile(bridge, self.factory())
        self.assertIsNone(bridge.requests.phase('key'))

    def test_legacy_result_does_not_clear_input_changed_on_completion(self):
        bridge = self.bridge()
        self.prepare(bridge)
        bridge.requests.finish('key', {'status': 'needs_review', 'input_changed': True,
                                      'original_reason': 'Connection timeout', 'thread_id': THREAD, 'turn_id': TURN})
        result = self.reconcile(bridge, self.factory(status='completed', items=[
            {'type': 'agentMessage', 'text': 'Late answer'}]))
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(self.manifest(bridge)['state'], 'indeterminate')
        stored = bridge.requests.lookup({'request_key': 'key'})['result']
        self.assertTrue(stored['input_changed'])
        self.assertEqual(stored['original_reason'], 'Connection timeout')
        self.assertEqual(stored['response'], 'Late answer')

    def test_changed_inputs_after_detachment_require_review(self):
        from delegation_store import file_evidence
        bridge = self.bridge()
        source = self.root / 'input.txt'
        source.write_text('before')
        bridge.requests.begin('key', {'question': 'Review'})
        bridge.requests.attach('key', {'phase': 'active', 'thread_id': THREAD, 'turn_id': TURN,
                                      'context': {'scope': bridge.scope, 'file_evidence': file_evidence(self.root, ['input.txt'])}})
        source.write_text('after')
        result = self.reconcile(bridge, self.factory(status='completed', items=[
            {'type': 'agentMessage', 'text': 'Answer'}]))
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(self.manifest(bridge)['state'], 'indeterminate')

    def test_all_final_messages_are_preserved_and_terminal_receipt_is_stable(self):
        bridge = self.bridge()
        self.prepare(bridge)
        factory = self.factory(status='completed', items=[
            {'type': 'agentMessage', 'phase': 'final_answer', 'text': 'One'},
            {'type': 'agentMessage', 'phase': 'commentary', 'text': 'Ignore'},
            {'type': 'agentMessage', 'text': 'Two'}])
        result = self.reconcile(bridge, factory)
        self.assertEqual(result['response'], 'One\nTwo')
        digest = self.manifest(bridge)['result_sha256']
        factory = self.factory(status='inProgress')
        self.assertEqual(self.reconcile(bridge, factory)['status'], 'finished')
        self.assertEqual(factory.instances, [])
        self.assertEqual(self.manifest(bridge)['result_sha256'], digest)

    def test_route_mismatch_is_rejected_before_connection(self):
        bridge = self.bridge()
        self.prepare(bridge)
        bridge.scope = 'different-socket'
        factory = self.factory()
        with self.assertRaisesRegex(ValueError, 'another configured route'):
            self.reconcile(bridge, factory)
        self.assertEqual(factory.instances, [])

    def test_active_client_lock_blocks_reconciliation(self):
        import fcntl
        bridge = self.bridge()
        self.prepare(bridge)
        with bridge.lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            factory = self.factory()
            result = self.reconcile(bridge, factory, action='stop', reason='user_stop')
        self.assertEqual(result['status'], 'busy')
        self.assertEqual(factory.instances, [])


if __name__ == '__main__':
    unittest.main()
