"""Fermeture du transport : certaine avant l'envoi, incertaine après.

Le contrat du transport est inchangé (`Closed` reste une `Unavailable` : une fermeture
n'est jamais un succès). C'est `execute()` qui distingue les deux situations, seul endroit
où `dispatch_attempted` est connu.
"""
import sys
import tempfile
import unittest
from collections import deque
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import codex_rpc
from engines import Unavailable


class ClosingClient:
    """Client propriétaire qui ferme la connexion à l'étape demandée par `close_at`."""

    close_at = 'initialize'
    status = 'idle'
    cwd = '/tmp'
    limits = True

    def __init__(self, *args):
        self.calls, self.notifications = [], deque()
        self.__class__.last = self

    def send(self, value):
        pass

    def call(self, method, params=None):
        if method == self.close_at:
            raise codex_rpc.Closed('Le serveur propriétaire a fermé la connexion.')
        self.calls.append((method, params))
        if method == 'initialize':
            return {}
        if method.startswith('thread/'):
            return {'thread': {'id': 'session', 'cwd': self.cwd,
                               'status': {'type': self.status}, 'modelProvider': 'openai'}}
        if method == 'account/read':
            return {'account': {'type': 'chatgpt'}}
        if method == 'account/rateLimits/read':
            return {'ordinaryUsageAllowed': self.limits}
        if method == 'turn/start':
            return {'turn': {'id': 'own'}}
        return {}

    def receive(self, timeout=None):
        raise codex_rpc.Closed('Le serveur propriétaire a fermé la connexion.')

    def close(self):
        self.closed = True


class ClosedIsNotAlwaysUncertain(unittest.TestCase):
    def setUp(self):
        ClosingClient.status, ClosingClient.cwd, ClosingClient.limits = 'idle', '/tmp', True
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.states = []
        self.task = {'id': 'task', 'session': 'session', 'cwd': '/tmp', 'prompt': 'continue'}

    def execute(self, **kw):
        with patch('codex_rpc.Client', ClosingClient), \
                patch('run_status.save', side_effect=lambda db, task, state, *a, **k: self.states.append(state)):
            return codex_rpc.execute(None, self.task, 'codex', {}, Path(self.temp.name) / 'log', **kw)

    def test_closed_remains_an_unavailability_for_other_callers(self):
        self.assertTrue(issubclass(codex_rpc.Closed, Unavailable))
        with self.assertRaises(Unavailable):
            raise codex_rpc.Closed('fermé')

    def test_closed_before_dispatch_is_retryable_in_automatic_mode(self):
        ClosingClient.close_at = 'thread/read'
        self.assertEqual(self.execute(automatic=True), 'ready')
        self.assertEqual(self.states[-1], 'engine_unavailable')
        self.assertNotIn('turn/start', [method for method, _ in ClosingClient.last.calls])

    def test_closed_before_dispatch_is_never_reported_as_success(self):
        ClosingClient.close_at = 'thread/read'
        self.assertEqual(self.execute(), 'needs_review')
        self.assertEqual(self.states[-1], 'engine_unavailable')
        self.assertNotIn('turn/start', [method for method, _ in ClosingClient.last.calls])

    def test_closed_after_dispatch_stays_uncertain(self):
        ClosingClient.close_at = None
        self.assertEqual(self.execute(automatic=True), 'needs_review')
        self.assertEqual(self.states[-1], 'needs_review')
        self.assertIn('turn/start', [method for method, _ in ClosingClient.last.calls])


if __name__ == '__main__':
    unittest.main()
