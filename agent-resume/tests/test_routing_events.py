import sqlite3
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import routing_events


def memory_db():
    db = sqlite3.connect(':memory:')
    routing_events.setup(db)
    return db


class RoutingEventsTests(unittest.TestCase):
    def test_routed_event(self):
        db = memory_db()
        event_id = routing_events.record(db, 'routed', 'reprise_delegate', 'codex',
                                         served={'model': 'gpt-6-sol', 'effort': 'medium'},
                                         level='standard', request_key='rk-1', thread_id='sess-123')
        row = routing_events.recent(db)[0]
        self.assertEqual(row['type'], 'routed')
        self.assertIsNone(row['requested_route'])
        self.assertIn('gpt-6-sol', row['served_route'])
        self.assertEqual(row['terminal_state'], 'dispatched')
        routing_events.finalize(db, event_id, 'finished')
        self.assertEqual(routing_events.recent(db)[0]['terminal_state'], 'finished')

    def test_explicit_event(self):
        db = memory_db()
        routing_events.record(db, 'explicit', 'reprise_delegate', 'codex',
                              requested={'model': 'gpt-6-astra'}, served={'model': 'gpt-6-astra', 'effort': 'high'},
                              level='deep')
        row = routing_events.recent(db)[0]
        self.assertEqual(row['type'], 'explicit')
        self.assertIn('gpt-6-astra', row['requested_route'])

    def test_routing_substituted_type_accepted_and_thread_pseudonymized(self):
        db = memory_db()
        # La substitution est gelée : le type est journalisable par le schéma,
        # mais aucun code de production ne le produit aujourd'hui.
        routing_events.record(db, 'routing_substituted', 'reprise_delegate', 'codex',
                              requested={'model': 'gpt-6-astra'}, served={'model': 'gpt-6-sol'},
                              thread_id='01a1418b-f417-7be0-b62b-25c63629a6f2', loop_signature='ls-abc')
        row = routing_events.recent(db)[0]
        self.assertEqual(row['type'], 'routing_substituted')
        self.assertEqual(len(row['thread_hash']), 16)
        self.assertNotIn('01a1418b', row['thread_hash'])
        self.assertEqual(row['loop_signature'], 'ls-abc')

    def test_rejects_unknown_type_and_non_dict_route(self):
        db = memory_db()
        with self.assertRaises(ValueError):
            routing_events.record(db, 'invented', 'tool', 'codex')
        with self.assertRaises(ValueError):
            routing_events.record(db, 'routed', 'tool', 'codex', requested='flash')

    def test_no_prompt_content_stored(self):
        db = memory_db()
        routing_events.record(db, 'routed', 'reprise_delegate', 'claude-desktop',
                              served={'backend': 'claude-desktop'}, reason='Default route, no model requested')
        payload = str(routing_events.recent(db)[0])
        self.assertNotIn('/Users/', payload)


if __name__ == '__main__':
    unittest.main()
