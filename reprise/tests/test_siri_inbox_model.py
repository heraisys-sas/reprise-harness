"""Pure inbox contract; no runtime, Keychain or user state."""
import copy
from pathlib import Path
import sys
import unittest
import uuid

sys.path.insert(0, str(Path(__file__).parents[1]))
import siri_inbox_model as inbox


def uid():
    return str(uuid.uuid4())


class InboxModelTests(unittest.TestCase):
    def test_same_identity_is_idempotent_and_conflicts_do_not_change_state(self):
        ident = uid()
        state, result = inbox.put(inbox.empty(), ident, 'Une demande', now=42)
        original = copy.deepcopy(state)
        self.assertEqual(result['status'], 'pending')
        again, repeated = inbox.put(state, ident, 'Une demande', now=43)
        self.assertEqual(again, original)
        self.assertEqual(repeated['status'], 'pending')
        with self.assertRaises(inbox.Conflict):
            inbox.put(state, ident, 'Un autre texte', now=44)
        self.assertEqual(state, original)

    def test_dismiss_keeps_identity_without_resurrection(self):
        ident = uid()
        state, _ = inbox.put(inbox.empty(), ident, 'Texte privé', now=1)
        state, result = inbox.dismiss(state, ident, now=2)
        self.assertEqual(result['status'], 'dismissed')
        self.assertNotIn('Texte privé', repr(state))
        state, repeated = inbox.put(state, ident, 'Texte privé', now=3)
        self.assertEqual(repeated['status'], 'dismissed')
        with self.assertRaises(inbox.NotFound):
            inbox.read(state, ident)
        unchanged, _ = inbox.dismiss(state, ident, now=4)
        self.assertEqual(unchanged, state)
        with self.assertRaises(inbox.Conflict):
            inbox.put(state, ident, 'modifié', now=5)

    def test_list_never_returns_text_or_digest(self):
        state, _ = inbox.put(inbox.empty(), uid(), 'mot secret', now=1)
        page = inbox.list_entries(state)
        self.assertEqual(len(page), 1)
        self.assertEqual(set(page[0]), {'id', 'status', 'created_at', 'dismissed_at'})
        self.assertNotIn('mot secret', repr(page))

    def test_strict_text_identity_and_corrupt_state(self):
        for text in (' ', '\x00secret', 'a\r', 'é' * 513):
            with self.subTest(text=text), self.assertRaises(ValueError):
                inbox.put(inbox.empty(), uid(), text, now=1)
        with self.assertRaises(ValueError):
            inbox.put(inbox.empty(), uid().upper(), 'a', now=1)
        state, _ = inbox.put(inbox.empty(), uid(), 'texte', now=1)
        state['entries'][0]['digest'] = 'x' * 64
        with self.assertRaises(ValueError):
            inbox.list_entries(state)

    def test_limits_refuse_without_evicting_and_dismiss_remains_possible(self):
        state = inbox.empty()
        for index in range(inbox.MAX_PENDING):
            state, _ = inbox.put(state, uid(), f'demande {index}', now=index)
        with self.assertRaises(inbox.CapacityExceeded):
            inbox.put(state, uid(), 'suivante', now=100)
        state, _ = inbox.dismiss(state, state['entries'][0]['id'], now=101)
        state, result = inbox.put(state, uid(), 'suivante', now=102)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(len(state['entries']), inbox.MAX_PENDING + 1)

    def test_timestamp_bounds_reject_without_numeric_overflow_or_mutation(self):
        state, _ = inbox.put(inbox.empty(), uid(), 'synthetic', now=1)
        original = copy.deepcopy(state)
        for timestamp in (-1, 10**1000, -(10**1000), float('inf'), float('-inf'),
                          float('nan'), True, inbox.MAX_TIMESTAMP + 1):
            with self.subTest(timestamp=repr(timestamp)[:40]):
                with self.assertRaises(ValueError):
                    inbox.put(state, uid(), 'new', now=timestamp)
                with self.assertRaises(ValueError):
                    inbox.dismiss(state, state['entries'][0]['id'], now=timestamp)
                altered = copy.deepcopy(state)
                altered['entries'][0]['created_at'] = timestamp
                with self.assertRaises(ValueError):
                    inbox.validate(altered)
                self.assertEqual(state, original)
        for timestamp in (0, 0.5, inbox.MAX_TIMESTAMP):
            inbox.validate(inbox.put(inbox.empty(), uid(), 'boundary', now=timestamp)[0])
