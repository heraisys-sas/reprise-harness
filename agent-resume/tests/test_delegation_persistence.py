import concurrent.futures
import fcntl
import socket
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import Bridge
from claude_desktop_quick import Endpoint, serve
from delegation_store import Store
from ux_review_probe import Probe


class DurableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'

    def bridge(self):
        bridge = Bridge(self.root, {}, self.state, daily_limit=0)
        bridge.program = 'codex'
        self.addCleanup(bridge.db.close)
        return bridge

    def args(self, key='one', question='Inspect contract', **extra):
        return {'request_key': key, 'question': question, 'kind': 'review', **extra}

    def test_lost_ack_restart_recovers_result_without_dispatch(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'finished', 'response': 'answer'}) as run:
            original = self.bridge().call('reprise_delegate', self.args())
            restarted = self.bridge()
            self.assertEqual(restarted.call('reprise_delegate', self.args()), original)
            found = restarted.call('reprise_requests', {'request_key': 'one'})
            self.assertEqual(found['result'], original)
            self.assertEqual(run.call_count, 1)
            for _ in range(2):
                self.assertTrue(restarted.call('reprise_requests', {'request_key': 'one', 'acknowledge': True})['acknowledged'])
            self.assertEqual(len(restarted.call('reprise_requests', {})['requests']), 1)

    def test_crash_leaves_uncertain_even_with_a_new_key(self):
        with patch('codex_expert_mcp.review', side_effect=OSError('lost after send')) as run:
            with self.assertRaises(OSError):
                self.bridge().call('reprise_delegate', self.args())
            restarted = self.bridge()
            self.assertEqual(restarted.call('reprise_delegate', self.args())['status'], 'indeterminate')
            self.assertEqual(restarted.call('reprise_delegate', self.args('new'))['status'], 'indeterminate')
            self.assertEqual(run.call_count, 1)
            with self.assertRaises(ValueError):
                restarted.call('reprise_requests', {'request_key': 'one', 'acknowledge': True})

    def test_timeout_is_not_permission_to_execute_again(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'timed_out'}) as run:
            bridge = self.bridge()
            bridge.call('reprise_delegate', self.args())
            self.assertEqual(bridge.call('reprise_delegate', self.args('two'))['status'], 'indeterminate')
            self.assertEqual(run.call_count, 1)

    def test_same_key_different_content_or_parent_refused(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'finished'}):
            bridge = self.bridge()
            bridge.call('reprise_delegate', self.args())
            with self.assertRaises(ValueError):
                bridge.call('reprise_delegate', self.args(question='Different'))
            with self.assertRaises(ValueError):
                bridge.call('reprise_delegate', self.args('child', parent_request_key='missing'))

    def test_cycle_survives_restart_but_independent_iteration_allowed(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'finished'}) as run:
            bridge = self.bridge()
            bridge.call('reprise_delegate', self.args('a', 'A'))
            bridge.call('reprise_delegate', self.args('b', 'B', parent_request_key='a'))
            restarted = self.bridge()
            blocked = restarted.call('reprise_delegate', self.args('c', 'A', parent_request_key='b'))
            self.assertEqual(blocked['status'], 'cycle_blocked')
            self.assertEqual(blocked['ancestor_request_key'], 'a')
            self.assertEqual(restarted.call('reprise_delegate', self.args('independent', 'A'))['status'], 'finished')
            self.assertEqual(run.call_count, 3)

    def test_read_and_result_return_available_during_active_expertise(self):
        entered, release = threading.Event(), threading.Event()
        probe = Probe(self.root/'probe', 'Review', None)
        self.addCleanup(probe.db.close)
        task = probe.call('get_task', {'claim_key': 'worker'})
        bridge = self.bridge()
        def slow(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return {'status': 'finished'}
        def worker():
            active = Bridge(self.root, {}, self.state)
            active.program = 'codex'
            try:
                return active.call('reprise_delegate', self.args())
            finally:
                active.db.close()
        with patch('codex_expert_mcp.review', side_effect=slow), concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(worker)
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(bridge.call('reprise_requests', {'request_key': 'one'})['state'], 'indeterminate')
                self.assertEqual(bridge.call('reprise_delegate', self.args('two'))['status'], 'busy')
                self.assertEqual(probe.call('submit_result', {'job_id': task['job_id'], 'attempt_id': task['attempt_id'], 'analysis': 'late result'})['status'], 'accepted')
                self.assertFalse(bridge.db.in_transaction)
            finally:
                release.set()
            self.assertEqual(future.result()['status'], 'finished')

    def test_atomic_same_key_admission_between_connections(self):
        path = self.root/'requests.sqlite'
        barrier = threading.Barrier(2)
        def worker():
            db = sqlite3.connect(path, timeout=5)
            try:
                store = Store(db, 'project')
                barrier.wait(timeout=5)
                return store.begin('key', {'question': 'A'})
            finally:
                db.close()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = list(pool.map(lambda _: worker(), range(2)))
        self.assertEqual(sum(result is None for result in results), 1)
        self.assertEqual(next(result for result in results if result)['status'], 'indeterminate')

    def test_scope_isolation(self):
        db = sqlite3.connect(self.root/'requests.sqlite')
        self.addCleanup(db.close)
        first, other = Store(db, 'first'), Store(db, 'other')
        first.begin('key', {'question': 'A'})
        self.assertEqual(other.lookup({'request_key': 'key'})['status'], 'not_found')
        with self.assertRaises(ValueError):
            other.begin('child', {'question': 'B'}, 'key')

    def test_quick_ack_lost_and_legacy_counter_ignored_after_restart(self):
        endpoint = Endpoint(self.state, daily_limit=0)
        self.addCleanup(endpoint.close)
        endpoint.db.execute('INSERT INTO quick_calls VALUES (?,?,?)', (endpoint.scope, 0, 999999))
        endpoint.db.commit()
        payload = {'request_key': 'quick', 'prompt': 'Review this contract'}
        receipt = endpoint.submit(payload)
        other = Endpoint(self.state, daily_limit=-1)
        self.addCleanup(other.close)
        self.assertEqual(other.submit(payload), receipt)
        self.assertTrue(receipt['admission_only'])
        self.assertEqual(other.call('quick_entry/requests', {'request_key': 'quick'})['result'], receipt)
        self.assertEqual(other.db.execute('SELECT count(*) FROM quick_submissions').fetchone()[0], 1)
        for i in range(10):
            self.assertEqual(other.submit(dict(payload, request_key=str(i)))['status'], 'finished')

    def test_reservation_ack_lost_restart_and_late_result(self):
        source = self.root/'file.swift'
        source.write_text('original')
        first = Probe(self.state, 'Review', source, 'request')
        self.addCleanup(first.db.close)
        task = first.call('get_task', {'claim_key': 'executor'})
        other = Probe(self.state, 'Review', source, 'request')
        self.addCleanup(other.db.close)
        recovered = other.call('get_task', {'claim_key': 'executor'})
        self.assertEqual(task['attempt_id'], recovered['attempt_id'])
        self.assertTrue(recovered['recovered_reservation'])
        self.assertNotIn('task', other.call('get_task', {'claim_key': 'another'}))
        self.assertEqual(other.db.execute('SELECT claims FROM job').fetchone()[0], 1)
        submission = {'job_id': task['job_id'], 'attempt_id': task['attempt_id'], 'analysis': 'late answer'}
        self.assertEqual(first.call('submit_result', submission), other.call('submit_result', submission))
        result = other.call('get_result', {'request_key': 'request'})
        self.assertEqual(result['analysis'], 'late answer')
        self.assertFalse(result['acknowledged'])
        self.assertTrue(other.call('get_result', {'request_key': 'request', 'acknowledge': True})['acknowledged'])
        with self.assertRaises(ValueError):
            other.call('submit_result', dict(submission, analysis='conflict'))
        source.write_text('changed')
        with self.assertRaises(ValueError):
            Probe(self.state, 'Review', source, 'request')
        recovery = Probe(self.state, None, None, 'request')
        self.addCleanup(recovery.db.close)
        self.assertEqual(recovery.call('get_result', {'request_key': 'request'})['analysis'], 'late answer')
        self.assertEqual(recovery.attachment, 'original')

    def test_missing_identity_never_dispatches(self):
        with patch('codex_expert_mcp.review') as run:
            with self.assertRaises(ValueError):
                self.bridge().call('reprise_delegate', {'question': 'Review', 'kind': 'review'})
            run.assert_not_called()

    def test_changed_file_conflicts_with_key_and_allows_distinct_child(self):
        source = self.root/'source.txt'
        source.write_text('first')
        with patch('codex_expert_mcp.review', return_value={'status': 'finished'}) as run:
            bridge = self.bridge()
            args = self.args(paths=['source.txt'])
            bridge.call('reprise_delegate', args)
            source.write_text('second')
            with self.assertRaises(ValueError):
                bridge.call('reprise_delegate', args)
            self.assertEqual(bridge.call('reprise_delegate', dict(args, request_key='child', parent_request_key='one'))['status'], 'finished')
            self.assertEqual(run.call_count, 2)

    def test_file_changed_during_inference_is_uncertain(self):
        source = self.root/'source.txt'
        source.write_text('first')
        def change(*args, **kwargs):
            source.write_text('second')
            return {'status': 'finished', 'response': 'stale'}
        with patch('codex_expert_mcp.review', side_effect=change):
            bridge = self.bridge()
            result = bridge.call('reprise_delegate', self.args(paths=['source.txt']))
            self.assertEqual(result['status'], 'needs_review')
            self.assertEqual(bridge.call('reprise_requests', {'request_key': 'one'})['state'], 'indeterminate')

    def test_permission_without_stop_confirmation_keeps_barrier(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'blocked_permission', 'turn_id': 'active'}) as run:
            bridge = self.bridge()
            bridge.call('reprise_delegate', self.args())
            self.assertEqual(bridge.call('reprise_delegate', self.args('two'))['status'], 'indeterminate')
            self.assertEqual(run.call_count, 1)

    def test_depth_bound_does_not_limit_independent_roots(self):
        db = sqlite3.connect(self.root/'requests.sqlite')
        self.addCleanup(db.close)
        store = Store(db, 'project')
        parent = None
        for i in range(9):
            self.assertIsNone(store.begin(str(i), {'question': str(i)}, parent))
            store.finish(str(i), {'status': 'finished'})
            parent = str(i)
        self.assertEqual(store.begin('too-deep', {'question': 'different'}, parent)['status'], 'lineage_blocked')
        self.assertIsNone(store.begin('new-root', {'question': 'new'}))

    def test_desktop_receipt_remains_admission_only_after_recovery(self):
        with patch('codex_expert_mcp.claude_desktop.review', return_value={'status': 'finished', 'response': None}):
            bridge = self.bridge()
            bridge.call('reprise_delegate', self.args(backend='claude-desktop'))
            result = bridge.call('reprise_requests', {'request_key': 'one'})['result']
            self.assertTrue(result['admission_only'])
            self.assertIsNone(result['response'])

    def test_quick_second_daemon_refuses_owned_socket(self):
        path = self.root/'quick.sock'
        endpoint = Endpoint(self.state)
        self.addCleanup(endpoint.close)
        with Path(str(path)+'.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch('claude_desktop_quick._serve_owned') as run:
                with self.assertRaises(SystemExit):
                    serve(path, endpoint)
                run.assert_not_called()

    def test_quick_does_not_unlink_live_legacy_socket(self):
        path = self.root/'quick.sock'
        endpoint = Endpoint(self.state)
        self.addCleanup(endpoint.close)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            try:
                listener.bind(str(path))
            except PermissionError:
                self.skipTest('Sandbox denies local socket bind')
            listener.listen(2)
            with self.assertRaises(SystemExit):
                serve(path, endpoint)
            self.assertTrue(path.exists())

    def test_non_codex_old_daily_counters_never_block(self):
        bridge = self.bridge()
        bridge.claude_program = '/fixture-claude'
        bridge.claude_subscription_only = True
        bridge.db.execute('INSERT INTO expert_calls VALUES (?,?,?)', (bridge.scope, 0, 999999))
        bridge.db.commit()
        with patch('codex_expert_mcp.claude_review', return_value={'status': 'finished'}) as run:
            for i in range(10):
                result = bridge.call('reprise_delegate', self.args(str(i), backend='claude-code'))
                self.assertEqual(result['status'], 'finished')
            self.assertEqual(run.call_count, 10)
        other = self.bridge()
        self.assertEqual(other.call('reprise_requests', {'request_key': '9'})['state'], 'recorded')
