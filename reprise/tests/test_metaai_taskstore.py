import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import metaai_taskstore as mts


class TestTaskStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = mts.TaskStore(self.root / 'tasks.sqlite')

    def tearDown(self):
        self.tmp.cleanup()

    def test_enqueue_claim_order(self):
        a = self.store.enqueue('question', {'q': '1'})
        b = self.store.enqueue('question', {'q': '2'})
        first = self.store.claim_next()
        self.assertEqual(first['id'], a)
        self.assertEqual(first['status'], 'dispatched')
        second = self.store.claim_next()
        self.assertEqual(second['id'], b)
        self.assertIsNone(self.store.claim_next())

    def test_claim_specific_id_refuses_dispatched(self):
        tid = self.store.enqueue('question', {'q': 'x'})
        self.store.claim_next(task_id=tid)
        self.assertIsNone(self.store.claim_next(task_id=tid))

    def test_enqueue_existing_id_left_intact(self):
        # INSERT OR REPLACE historique effaçait result/status d'une tâche existante :
        # un id déjà présent (ex. answered) ne doit plus être réinitialisé.
        tid = self.store.enqueue('question', {'q': 'x'})
        self.store.claim_next(task_id=tid)
        self.store.record_result(tid, {'reponse': '391'}, '{}', 'metaai')
        self.assertEqual(self.store.enqueue('question', {'q': 'y'}, task_id=tid), tid)
        task = self.store.get(tid)
        self.assertEqual(task['status'], 'answered')
        self.assertEqual(json.loads(task['payload'])['q'], 'x')

    def test_claim_next_atomic_between_connections(self):
        # Le store est ouvert par deux processus (pont MCP + metaaid) : la
        # réservation concurrente doit échouer côté perdant (garde pending).
        other = mts.TaskStore(self.root / 'tasks.sqlite')
        tid = self.store.enqueue('question', {'q': 'x'})
        first = self.store.claim_next()
        second = other.claim_next()
        self.assertEqual(first['id'], tid)
        self.assertIsNone(second)
        self.assertIsNone(other.claim_next(task_id=tid))

    def test_dispatch_write_inbox_atomic_and_content(self):
        tid = self.store.enqueue('question', {'question': 'Combien font 17 x 23 ?'})
        task = self.store.claim_next(task_id=tid)
        inbox = self.root / 'inbox'
        path = self.store.dispatch_write_inbox(task, inbox)
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual(payload['task_id'], tid)
        self.assertEqual(payload['question'], 'Combien font 17 x 23 ?')
        leftovers = list(inbox.glob('*.tmp'))
        self.assertEqual(leftovers, [])

    def test_record_result_dispatched_becomes_answered(self):
        tid = self.store.enqueue('question', {'q': 'x'})
        self.store.claim_next(task_id=tid)
        status = self.store.record_result(tid, {'reponse': '391'}, '{"reponse": "391"}', 'metaai')
        self.assertEqual(status, 'answered')
        task = self.store.get(tid)
        self.assertEqual(json.loads(task['result'])['reponse'], '391')
        self.assertEqual(task['result_source'], 'metaai')

    def test_record_result_orphan_for_unknown_task(self):
        status = self.store.record_result('tache-orphelin', {'reponse': 'x'}, '{}', 'metaai')
        self.assertEqual(status, 'answered_orphan')

    def test_fail_and_list_tasks(self):
        tid = self.store.enqueue('question', {'q': 'x'})
        self.store.fail(tid)
        tasks = self.store.list_tasks(status='failed')
        self.assertEqual([t['id'] for t in tasks], [tid])

    def test_summary_shape(self):
        tid = self.store.enqueue('question', {'q': 'x'})
        task = self.store.get(tid)
        s = mts.summary(task)
        self.assertEqual(s['task_id'], tid)
        self.assertEqual(s['status'], 'pending')
        self.assertIsNone(s['result'])


if __name__ == '__main__':
    unittest.main()
