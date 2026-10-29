import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
from pathlib import Path
import unittest
from desktop_task_store import TaskStore


class TaskStoreTests(unittest.TestCase):
    def test_restart_scope_ownership_and_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'a').mkdir(); (root/'b').mkdir()
            store = TaskStore(root/'state', root/'a')
            store.admit('k', 'hello', 'chat')
            store.admit('k', 'hello', 'chat')
            with self.assertRaises(ValueError): store.admit('k', 'changed', 'chat')
            receipt = store.claim('k', 'caller')
            with self.assertRaises(ValueError): store.claim('k', 'other')
            with self.assertRaises(ValueError): store.result('k', True)
            store.close()
            store = TaskStore(root/'state', root/'a')
            replay = store.claim('k', 'caller')  # contract: after a delivery attempt the replay carries no payload
            self.assertEqual((replay['attempt_id'], replay['delivery'], 'prompt' in replay), (receipt['attempt_id'], 'attempted', False))
            with self.assertRaises(ValueError): store.submit('k', 'wrong', 'answer')
            store.submit('k', receipt['attempt_id'], 'answer')
            store.submit('k', receipt['attempt_id'], 'answer')
            with self.assertRaises(ValueError): store.submit('k', receipt['attempt_id'], 'different')
            self.assertFalse(store.result('k')['acknowledged'])
            self.assertTrue(store.result('k', True)['acknowledged'])
            self.assertEqual(store.result('k', view='full')['answer'], 'answer')
            other = TaskStore(root/'state', root/'b')
            with self.assertRaises(ValueError): other.result('k')
            other.admit('k', 'independent')
            other.close(); store.close()

    def test_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp, tmp)
            with self.assertRaises(ValueError): store.admit('', 'hello')
            store.admit('k', 'hello')
            attempt = store.claim('k', 'caller')['attempt_id']
            with self.assertRaises(ValueError): store.submit('k', attempt, 'x'*2_000_001)
            with self.assertRaises(ValueError): store.result('k', 1)
            store.close()
