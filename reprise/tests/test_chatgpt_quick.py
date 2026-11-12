import sys
import tempfile
import unittest
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from chatgpt_quick import Endpoint
from delegation_store import file_evidence


class ChatgptQuickTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)/'project'
        self.project.mkdir()
        (self.project/'note.py').write_text('print("ok")\n')
        self.state = Path(self.temp.name)/'chatgpt-state'
        self.endpoint = Endpoint(self.state, self.project)
        self.addCleanup(self.endpoint.close)

    def test_submit_admits_task_durable_and_idempotent(self):
        key = str(uuid.uuid4())
        first = self.endpoint.submit({'question': 'Revue du module', 'request_key': key})
        self.assertEqual(first['status'], 'finished')
        self.assertEqual(first['delivery'], 'attempted')
        self.assertTrue(first['admission_only'])
        row = self.endpoint.db.execute(
            'SELECT delivery, prompt FROM chatgpt_submissions WHERE request_key=?', (key,)).fetchone()
        self.assertEqual(row[0], 'attempted')
        self.assertIn(key, row[1])
        second = self.endpoint.submit({'question': 'Revue du module', 'request_key': key})
        self.assertEqual(second['resent'], False)
        self.assertEqual(second['delivery'], 'attempted')

    def test_claim_and_submit_result_close_roundtrip_with_claim_key(self):
        key = str(uuid.uuid4())
        self.endpoint.submit({'question': 'Analyse', 'request_key': key})
        claim_key = 'claude-chat-' + str(uuid.uuid4())
        claimed = self.endpoint.tasks.claim(key, claim_key)
        self.assertEqual(claimed['delivery'], 'handed_over')
        self.assertIn('Analyse', claimed['prompt'])
        attempt_id = claimed['attempt_id']
        submitted = self.endpoint.tasks.submit(key, attempt_id, 'answer body')
        self.assertTrue(submitted['completed'])
        self.assertEqual(submitted['sha256'], __import__('hashlib').sha256(b'answer body').hexdigest())

    def test_delivery_attempted_never_retries(self):
        key = str(uuid.uuid4())
        self.endpoint.submit({'question': 'Question', 'request_key': key})
        claim_key = 'claude-chat-' + str(uuid.uuid4())
        first = self.endpoint.tasks.claim(key, claim_key)
        self.assertEqual(first['delivery'], 'handed_over')
        # Replay with the same claim_key: receipt only, the prompt is never redelivered.
        replay = self.endpoint.tasks.claim(key, claim_key)
        self.assertEqual(replay['delivery'], 'attempted')
        self.assertNotIn('prompt', replay)
        # A second submission on the same request_key is never resent either.
        again = self.endpoint.submit({'question': 'Question', 'request_key': key})
        self.assertFalse(again['resent'])

    def test_file_evidence_changed_marks_needs_review(self):
        key = str(uuid.uuid4())
        before = file_evidence(self.project, ['note.py'])
        result = self.endpoint.submit({'question': 'Revue', 'request_key': key,
                                       'paths': ['note.py'], 'before_evidence': before})
        self.assertFalse(result['needs_review'])
        (self.project/'note.py').write_text('print("changed")\n')
        key2 = str(uuid.uuid4())
        result2 = self.endpoint.submit({'question': 'Revue', 'request_key': key2, 'before_evidence': before,
                                        'paths': ['note.py']})
        self.assertTrue(result2['needs_review'])
        row = self.endpoint.db.execute(
            'SELECT needs_review, after_evidence FROM chatgpt_submissions WHERE request_key=?', (key2,)).fetchone()
        self.assertEqual(row[0], 1)
        self.assertIsNotNone(row[1])


if __name__ == '__main__':
    unittest.main()
