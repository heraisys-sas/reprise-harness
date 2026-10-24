import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_git_policy import Authorization, Observation, Request, evaluate, fingerprint


class LocalGitPolicyTests(unittest.TestCase):
    def setUp(self):
        self.request = Request('git.fast_forward', 'repo-source', 'repo-destination',
                               'refs/heads/dev', '1' * 40, '2' * 40)
        self.authorization = Authorization('allowed', 'trusted',
                                           fingerprint(self.request), 'auth:1', 10, 30)
        self.observation = Observation(
            'repo-source', 'repo-destination', 'refs/heads/dev', '1' * 40, '2' * 40,
            '/source/.git', '/destination/.git', 'ancestor', 'validated_plain',
            'trusted', 'snapshot:1', 18, 25, True, True, True, True, True, True,
            True, 'none')

    def plan(self, **changes):
        return evaluate(self.request, self.authorization,
                        replace(self.observation, **changes), now=20)

    def test_eligible_is_never_executable(self):
        plan = self.plan()
        self.assertEqual(plan.status, 'eligible_for_review')
        self.assertFalse(plan.execution_enabled)
        self.assertEqual(plan.evidence_refs, ('auth:1', 'snapshot:1'))

    def test_unknown_and_unsafe_observations(self):
        cases = [
            ({'complete': None}, 'needs_evidence'),
            ({'provenance': 'harness'}, 'needs_evidence'),
            ({'observed_at': 21}, 'needs_evidence'),
            ({'valid_until': 20}, 'needs_evidence'),
            ({'configuration': 'unknown'}, 'needs_evidence'),
            ({'configuration': 'unsupported'}, 'blocked'),
            ({'ancestry': 'unknown'}, 'needs_evidence'),
            ({'ancestry': 'behind'}, 'blocked'),
            ({'ancestry': 'diverged'}, 'blocked'),
            ({'source_common_dir': ''}, 'needs_evidence'),
            ({'source_common_dir': '/destination/.git'}, 'blocked'),
            ({'previous_attempt': 'active'}, 'blocked'),
            ({'previous_attempt': 'uncertain'}, 'blocked'),
            ({'previous_attempt': 'unknown'}, 'needs_evidence'),
            ({'target_oid': '3' * 40}, 'blocked'),
            ({'destination_repository': 'another'}, 'blocked'),
        ]
        for change, status in cases:
            with self.subTest(change=change):
                self.assertEqual(self.plan(**change).status, status)
        for field in ('objects_available', 'index_clean', 'worktree_clean',
                      'untracked_clean', 'ignored_clean', 'no_ongoing_operation'):
            for value, status in ((None, 'needs_evidence'), (False, 'blocked')):
                with self.subTest(field=field, value=value):
                    self.assertEqual(self.plan(**{field: value}).status, status)

    def test_authorization_cannot_be_masked_by_unknown_evidence(self):
        authorization = replace(self.authorization, decision='denied')
        plan = evaluate(self.request, authorization,
                        replace(self.observation, complete=None), now=20)
        self.assertEqual(plan.status, 'blocked')
        self.assertIn('authorization_denied', plan.reasons)

    def test_authorization_scope_provenance_and_validity(self):
        for change, status in (({'provenance': 'harness'}, 'needs_evidence'),
                               ({'decision': 'unknown'}, 'needs_evidence'),
                               ({'valid_until': 20}, 'blocked'),
                               ({'request_fingerprint': 'wrong'}, 'blocked')):
            with self.subTest(change=change):
                plan = evaluate(self.request, replace(self.authorization, **change),
                                self.observation, now=20)
                self.assertEqual(plan.status, status)

    def test_noop_still_requires_all_guards(self):
        request = replace(self.request, target_oid=self.request.expected_oid)
        authorization = replace(self.authorization, request_fingerprint=fingerprint(request))
        observation = replace(self.observation, target_oid=request.expected_oid)
        self.assertEqual(evaluate(request, authorization, observation, now=20).status, 'noop')
        self.assertEqual(evaluate(request, authorization,
                                 replace(observation, index_clean=False), now=20).status,
                         'blocked')

    def test_canonical_fingerprint_and_changes(self):
        self.assertEqual(fingerprint({'b': 2, 'a': 1}), fingerprint({'a': 1, 'b': 2}))
        self.assertEqual(self.plan(), self.plan())
        self.assertNotEqual(self.plan().fingerprint,
                            self.plan(evidence_ref='snapshot:2').fingerprint)
        self.assertNotEqual(self.plan().fingerprint,
                            self.plan(index_clean=False).fingerprint)

    def test_vote_field_is_not_part_of_contract(self):
        with self.assertRaises(TypeError):
            replace(self.observation, votes=3)
        with self.assertRaises(TypeError):
            replace(self.authorization, votes=3)
        self.assertEqual(self.plan(index_clean=False).status, 'blocked')

    def test_invalid_refs_are_blocked(self):
        for ref in ('dev', 'refs/heads/a..b', 'refs/heads/a.lock',
                    'refs/heads/.hidden', 'refs/heads/a b', 'refs/heads/a@{b',
                    'refs/heads/a\x7fb'):
            with self.subTest(ref=ref):
                request = replace(self.request, destination_ref=ref)
                plan = evaluate(request, self.authorization, self.observation, now=20)
                self.assertEqual(plan.status, 'blocked')
                self.assertIn('unsupported_ref', plan.reasons)

    def test_same_repository_identity_blocks_distinct_common_dirs(self):
        request = replace(self.request, destination_repository=self.request.source_repository)
        authorization = replace(self.authorization, request_fingerprint=fingerprint(request))
        observation = replace(self.observation,
                              destination_repository=request.destination_repository)
        self.assertNotEqual(observation.source_common_dir, observation.destination_common_dir)
        plan = evaluate(request, authorization, observation, now=20)
        self.assertEqual(plan.status, 'blocked')
        self.assertIn('same_repository_identity', plan.reasons)

    def test_nonfinite_inputs_fail_closed(self):
        with self.assertRaises(ValueError):
            evaluate(self.request, self.authorization, self.observation, now=float('nan'))
        with self.assertRaises(ValueError):
            evaluate(self.request, replace(self.authorization, valid_until=float('inf')),
                     self.observation, now=20)

    def test_unsupported_operation(self):
        request = replace(self.request, operation='shell')
        plan = evaluate(request, self.authorization, self.observation, now=20)
        self.assertEqual(plan.status, 'blocked')
        self.assertIn('unsupported_operation', plan.reasons)


if __name__ == '__main__':
    unittest.main()
