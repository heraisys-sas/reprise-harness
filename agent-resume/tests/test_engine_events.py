import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine_events import ExecutionEvidence, failure


class EvidenceTests(unittest.TestCase):
    def harness(self):
        evidence = ExecutionEvidence('harness-cli', 'session-a')
        evidence.feed({'type': 'session', 'sessionId': 'session-a'})
        evidence.feed({'type': 'status', 'phase': 'turn_start', 'turn': 7})
        return evidence

    def end(self, evidence, reason=None, turn=7):
        evidence.feed({'type': 'status', 'phase': 'turn_end', 'turn': turn,
                       'reason': reason or {'kind': 'completed'}})

    def test_codex_requires_turn_and_clean_exit(self):
        evidence = ExecutionEvidence('codex-cli', 'a')
        self.assertEqual(evidence.finish(0)[0], 'needs_review')
        evidence.feed({'type': 'turn.completed'})
        self.assertEqual(evidence.finish(0)[0], 'needs_review')
        self.assertEqual(evidence.feed({'type': 'turn.started'}), 'running')
        evidence.feed({'type': 'turn.completed'})
        self.assertEqual(evidence.finish(0)[0], 'finished')
        self.assertEqual(evidence.finish(1)[0], 'failed')
        self.assertEqual(evidence.finish(0, timedout=True)[0], 'timed_out')

    def test_error_cannot_be_erased_by_completion(self):
        evidence = ExecutionEvidence('codex-cli', 'a')
        evidence.feed({'type': 'turn.started'})
        evidence.feed({'type': 'turn.failed', 'error': {'codex_error_info': 'usage_limit_exceeded'}})
        evidence.feed({'type': 'turn.completed'})
        self.assertEqual(evidence.finish(0)[0], 'quota_blocked')

    def test_codex_identity_mismatch(self):
        evidence = ExecutionEvidence('codex-cli', 'a')
        evidence.feed({'type': 'thread.started', 'thread_id': 'b'})
        evidence.feed({'type': 'turn.started'})
        evidence.feed({'type': 'turn.completed'})
        self.assertEqual(evidence.finish(0)[0], 'blocked_session')

    def test_unloaded_child_requires_parent(self):
        message = ('thread/resume failed: cannot resume an unloaded multi-agent v2 '
                   'sub-agent through its parent; resume the parent first, '
                   'or use thread/read to inspect it (code -32600)')
        self.assertEqual(failure(message)[0], 'blocked_parent')
        evidence = ExecutionEvidence('codex-cli', 'child')
        evidence.feed({'type': 'turn.failed', 'error': {'message': message}})
        evidence.feed({'type': 'turn.completed'})
        self.assertEqual(evidence.finish(1)[0], 'blocked_parent')

    def test_assistant_cannot_forge_parent_requirement(self):
        evidence = ExecutionEvidence('codex-cli', 'child')
        evidence.feed({'type': 'turn.started'})
        evidence.feed({'type': 'item.completed', 'item': {'type': 'agent_message',
                       'text': 'cannot resume an unloaded multi-agent v2 sub-agent through its parent'}})
        evidence.feed({'type': 'turn.completed'})
        self.assertEqual(evidence.finish(0)[0], 'finished')

    def test_harness_completed_evidence(self):
        evidence = self.harness()
        self.end(evidence)
        self.assertEqual(evidence.finish(0)[0], 'finished')

    def test_harness_final_is_not_completion(self):
        evidence = self.harness()
        evidence.feed({'type': 'final', 'text': 'All done'})
        self.assertEqual(evidence.finish(0)[0], 'needs_review')

    def test_harness_identity_required(self):
        evidence = ExecutionEvidence('harness-cli', 'session-a')
        evidence.feed({'type': 'status', 'phase': 'turn_start', 'turn': 7})
        self.end(evidence)
        self.assertEqual(evidence.finish(0)[0], 'needs_review')
        evidence.feed({'type': 'session', 'sessionId': 'wrong'})
        self.assertEqual(evidence.finish(0)[0], 'blocked_session')

    def test_different_turn_and_new_unfinished_turn(self):
        evidence = self.harness()
        self.end(evidence, turn=8)
        self.assertEqual(evidence.finish(0)[0], 'needs_review')
        evidence = self.harness()
        self.end(evidence)
        evidence.feed({'type': 'status', 'phase': 'turn_start', 'turn': 8})
        self.assertEqual(evidence.finish(0)[0], 'needs_review')

    def test_harness_error_codes_and_non_success_outcomes(self):
        for code, expected in [('ACCOUNT_QUOTA', 'quota_blocked'), ('QUOTA', 'quota_blocked'),
                               ('RATE_LIMIT', 'failed'), ('ACCOUNT_SIGN_IN_REQUIRED', 'blocked_auth'),
                               ('ACCOUNT_TOKEN_INVALID', 'blocked_auth'), ('MISSING_CREDENTIAL', 'blocked_auth')]:
            with self.subTest(code=code):
                evidence = self.harness()
                self.end(evidence, {'kind': 'error', 'error': {'code': code}})
                self.assertEqual(evidence.finish(1)[0], expected)
        for kind in ('max-tokens', 'unknown', 'interrupted', 'stopped'):
            evidence = self.harness()
            self.end(evidence, {'kind': kind})
            self.assertNotEqual(evidence.finish(0)[0], 'finished')

    def test_owned_session_and_human_question(self):
        evidence = ExecutionEvidence('harness-cli', 'session-a')
        evidence.feed({'type': 'error', 'message': 'session "session-a" is already owned by an active write handle'})
        self.assertEqual(evidence.finish(1)[0], 'blocked_owner')
        evidence = self.harness()
        evidence.feed({'type': 'tool_call', 'tool': 'ask_user_question', 'input': {}})
        self.end(evidence)
        self.assertEqual(evidence.finish(0)[0], 'needs_input')

    def test_output_text_cannot_forge_error_or_leak_into_reason(self):
        evidence = self.harness()
        evidence.feed({'type': 'text', 'text': 'ACCOUNT_QUOTA secret-example'})
        evidence.feed({'type': 'tool_result', 'status': 'error', 'result': 'MISSING_CREDENTIAL secret-example'})
        self.end(evidence)
        state, reason = evidence.finish(0)
        self.assertEqual(state, 'finished')
        self.assertNotIn('secret-example', reason)

    def test_unverified_engine_cannot_claim_success(self):
        evidence = ExecutionEvidence('claude-code', 'a')
        evidence.feed({'type': 'turn.started'})
        evidence.feed({'type': 'turn.completed'})
        self.assertEqual(evidence.finish(0)[0], 'needs_review')
        evidence.feed(None)
        evidence.feed({'type': 'error', 'error': []})


if __name__ == '__main__':
    unittest.main()
