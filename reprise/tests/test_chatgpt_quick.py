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


class ChatgptCrashSafetyTests(unittest.TestCase):
    """Livrable 3 (c)/(d) : admission crash-safe et projection acknowledged."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)/'project'
        self.project.mkdir()
        self.state = Path(self.temp.name)/'chatgpt-state'
        self.endpoint = Endpoint(self.state, self.project)
        self.addCleanup(self.endpoint.close)

    def test_invalid_question_leaves_no_trace(self):
        key = str(uuid.uuid4())
        with self.assertRaises(ValueError):
            self.endpoint.submit({'question': '   ', 'request_key': key})
        with self.assertRaises(ValueError):
            self.endpoint.submit({'question': 'x' * 12001, 'request_key': key})
        with self.assertRaises(ValueError):
            self.endpoint.submit({'question': 'ok', 'request_key': 'k' * 129})
        with self.assertRaises(ValueError):
            self.endpoint.submit({'question': 'ok', 'request_key': key, 'unexpected': 1})
        # Neither ledger nor TaskStore row was written.
        row = self.endpoint.db.execute(
            'SELECT 1 FROM chatgpt_submissions WHERE request_key=?', (key,)).fetchone()
        self.assertIsNone(row)
        self.assertFalse(self.endpoint.tasks.observe_result(key))

    def test_admission_rolls_forward_when_first_write_interrupted(self):
        # Crash between tasks.admit and the ledger INSERT: the SAME question
        # regenerates the SAME prompt, so admit() is a no-op and the replay
        # finishes the projection without re-executing.
        key = str(uuid.uuid4())
        self.endpoint.tasks.admit(key, self.endpoint_submit_prompt(key, 'Reprise après crash'))
        receipt = self.endpoint.submit({'question': 'Reprise après crash', 'request_key': key})
        self.assertEqual(receipt['status'], 'finished')
        self.assertFalse(receipt['resent'])
        row = self.endpoint.db.execute(
            'SELECT delivery, prompt FROM chatgpt_submissions WHERE request_key=?', (key,)).fetchone()
        self.assertEqual(row[0], 'attempted')
        self.assertIn('Reprise après crash', row[1])

    def endpoint_submit_prompt(self, key, question):
        from chatgpt_quick import CHATGPT_TASK_INSTRUCTION
        import json as _json
        return CHATGPT_TASK_INSTRUCTION.format(key=_json.dumps(key)) + '\n\n' + question

    def test_mark_acknowledged_one_way_and_idempotent(self):
        import hashlib
        key = str(uuid.uuid4())
        self.endpoint.submit({'question': 'Analyse', 'request_key': key})
        claimed = self.endpoint.tasks.claim(key, 'chatgpt-claim-' + str(uuid.uuid4()))
        submitted = self.endpoint.tasks.submit(key, claimed['attempt_id'], 'réponse')
        sha = submitted['sha256']
        first = self.endpoint.mark_acknowledged(key, claimed['attempt_id'], sha)
        self.assertTrue(first['changed'])
        row = self.endpoint.db.execute(
            'SELECT delivery FROM chatgpt_submissions WHERE request_key=?', (key,)).fetchone()
        self.assertEqual(row[0], 'acknowledged')
        # Idempotent: no rollback, no error.
        again = self.endpoint.mark_acknowledged(key, claimed['attempt_id'], sha)
        self.assertFalse(again['changed'])
        # A plain sha256 hex string of the immutable result is accepted.
        self.assertEqual(again['delivery'], 'acknowledged')
        self.assertEqual(hashlib.sha256('réponse'.encode()).hexdigest(), sha)

    def test_mark_acknowledged_rejects_bad_inputs_and_states(self):
        key = str(uuid.uuid4())
        self.endpoint.submit({'question': 'Analyse', 'request_key': key})
        with self.assertRaises(ValueError):
            self.endpoint.mark_acknowledged(key, 'attempt', 'short')
        with self.assertRaises(ValueError):
            self.endpoint.mark_acknowledged('missing-key', 'attempt', 'a' * 64)
        # Still 'attempted' (never claimed/submit): refusing keeps one-way semantics.
        with self.assertRaises(ValueError):
            self.endpoint.mark_acknowledged(key, 'unbound-attempt', 'a' * 64)


if __name__ == '__main__':
    unittest.main()
