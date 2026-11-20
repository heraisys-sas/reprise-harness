import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import Bridge
from delegation_notifications import KINDS, Queue, install, record_event
from delegation_store import Store
from support_clock import FakeClock

THREAD = '12345678-1234-1234-1234-123456789abc'
TURN = 'turn-1'


class StoreNotificationTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.store = Store(self.db, 'project')
        self.store.begin('key', {'question': 'A'})
        self.store.attach('key', {'phase': 'active', 'thread_id': THREAD, 'turn_id': TURN})

    def events(self):
        return self.db.execute('SELECT event_id, kind, attempt_id, thread_id, turn_id FROM notification_events ORDER BY event_id').fetchall()

    def test_finish_emits_events_inside_same_transaction(self):
        self.store.finish('key', {'status': 'finished', 'phase': 'finished'})
        events = self.events()
        self.assertEqual([event[1] for event in events], ['turn_terminal'])
        self.assertTrue(events[0][2])  # attempt id set by attach
        self.assertEqual(events[0][3], THREAD)
        self.assertEqual(events[0][4], TURN)
        # Joint rollback: a failure while recording the event must undo the
        # receipt update too — emission lives in finish()'s `with self.db:` block.
        with patch('delegation_store.delegation_notifications.record_event', side_effect=ValueError('boom')):
            with self.assertRaises(ValueError):
                self.store.finish('key', {'status': 'finished', 'phase': 'finished', 'response': 'changed'})
        state, = self.db.execute('SELECT state FROM delegation_requests WHERE scope=? AND request_key=?', ('project', 'key')).fetchone()
        self.assertEqual(state, 'recorded')
        result = json.loads(self.db.execute('SELECT result FROM delegation_requests WHERE scope=? AND request_key=?', ('project', 'key')).fetchone()[0])
        self.assertNotIn('response', result)
        self.assertEqual(len(self.events()), 1)

    def test_event_id_dedup_on_idempotent_refinish(self):
        result = {'status': 'finished', 'phase': 'finished'}
        self.store.finish('key', result)
        self.store.finish('key', dict(result))
        self.assertEqual(len(self.events()), 1)
        self.store.finish('key', dict(result, response='different'))
        self.assertEqual(len(self.events()), 2)

    def test_kind_mapping_requires_proven_success(self):
        self.store.finish('key', {'status': 'finished', 'phase': 'finished'})
        self.assertEqual(sorted(kind for _, kind, *_ in self.events()), ['turn_terminal'])
        # A distinct result digest gets its own deterministic event id: reset
        # between cases to observe the kind mapping alone.
        self.db.execute('DELETE FROM notification_events')
        self.store.finish('key', {'status': 'interrupted', 'phase': 'active', 'terminal_evidence': 'sigquit'})
        kinds = sorted(kind for _, kind, *_ in self.events())
        self.assertEqual(kinds, ['attention_required', 'turn_terminal'])
        self.db.execute('DELETE FROM notification_events')
        self.store.finish('key', {'status': 'quota_blocked', 'phase': 'active'})
        # quota_blocked is still a `recorded` terminal state: terminated but
        # never a proven success.
        self.assertEqual(sorted(kind for _, kind, *_ in self.events()),
                         ['attention_required', 'turn_terminal'])
        self.db.execute('DELETE FROM notification_events')
        self.store.finish('key', {'status': 'blocked_permission', 'phase': 'active'})
        self.assertEqual([kind for _, kind, *_ in self.events()], ['attention_required'])
        self.db.execute('DELETE FROM notification_events')
        self.store.finish('key', {'status': 'crashed_unknown', 'phase': 'active'})
        self.assertEqual([kind for _, kind, *_ in self.events()], ['attention_required'])
        self.db.execute('DELETE FROM notification_events')
        self.store.finish('key', {'status': 'running', 'phase': 'active'})
        self.assertEqual(self.events(), [])

    def test_turn_terminal_without_turn_row(self):
        store = Store(self.db, 'other')
        store.begin('bare', {'question': 'B'})
        store.finish('bare', {'status': 'finished', 'phase': 'finished'})
        event_id, kind, attempt_id, thread_id, turn_id = self.db.execute(
            'SELECT event_id, kind, attempt_id, thread_id, turn_id FROM notification_events WHERE scope=?', ('other',)).fetchone()
        self.assertEqual(kind, 'turn_terminal')
        self.assertIsNone(attempt_id)
        self.assertIsNone(thread_id)
        self.assertIsNone(turn_id)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        install(self.db)
        self.queue = Queue(self.db, 'project')
        for kind in KINDS:
            record_event(self.db, 'project', 'key', kind, now=1000.0, event_id=f'{kind}:key:x')

    def test_read_and_wait_never_consume(self):
        first = self.queue.read('consumer-a')
        self.assertEqual(len(first), 2)
        self.assertEqual(self.queue.read('consumer-a'), first)
        waited = self.queue.wait('consumer-a', timeout_ms=50)
        self.assertEqual(waited['status'], 'notified')
        self.assertEqual(waited['events'], first)
        self.assertEqual(self.queue.read('consumer-a'), first)
        self.db.execute('SELECT COUNT(*) FROM notification_events')

    def test_wait_timeout_returns_empty(self):
        # Events are never consumed, so a timeout requires an empty view for
        # this consumer: ack everything first.
        ids = [event['event_id'] for event in self.queue.read('done')]
        self.queue.ack('done', ids)
        self.assertEqual(self.queue.wait('done', timeout_ms=30),
                         {'status': 'timeout', 'events': []})

    def test_ack_idempotent_per_consumer_and_scope_isolation(self):
        ids = [event['event_id'] for event in self.queue.read('consumer-a')]
        self.assertEqual(len(ids), 2)
        self.assertEqual(self.queue.ack('consumer-a', ids),
                         {'status': 'acknowledged', 'event_ids': ids})
        self.assertEqual(self.queue.ack('consumer-a', ids),
                         {'status': 'acknowledged', 'event_ids': ids})
        self.assertEqual(self.queue.read('consumer-a'), [])
        other = Queue(self.db, 'other')
        record_event(self.db, 'other', 'key', 'turn_terminal', now=1001.0, event_id='turn_terminal:other:x')
        # Ack is per (event_id, consumer_id): another consumer still sees the
        # same project events (full retention, at-least-once).
        self.assertEqual(len(self.queue.read('consumer-b')), 2)
        self.assertEqual(len(other.read('consumer-b')), 1)
        self.assertEqual(self.queue.ack('consumer-a', ids)['event_ids'], ids)

    def test_ack_validation(self):
        with self.assertRaises(ValueError):
            self.queue.ack('', ['x'])
        with self.assertRaises(ValueError):
            self.queue.ack('c', [])
        with self.assertRaises(ValueError):
            self.queue.ack('c', ['x', ''])
        with self.assertRaises(ValueError):
            self.queue.read('')

    def test_reread_after_wake_at_least_once(self):
        # A wake is only an optimization: events are re-read from the queue,
        # even ones the consumer already saw (no global consumption mark).
        seen = self.queue.read('consumer-a')
        cancel = threading.Event()
        cancel.set()
        result = self.queue.wait('consumer-a', timeout_ms=1000, cancel=cancel)
        self.assertEqual(result['status'], 'notified')
        self.assertEqual(result['events'], seen)

    def test_unknown_kind_refused(self):
        with self.assertRaises(ValueError):
            record_event(self.db, 'project', 'key', 'redispatch_now', now=1.0, event_id='x')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM notification_events').fetchone()[0], 2)

    def test_request_keys_filter_narrows_only(self):
        record_event(self.db, 'project', 'other-key', 'turn_terminal', now=1002.0, event_id='turn_terminal:other-key:x')
        all_events = self.queue.read('consumer-a')
        self.assertEqual(len(all_events), 3)
        filtered = self.queue.read('consumer-a', request_keys=['other-key'])
        self.assertEqual([event['request_key'] for event in filtered], ['other-key'])
        with self.assertRaises(ValueError):
            self.queue.read('consumer-a', request_keys=[])
        with self.assertRaises(ValueError):
            self.queue.read('consumer-a', request_keys=[''])
        with self.assertRaises(ValueError):
            self.queue.read('consumer-a', request_keys=['a'] * 101)


class BridgeNotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'project'
        self.root.mkdir()
        (self.root/'rollout.jsonl').touch()
        self.state = Path(self.temp.name)/'state'
        self.bridge = Bridge(self.root, {}, self.state, daily_limit=0)
        self.addCleanup(self.bridge.close)
        self.bridge.requests.begin('key', {'question': 'A'})
        self.bridge.requests.finish('key', {'status': 'finished', 'phase': 'finished'})

    def notifications(self, args):
        return self.bridge.call('reprise_notifications', args)

    def test_read_wait_ack_roundtrip(self):
        events = self.notifications({'action': 'read', 'consumer_id': 'orchestrator'})
        self.assertEqual(len(events['events']), 1)
        self.assertEqual(events['events'][0]['kind'], 'turn_terminal')
        self.assertEqual(events['status'], 'read')
        waited = self.notifications({'action': 'wait', 'consumer_id': 'orchestrator', 'timeout_ms': 50})
        self.assertEqual(waited['status'], 'notified')
        # read/wait never consume: the event survives both calls.
        self.assertEqual(len(self.notifications({'action': 'read', 'consumer_id': 'orchestrator'})['events']), 1)
        acked = self.notifications({'action': 'ack', 'consumer_id': 'orchestrator',
                                    'event_ids': [events['events'][0]['event_id']]})
        self.assertEqual(acked['status'], 'acknowledged')
        self.assertEqual(self.notifications({'action': 'read', 'consumer_id': 'orchestrator'})['events'], [])
        # Another consumer still sees the event (acks are per consumer).
        self.assertEqual(len(self.notifications({'action': 'read', 'consumer_id': 'watcher'})['events']), 1)

    def test_wait_timeout_and_filters(self):
        timeout = self.notifications({'action': 'wait', 'consumer_id': 'orchestrator',
                                      'timeout_ms': 30, 'request_keys': ['missing']})
        self.assertEqual(timeout, {'status': 'timeout', 'events': []})
        hit = self.notifications({'action': 'wait', 'consumer_id': 'orchestrator',
                                  'timeout_ms': 50, 'request_keys': ['key']})
        self.assertEqual(hit['status'], 'notified')
        self.assertEqual(len(hit['events']), 1)
        limited = self.notifications({'action': 'read', 'consumer_id': 'orchestrator', 'limit': 1})
        self.assertEqual(len(limited['events']), 1)

    def test_invalid_arguments_refused(self):
        for args in ({}, {'action': 'purge', 'consumer_id': 'c'},
                     {'action': 'read', 'consumer_id': ''},
                     {'action': 'read', 'consumer_id': 'c', 'unexpected': 1},
                     {'action': 'ack', 'consumer_id': 'c'},
                     {'action': 'ack', 'consumer_id': 'c', 'event_ids': []},
                     {'action': 'wait', 'consumer_id': 'c', 'timeout_ms': -1},
                     {'action': 'wait', 'consumer_id': 'c', 'timeout_ms': 600001},
                     {'action': 'read', 'consumer_id': 'c', 'limit': 0},
                     {'action': 'read', 'consumer_id': 'c', 'request_keys': []}):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    self.notifications(args)


class ClockBehaviorTests(unittest.TestCase):
    def test_created_at_uses_store_clock(self):
        clock = FakeClock(2000.0)
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        store = Store(db, 'project', now=clock.now)
        store.begin('key', {'question': 'A'})
        store.finish('key', {'status': 'finished', 'phase': 'finished'})
        created_at, = db.execute('SELECT created_at FROM notification_events').fetchone()
        self.assertEqual(created_at, 2000.0)


if __name__ == '__main__':
    unittest.main()
