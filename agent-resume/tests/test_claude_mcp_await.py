import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import queue
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


class ClaimNextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        self.store = TaskStore(self.tmp, self.tmp)
        self.addCleanup(self.store.close)

    def test_oldest_first_and_nothing_when_empty(self):
        self.assertIsNone(self.store.claim_next('w1'))
        self.store.admit('b', 'second')
        self.store.admit('a', 'third')
        first = self.store.claim_next('w1')
        self.assertEqual((first['request_key'], first['prompt'], first['resumed']), ('b', 'second', False))

    def test_same_claim_key_resumes_unfinished_task_with_same_attempt(self):
        self.store.admit('k', 'prompt', chat_id='chat-1')
        first = self.store.claim_next('w1')
        again = self.store.claim_next('w1')
        self.assertEqual((again['request_key'], again['attempt_id'], again['resumed'], again['chat_id']),
                         ('k', first['attempt_id'], True, 'chat-1'))
        self.store.submit('k', first['attempt_id'], 'done')
        self.assertIsNone(self.store.claim_next('w1'))

    def test_two_claim_keys_never_share_a_task(self):
        self.store.admit('only', 'p')
        self.assertEqual(self.store.claim_next('w1')['request_key'], 'only')
        self.assertIsNone(self.store.claim_next('w2'))
        self.store.admit('next', 'p2')
        self.assertEqual(self.store.claim_next('w2')['request_key'], 'next')

    def test_explicit_get_task_claim_is_respected(self):
        self.store.admit('k', 'p')
        self.store.claim('k', 'explicit-owner')
        self.assertIsNone(self.store.claim_next('other'))
        self.assertEqual(self.store.claim_next('explicit-owner')['request_key'], 'k')

    def test_invalid_claim_key_refused(self):
        for bad in ('', ' ', None, 'x' * 129):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.store.claim_next(bad)


class AwaitTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        self.store = TaskStore(self.tmp, self.tmp)
        self.addCleanup(self.store.close)
        self.facade = ClaudeMCP(self.store, Mock(), max_wait=3, poll=0.02, progress_interval=0.05)

    def test_listed_with_bounded_schema(self):
        tool = next(item for item in self.facade.dispatch({'method': 'tools/list'})['tools'] if item['name'] == 'await_task')
        self.assertEqual(tool['inputSchema']['required'], ['claim_key'])
        self.assertIn('Call it only in a chat the operator armed', tool['description'])

    def test_returns_immediately_when_a_task_is_waiting(self):
        self.store.admit('k', 'do it')
        started = time.monotonic()
        result = call(self.facade, 'await_task', {'claim_key': 'w1'})
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual((result['task'], result['request_key'], result['prompt']), (True, 'k', 'do it'))
        self.assertIn('submit_result', result['instruction'])

    def test_idle_after_the_bounded_wait(self):
        started = time.monotonic()
        result = call(self.facade, 'await_task', {'claim_key': 'w1', 'wait_seconds': 1})
        elapsed = time.monotonic() - started
        self.assertTrue(0.9 <= elapsed < 2.5, elapsed)
        self.assertEqual((result['idle'], result['task']), (True, False))
        self.assertIn('same claim_key', result['instruction'])

    def test_task_admitted_during_the_wait_is_delivered(self):
        def admit_later():
            other = TaskStore(self.tmp, self.tmp)  # own connection in the timer thread
            try:
                other.admit('late', 'arrived')
            finally:
                other.close()
        threading.Timer(0.2, admit_later).start()
        result = call(self.facade, 'await_task', {'claim_key': 'w1', 'wait_seconds': 3})
        self.assertEqual((result['request_key'], result['prompt']), ('late', 'arrived'))

    def test_arguments_are_strict(self):
        for args in ({}, {'claim_key': 'w', 'wait_seconds': 0}, {'claim_key': 'w', 'wait_seconds': 4},
                     {'claim_key': 'w', 'wait_seconds': True}, {'claim_key': 'w', 'wait_seconds': '2'},
                     {'claim_key': 'w', 'extra': 1}, {'claim_key': ''}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                call(self.facade, 'await_task', args)

    def test_invalid_progress_token_refused(self):
        with self.assertRaises(ValueError):
            call(self.facade, 'await_task', {'claim_key': 'w'}, _meta={'progressToken': {'a': 1}})

    def test_progress_notifications_are_sent_only_with_a_token(self):
        sent = []
        self.facade.await_task(self.store, {'claim_key': 'w', 'wait_seconds': 1}, token='tok', emit=sent.append)
        self.assertGreaterEqual(len(sent), 2)
        self.assertEqual([item['params']['progress'] for item in sent][:2], [1, 2])
        self.assertEqual({item['params']['progressToken'] for item in sent}, {'tok'})
        silent = []
        self.facade.await_task(self.store, {'claim_key': 'w', 'wait_seconds': 1}, token=None, emit=silent.append)
        self.assertEqual(silent, [])

    def test_cancel_ends_the_wait_without_a_result_and_without_claiming(self):
        cancel = threading.Event()
        threading.Timer(0.1, cancel.set).start()
        started = time.monotonic()
        self.assertIsNone(self.facade.await_task(self.store, {'claim_key': 'w', 'wait_seconds': 3}, cancel=cancel))
        self.assertLess(time.monotonic() - started, 1.5)
        self.store.admit('k', 'p')
        self.assertEqual(self.store.claim_next('w')['request_key'], 'k')

    def test_max_wait_is_bounded_at_construction(self):
        for bad in (0, 601, True, 1.5, '45'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ClaudeMCP(self.store, Mock(), max_wait=bad)


class StdioProcessTests(unittest.TestCase):
    """The real server over pipes: waiting never blocks ping, cancel is silent, delivery and retry work."""

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

    def test_ping_is_served_while_a_wait_is_pending_then_task_is_delivered_and_completed(self):
        self.tool(1, 'await_task', {'claim_key': 'w1', 'wait_seconds': 2})
        self.send({'jsonrpc': '2.0', 'id': 2, 'method': 'ping'})
        self.assertEqual(self.reply(1), {'jsonrpc': '2.0', 'id': 2, 'result': {}})
        store = TaskStore(self.state, self.tmp / 'project')
        self.addCleanup(store.close)
        store.admit('job-1', 'sort 7,2,5', chat_id='chat-9')
        delivered = self.reply(3)
        self.assertEqual(delivered['id'], 1)
        task = json.loads(delivered['result']['content'][0]['text'])
        self.assertEqual((task['request_key'], task['prompt'], task['chat_id']), ('job-1', 'sort 7,2,5', 'chat-9'))
        self.tool(3, 'submit_result', {'request_key': 'job-1', 'attempt_id': task['attempt_id'], 'answer': '2,5,7'})
        self.assertTrue(json.loads(self.reply()['result']['content'][0]['text'])['completed'])
        self.tool(4, 'await_task', {'claim_key': 'w1', 'wait_seconds': 1})
        idle = json.loads(self.reply(4)['result']['content'][0]['text'])
        self.assertTrue(idle['idle'])

    def test_cancelled_wait_sends_no_response(self):
        self.tool(7, 'await_task', {'claim_key': 'w1', 'wait_seconds': 2})
        time.sleep(0.2)
        self.send({'jsonrpc': '2.0', 'method': 'notifications/cancelled', 'params': {'requestId': 7}})
        time.sleep(2.6)
        self.send({'jsonrpc': '2.0', 'id': 8, 'method': 'ping'})
        self.assertEqual(self.reply(2)['id'], 8)

    def test_bad_arguments_get_an_error_not_a_hang(self):
        self.tool(5, 'await_task', {'wait_seconds': 1})
        self.assertIn('error', self.reply(2))
        self.tool(6, 'await_task', {'claim_key': 'w', 'wait_seconds': 99})
        self.assertIn('error', self.reply(2))


if __name__ == '__main__':
    unittest.main()
