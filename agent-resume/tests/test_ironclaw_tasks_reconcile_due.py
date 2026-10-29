"""Régression #41 : une tâche incertaine doit pouvoir être réconciliée en lot.

`reconcile()` traite une clé et lève quand la preuve est insuffisante. Rien ne
balayait le stock : une tâche laissée `needs_review`, `ambiguous` ou `dispatching`
y restait jusqu'à ce qu'un humain demande. Ces tests verrouillent le balayage borné
et son contrat d'erreur : un échec par clé, jamais une exception globale.
"""
import sqlite3
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ironclaw_ssh import Endpoint
from ironclaw_tasks import BUSY_BACKOFF_SECONDS, BUSY_MAX_ATTEMPTS, Tasks, TaskError

ID_FILE = '/run/reprise/id'
TOKEN_FILE = '/run/reprise/token'


class ReconcileDueTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.endpoint = Endpoint('host.example', 'agent', 22, ID_FILE, TOKEN_FILE)
        self.timeline = []

    def transport(self, endpoint, method, path, payload=None, **kwargs):
        if path.endswith('/session'):
            return {'session_channel_extension_id': 'channel'}
        if path.endswith('/threads'):
            return {'thread': {'thread_id': 'thread'}}
        if '/timeline' in path:
            return {'messages': self.timeline, 'next_cursor': None}
        raise OSError('connexion perdue')

    def leave_ambiguous(self, key='key', age=3600):
        """Soumet jusqu'à l'échec du POST de message : thread connu, ACK perdu."""
        tasks = Tasks(self.db, self.endpoint, self.transport)
        with self.assertRaises(TaskError):
            tasks.submit(key, 'question', allow_submit=True)
        with self.db:
            self.db.execute('UPDATE ironclaw_tasks SET admitted_at=? WHERE request_key=?',
                            (time.time() - age, key))
        return tasks

    def test_sweep_recovers_a_task_with_one_matching_message(self):
        tasks = self.leave_ambiguous()
        self.timeline = [{'kind': 'user', 'content': 'question', 'turn_run_id': 'run'}]
        results = tasks.reconcile_due()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['request_key'], 'key')
        self.assertEqual(results[0]['state'], 'pending')
        self.assertEqual(results[0]['run_id'], 'run')

    def test_inconclusive_evidence_is_reported_and_leaves_the_task_alone(self):
        tasks = self.leave_ambiguous()
        self.timeline = []
        results = tasks.reconcile_due()
        self.assertEqual(results[0]['state'], 'unreconciled')
        self.assertIn('exactly one matching user message', results[0]['reason'])
        state = self.db.execute(
            "SELECT state FROM ironclaw_tasks WHERE request_key='key'").fetchone()[0]
        self.assertEqual(state, 'ambiguous')

    def test_sweep_skips_tasks_younger_than_min_age(self):
        tasks = self.leave_ambiguous(age=0)
        self.timeline = [{'kind': 'user', 'content': 'question', 'turn_run_id': 'run'}]
        self.assertEqual(tasks.reconcile_due(min_age=3600), [])

    def test_one_failure_does_not_abort_the_sweep(self):
        tasks = self.leave_ambiguous(key='key')
        self.timeline = []
        results = tasks.reconcile_due()
        self.assertEqual(len(results), 1)
        self.assertIn('TaskError', results[0]['reason'])

    def submit_with_outcome(self, key='key', outcome=None, age=3600, content='question'):
        def transport(endpoint, method, path, payload=None, **kwargs):
            if path.endswith('/session'):
                return {'session_channel_extension_id': 'channel'}
            if path.endswith('/threads'):
                return {'thread': {'thread_id': 'thread'}}
            if '/timeline' in path:
                return {'messages': self.timeline, 'next_cursor': None}
            ack = {'thread_id': 'thread', 'run_id': 'run', 'status': 'queued'}
            if outcome:
                ack['outcome'] = outcome
            return ack
        tasks = Tasks(self.db, self.endpoint, transport)
        result = tasks.submit(key, content, allow_submit=True)
        with self.db:
            self.db.execute('UPDATE ironclaw_tasks SET admitted_at=? WHERE request_key=?',
                            (time.time() - age, key))
        return tasks, result

    def test_sweep_includes_blocked_busy_and_recovers_on_match(self):
        tasks, result = self.submit_with_outcome(outcome='rejected_busy')
        self.assertEqual(result['state'], 'blocked_busy')
        self.timeline = [{'kind': 'user', 'content': 'question', 'turn_run_id': 'run'}]
        results = tasks.reconcile_due()
        self.assertEqual(results[0]['state'], 'pending')
        self.assertEqual(results[0]['run_id'], 'run')

    def test_blocked_busy_without_evidence_backs_off_then_needs_operator(self):
        tasks, _ = self.submit_with_outcome(outcome='rejected_busy')
        now = time.time()
        self.assertEqual(tasks.reconcile_due(now=now)[0]['state'], 'blocked_busy')
        self.assertEqual(self.db.execute(
            "SELECT busy_attempts FROM ironclaw_tasks WHERE request_key='key'").fetchone()[0], 1)
        self.assertEqual(tasks.reconcile_due(now=now), [])
        for attempt in range(2, BUSY_MAX_ATTEMPTS):
            now += BUSY_BACKOFF_SECONDS + 1
            self.assertEqual(tasks.reconcile_due(now=now)[0]['state'], 'blocked_busy')
            self.assertEqual(self.db.execute(
                'SELECT busy_attempts FROM ironclaw_tasks WHERE request_key=?', ('key',)).fetchone()[0], attempt)
        now += BUSY_BACKOFF_SECONDS + 1
        results = tasks.reconcile_due(now=now)
        self.assertEqual(results[0]['state'], 'needs_operator')
        self.assertEqual(self.db.execute(
            "SELECT last_error FROM ironclaw_tasks WHERE request_key='key'").fetchone()[0], 'busy: never accepted')
        self.assertEqual(self.db.execute(
            'SELECT next_attempt_at FROM ironclaw_tasks WHERE request_key=?', ('key',)).fetchone()[0], None)

    def test_poll_sweeps_backlog_without_touching_the_polled_task(self):
        tasks, busy = self.submit_with_outcome(key='busy', outcome='rejected_busy')
        self.assertEqual(busy['state'], 'blocked_busy')

        def transport(endpoint, method, path, payload=None, **kwargs):
            if path.endswith('/session'):
                return {'session_channel_extension_id': 'channel'}
            if path.endswith('/threads'):
                return {'thread': {'thread_id': 'thread'}}
            if '/timeline' in path:
                return {'messages': [], 'next_cursor': None}
            return {'thread_id': 'thread', 'run_id': 'run', 'status': 'queued'}
        poller = Tasks(self.db, self.endpoint, transport)
        self.assertEqual(poller.submit('key', 'another question', allow_submit=True)['state'], 'pending')
        self.assertEqual(poller.poll('key')['state'], 'pending')
        self.assertEqual(self.db.execute(
            "SELECT busy_attempts FROM ironclaw_tasks WHERE request_key='busy'").fetchone()[0], 1)

    def test_invalid_bounds_are_rejected(self):
        tasks = Tasks(self.db, self.endpoint, self.transport)
        with self.assertRaises(TaskError):
            tasks.reconcile_due(0)
        with self.assertRaises(TaskError):
            tasks.reconcile_due(101)
        with self.assertRaises(TaskError):
            tasks.reconcile_due(min_age=-1)
        with self.assertRaises(TaskError):
            tasks.reconcile_due(min_age='3600')


if __name__ == '__main__':
    unittest.main()
