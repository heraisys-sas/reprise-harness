import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).parents[1]))
import chatgpt_desktop_pipe
from chatgpt_quick import Endpoint, dispatch_to_holder, finish_with_holder_dispatch
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

    def test_replay_rejects_changed_question_without_mutating_receipt(self):
        key = str(uuid.uuid4())
        self.endpoint.submit({'question': 'Original', 'request_key': key})
        before = self.endpoint.db.execute(
            'SELECT prompt,delivery,dispatch_receipt FROM chatgpt_submissions '
            'WHERE request_key=?', (key,)).fetchone()
        with self.assertRaisesRegex(ValueError, 'Request identity conflict'):
            self.endpoint.submit({'question': 'Different', 'request_key': key})
        self.assertEqual(self.endpoint.db.execute(
            'SELECT prompt,delivery,dispatch_receipt FROM chatgpt_submissions '
            'WHERE request_key=?', (key,)).fetchone(), before)
        self.assertEqual(self.endpoint.tasks._row(key)[0], before[0])

    def test_legacy_admitted_replay_repairs_only_projection(self):
        key = str(uuid.uuid4())
        self.endpoint.submit({'question': 'Crash', 'request_key': key})
        # Reproduce an older binary stopped between its ledger INSERT and UPDATE.
        with self.endpoint.db:
            self.endpoint.db.execute('''UPDATE chatgpt_submissions
                SET delivery='admitted',dispatch_receipt=NULL WHERE request_key=?''', (key,))
        attempt_before = self.endpoint.tasks._row(key)[3]
        replay = self.endpoint.submit({'question': 'Crash', 'request_key': key})
        self.assertEqual(replay['delivery'], 'attempted')
        self.assertFalse(replay['resent'])
        self.assertEqual(self.endpoint.tasks._row(key)[3], attempt_before)
        row = self.endpoint.db.execute(
            'SELECT delivery,dispatch_receipt FROM chatgpt_submissions WHERE request_key=?',
            (key,)).fetchone()
        self.assertEqual(row[0], 'attempted')
        self.assertEqual(__import__('json').loads(row[1])['stage'], 'admitted')

    def test_legacy_admitted_replay_rejects_missing_taskstore(self):
        key = str(uuid.uuid4())
        self.endpoint.submit({'question': 'Crash', 'request_key': key})
        with self.endpoint.db:
            self.endpoint.db.execute(
                "UPDATE chatgpt_submissions SET delivery='admitted' WHERE request_key=?", (key,))
        with self.endpoint.tasks.db:
            self.endpoint.tasks.db.execute(
                'DELETE FROM desktop_tasks WHERE scope=? AND request_key=?',
                (self.endpoint.tasks.scope, key))
        with self.assertRaisesRegex(ValueError, 'Admission ledger conflicts'):
            self.endpoint.submit({'question': 'Crash', 'request_key': key})
        self.assertEqual(self.endpoint.db.execute(
            'SELECT delivery FROM chatgpt_submissions WHERE request_key=?',
            (key,)).fetchone(), ('admitted',))

    def test_concurrent_same_key_different_questions_cannot_both_succeed(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        key = str(uuid.uuid4())
        gate = threading.Barrier(2)

        def submit(question):
            endpoint = Endpoint(self.state, self.project)
            try:
                gate.wait(timeout=10)
                try:
                    return ('ok', question, endpoint.submit(
                        {'question': question, 'request_key': key})['delivery'])
                except ValueError as error:
                    return ('conflict', question, str(error))
            finally:
                endpoint.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            result = list(executor.map(submit, ('first question', 'second question')))
        self.assertEqual(sorted(item[0] for item in result), ['conflict', 'ok'])
        self.assertIn('Request identity conflict', next(
            item[2] for item in result if item[0] == 'conflict'))
        winning_question = next(item[1] for item in result if item[0] == 'ok')
        self.assertTrue(self.endpoint.tasks._row(key)[0].endswith(winning_question))
        self.assertEqual(self.endpoint.db.execute(
            'SELECT COUNT(*) FROM chatgpt_submissions WHERE request_key=?',
            (key,)).fetchone()[0], 1)

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


class ChatgptHolderDispatchTests(unittest.TestCase):
    """Issue #226: admitted task -> holder client dispatch -> captured response."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()
        (self.project / 'note.py').write_text('print("ok")\n')
        self.state = Path(self.temp.name) / 'chatgpt-state'
        self.endpoint = Endpoint(self.state, self.project)
        self.addCleanup(self.endpoint.close)

    def invoke(self, transport_receipt):
        with mock.patch.object(chatgpt_desktop_pipe, 'send',
                               return_value=transport_receipt) as send:
            admission = self.endpoint.submit({'question': 'Revue du module',
                                              'request_key': 'req-chain'})
            result = finish_with_holder_dispatch(self.project, self.state, admission)
        return send, admission, result

    def test_captured_response_becomes_the_answer(self):
        receipt = {'status': 'confirmed', 'attempted': True, 'click_observed': True,
                   'composer_emptied': True, 'response_observed': True,
                   'response_complete': True, 'response_source': 'rendered-conversation',
                   'response_anchor': 'footer', 'response_sha256': 'a' * 64,
                   'response_chars': 17, 'response_excerpt': 'Assistant reply.',
                   'response_text': 'Assistant reply.'}
        send, admission, result = self.invoke(receipt)
        send.assert_called_once()
        self.assertEqual(send.call_args.kwargs.get('request_key'), 'req-chain')
        self.assertEqual(send.call_args.kwargs.get('name'), 'dedicated')
        self.assertTrue(admission['admission_only'])
        self.assertFalse(result['admission_only'])
        self.assertEqual(result['delivery'], 'captured')
        self.assertEqual(result['response'], 'Assistant reply.')
        self.assertEqual(result['dispatch_receipt']['response_excerpt'], 'Assistant reply.')
        self.assertNotIn('response_text', result['dispatch_receipt'])
        row = self.endpoint.db.execute(
            'SELECT dispatch_receipt FROM chatgpt_submissions WHERE request_key=?',
            ('req-chain',)).fetchone()
        self.assertNotIn('response_text', row[0])

    def test_sentinel_complete_capture_declares_output_complete(self):
        # Issue #236: a sentinel-anchored capture is producer-attested
        # completeness — the delegated result carries output_complete=true and
        # the persistence layer serves it as an integral answer.
        receipt = {'status': 'confirmed', 'attempted': True, 'click_observed': True,
                   'composer_emptied': True, 'response_observed': True,
                   'response_complete': True, 'response_source': 'rendered-conversation',
                   'response_anchor': 'sentinel', 'response_sha256': 'b' * 64,
                   'response_chars': 4, 'response_excerpt': 'Oui.',
                   'response_text': 'Oui.', 'output_complete': True}
        _send, _admission, result = self.invoke(receipt)
        self.assertEqual(result['delivery'], 'captured')
        self.assertEqual(result['response'], 'Oui.')
        self.assertTrue(result['output_complete'])
        self.assertNotIn('output_truncated', result)
        self.assertEqual(result['status'], 'finished')

    def test_truncated_capture_keeps_partial_text_and_requires_review(self):
        # Issue #236: an amputated capture is never a finished answer. The
        # partial text is kept as review material, the delegated result flips
        # to needs_review/reconcile, and the completeness flags let
        # delegation_collect.py refuse it downstream.
        receipt = {'status': 'captured_incomplete', 'attempted': True,
                   'click_observed': True, 'composer_emptied': True,
                   'response_observed': True, 'response_complete': False,
                   'response_source': 'rendered-conversation',
                   'response_anchor': 'footer', 'response_sha256': 'c' * 64,
                   'response_chars': 58000, 'response_excerpt': 'part…',
                   'response_text': 'P' * 58000, 'reason': 'capture_truncated',
                   'output_truncated': True, 'output_complete': False,
                   'response_full_length': 90123, 'capture_limit_characters': 58000}
        _send, _admission, result = self.invoke(receipt)
        self.assertEqual(result['status'], 'needs_review')
        self.assertFalse(result['work_completed'])
        self.assertEqual(result['next_action'], 'reconcile')
        self.assertEqual(result['delivery'], 'captured')
        self.assertFalse(result['admission_only'])
        self.assertEqual(len(result['response']), 58000)
        self.assertTrue(result['output_truncated'])
        self.assertFalse(result['output_complete'])
        self.assertEqual(result['capture_limit_characters'], 58000)
        self.assertEqual(result['response_full_length'], 90123)
        self.assertNotIn('response_text', result['dispatch_receipt'])

    def test_refused_dispatch_keeps_admission_replayable(self):
        receipt = {'status': 'refused', 'attempted': False, 'reason': 'holder_unavailable'}
        send, admission, result = self.invoke(receipt)
        send.assert_called_once()
        self.assertTrue(result['admission_only'])
        self.assertEqual(result['dispatch_receipt']['reason'], 'holder_unavailable')
        self.assertNotIn('response', result)
        row = self.endpoint.db.execute(
            'SELECT delivery FROM chatgpt_submissions WHERE request_key=?',
            ('req-chain',)).fetchone()
        self.assertEqual(row[0], 'attempted')

    def test_terminal_stored_receipt_short_circuits_redispatch(self):
        self.endpoint.submit({'question': 'Revue du module', 'request_key': 'req-chain'})
        stored = json.dumps({'status': 'confirmed', 'response_excerpt': 'Ancienne réponse.',
                             'response_chars': 16})
        with self.endpoint.db:
            self.endpoint.db.execute(
                'UPDATE chatgpt_submissions SET dispatch_receipt=? WHERE request_key=?',
                (stored, 'req-chain'))
        with mock.patch.object(chatgpt_desktop_pipe, 'send') as send:
            replay = dispatch_to_holder(self.project, self.state, 'req-chain')
        send.assert_not_called()
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['response_excerpt'], 'Ancienne réponse.')

    def test_missing_admission_raises(self):
        with self.assertRaisesRegex(ValueError, 'Missing ChatGPT admission'):
            dispatch_to_holder(self.project, self.state, 'absent-key')


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

    def test_nonserializable_before_evidence_cannot_leave_a_ghost_task(self):
        key = str(uuid.uuid4())
        with self.assertRaises(TypeError):
            self.endpoint.submit({'question': 'Analyse', 'request_key': key,
                                  'before_evidence': {'cannot-json-encode'}})
        self.assertIsNone(self.endpoint.db.execute(
            'SELECT 1 FROM chatgpt_submissions WHERE request_key=?', (key,)).fetchone())
        self.assertIsNone(self.endpoint.tasks._row(key))

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

    def test_mark_acknowledged_rejects_uncommitted_result_and_forged_hash(self):
        import hashlib
        key = str(uuid.uuid4())
        self.endpoint.submit({'question': 'Analyse', 'request_key': key})
        claimed = self.endpoint.tasks.claim(key, 'chatgpt-claim-' + str(uuid.uuid4()))
        fake = hashlib.sha256(b'forged').hexdigest()
        with self.assertRaisesRegex(ValueError, 'not recorded'):
            self.endpoint.mark_acknowledged(key, claimed['attempt_id'], fake)
        self.endpoint.tasks.submit(key, claimed['attempt_id'], 'réponse')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.endpoint.mark_acknowledged(key, claimed['attempt_id'], fake)
        self.assertEqual(self.endpoint.db.execute(
            'SELECT delivery FROM chatgpt_submissions WHERE request_key=?',
            (key,)).fetchone()[0], 'attempted')

    def test_mark_acknowledged_reconciles_preexisting_admitted_row(self):
        key = str(uuid.uuid4())
        self.endpoint.submit({'question': 'Crash before attempted', 'request_key': key})
        receipt = self.endpoint.tasks.claim(key, 'stable-' + str(uuid.uuid4()))
        submitted = self.endpoint.tasks.submit(key, receipt['attempt_id'], 'done')
        with self.endpoint.db:
            self.endpoint.db.execute('''UPDATE chatgpt_submissions
                SET delivery='admitted',dispatch_receipt=NULL WHERE request_key=?''', (key,))
        result = self.endpoint.mark_acknowledged(key, receipt['attempt_id'], submitted['sha256'])
        self.assertTrue(result['changed'])
        self.assertEqual(self.endpoint.db.execute(
            'SELECT delivery FROM chatgpt_submissions WHERE request_key=?',
            (key,)).fetchone(), ('acknowledged',))
        again = self.endpoint.mark_acknowledged(key, receipt['attempt_id'], submitted['sha256'])
        self.assertFalse(again['changed'])

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
