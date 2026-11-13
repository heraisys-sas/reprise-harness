import io
import json
from pathlib import Path
import queue
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock
import sys

sys.path.insert(0, str(Path(__file__).parents[1]))
import codex_expert_mcp as mcp
from claude_mcp import ClaudeMCP
from delegation_store import receipt_view
from desktop_task_store import TaskStore


def call(ident, name='wait_result', **arguments):
    return {'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments}}


def decode(content):
    return json.loads(content['content'][0]['text'])


class PureWaitTests(unittest.TestCase):
    def run_wait(self, snapshots, seconds=1, action=None):
        reader = Mock()
        reader.observe_result.side_effect = snapshots
        cancel = Mock()
        cancel.is_set.return_value = False
        clock = [0.0]
        pauses = []

        def pause(duration):
            pauses.append(duration)
            clock[0] += duration
            if action:
                action(cancel)

        cancel.wait.side_effect = pause
        with patch.object(TaskStore, 'open_reader', return_value=reader), \
             patch.object(mcp.time, 'monotonic', side_effect=lambda: clock[0]):
            result = mcp.wait_for_result('.', '.', {'request_key': 'k', 'wait_seconds': seconds}, cancel)
        reader.close.assert_called_once()
        return result, reader, pauses

    def test_immediate_result_uses_canonical_get_result_hash(self):
        snapshot = {'completed': True, 'result': {'answer': 'réponse'}}
        result, reader, pauses = self.run_wait([snapshot])
        self.assertEqual(result['result_sha256'], receipt_view(snapshot)[0]['result_sha256'])
        self.assertEqual((result['state'], result['completed'], result['idle'],
                          result['next_action']), ('recorded', True, False, 'get_result'))
        self.assertEqual(pauses, [])
        reader.observe_result.assert_called_once_with('k')

    def test_missing_is_immediate(self):
        result, _, pauses = self.run_wait([None])
        self.assertEqual(result['status'], 'not_found')
        self.assertEqual(pauses, [])

    def test_last_snapshot_at_deadline_wins(self):
        pending = {'completed': False}
        done = {'completed': True, 'result': {'answer': 'at deadline'}}
        result, reader, pauses = self.run_wait([pending, pending, pending, done])
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(pauses, [0.25, 0.5, 0.25])
        self.assertEqual(reader.observe_result.call_count, 4)

    def test_timeout_backoff_and_final_read(self):
        result, reader, pauses = self.run_wait([{'completed': False}] * 5, seconds=2)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual((result['state'], result['completed'], result['idle'],
                          result['next_action']), ('pending', False, True, 'reconcile'))
        self.assertEqual(result['waited_seconds'], 2)
        self.assertEqual(pauses, [0.25, 0.5, 1.0, 0.25])
        self.assertEqual(reader.observe_result.call_count, 5)

    def test_cancel_releases_reader_without_final_result(self):
        def cancel(event):
            event.is_set.return_value = True
        result, reader, pauses = self.run_wait([{'completed': False}], action=cancel)
        self.assertIsNone(result)
        self.assertEqual(reader.observe_result.call_count, 1)

    def test_storage_error_is_distinct_and_closes_reader(self):
        reader = Mock()
        reader.observe_result.side_effect = sqlite3.OperationalError('locked')
        with patch.object(TaskStore, 'open_reader', return_value=reader):
            with self.assertRaises(mcp.DesktopRegistryUnavailable):
                mcp.wait_for_result('.', '.', {'request_key': 'k'})
        reader.close.assert_called_once()
        error = mcp.error_payload(mcp.DesktopRegistryUnavailable())
        self.assertEqual(error['data']['kind'], 'desktop_registry_unavailable')

    def test_invalid_arguments_and_default_ceiling(self):
        self.assertEqual(mcp.wait_arguments({'request_key': 'k'}), ('k', 600))
        self.assertEqual(mcp.wait_arguments({'request_key': 'k', 'wait_seconds': 600}), ('k', 600))
        cases = [None, {}, {'request_key': ''}, {'request_key': ' '},
                 {'request_key': 'k' * 129}, {'request_key': 1},
                 {'request_key': 'k', 'claim_key': 'c'}]
        cases += [{'request_key': 'k', 'wait_seconds': value} for value in
                  (None, True, False, 0, -1, 600.001, float('nan'), float('inf'), '1')]
        with patch.object(TaskStore, 'open_reader') as opener:
            for args in cases:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    mcp.wait_for_result('.', '.', args)
            opener.assert_not_called()


    def test_reader_factory_never_initializes_state(self):
        connection = Mock()
        with patch('desktop_task_store.sqlite3.connect', return_value=connection) as connect, \
             patch.object(Path, 'mkdir') as mkdir:
            reader = TaskStore.open_reader('.', '.')
        self.assertTrue(connect.call_args.args[0].endswith('/quick.sqlite?mode=ro'))
        self.assertEqual(connect.call_args.kwargs, {'timeout': 0, 'isolation_level': None, 'uri': True})
        mkdir.assert_not_called()
        connection.execute.assert_not_called()
        reader.close()

    def test_late_lookup_projection_and_provenance_without_writes(self):
        bridge = mcp.Bridge.__new__(mcp.Bridge)
        bridge.requests = Mock()
        historic = {'status': 'finished', 'request_key': 'k', 'state': 'recorded',
                    'attempt_id': 'delegation-attempt', 'acknowledged': False,
                    'result': {'backend': 'claude-desktop', 'admission_only': True,
                               'status': 'finished', 'response': None}}
        bridge.requests.lookup.return_value = historic
        snapshot = {'status': 'finished', 'request_key': 'k', 'state': 'recorded',
                    'completed': True, 'acknowledged': False, 'result': {'answer': 'late'}}
        bridge._task_snapshot = Mock(return_value=snapshot)
        full = bridge._lookup_requests({'request_key': 'k'})
        self.assertEqual(full['delegation_admission'], historic)
        self.assertEqual(full['response'], 'late')
        receipt = bridge._lookup_requests({'request_key': 'k', 'view': 'receipt'})
        self.assertEqual(receipt['result_sha256'], receipt_view(snapshot)[0]['result_sha256'])
        page = bridge._lookup_requests({'request_key': 'k', 'view': 'page'})
        self.assertEqual(json.loads(page['text']), {'answer': 'late'})
        bridge.requests.finish.assert_not_called()
        bridge.requests.begin.assert_not_called()

    def test_integration_allowlist_and_transport_budget(self):
        import harness_integration as integration
        self.assertIn('wait_result', integration.OBSERVE)
        entries = integration._bridge_entries(Path('.'), '.', integration.OBSERVE,
                                             '.', 'test', Path('.'))
        config = entries[0]['config']
        self.assertEqual(config['toolCallTimeoutMs'], 630000)
        self.assertIn('wait_result', config['args'][-1])


class DesktopWaitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'desktop'
        self.store = TaskStore(self.state, self.root)
        self.addCleanup(self.store.close)

    def rows(self):
        return list(self.store.db.iterdump())

    def admit(self):
        self.store.admit('k', 'one prompt')
        return self.store.claim('k', 'owner')['attempt_id']

    def start_waits(self, count=1):
        ready = queue.Queue()
        results = queue.Queue()
        original = TaskStore.observe_result
        seen = set()
        lock = threading.Lock()

        def observe(store, key):
            result = original(store, key)
            with lock:
                ident = threading.get_ident()
                if ident not in seen:
                    seen.add(ident)
                    ready.put(ident)
            return result

        self.guard = patch.object(TaskStore, 'observe_result', observe)
        self.guard.start()
        self.addCleanup(self.guard.stop)
        threads = []
        cancels = []
        for _ in range(count):
            cancel = threading.Event()

            def run(event=cancel):
                try:
                    results.put(mcp.wait_for_result(self.state, self.root,
                        {'request_key': 'k', 'wait_seconds': 3}, event))
                except Exception as exc:
                    results.put(exc)

            thread = threading.Thread(target=run)
            thread.start()
            threads.append(thread)
            cancels.append(cancel)
            self.addCleanup(thread.join, 4)
            self.addCleanup(cancel.set)
        for _ in range(count):
            ready.get(timeout=2)
        return results, threads, cancels

    def test_recorded_result_has_no_mutation(self):
        attempt = self.admit()
        self.store.submit('k', attempt, 'answer')
        before = self.rows()
        result = mcp.wait_for_result(self.state, self.root, {'request_key': 'k'})
        self.assertEqual(result['result_sha256'], self.store.result('k')['result_sha256'])
        self.assertEqual(self.rows(), before)
        reader = TaskStore.open_reader(self.state, self.root)
        try:
            self.assertEqual(reader.db.isolation_level, None)
            self.assertFalse(reader.db.in_transaction)
            with self.assertRaises(sqlite3.OperationalError):
                reader.db.execute("UPDATE desktop_tasks SET acknowledged=1")
            self.assertEqual(reader.db.total_changes, 0)
        finally:
            reader.close()

    def test_late_result_and_two_observers_do_not_redeliver(self):
        attempt = self.admit()
        before = self.rows()
        results, threads, _ = self.start_waits(2)
        self.assertEqual(self.rows(), before)
        self.store.submit('k', attempt, 'shared answer')
        after = self.rows()
        answers = [results.get(timeout=2) for _ in threads]
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual([item['status'] for item in answers], ['finished', 'finished'])
        self.assertEqual(answers[0]['result_sha256'], answers[1]['result_sha256'])
        self.assertEqual(self.rows(), after)
        replay = self.store.claim('k', 'owner')
        self.assertNotIn('prompt', replay)
        self.assertEqual(replay['attempt_id'], attempt)

    def test_timeout_then_submit_is_recoverable_after_reconnection(self):
        attempt = self.admit()
        before = self.rows()
        result = mcp.wait_for_result(self.state, self.root,
                                    {'request_key': 'k', 'wait_seconds': 0.01})
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(self.rows(), before)
        self.store.submit('k', attempt, 'after timeout')
        result = mcp.wait_for_result(self.state, self.root, {'request_key': 'k'})
        self.assertEqual(result['status'], 'finished')

    def test_await_task_delivery_then_wait_result_response(self):
        self.store.admit('k', 'one prompt')
        results, threads, _ = self.start_waits()
        facade = ClaudeMCP(self.store, Mock(), enable_await=True)
        delivered = decode(facade.await_task(self.store,
            {'request_key': 'k', 'claim_key': 'owner', 'wait_seconds': 1}))
        self.assertTrue(delivered['task'])
        self.assertEqual(delivered['delivery'], 'handed_over')
        self.assertTrue(results.empty())
        replay = decode(facade.await_task(self.store,
            {'request_key': 'k', 'claim_key': 'owner', 'wait_seconds': 1}))
        self.assertFalse(replay['task'])
        self.assertEqual(replay['delivery'], 'attempted')
        self.assertEqual(replay['attempt_id'], delivered['attempt_id'])
        self.store.submit('k', delivered['attempt_id'], 'only submit')
        self.assertEqual(results.get(timeout=2)['status'], 'finished')
        threads[0].join(2)

    def test_bad_attempt_never_yields_false_success(self):
        self.admit()
        before = self.rows()
        with self.assertRaises(ValueError):
            self.store.submit('k', 'wrong', 'not accepted')
        result = mcp.wait_for_result(self.state, self.root,
                                    {'request_key': 'k', 'wait_seconds': 0.01})
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(self.rows(), before)

    def test_cancellation_then_restart_preserves_state(self):
        attempt = self.admit()
        before = self.rows()
        results, threads, cancels = self.start_waits()
        cancels[0].set()
        self.assertIsNone(results.get(timeout=2))
        threads[0].join(2)
        self.assertEqual(self.rows(), before)
        self.store.submit('k', attempt, 'after restart')
        self.assertEqual(mcp.wait_for_result(self.state, self.root,
                         {'request_key': 'k'})['status'], 'finished')

    def test_missing_database_is_not_created_or_migrated(self):
        missing = self.root / 'missing'
        with self.assertRaises(mcp.DesktopRegistryUnavailable):
            mcp.wait_for_result(missing, self.root, {'request_key': 'k'})
        self.assertFalse(missing.exists())
        self.store.db.execute('CREATE TABLE sentinel(value TEXT)')
        self.store.db.commit()
        before = self.rows()
        self.assertEqual(mcp.wait_for_result(self.state, self.root,
                         {'request_key': 'missing'})['status'], 'not_found')
        self.assertEqual(self.rows(), before)

    def test_late_lookup_preserves_receipt_and_pagination_hash(self):
        bridge = mcp.Bridge(self.root, {}, self.root / 'expert', desktop_state=self.state)
        self.addCleanup(bridge.db.close)
        self.addCleanup(bridge.desktop_tasks.close)
        bridge.requests.begin('k', {'backend': 'claude-desktop'})
        bridge.requests.finish('k', {'status': 'finished', 'backend': 'claude-desktop',
                                    'admission_only': True, 'response': None})
        historic = bridge.requests.lookup({'request_key': 'k'})
        attempt = self.admit()
        self.store.submit('k', attempt, 'é' * 12000)
        receipt = bridge.call('reprise_requests', {'request_key': 'k', 'view': 'receipt'})
        self.assertEqual(receipt['source'], 'desktop_tasks')
        self.assertEqual(receipt['result_sha256'], self.store.result('k')['result_sha256'])
        self.assertTrue(receipt['delegation_receipt']['available'])
        text, offset = '', 0
        while True:
            page = bridge.call('reprise_requests', {'request_key': 'k', 'view': 'page',
                'offset': offset, 'max_chars': 1000, 'result_sha256': receipt['result_sha256']})
            text += page['text']
            if not page['has_more']:
                break
            offset = page['next_offset']
        self.assertEqual(json.loads(text), {'answer': 'é' * 12000})
        full = bridge.call('reprise_requests', {'request_key': 'k'})
        self.assertEqual(full['delegation_admission'], historic)
        self.assertEqual(full['response'], 'é' * 12000)
        with self.assertRaises(ValueError):
            bridge.call('reprise_requests', {'request_key': 'k', 'acknowledge': True})
        self.assertEqual(bridge.requests.lookup({'request_key': 'k'}), historic)
        bridge.requests.begin('other', {'backend': 'codex'})
        bridge.requests.finish('other', {'status': 'finished', 'backend': 'codex', 'response': 'native'})
        self.store.admit('other', 'unrelated')
        other = self.store.claim('other', 'owner')
        self.store.submit('other', other['attempt_id'], 'desktop')
        self.assertEqual(bridge.call('reprise_requests',
                         {'request_key': 'other'})['result']['response'], 'native')


class TransportTests(unittest.TestCase):
    class Input:
        def __init__(self):
            self.items = queue.Queue()

        def put(self, message):
            self.items.put(json.dumps(message).encode() + b'\n')

        def readline(self, maximum):
            return self.items.get(timeout=4)

    class Output:
        def __init__(self):
            self.items = queue.Queue()

        def write(self, text):
            self.items.put(json.loads(text))

        def flush(self):
            pass

    def test_saturation_cancellation_eof_and_ping(self):
        source, output = self.Input(), self.Output()
        entered, left = queue.Queue(), queue.Queue()
        bridge = Mock()
        bridge.allowed_tools = {'wait_result'}

        def wait(arguments, cancel):
            entered.put(arguments['request_key'])
            cancel.wait(3)
            left.put(arguments['request_key'])
            return None

        bridge.wait_result.side_effect = wait
        bridge.dispatch.return_value = {}
        server = threading.Thread(target=mcp.serve, args=(bridge, source, output))
        server.start()
        self.addCleanup(server.join, 3)
        self.addCleanup(source.items.put, b'')
        for i in range(8):
            source.put(call(i, request_key='k'))
            entered.get(timeout=2)
        source.put(call(8, request_key='k'))
        busy = output.items.get(timeout=2)
        self.assertEqual(decode(busy['result'])['status'], 'busy')
        self.assertFalse(busy['result']['isError'])
        source.put({'id': 'ping', 'method': 'ping'})
        self.assertEqual(output.items.get(timeout=2), {'jsonrpc': '2.0', 'id': 'ping', 'result': {}})
        source.put({'id': 0, 'method': 'ping'})
        duplicate = output.items.get(timeout=2)
        self.assertIsNone(duplicate['id'])
        self.assertEqual(duplicate['error']['code'], -32600)
        source.put({'method': 'notifications/cancelled', 'params': {'requestId': 0}})
        left.get(timeout=2)
        source.items.put(b'')
        server.join(2)
        self.assertFalse(server.is_alive())
        for _ in range(7):
            left.get(timeout=2)
        self.assertTrue(output.items.empty())


    def test_worker_start_failure_releases_slot_and_server_continues(self):
        bridge = Mock()
        bridge.allowed_tools = {'wait_result'}
        bridge.dispatch.return_value = {}
        source = io.BytesIO((json.dumps(call(1, request_key='k')) + '\n' +
                             json.dumps({'id': 2, 'method': 'ping'}) + '\n').encode())
        output = io.StringIO()
        worker = Mock()
        worker.start.side_effect = RuntimeError('cannot start')
        with patch.object(mcp.threading, 'Thread', return_value=worker):
            mcp.serve(bridge, source, output)
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertIn('error', replies[0])
        self.assertEqual(replies[1]['result'], {})
        worker.join.assert_not_called()

    def test_new_connection_and_worker_error_keep_ping_usable(self):
        for error in (False, True):
            with self.subTest(error=error):
                source, output = self.Input(), self.Output()
                bridge = Mock()
                bridge.allowed_tools = {'wait_result'}
                if error:
                    bridge.wait_result.side_effect = mcp.DesktopRegistryUnavailable()
                else:
                    bridge.wait_result.return_value = mcp.wait_status('k', 'finished',
                        state='recorded', result_sha256='a' * 64)
                bridge.dispatch.return_value = {}
                server = threading.Thread(target=mcp.serve, args=(bridge, source, output))
                server.start()
                source.put(call(1, request_key='k'))
                reply = output.items.get(timeout=2)
                if error:
                    self.assertEqual(reply['error']['data']['kind'], 'desktop_registry_unavailable')
                else:
                    self.assertEqual(decode(reply['result'])['status'], 'finished')
                source.put({'id': 2, 'method': 'ping'})
                self.assertEqual(output.items.get(timeout=2)['result'], {})
                source.items.put(b'')
                server.join(2)
                self.assertFalse(server.is_alive())


if __name__ == '__main__':
    unittest.main()
