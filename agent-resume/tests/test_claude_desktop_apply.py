"""Tests du transport apply : contrat, borne, et non-ingérence dans request_key."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claude_desktop_apply as transport  # noqa: E402


class PromptGuardTests(unittest.TestCase):
    def test_empty_prompt_rejected(self):
        self.assertEqual(transport.apply("").get("stage"), "vide")

    def test_whitespace_prompt_rejected(self):
        self.assertEqual(transport.apply("   \n ").get("stage"), "vide")

    def test_prompt_at_bound_accepted_or_connexion(self):
        # À la borne exacte, la borne ne doit pas rejeter : le transport
        # poursuit (et échoue plus loin faute de navigateur, pas sur la taille).
        result = transport.apply("x" * transport.MAX_PROMPT_CHARS, port=1)
        self.assertNotEqual(result.get("stage"), "trop-long")

    def test_prompt_over_bound_rejected_before_any_io(self):
        result = transport.apply("x" * (transport.MAX_PROMPT_CHARS + 1), port=1)
        self.assertEqual(result.get("stage"), "trop-long")
        self.assertEqual(result["detail"]["max"], transport.MAX_PROMPT_CHARS)


class SubmissionMappingTests(unittest.TestCase):
    def test_sent_maps_to_finished(self):
        mapped = transport.to_submission({"ok": True, "stage": "envoyé"})
        self.assertEqual(mapped["status"], "finished")
        self.assertTrue(mapped["admission_only"])
        self.assertIsNone(mapped["response"])

    def test_prefilled_is_not_a_turn(self):
        mapped = transport.to_submission({"ok": True, "stage": "posé"})
        self.assertEqual(mapped["status"], "finished")
        self.assertFalse(mapped["admission_only"])

    def test_quota_is_deferred_admission(self):
        mapped = transport.to_submission({"ok": False, "stage": "quota"})
        self.assertEqual(mapped["status"], "blocked_session")
        self.assertTrue(mapped["staged"])

    def test_session_maps_to_blocked_auth(self):
        mapped = transport.to_submission({"ok": False, "stage": "session"})
        self.assertEqual(mapped["status"], "blocked_auth")

    def test_unknown_maps_to_engine_unavailable(self):
        mapped = transport.to_submission({"ok": False, "stage": "frappe"})
        self.assertEqual(mapped["status"], "engine_unavailable")

    def test_mapping_carries_no_request_key(self):
        mapped = transport.to_submission({"ok": True, "stage": "envoyé"})
        self.assertNotIn("request_key", mapped)


class StatelessnessTests(unittest.TestCase):
    def test_transport_never_handles_request_key(self):
        """L'idempotence appartient au démon ; le transport est sans état."""
        with open(transport.__file__, encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("request_key", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
