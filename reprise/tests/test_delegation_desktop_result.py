"""Retour Desktop : `TaskStore.submit` est la seule complétion admise (issue #102).

Aucun de ces cas ne dépend de l'orchestration : ils fixent le registre durable
(attempt id, idempotence, bornes, isolation par scope) que les reçus utilisent.
"""
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from desktop_task_store import TaskStore


class SubmitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='desktop-result-')
        self.addCleanup(lambda: __import__('shutil').rmtree(self.tmp, ignore_errors=True))
        self.root = Path(self.tmp)
        self.store = TaskStore(self.root / 'state', self.root)
        self.addCleanup(self.store.close)

    def answer(self, key):
        return self.store.db.execute(
            'SELECT answer FROM desktop_tasks WHERE scope=? AND request_key=?',
            (self.store.scope, key)).fetchone()

    def claimed(self, key):
        self.store.admit(key, 'prompt de ' + key)
        return self.store.claim(key, 'claim-' + key)['attempt_id']

    def test_unknown_and_foreign_attempts_are_refused_without_writing(self):
        mine = self.claimed('mine')
        other = self.claimed('other')
        for key, attempt in (('mine', 'inconnu'), ('mine', other), ('absente', mine)):
            with self.subTest(key=key, attempt=attempt):
                with self.assertRaises(ValueError):
                    self.store.submit(key, attempt, 'réponse intruse')
        self.assertIsNone(self.answer('mine')[0])
        self.assertIsNone(self.answer('other')[0])

    def test_identical_retries_are_idempotent_and_a_conflict_keeps_the_first(self):
        attempt = self.claimed('idem')
        first = self.store.submit('idem', attempt, 'la bonne réponse')
        again = self.store.submit('idem', attempt, 'la bonne réponse')
        self.assertTrue(first['completed'])
        self.assertTrue(again['completed'])
        self.assertEqual(first['sha256'], again['sha256'])
        self.assertEqual(first['sha256'],
                         hashlib.sha256('la bonne réponse'.encode()).hexdigest())
        with self.assertRaises(ValueError):
            self.store.submit('idem', attempt, 'une autre réponse')
        self.assertEqual(self.answer('idem')[0], 'la bonne réponse')
        self.assertEqual(self.store.result('idem', view='full')['answer'], 'la bonne réponse')

    def test_empty_and_oversized_answers_are_refused_before_any_write(self):
        attempt = self.claimed('bornes')
        for bad in ('', '   ', 'x' * 2_000_001):
            with self.subTest(length=len(bad)):
                with self.assertRaises(ValueError):
                    self.store.submit('bornes', attempt, bad)
                self.assertIsNone(self.answer('bornes')[0])
        # La borne exacte reste acceptée.
        accepted = self.store.submit('bornes', attempt, 'x' * 2_000_000)
        self.assertTrue(accepted['completed'])
        self.assertEqual(len(self.answer('bornes')[0]), 2_000_000)

    def test_results_are_bound_to_their_project_scope(self):
        left = Path(self.tmp) / 'projet-a'
        right = Path(self.tmp) / 'projet-b'
        left.mkdir()
        right.mkdir()
        store_left = TaskStore(self.root / 'state-a', left)
        self.addCleanup(store_left.close)
        store_right = TaskStore(self.root / 'state-b', right)
        self.addCleanup(store_right.close)
        store_left.admit('partage', 'prompt du projet A')
        store_right.admit('partage', 'prompt du projet B')
        attempt_left = store_left.claim('partage', 'w')['attempt_id']
        attempt_right = store_right.claim('partage', 'w')['attempt_id']
        self.assertNotEqual(attempt_left, attempt_right)
        store_left.submit('partage', attempt_left, 'réponse A')
        with self.assertRaises(ValueError):
            store_right.submit('partage', attempt_left, 'réponse volée')
        with self.assertRaises(ValueError):
            store_left.submit('partage', attempt_right, 'réponse volée')
        self.assertIsNone(store_right.result('partage', view='full')['answer'])
        store_right.submit('partage', attempt_right, 'réponse B')
        self.assertEqual(store_left.result('partage', view='full')['answer'], 'réponse A')
        self.assertEqual(store_right.result('partage', view='full')['answer'], 'réponse B')
        # Deux TaskStores du même projet partagent la même ligne, pas deux copies.
        shared = TaskStore(self.root / 'state-a', left)
        self.addCleanup(shared.close)
        self.assertEqual(shared.result('partage', view='full')['answer'], 'réponse A')


if __name__ == '__main__':
    unittest.main()
