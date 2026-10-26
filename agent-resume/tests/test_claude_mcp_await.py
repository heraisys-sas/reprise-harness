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
from unittest.mock import Mock

from claude_mcp import ClaudeMCP
from desktop_task_store import TaskStore

ROOT = Path(__file__).resolve().parents[1]


def call(facade, name, args, **extra):
    params = dict({'name': name, 'arguments': args}, **extra)
    result = facade.dispatch({'method': 'tools/call', 'params': params})
    return json.loads(result['content'][0]['text']) if result else None


def content(result):
    return json.loads(result['content'][0]['text'])


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        self.store = TaskStore(self.tmp, self.tmp)
        self.addCleanup(self.store.close)

    def row(self, key):
        return self.store.db.execute('SELECT claim_key,attempt_id,delivery,answer FROM desktop_tasks WHERE request_key=?', (key,)).fetchone()


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

    def test_get_task_shares_the_delivery_marker_and_keeps_its_identical_replay(self):
        self.store.admit('k', 'p')
        first = self.store.claim('k', 'w1')
        self.assertEqual(self.store.claim('k', 'w1'), first)  # explicit recovery stays byte-identical
        self.assertEqual(self.row('k')[2], 'attempted')
        receipt = self.store.reserve('k', 'w1')
        self.assertEqual((receipt['state'], 'prompt' in receipt), ('attempted', False))

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
        self.facade = ClaudeMCP(self.store, Mock(), max_wait=3, poll=0.02, progress_interval=0.05)

    def await_(self, **args):
        return call(self.facade, 'await_task', dict({'request_key': 'k', 'claim_key': 'w1'}, **args))

    def test_listed_with_exact_key_schema_and_no_loop_instruction(self):
        tool = next(item for item in self.facade.dispatch({'method': 'tools/list'})['tools'] if item['name'] == 'await_task')
        self.assertEqual(tool['inputSchema']['required'], ['request_key', 'claim_key'])
        self.assertIn('explicit project task', tool['description'])
        self.assertIn('stop unless the operator asked', tool['description'])

    def test_exact_task_is_handed_over_immediately_and_only_that_one(self):
        self.store.admit('older', 'not me')
        self.store.admit('k', 'do it')
        started = time.monotonic()
        result = self.await_()
        self.assertLess(time.monotonic() - started, 1)
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
        self.assertNotIn('prompt', again)
        self.assertNotIn('secret prompt', json.dumps(again))

    def test_idle_instruction_never_asks_for_an_automatic_rearm(self):
        started = time.monotonic()
        result = self.await_(wait_seconds=1)
        elapsed = time.monotonic() - started
        self.assertTrue(0.9 <= elapsed < 2.5, elapsed)
        self.assertEqual((result['idle'], result['task']), (True, False))
        self.assertIn('Do not call await_task again unless the operator asked', result['instruction'])
        self.assertIsNone(self.row('k'))

    def test_task_admitted_during_the_wait_is_delivered(self):
        def admit_later():
            other = TaskStore(self.tmp, self.tmp)  # own connection in the timer thread
            try:
                other.admit('k', 'arrived')
            finally:
                other.close()
        threading.Timer(0.2, admit_later).start()
        result = self.await_(wait_seconds=3)
        self.assertEqual((result['request_key'], result['prompt']), ('k', 'arrived'))

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
            facade = ClaudeMCP(local, Mock(), max_wait=3, poll=0.01)
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

    def test_cancel_before_arrival_returns_nothing_and_creates_no_reservation(self):
        cancel = threading.Event()
        threading.Timer(0.1, cancel.set).start()
        started = time.monotonic()
        self.assertIsNone(self.facade.await_task(self.store, {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 3}, cancel=cancel))
        self.assertLess(time.monotonic() - started, 1.5)
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

    def test_cancel_after_the_intention_keeps_the_task_uncertain(self):
        self.store.admit('k', 'p')
        attempt = self.store.reserve('k', 'w1')['attempt_id']
        self.store.mark_attempted('k', attempt)
        cancel = threading.Event()
        cancel.set()
        receipt = content(self.facade.await_task(self.store, {'request_key': 'k', 'claim_key': 'w1'}, cancel=cancel))
        self.assertEqual(receipt['delivery'], 'attempted')
        self.assertEqual(self.row('k')[2], 'attempted')

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

    def test_progress_notifications_are_sent_only_with_a_token(self):
        sent = []
        self.facade.await_task(self.store, {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 1}, token='tok', emit=sent.append)
        self.assertGreaterEqual(len(sent), 2)
        self.assertEqual([item['params']['progress'] for item in sent][:2], [1, 2])
        silent = []
        self.facade.await_task(self.store, {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 1}, token=None, emit=silent.append)
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


class StdioProcessTests(unittest.TestCase):
    """The real server over pipes: waiting never blocks ping, cancel is silent, delivery happens once."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        (self.tmp / 'project').mkdir()
        self.state = self.tmp / 'state'
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / 'claude_mcp.py'), '--project-root', str(self.tmp / 'project'),
             '--state', str(self.state), '--expert-state', str(self.tmp / 'expert'), '--max-wait', '2'],
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
        self.assertEqual(self.reply(1), {'jsonrpc': '2.0', 'id': 2, 'result': {}})
        store = TaskStore(self.state, self.tmp / 'project')
        self.addCleanup(store.close)
        store.admit('job-0', 'not this one')
        store.admit('job-1', 'sort 7,2,5', chat_id='chat-9')
        delivered = self.reply(3)
        self.assertEqual(delivered['id'], 1)
        task = json.loads(delivered['result']['content'][0]['text'])
        self.assertEqual((task['request_key'], task['prompt'], task['chat_id']), ('job-1', 'sort 7,2,5', 'chat-9'))
        self.tool(3, 'await_task', {'request_key': 'job-1', 'claim_key': 'w1', 'wait_seconds': 1})
        replay = json.loads(self.reply(3)['result']['content'][0]['text'])
        self.assertEqual((replay['delivery'], 'prompt' in replay), ('attempted', False))
        self.tool(4, 'submit_result', {'request_key': 'job-1', 'attempt_id': task['attempt_id'], 'answer': '2,5,7'})
        self.assertTrue(json.loads(self.reply()['result']['content'][0]['text'])['completed'])

    def test_cancelled_wait_sends_no_response_and_keeps_no_reservation(self):
        self.tool(7, 'await_task', {'request_key': 'later', 'claim_key': 'w1', 'wait_seconds': 2})
        time.sleep(0.2)
        self.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 7}})
        time.sleep(2.6)
        self.send({'jsonrpc': '2.0', 'id': 8, 'method': 'ping'})
        self.assertEqual(self.reply(2)['id'], 8)

    def test_bad_arguments_get_an_error_not_a_hang(self):
        self.tool(5, 'await_task', {'claim_key': 'w', 'wait_seconds': 1})
        self.assertIn('error', self.reply(2))
        self.tool(6, 'await_task', {'request_key': 'k', 'claim_key': 'w', 'wait_seconds': 99})
        self.assertIn('error', self.reply(2))


if __name__ == '__main__':
    unittest.main()
