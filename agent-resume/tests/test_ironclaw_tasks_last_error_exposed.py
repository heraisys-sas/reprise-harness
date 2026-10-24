"""Régression #37 : `last_error` doit être lisible par l'opérateur, pas seulement écrite.

`get()` sélectionnait `state,thread_id,run_id,result,acknowledged,identity,content`
sans `last_error` : la cause d'un dispatch `ambiguous` était bien persistée, mais
consultable seulement en ouvrant SQLite à la main. Ces tests verrouillent
l'exposition, sans quoi le critère « message actionnable » de #37 n'est rempli que
par le texte du `TaskError` levé au moment de l'échec — pas par l'état relu plus tard.
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


class LastErrorExposedTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.endpoint = Endpoint('host.example', 'agent', 22, ID_FILE, TOKEN_FILE)

    def failing_transport(self):
        def transport(endpoint, method, path, payload=None, **kwargs):
            if path.endswith('/session'):
                return {'session_channel_extension_id': 'channel'}
            raise OSError('connexion perdue')

        return transport

    def succeeding_transport(self):
        def transport(endpoint, method, path, payload=None, **kwargs):
            if path.endswith('/session'):
                return {'session_channel_extension_id': 'channel'}
            if path.endswith('/threads'):
                return {'thread': {'thread_id': 'thread'}}
            return {'thread_id': 'thread', 'run_id': 'run', 'status': 'queued'}

        return transport

    def test_get_exposes_recorded_cause(self):
        tasks = Tasks(self.db, self.endpoint, self.failing_transport())
        with self.assertRaises(TaskError):
            tasks.submit('key', 'question', allow_submit=True)
        task = tasks.get('key')
        self.assertEqual(task['state'], 'ambiguous')
        self.assertIn('last_error', task)
        self.assertIn('OSError', task['last_error'])
        self.assertIn('connexion perdue', task['last_error'])

    def test_get_exposes_no_cause_on_success(self):
        tasks = Tasks(self.db, self.endpoint, self.succeeding_transport())
        task = tasks.submit('key', 'question', allow_submit=True)
        self.assertEqual(task['state'], 'pending')
        self.assertIn('last_error', task)
        self.assertIsNone(task['last_error'])

    def test_submit_return_value_carries_the_field(self):
        tasks = Tasks(self.db, self.endpoint, self.succeeding_transport())
        self.assertIn('last_error', tasks.submit('key', 'question', allow_submit=True))


if __name__ == '__main__':
    unittest.main()
