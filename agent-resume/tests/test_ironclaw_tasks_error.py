"""Régression #37 : une erreur imprévue ne doit jamais devenir un état incertain muet.

Le correctif de #43 remplace `except Exception` par une liste fermée et persiste la
cause dans `last_error`. Ces tests vérifient les deux critères d'acceptation de #37 :

- un bug de programmation (AttributeError, KeyError) remonte au lieu d'être classé
  `ambiguous` ;
- une vraie erreur de transport (OSError) produit `ambiguous` **et** une cause
  persistée, avec un message resté actionnable pour l'opérateur.

Les chemins d'endpoint ne sont jamais lus : `Endpoint.validate()` vérifie seulement
qu'ils sont absolus.
"""
import sqlite3
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ironclaw_ssh import Endpoint
from ironclaw_tasks import Tasks, TaskError

ID_FILE = '/run/reprise/id'
TOKEN_FILE = '/run/reprise/token'


class DispatchErrorTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.endpoint = Endpoint('host.example', 'agent', 22, ID_FILE, TOKEN_FILE)

    def transport_failing_after_session(self, failure):
        """Accepte /session, puis échoue sur le premier POST (threads)."""
        calls = []

        def transport(endpoint, method, path, payload=None, **kwargs):
            calls.append((method, path))
            if path.endswith('/session'):
                return {'session_channel_extension_id': 'channel'}
            raise failure

        return transport, calls

    def stored(self):
        return self.db.execute(
            "SELECT state, last_error FROM ironclaw_tasks WHERE request_key='key'").fetchone()

    def submit(self, transport):
        return Tasks(self.db, self.endpoint, transport).submit('key', 'question', allow_submit=True)

    def test_unexpected_bug_propagates_instead_of_becoming_ambiguous(self):
        transport, calls = self.transport_failing_after_session(AttributeError('bug local'))
        with self.assertRaises(AttributeError):
            self.submit(transport)
        self.assertIn(('POST', '/api/webchat/v2/threads'), calls)
        state, last_error = self.stored()
        self.assertNotEqual(state, 'ambiguous')
        self.assertNotEqual(state, 'prepared')
        self.assertIsNone(last_error)

    def test_transport_failure_is_ambiguous_and_records_cause(self):
        transport, calls = self.transport_failing_after_session(OSError('connexion perdue'))
        with self.assertRaises(TaskError) as raised:
            self.submit(transport)
        self.assertIn(('POST', '/api/webchat/v2/threads'), calls)
        state, last_error = self.stored()
        self.assertEqual(state, 'ambiguous')
        self.assertIn('OSError', last_error)
        self.assertIn('connexion perdue', last_error)
        self.assertIn('inspect remotely', str(raised.exception))

    def test_unexpected_bug_never_writes_last_error(self):
        transport, _ = self.transport_failing_after_session(KeyError('ack_json'))
        with self.assertRaises(KeyError):
            self.submit(transport)
        state, last_error = self.stored()
        self.assertNotEqual(state, 'ambiguous')
        self.assertIsNone(last_error)


if __name__ == '__main__':
    unittest.main()
