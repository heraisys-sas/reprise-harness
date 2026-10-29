import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import queue
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from claude_mcp import AwaitRequests, ClaudeMCP, MAX_ACTIVE_AWAITS, send_worker_error
from desktop_task_store import TaskStore

ROOT = Path(__file__).resolve().parents[1]


def call(facade, name, args, **extra):
    params = dict({'name': name, 'arguments': args}, **extra)
    result = facade.dispatch({'method': 'tools/call', 'params': params})
    return json.loads(result['content'][0]['text']) if result else None


def content(result):
    return json.loads(result['content'][0]['text'])


class FakeTime:
    """Deterministic time: wait() advances a virtual clock and fires callbacks that came due; nothing sleeps."""

    def __init__(self):
        self.now, self.events, self.waits = 1000.0, [], []

    def clock(self):
        return self.now

    def at(self, delay, callback):
        self.events.append((self.now + delay, callback))

    def wait(self, cancel, seconds):
        self.waits.append(round(seconds, 3))
        self.now += seconds
        for event in sorted(self.events, key=lambda item: item[0]):
            if event[0] <= self.now and event in self.events:
                self.events.remove(event)
                event[1]()


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        self.store = TaskStore(self.tmp, self.tmp)
        self.addCleanup(self.store.close)

    def row(self, key):
        return self.store.db.execute('SELECT claim_key,attempt_id,delivery,answer FROM desktop_tasks WHERE request_key=?', (key,)).fetchone()


class LegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        self.state, self.project = self.tmp / 'state', self.tmp / 'project'
        self.state.mkdir()
        self.project.mkdir()

    def create_legacy_db(self):
        db = sqlite3.connect(self.state / 'quick.sqlite')
        db.execute('''CREATE TABLE desktop_tasks(
            scope TEXT NOT NULL, request_key TEXT NOT NULL, prompt TEXT NOT NULL,
            chat_id TEXT, claim_key TEXT, attempt_id TEXT, answer TEXT,
            acknowledged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope,request_key))''')
        scope = str(self.project.resolve())
        db.executemany('INSERT INTO desktop_tasks(scope,request_key,prompt,claim_key,attempt_id,answer) VALUES(?,?,?,?,?,?)', [
            (scope, 'fresh', 'fresh prompt', None, None, None),
            (scope, 'claimed', 'old prompt', 'owner', 'attempt-1', None),
            (scope, 'done', 'done prompt', 'owner', 'attempt-2', 'answer'),
            (scope, 'claim_only', 'uncertain A', 'owner', None, None),
            (scope, 'attempt_only', 'uncertain B', None, 'attempt-3', None),
            (scope, 'answer_only', 'uncertain C', None, None, 'answer'),
        ])
        db.commit()
        db.close()

    def test_legacy_claimed_and_incomplete_rows_are_uncertain_without_inventing_attempt(self):
        self.create_legacy_db()
        store = TaskStore(self.state, self.project)
        self.addCleanup(store.close)
        self.assertEqual(store.reserve('fresh', 'new')['prompt'], 'fresh prompt')
        for key in ('claimed', 'claim_only', 'attempt_only'):
            self.assertEqual(store.reserve(key, 'owner')['state'], 'attempted')
            receipt = store.claim(key, 'owner')
            self.assertNotIn('prompt', receipt)
        self.assertEqual(store.claim('claim_only', 'owner')['attempt_id'], None)
        self.assertEqual(store.reserve('done', 'owner')['state'], 'completed')
        self.assertNotIn('prompt', store.claim('done', 'owner'))
        self.assertEqual(store.reserve('answer_only', 'owner')['state'], 'completed')
        self.assertNotIn('prompt', store.claim('answer_only', 'owner'))

    def test_migration_is_stable_after_reopening(self):
        self.create_legacy_db()
        first = TaskStore(self.state, self.project)
        first.close()
        second = TaskStore(self.state, self.project)
        self.addCleanup(second.close)
        self.assertEqual(second.reserve('claimed', 'owner')['state'], 'attempted')
        self.assertEqual(second.reserve('fresh', 'owner')['prompt'], 'fresh prompt')

    def test_simultaneous_initialization_serializes_legacy_migration(self):
        self.create_legacy_db()
        barrier = threading.Barrier(2)
        results, errors = [], []
        def open_store():
            store = None
            try:
                barrier.wait()
                store = TaskStore(self.state, self.project)
                results.append(store.reserve('claimed', 'owner')['state'])
            except Exception as exc:
                errors.append(exc)
            finally:
                if store is not None:
                    store.close()
        threads = [threading.Thread(target=open_store) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5)
        self.assertFalse(errors)
        self.assertEqual(results, ['attempted', 'attempted'])
        self.assertTrue(all(not thread.is_alive() for thread in threads))


class ReserveTests(Workspace):
    def test_unknown_task_is_absent_and_nothing_is_created(self):
        self.assertEqual(self.store.reserve('missing', 'w1'), {'state': 'absent'})
        self.assertIsNone(self.row('missing'))

    def test_reserve_is_exact_and_never_picks_another_task(self):
        self.store.admit('old', 'first')
        self.store.admit('wanted', 'second')
        reserved = self.store.reserve('wanted', 'w1')
        self.assertEqual((reserved['state'], reserved['prompt'], reserved['fresh']), ('reserved', 'second', True))
        self.assertIsNone(self.row('old')[0])

    def test_delivery_intention_is_recorded_once_and_blocks_further_payloads(self):
        self.store.admit('k', 'p')
        attempt = self.store.reserve('k', 'w1')['attempt_id']
        self.assertTrue(self.store.mark_attempted('k', attempt))
        self.assertFalse(self.store.mark_attempted('k', attempt))
        again = self.store.reserve('k', 'w1')
        self.assertEqual(again['state'], 'attempted')
        self.assertNotIn('prompt', again)
        self.assertFalse(self.store.release('k', attempt))
        self.assertEqual(self.row('k')[:3], ('w1', attempt, 'attempted'))

    def test_release_only_drops_a_never_attempted_reservation(self):
        self.store.admit('k', 'p')
        attempt = self.store.reserve('k', 'w1')['attempt_id']
        self.assertTrue(self.store.release('k', attempt))
        self.assertEqual(self.row('k')[:3], (None, None, None))
        self.assertEqual(self.store.reserve('k', 'w2')['state'], 'reserved')

    def test_recovered_reservation_is_not_fresh(self):
        self.store.admit('k', 'p')
        first = self.store.reserve('k', 'w1')
        second = self.store.reserve('k', 'w1')
        self.assertEqual((first['fresh'], second['fresh'], second['attempt_id']), (True, False, first['attempt_id']))

    def test_other_claim_key_is_refused(self):
        self.store.admit('k', 'p')
        self.store.reserve('k', 'w1')
        with self.assertRaises(ValueError):
            self.store.reserve('k', 'w2')

    def test_completed_task_reports_completed_without_a_payload(self):
        self.store.admit('k', 'p')
        attempt = self.store.reserve('k', 'w1')['attempt_id']
        self.store.mark_attempted('k', attempt)
        self.store.submit('k', attempt, 'done')
        self.assertEqual(self.store.reserve('k', 'w1')['state'], 'completed')

    def test_get_task_hands_over_once_and_replays_a_receipt_without_payload(self):
        self.store.admit('k', 'p')
        first = self.store.claim('k', 'w1')
        self.assertEqual((first['delivery'], first['prompt']), ('handed_over', 'p'))
        again = self.store.claim('k', 'w1')
        self.assertEqual((again['delivery'], again['attempt_id'], 'prompt' in again), ('attempted', first['attempt_id'], False))
        self.assertIn('receipt is unconfirmed', again['instruction'])
        self.assertIn('reconcile the state before any new admission', again['instruction'])
        self.assertNotIn('new key', again['instruction'])
        self.assertEqual(self.store.reserve('k', 'w1')['state'], 'attempted')

    def test_await_then_get_task_cannot_bypass_the_barrier(self):
        self.store.admit('k', 'secret prompt')
        attempt = self.store.reserve('k', 'w1')['attempt_id']
        self.assertTrue(self.store.mark_attempted('k', attempt))
        receipt = self.store.claim('k', 'w1')
        self.assertEqual(receipt['delivery'], 'attempted')
        self.assertNotIn('secret prompt', json.dumps(receipt))

    def test_get_task_then_await_returns_a_receipt(self):
        self.store.admit('k', 'p')
        self.store.claim('k', 'w1')
        self.assertEqual(self.store.reserve('k', 'w1')['state'], 'attempted')

    def test_get_task_on_a_pending_reservation_wins_and_blocks_a_later_mark(self):
        self.store.admit('k', 'p')
        attempt = self.store.reserve('k', 'w1')['attempt_id']
        self.assertEqual(self.store.claim('k', 'w1')['delivery'], 'handed_over')
        self.assertFalse(self.store.mark_attempted('k', attempt))

    def test_two_scopes_sharing_a_request_key_stay_independent(self):
        other_root = Path(self.tmp) / 'other'
        other_root.mkdir()
        other = TaskStore(self.tmp, other_root)
        self.addCleanup(other.close)
        self.store.admit('k', 'prompt A')
        other.admit('k', 'prompt B')
        self.assertEqual(self.store.reserve('k', 'w1')['prompt'], 'prompt A')
        self.assertEqual(other.reserve('k', 'w1')['prompt'], 'prompt B')
        self.assertTrue(self.store.mark_attempted('k', self.store.reserve('k', 'w1')['attempt_id']))
        self.assertEqual(other.reserve('k', 'w1')['state'], 'reserved')

    def test_a_result_submitted_before_the_intention_is_never_downgraded(self):
        self.store.admit('k', 'p')
        attempt = self.store.reserve('k', 'w1')['attempt_id']
        self.store.submit('k', attempt, 'answer')
        self.assertFalse(self.store.mark_attempted('k', attempt))
        self.assertEqual(self.row('k')[2:], ('reserved', 'answer'))
        self.assertEqual(self.store.reserve('k', 'w1')['state'], 'completed')
        self.assertTrue(self.store.submit('k', attempt, 'answer')['completed'])
        with self.assertRaises(ValueError):
            self.store.submit('k', attempt, 'different')

    def test_invalid_keys_refused(self):
        for bad in ('', ' ', None, 'x' * 129):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.store.reserve(bad, 'w')
                with self.assertRaises(ValueError):
                    self.store.reserve('k', bad)

    def test_old_database_is_migrated_with_the_delivery_column(self):
        path = Path(self.tmp) / 'old' / 'quick.sqlite'
        path.parent.mkdir()
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE desktop_tasks(scope TEXT NOT NULL, request_key TEXT NOT NULL, prompt TEXT NOT NULL, chat_id TEXT, claim_key TEXT, attempt_id TEXT, answer TEXT, acknowledged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope,request_key))')
        db.execute("INSERT INTO desktop_tasks(scope,request_key,prompt) VALUES(?,?,?)", (str(Path(self.tmp).resolve()), 'legacy', 'p'))
        db.commit()
        db.close()
        migrated = TaskStore(path.parent, self.tmp)
        self.addCleanup(migrated.close)
        self.assertEqual(migrated.reserve('legacy', 'w')['state'], 'reserved')


class AwaitTaskTests(Workspace):
    def setUp(self):
        super().setUp()
        self.time = FakeTime()
        self.facade = ClaudeMCP(self.store, Mock(), max_wait=3, clock=self.time.clock, wait=self.time.wait, enable_await=True)

    def await_(self, **args):
        return call(self.facade, 'await_task', dict({'request_key': 'k', 'claim_key': 'w1'}, **args))

    def test_listed_with_exact_key_schema_and_no_loop_instruction(self):
        tool = next(item for item in self.facade.dispatch({'method': 'tools/list'})['tools'] if item['name'] == 'await_task')
        self.assertEqual(tool['inputSchema']['required'], ['request_key', 'claim_key'])
        self.assertIn('explicit project task', tool['description'])
        self.assertIn('stop unless the operator asked', tool['description'])

    def test_tool_is_hidden_and_refused_unless_the_operator_enables_it(self):
        default = ClaudeMCP(self.store, Mock(), max_wait=3, clock=self.time.clock, wait=self.time.wait)
        self.assertFalse(default.enable_await)
        self.assertNotIn('await_task', {item['name'] for item in default.dispatch({'method': 'tools/list'})['tools']})
        self.store.admit('k', 'p')
        with self.assertRaises(ValueError):
            call(default, 'await_task', {'request_key': 'k', 'claim_key': 'w1'})
        self.assertIsNone(self.row('k')[0])  # nothing reserved, nothing delivered
        self.assertEqual(self.time.waits, [])
        self.assertIn('await_task', {item['name'] for item in self.facade.dispatch({'method': 'tools/list'})['tools']})

    def test_exact_task_is_handed_over_immediately_and_only_that_one(self):
        self.store.admit('older', 'not me')
        self.store.admit('k', 'do it')
        result = self.await_()
        self.assertEqual(self.time.waits, [])
        self.assertEqual((result['task'], result['delivery'], result['prompt'], result['request_key']), (True, 'handed_over', 'do it', 'k'))
        self.assertIn('submit_result', result['instruction'])
        self.assertNotIn('call await_task again.', result['instruction'])
        self.assertIsNone(self.row('older')[0])
        self.assertEqual(self.row('k')[2], 'attempted')

    def test_second_call_returns_a_receipt_and_never_the_payload(self):
        self.store.admit('k', 'secret prompt')
        self.await_()
        again = self.await_()
        self.assertEqual((again['task'], again['delivery']), (False, 'attempted'))
        self.assertNotIn('secret prompt', json.dumps(again))
        self.assertIn('receipt is unconfirmed', again['instruction'])
        self.assertIn('reconcile the state before any new admission', again['instruction'])
        self.assertNotIn('new key', again['instruction'])

    def test_timeout_terminates_once_without_a_task_a_reservation_or_further_wakeups(self):
        result = self.await_(wait_seconds=1)
        self.assertEqual((result['idle'], result['task'], result['waited_seconds']), (True, False, 1.0))
        self.assertIn('Do not call await_task again unless the operator asked', result['instruction'])
        self.assertIsNone(self.row('k'))
        woke = len(self.time.waits)
        self.assertEqual(self.time.waits, [0.25, 0.5, 0.25])  # bounded backoff, ends at the deadline
        self.assertEqual(len(self.time.waits), woke)
        self.assertEqual(self.time.events, [])

    def test_task_admitted_during_the_wait_is_delivered_once(self):
        self.time.at(0.5, lambda: self.store.admit('k', 'arrived'))
        result = self.await_(wait_seconds=3)
        self.assertEqual((result['request_key'], result['prompt']), ('k', 'arrived'))
        self.assertEqual(self.time.waits, [0.25, 0.5])

    def test_arrival_between_the_first_read_and_the_next_one_is_not_lost(self):
        outer = self

        class AdmitAfterFirstRead:
            def __init__(self, inner):
                self.inner, self.calls = inner, 0

            def reserve(self, *args):
                self.calls += 1
                reserved = self.inner.reserve(*args)
                if self.calls == 1:
                    self.inner.admit('k', 'raced in')  # lands right after the empty read
                return reserved

            def __getattr__(self, name):
                return getattr(self.inner, name)
        result = content(self.facade.await_task(AdmitAfterFirstRead(self.store), {'request_key': 'k', 'claim_key': 'w1', 'wait_seconds': 3}))
        self.assertEqual((result['prompt'], result['delivery']), ('raced in', 'handed_over'))
        self.assertEqual(outer.row('k')[2], 'attempted')

    def test_another_claim_key_is_refused_not_waited_on(self):
        self.store.admit('k', 'p')
        self.await_()
        with self.assertRaises(ValueError):
            call(self.facade, 'await_task', {'request_key': 'k', 'claim_key': 'w2'})

    def test_completed_task_reports_completion(self):
        self.store.admit('k', 'p')
        attempt = self.await_()['attempt_id']
        self.store.submit('k', attempt, 'answer')
        done = self.await_()
        self.assertEqual((done['task'], done['delivery']), (False, 'completed'))

    def test_concurrent_waits_with_the_same_keys_hand_over_exactly_one_payload(self):
        self.store.admit('k', 'once only')
        results, errors = [], []
        barrier = threading.Barrier(4)

        def waiter():
            local = TaskStore(self.tmp, self.tmp)
            facade = ClaudeMCP(local, Mock(), max_wait=3, enable_await=True)
            try:
                barrier.wait()
                results.append(facade.await_task(local, {'request_key': 'k', 'claim_key': 'w1', 'wait_seconds': 1}))
            except Exception as exc:  # pragma: no cover - reported below
                errors.append(exc)
            finally:
                local.close()
        threads = [threading.Thread(target=waiter) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        payloads = [content(item) for item in results]
        self.assertEqual(sum(1 for item in payloads if item.get('prompt') == 'once only'), 1)
        self.assertEqual(sum(1 for item in payloads if item.get('delivery') == 'attempted'), 3)

    def test_cancel_while_waiting_returns_nothing_and_creates_no_reservation(self):
        cancel = threading.Event()
        self.time.at(0.3, cancel.set)
        self.assertIsNone(self.facade.await_task(self.store, {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 3}, cancel=cancel))
        self.assertIsNone(self.row('k'))

    def test_cancel_after_a_fresh_reservation_and_before_the_intention_releases_it(self):
        self.store.admit('k', 'p')
        cancel = threading.Event()

        class CancelAfterReserve:
            def __init__(self, inner):
                self.inner = inner

            def reserve(self, *args):
                reserved = self.inner.reserve(*args)
                cancel.set()
                return reserved

            def __getattr__(self, name):
                return getattr(self.inner, name)
        self.assertIsNone(self.facade.await_task(CancelAfterReserve(self.store), {'request_key': 'k', 'claim_key': 'w1'}, cancel=cancel))
        self.assertEqual(self.row('k')[:3], (None, None, None))
        self.assertEqual(self.store.reserve('k', 'w2')['state'], 'reserved')

    def test_cancel_never_erases_a_recovered_reservation(self):
        self.store.admit('k', 'p')
        attempt = self.store.reserve('k', 'w1')['attempt_id']  # earlier call, then reconnection
        cancel = threading.Event()
        cancel.set()
        self.assertIsNone(self.facade.await_task(self.store, {'request_key': 'k', 'claim_key': 'w1'}, cancel=cancel))
        self.assertEqual(self.row('k')[:3], ('w1', attempt, 'reserved'))

    def test_cancel_between_the_intention_and_the_write_keeps_the_task_uncertain_and_writes_nothing(self):
        self.store.admit('k', 'p')
        cancel, written = threading.Event(), []

        class CancelAfterMark:
            def __init__(self, inner):
                self.inner = inner

            def mark_attempted(self, *args):
                marked = self.inner.mark_attempted(*args)
                cancel.set()
                return marked

            def __getattr__(self, name):
                return getattr(self.inner, name)
        wrote = self.facade.deliver_wait(1, CancelAfterMark(self.store), {'arguments': {'request_key': 'k', 'claim_key': 'w1'}}, cancel, written.append)
        self.assertFalse(wrote)
        self.assertEqual(written, [])
        self.assertEqual(self.row('k')[:3][2], 'attempted')
        self.assertEqual(self.store.reserve('k', 'w1')['state'], 'attempted')

    def test_a_failed_write_after_the_intention_never_releases_or_redelivers(self):
        self.store.admit('k', 'p')

        def broken_pipe(_payload):
            raise BrokenPipeError('client went away')
        with self.assertRaises(BrokenPipeError):
            self.facade.deliver_wait(1, self.store, {'arguments': {'request_key': 'k', 'claim_key': 'w1'}}, threading.Event(), broken_pipe)
        self.assertEqual(self.row('k')[2], 'attempted')
        receipt = self.await_()
        self.assertEqual((receipt['delivery'], 'prompt' in receipt), ('attempted', False))
        attempt = receipt['attempt_id']
        self.assertTrue(self.store.submit('k', attempt, 'late answer')['completed'])  # a late result is still accepted
        self.assertEqual(self.row('k')[3], 'late answer')

    def test_deliver_wait_writes_exactly_one_response_with_the_request_id(self):
        self.store.admit('k', 'p')
        written = []
        self.assertTrue(self.facade.deliver_wait(41, self.store, {'arguments': {'request_key': 'k', 'claim_key': 'w1'}}, threading.Event(), written.append))
        self.assertEqual([item['id'] for item in written], [41])
        self.assertEqual(json.loads(written[0]['result']['content'][0]['text'])['prompt'], 'p')

    def test_arguments_are_strict(self):
        for args in ({}, {'claim_key': 'w'}, {'request_key': 'k'}, {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 0},
                     {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 4}, {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': True},
                     {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': '2'}, {'request_key': 'k', 'claim_key': 'w', 'extra': 1},
                     {'request_key': '', 'claim_key': 'w'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                call(self.facade, 'await_task', args)

    def test_invalid_progress_token_refused(self):
        with self.assertRaises(ValueError):
            call(self.facade, 'await_task', {'request_key': 'k', 'claim_key': 'w'}, _meta={'progressToken': {'a': 1}})

    def test_no_progress_notification_by_default_even_with_a_token(self):
        sent = []
        self.facade.await_task(self.store, {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 2}, token='tok', emit=sent.append)
        self.assertEqual(sent, [])

    def test_progress_is_opt_in_and_needs_a_token(self):
        facade = ClaudeMCP(self.store, Mock(), max_wait=3, progress_interval=0.4, clock=self.time.clock, wait=self.time.wait, enable_await=True)
        sent = []
        facade.await_task(self.store, {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 2}, token='tok', emit=sent.append)
        self.assertGreaterEqual(len(sent), 2)
        self.assertEqual([item['params']['progress'] for item in sent][:2], [1, 2])
        silent = []
        facade.await_task(self.store, {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 2}, token=None, emit=silent.append)
        self.assertEqual(silent, [])

    def test_submit_result_stays_idempotent_and_conflicts_are_refused_after_await(self):
        self.store.admit('k', 'p')
        attempt = self.await_()['attempt_id']
        self.assertTrue(self.store.submit('k', attempt, 'A')['completed'])
        self.assertTrue(self.store.submit('k', attempt, 'A')['completed'])
        with self.assertRaises(ValueError):
            self.store.submit('k', attempt, 'B')
        with self.assertRaises(ValueError):
            self.store.submit('k', 'other-attempt', 'A')

    def test_max_wait_is_bounded_at_construction(self):
        for bad in (0, 601, True, 1.5, '45'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ClaudeMCP(self.store, Mock(), max_wait=bad)


class AwaitRequestRegistryTests(unittest.TestCase):
    def test_completed_workers_are_pruned_on_next_admission(self):
        registry = AwaitRequests(limit=1)
        release = threading.Event()
        worker = threading.Thread(target=release.wait, daemon=True)
        self.assertIsNone(registry.admit('1', threading.Event(), worker))
        worker.start()
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        registry.finished('1', registry.active['1'][0])
        self.assertIn(worker, registry.workers)
        next_worker = threading.Thread(target=lambda: None, daemon=True)
        self.assertIsNone(registry.admit('2', threading.Event(), next_worker))
        self.assertNotIn(worker, registry.workers)
        self.assertIn(next_worker, registry.workers)

    def test_duplicate_id_and_full_registry_are_distinct_admission_errors(self):
        registry = AwaitRequests(limit=1)
        first_cancel, second_cancel = threading.Event(), threading.Event()
        first = threading.Thread(target=lambda: None, daemon=True)
        second = threading.Thread(target=lambda: None, daemon=True)
        self.assertIsNone(registry.admit('1', first_cancel, first))
        self.assertEqual(registry.admit('1', second_cancel, second), 'duplicate')
        self.assertEqual(registry.admit('2', second_cancel, second), 'full')
        self.assertIs(registry.active['1'][0], first_cancel)

    def test_start_failure_removes_unstarted_worker_before_shutdown(self):
        registry = AwaitRequests(limit=1)
        worker = threading.Thread(target=lambda: None, daemon=True)
        cancel = threading.Event()
        with patch.object(worker, 'start', side_effect=RuntimeError('start failed')):
            with self.assertRaisesRegex(RuntimeError, 'start failed'):
                registry.start('1', cancel, worker)
        self.assertEqual(registry.active, {})
        self.assertNotIn(worker, registry.workers)
        registry.shutdown(join_timeout=0)

    def test_shutdown_cancels_and_joins_with_one_bounded_deadline(self):
        registry = AwaitRequests(limit=1)
        cancel, release = threading.Event(), threading.Event()
        worker = threading.Thread(target=release.wait, daemon=True)
        self.assertIsNone(registry.start('1', cancel, worker))
        started = time.monotonic()
        registry.shutdown(join_timeout=0.05)
        elapsed = time.monotonic() - started
        self.assertTrue(cancel.is_set())
        self.assertTrue(worker.is_alive())
        self.assertLess(elapsed, 0.5)
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())

    def test_unexpected_worker_error_is_reported_without_traceback(self):
        sent = []
        send_worker_error('id-1', RuntimeError('unexpected'), threading.Event(), sent.append)
        self.assertEqual(sent[0]['jsonrpc'], '2.0')
        self.assertEqual(sent[0]['id'], 'id-1')
        self.assertEqual(sent[0]['error']['code'], -32602)
        self.assertNotIn('traceback', json.dumps(sent[0]).lower())
        sent.clear()
        cancel = threading.Event()
        cancel.set()
        send_worker_error('id-2', RuntimeError('cancelled'), cancel, sent.append)
        self.assertEqual(sent, [])


class StdioProcessTests(unittest.TestCase):
    """The real server over pipes. No sleep is used to synchronise: ordering comes from the replies."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        (self.tmp / 'project').mkdir()
        self.state = self.tmp / 'state'
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / 'claude_mcp.py'), '--project-root', str(self.tmp / 'project'),
             '--state', str(self.state), '--expert-state', str(self.tmp / 'expert'), '--max-wait', '2', '--enable-await'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=ROOT, text=True)
        self.lines = queue.Queue()
        threading.Thread(target=lambda: [self.lines.put(json.loads(line)) for line in self.process.stdout], daemon=True).start()
        self.addCleanup(self.stop)

    def stop(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.process.stdout.close()

    def send(self, payload):
        self.process.stdin.write(json.dumps(payload) + '\n')
        self.process.stdin.flush()

    def reply(self, timeout=4):
        return self.lines.get(timeout=timeout)

    def tool(self, ident, name, arguments, **extra):
        self.send({'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call', 'params': dict({'name': name, 'arguments': arguments}, **extra)})

    def test_ping_served_during_the_wait_then_exact_task_delivered_once_and_completed(self):
        self.tool(1, 'await_task', {'request_key': 'job-1', 'claim_key': 'w1', 'wait_seconds': 2})
        self.send({'jsonrpc': '2.0', 'id': 2, 'method': 'ping'})
        self.assertEqual(self.reply(2), {'jsonrpc': '2.0', 'id': 2, 'result': {}})
        store = TaskStore(self.state, self.tmp / 'project')
        self.addCleanup(store.close)
        store.admit('job-0', 'not this one')
        store.admit('job-1', 'sort 7,2,5', chat_id='chat-9')
        delivered = self.reply(4)
        self.assertEqual(delivered['id'], 1)
        task = json.loads(delivered['result']['content'][0]['text'])
        self.assertEqual((task['request_key'], task['prompt'], task['chat_id']), ('job-1', 'sort 7,2,5', 'chat-9'))
        self.tool(3, 'await_task', {'request_key': 'job-1', 'claim_key': 'w1', 'wait_seconds': 1})
        replay = json.loads(self.reply(3)['result']['content'][0]['text'])
        self.assertEqual((replay['delivery'], 'prompt' in replay), ('attempted', False))
        self.tool(5, 'get_task', {'request_key': 'job-1', 'claim_key': 'w1'})
        bypass = json.loads(self.reply(3)['result']['content'][0]['text'])
        self.assertEqual((bypass['delivery'], 'prompt' in bypass), ('attempted', False))
        self.tool(4, 'submit_result', {'request_key': 'job-1', 'attempt_id': task['attempt_id'], 'answer': '2,5,7'})
        self.assertTrue(json.loads(self.reply()['result']['content'][0]['text'])['completed'])

    def test_cancelled_wait_sends_no_response(self):
        self.tool(7, 'await_task', {'request_key': 'later', 'claim_key': 'w1', 'wait_seconds': 1})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 7}})
        self.send({'jsonrpc': '2.0', 'id': 8, 'method': 'ping'})
        self.assertEqual(self.reply(2)['id'], 8)
        with self.assertRaises(queue.Empty):  # the wait window is 1 s: no response may follow the cancellation
            self.lines.get(timeout=2)

    def test_bad_cancel_notification_and_meta_return_without_killing_server(self):
        self.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': ['bad shape']})
        self.send({'jsonrpc': '2.0', 'id': 70, 'method': 'tools/call',
                   'params': {'name': 'await_task', 'arguments': {'request_key': 'malformed', 'claim_key': 'w1'},
                              '_meta': ['bad shape']}})
        error = self.reply(2)
        self.assertEqual(error['id'], 70)
        self.assertIn('error', error)
        self.send({'jsonrpc': '2.0', 'id': 71, 'method': 'ping'})
        self.assertEqual(self.reply(2), {'jsonrpc': '2.0', 'id': 71, 'result': {}})
        store = TaskStore(self.state, self.tmp / 'project')
        self.addCleanup(store.close)
        self.assertEqual(store.reserve('malformed', 'w1')['state'], 'absent')

    def test_duplicate_inflight_id_is_rejected_without_disturbing_first_wait(self):
        self.tool(80, 'await_task', {'request_key': 'first', 'claim_key': 'w1', 'wait_seconds': 2})
        self.tool(80, 'await_task', {'request_key': 'second', 'claim_key': 'w1', 'wait_seconds': 2})
        self.send({'jsonrpc': '2.0', 'id': 81, 'method': 'ping'})
        duplicate, ping = self.reply(2), self.reply(2)
        self.assertEqual(duplicate['id'], None)
        self.assertEqual(duplicate['error']['code'], -32600)
        self.assertEqual(ping, {'jsonrpc': '2.0', 'id': 81, 'result': {}})
        store = TaskStore(self.state, self.tmp / 'project')
        self.addCleanup(store.close)
        store.admit('first', 'first prompt')
        delivered = self.reply(3)
        self.assertEqual(delivered['id'], 80)
        task = json.loads(delivered['result']['content'][0]['text'])
        self.assertEqual((task['request_key'], task['prompt']), ('first', 'first prompt'))
        self.assertEqual(store.reserve('second', 'w1')['state'], 'absent')

    def test_ping_reusing_await_id_is_rejected_but_distinct_ping_and_cancel_work(self):
        self.tool(90, 'await_task', {'request_key': 'waiting', 'claim_key': 'w1', 'wait_seconds': 2})
        self.send({'jsonrpc': '2.0', 'id': 90, 'method': 'ping'})
        self.send({'jsonrpc': '2.0', 'id': 91, 'method': 'ping'})
        duplicate, distinct = self.reply(2), self.reply(2)
        self.assertEqual((duplicate['id'], duplicate['error']['code']), (None, -32600))
        self.assertEqual(distinct, {'jsonrpc': '2.0', 'id': 91, 'result': {}})

        self.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 90}})
        self.send({'jsonrpc': '2.0', 'id': 92, 'method': 'ping'})
        self.assertEqual(self.reply(2), {'jsonrpc': '2.0', 'id': 92, 'result': {}})
        with self.assertRaises(queue.Empty):
            self.lines.get(timeout=2)

    def test_active_wait_limit_refuses_extra_request_and_releases_capacity(self):
        self.send({'jsonrpc': '2.0', 'id': 79, 'method': 'ping'})
        self.assertEqual(self.reply(2)['id'], 79)  # server is ready before arming the workers
        for ident in range(100, 100 + MAX_ACTIVE_AWAITS):
            self.tool(ident, 'await_task', {'request_key': f'idle-{ident}', 'claim_key': 'w1', 'wait_seconds': 2})
        self.tool(200, 'await_task', {'request_key': 'overflow', 'claim_key': 'w1', 'wait_seconds': 2})
        self.send({'jsonrpc': '2.0', 'id': 201, 'method': 'ping'})
        early = [self.reply(2) for _ in range(2)]
        overflow = next(item for item in early if item['id'] == 200)
        self.assertEqual(overflow['error']['code'], -32000)
        self.assertEqual(next(item for item in early if item['id'] == 201)['result'], {})
        # All admitted waits time out; their completion releases slots for later calls.
        idle_responses = [self.reply(4) for _ in range(MAX_ACTIVE_AWAITS)]
        self.assertEqual({item['id'] for item in idle_responses}, set(range(100, 100 + MAX_ACTIVE_AWAITS)))
        self.tool(202, 'await_task', {'request_key': 'after-release', 'claim_key': 'w1', 'wait_seconds': 1})
        released = self.reply(3)
        self.assertEqual(released['id'], 202)
        self.assertTrue(json.loads(released['result']['content'][0]['text'])['idle'])

    def test_eof_cancels_and_joins_all_active_waiters(self):
        for ident in range(300, 300 + MAX_ACTIVE_AWAITS):
            self.tool(ident, 'await_task', {'request_key': f'eof-{ident}', 'claim_key': 'w1', 'wait_seconds': 2})
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=5), 0)
        store = TaskStore(self.state, self.tmp / 'project')
        self.addCleanup(store.close)
        for ident in range(300, 300 + MAX_ACTIVE_AWAITS):
            self.assertEqual(store.reserve(f'eof-{ident}', 'w1')['state'], 'absent')

    def test_server_started_without_the_flag_refuses_await_task(self):
        self.process.stdin.close()
        self.process.wait(timeout=5)
        plain = subprocess.Popen(
            [sys.executable, str(ROOT / 'claude_mcp.py'), '--project-root', str(self.tmp / 'project'),
             '--state', str(self.tmp / 'plain'), '--expert-state', str(self.tmp / 'plain-expert')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=ROOT, text=True)
        self.addCleanup(plain.stdout.close)
        self.addCleanup(plain.wait)
        plain.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                      'params': {'name': 'await_task', 'arguments': {'request_key': 'k', 'claim_key': 'w'}}}) + '\n')
        plain.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}) + '\n')
        plain.stdin.flush()
        plain.stdin.close()
        refused, listing = json.loads(plain.stdout.readline()), json.loads(plain.stdout.readline())
        self.assertIn('error', refused)
        self.assertNotIn('await_task', {item['name'] for item in listing['result']['tools']})

    def test_eof_while_waiting_stops_the_waiter_and_reserves_nothing(self):
        self.tool(9, 'await_task', {'request_key': 'never', 'claim_key': 'w1', 'wait_seconds': 2})
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=6), 0)
        store = TaskStore(self.state, self.tmp / 'project')
        self.addCleanup(store.close)
        self.assertEqual(store.reserve('never', 'w1'), {'state': 'absent'})

    def test_bad_arguments_get_an_error_not_a_hang(self):
        self.tool(5, 'await_task', {'claim_key': 'w', 'wait_seconds': 1})
        self.assertIn('error', self.reply(2))
        self.tool(6, 'await_task', {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 99})
        self.assertIn('error', self.reply(2))


if __name__ == '__main__':
    unittest.main()
