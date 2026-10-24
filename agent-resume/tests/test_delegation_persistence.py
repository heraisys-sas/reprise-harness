import concurrent.futures
import fcntl
import hashlib
import json
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

    def test_failure_proven_before_dispatch_does_not_lock_equivalent_requests(self):
        for status in ('blocked_owner', 'blocked_session'):
            with self.subTest(status=status), patch('codex_expert_mcp.review', return_value={'status': status}) as run:
                bridge = self.bridge()
                key = 'one-' + status
                self.assertEqual(bridge.call('reprise_delegate', self.args(key))['status'], status)
                self.assertEqual(bridge.call('reprise_requests', {'request_key': key})['state'], 'recorded')
                self.assertEqual(bridge.call('reprise_delegate', self.args(key))['status'], status)
                self.assertEqual(run.call_count, 1)
                self.assertEqual(bridge.call('reprise_delegate', self.args('again-' + status))['status'], status)
                self.assertEqual(run.call_count, 2)

    def test_status_that_may_follow_dispatch_still_locks_equivalent_requests(self):
        for status in ('blocked_permission', 'needs_review'):
            with self.subTest(status=status), patch('codex_expert_mcp.review', return_value={'status': status}) as run:
                bridge = self.bridge()
                bridge.call('reprise_delegate', self.args('first-' + status, question='Question ' + status))
                self.assertEqual(bridge.call('reprise_requests', {'request_key': 'first-' + status})['state'], 'indeterminate')
                self.assertEqual(bridge.call('reprise_delegate', self.args('second-' + status, question='Question ' + status))['status'], 'indeterminate')
                self.assertEqual(run.call_count, 1)

    def test_full_mode_result_within_limit_is_delivered_unchanged(self):
        reply = {'status': 'finished', 'response': 'é' * 20000}
        with patch('codex_expert_mcp.review', return_value=reply):
            result = self.bridge().call('reprise_delegate', self.args())
        self.assertEqual(result['response'], reply['response'])
        self.assertNotIn('return_mode_effective', result)

    def test_full_mode_oversized_result_becomes_manifest_and_pages_recover_it(self):
        text = ('é𝄞\\" ligne\n' * 8000)
        with patch('codex_expert_mcp.review', return_value={'status': 'finished', 'response': text}) as run:
            bridge = self.bridge()
            first = bridge.call('reprise_delegate', self.args())
            replay = self.bridge().call('reprise_delegate', self.args())
            explicit = bridge.call('reprise_delegate', self.args(return_mode='receipt'))
        self.assertEqual(run.call_count, 1)
        for answer in (first, replay):
            self.assertEqual((answer['status'], answer['available'], answer['return_mode_effective']), ('finished', True, 'receipt'))
            self.assertNotIn('response', answer)
            self.assertLess(len(json.dumps(answer, ensure_ascii=False)), 1500)
            self.assertEqual((answer['total_length'], answer['result_sha256']), (explicit['total_length'], explicit['result_sha256']))
        pages, offset = [], 0
        while offset is not None:
            page = bridge.call('reprise_requests', {'request_key': 'one', 'view': 'page', 'offset': offset, 'max_chars': 6000, 'result_sha256': first['result_sha256']})
            pages.append(page['text'])
            offset = page['next_offset']
        recovered = ''.join(pages)
        self.assertEqual(len(recovered), first['total_length'])
        self.assertEqual(hashlib.sha256(recovered.encode('utf-8')).hexdigest(), first['result_sha256'])
        self.assertEqual(json.loads(recovered)['response'], text)

    def test_full_mode_oversized_uncertain_result_is_also_bounded(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'needs_review', 'response': 'x' * 60000, 'output_complete': False}):
            result = self.bridge().call('reprise_delegate', self.args())
        self.assertEqual((result['status'], result['state'], result['return_mode_effective']), ('needs_review', 'indeterminate', 'receipt'))
        self.assertNotIn('response', result)

    def test_receipt_reports_the_delegation_status_not_the_lookup_status(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'needs_review', 'response': 'x' * 60000, 'output_complete': False}):
            bridge = self.bridge()
            fresh = bridge.call('reprise_delegate', self.args(return_mode='receipt'))
            replay = bridge.call('reprise_delegate', self.args(return_mode='receipt'))
        for manifest in (fresh, replay):
            self.assertEqual((manifest['status'], manifest['state'], manifest['available']), ('needs_review', 'indeterminate', True))
        from codex_expert_mcp import tool_content
        self.assertTrue(tool_content(fresh)['isError'])

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
        bridge = Bridge(self.root, {}, self.state, daily_limit=0, desktop_socket='/private/tmp/quick.sock')
        bridge.program = 'codex'
        self.addCleanup(bridge.db.close)
        with patch('codex_expert_mcp.claude_desktop.review', return_value={'status': 'finished', 'response': None}):
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


class ReceiptPaginationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = Store(self.db, 'project')
        self.store.begin('key', {'question': 'A'})

    def lookup(self, **args):
        return self.store.lookup({'request_key': 'key', **args})

    def test_exact_reconstruction_and_unchanged_full_receipt(self):
        sample = '"\\\n\r\t\x00\x01e\u0301é😀'
        for length in (8850, 12862):
            with self.subTest(length=length):
                saved = self.store.finish('key', {'status': 'finished',
                    'response': (sample * length)[:length], 'details': sample * 1000})
                before = self.db.execute('SELECT * FROM delegation_requests').fetchall()
                manifest = self.lookup(view='receipt')
                expected = json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                parts, offset = [], 0
                while True:
                    args = {'view': 'page', 'offset': offset, 'max_chars': 6000,
                            'result_sha256': manifest['result_sha256']}
                    page = self.lookup(**args)
                    self.assertEqual(page, self.lookup(**args))
                    self.assertEqual(page['offset'], offset)
                    self.assertEqual(page['returned_length'], len(page['text']))
                    parts.append(page['text'])
                    if not page['has_more']:
                        self.assertIsNone(page['next_offset'])
                        break
                    self.assertGreater(page['returned_length'], 0)
                    self.assertEqual(page['next_offset'], offset + len(page['text']))
                    offset = page['next_offset']
                text = ''.join(parts)
                self.assertEqual(text, expected)
                self.assertEqual(json.loads(text), saved)
                self.assertEqual(len(text), manifest['total_length'])
                self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(),
                                 manifest['result_sha256'])
                full = {'status': 'finished', 'request_key': 'key', 'state': 'recorded',
                        'attempt_id': manifest['attempt_id'], 'acknowledged': False, 'result': saved}
                self.assertEqual(self.lookup(), full)
                self.assertEqual(self.lookup(view='full'), full)
                self.assertEqual(self.db.execute('SELECT * FROM delegation_requests').fetchall(), before)
                self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.store.lookup({}), {'status': 'finished', 'requests': [{
            'request_key': 'key', 'state': 'recorded', 'parent_request_key': None,
            'acknowledged': 0}], 'limit': 100})
        self.assertEqual(self.store.lookup({'request_key': 'missing'}),
                         {'status': 'not_found', 'request_key': 'missing'})

    def test_absence_boundaries_changed_result_and_scope(self):
        absent = self.lookup(view='page')
        self.assertFalse(absent['available'])
        self.assertIsNone(absent['total_length'])
        self.assertIsNone(absent['result_sha256'])
        self.assertNotIn('text', absent)
        self.store.finish('key', {'status': 'finished', 'response': 'abc'})
        manifest = self.lookup(view='receipt')
        fingerprint, total = manifest['result_sha256'], manifest['total_length']
        first = self.lookup(view='page', offset=0, max_chars=1)
        self.assertEqual((first['returned_length'], first['next_offset']), (1, 1))
        self.assertEqual(self.lookup(view='page', offset=0.0, max_chars=1.0), first)
        terminal = self.lookup(view='page', offset=total, result_sha256=fingerprint.upper())
        self.assertEqual((terminal['text'], terminal['returned_length'],
                          terminal['has_more'], terminal['next_offset']), ('', 0, False, None))
        outside = self.lookup(view='page', offset=total + 1, result_sha256=fingerprint)
        self.assertEqual(outside['status'], 'invalid_offset')
        self.assertNotIn('text', outside)
        self.store.finish('key', {'status': 'finished', 'response': 'changed'})
        for offset in (0, 1):
            changed = self.lookup(view='page', offset=offset, result_sha256=fingerprint)
            self.assertEqual(changed['status'], 'result_changed')
            self.assertNotIn('text', changed)
        other = Store(self.db, 'other')
        for view in ('receipt', 'page'):
            self.assertEqual(other.lookup({'request_key': 'key', 'view': view})['status'], 'not_found')
        self.assertFalse(self.db.in_transaction)

    def test_invalid_arguments(self):
        invalid = [None, [], {'unknown': 1}, {'view': 'other'}, {'view': None},
                   {'view': 'receipt'}, {'view': 'page'}, {'acknowledge': True},
                   {'request_key': None}, {'request_key': ''}, {'request_key': ' '},
                   {'request_key': 'x' * 129}, {'request_key': 1},
                   {'request_key': 'key', 'acknowledge': 1}]
        for field in ('offset', 'max_chars', 'result_sha256'):
            for view in ('full', 'receipt'):
                invalid.append({'request_key': 'key', 'view': view, field: 1})
        base = {'request_key': 'key', 'view': 'page'}
        for field, values in (
                ('offset', [-1, True, False, 1.5, '0', None]),
                ('max_chars', [0, 6001, True, False, 1.5, '1', None]),
                ('result_sha256', ['', 'a' * 63, 'g' * 64, 'a' * 64 + '\n', None, 1]),
                ('acknowledge', [True, 0, 'false']), ('extra', [False])):
            invalid.extend(dict(base, **{field: value}) for value in values)
        invalid.append(dict(base, offset=1))
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.store.lookup(args)
