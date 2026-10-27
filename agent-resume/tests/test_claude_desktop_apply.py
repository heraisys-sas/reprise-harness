"""Tests du transport apply : contrat, borne, et non-ingérence dans request_key."""

import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claude_desktop_apply as transport  # noqa: E402


class PromptGuardTests(unittest.TestCase):
    def test_empty_prompt_rejected(self):
        self.assertEqual(transport.apply("").get("stage"), "vide")

    def test_whitespace_prompt_rejected(self):
        self.assertEqual(transport.apply("   \n ").get("stage"), "vide")

    def test_prompt_at_bound_accepted_or_connexion(self):
        # À la borne exacte, le transport poursuit jusqu'à la connexion simulée.
        with patch.object(transport, "CDP", side_effect=OSError("offline")) as connect:
            result = transport.apply("x" * transport.MAX_PROMPT_CHARS)
        self.assertEqual(result.get("stage"), "connexion")
        connect.assert_called_once()

    def test_prompt_over_bound_rejected_before_any_io(self):
        with patch.object(transport, "CDP") as connect:
            result = transport.apply("x" * (transport.MAX_PROMPT_CHARS + 1))
        self.assertEqual(result.get("stage"), "trop-long")
        self.assertEqual(result["detail"]["max"], transport.MAX_PROMPT_CHARS)
        connect.assert_not_called()


class QuotaAvailabilityTests(unittest.TestCase):
    def test_missing_browser_is_unavailable_not_unblocked(self):
        with patch.object(transport, "CDP", side_effect=OSError("offline")):
            result = transport.quota()
        self.assertFalse(result["available"])
        self.assertIsNone(result["blocked"])

    def test_missing_send_button_is_unavailable_not_unblocked(self):
        client = Mock()
        with patch.object(transport, "CDP", return_value=client), \
             patch.object(transport, "state", return_value={"sendDisabled": None, "hasText": True}):
            result = transport.quota()
        self.assertFalse(result["available"])
        self.assertIsNone(result["blocked"])
        client.close.assert_called_once()

    def test_disabled_button_is_only_a_page_indicator(self):
        client = Mock()
        info = {"sendDisabled": True, "hasText": True, "blocked": False}
        with patch.object(transport, "CDP", return_value=client), \
             patch.object(transport, "state", return_value=info):
            result = transport.quota()
        self.assertTrue(result["available"])
        self.assertTrue(result["blocked"])


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
