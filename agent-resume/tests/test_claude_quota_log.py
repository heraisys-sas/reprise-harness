"""Tests du detecteur de quota Claude Desktop (journal renderer)."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claude_quota_log as ql  # noqa: E402

PARIS = ZoneInfo("Europe/Paris")

LINE_OLD = ('2026-10-26 22:29:57 [error] [COMPLETION] Request failed '
            '{"name":"Error","message":"{\\"type\\":\\"exceeded_limit\\",'
            '\\"resetsAt\\":1793065800,\\"remaining\\":null}"}')
LINE_NO_RESET = ('2026-10-26 22:29:57 [error] [COMPLETION] Request failed '
                 '{"name":"Error","message":"{\\"type\\":\\"rate_limit\\"}"}')
LINE_API_ERROR = ('2026-10-26 22:30:01 [error] [COMPLETION] Request failed '
                  '{"name":"Error","message":"{\\"type\\":\\"api_error\\",'
                  '\\"message\\":\\"private diagnostic text\\"}"}')
LINE_OUTSIDE_QUOTA = ('2026-10-26 22:30:02 [error] [COMPLETION] Request failed '
                      '{"name":"Error","message":"quota exceeded_limit in prose"}')
LINE_BAD_TIMESTAMP = ('2026-99-99 99:99:99 [error] [COMPLETION] Request failed '
                      '{"type":"exceeded_limit","resetsAt":1793065800}')


def completion_line(payload, timestamp="2026-10-26 22:30:03"):
    outer = json.dumps({"name": "Error", "message": json.dumps(payload)})
    return f"{timestamp} [error] [COMPLETION] Request failed {outer}"


class LatestTests(unittest.TestCase):
    def _write(self, lines):
        handle = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        handle.write("\n".join(lines) + "\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_parses_type_and_epoch(self):
        path = self._write([LINE_OLD])
        entry = ql.latest(path, now=1793065700)  # juste avant l'échéance
        self.assertEqual(entry["type"], "exceeded_limit")
        self.assertEqual(entry["resets_at"], 1793065800)
        self.assertTrue(entry["blocked"])
        self.assertFalse(entry["expired"])
        self.assertRegex(entry["evidence_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("evidence", entry)
        self.assertNotIn("remaining", str(entry))

    def test_past_reset_is_expired_but_current_state_is_unknown(self):
        path = self._write([LINE_OLD])
        entry = ql.latest(path, now=1793065900)  # après l'échéance
        self.assertIsNone(entry["blocked"])
        self.assertTrue(entry["expired"])

    def test_missing_reset_is_not_a_permanent_block(self):
        entry = ql.latest(self._write([LINE_NO_RESET]), now=1793065700)
        self.assertEqual(entry["type"], "rate_limit")
        self.assertIsNone(entry["resets_at"])
        self.assertIsNone(entry["expired"])
        self.assertIsNone(entry["blocked"])

    def test_unrelated_api_error_does_not_clear_last_quota_event(self):
        entry = ql.latest(self._write([LINE_OLD, LINE_API_ERROR]), now=1793065700)
        self.assertEqual(entry["type"], "exceeded_limit")
        self.assertTrue(entry["blocked"])

    def test_non_quota_type_in_prose_is_ignored(self):
        self.assertIsNone(ql.latest(self._write([LINE_OUTSIDE_QUOTA]), now=1793065700))

    def test_non_scalar_types_are_ignored(self):
        for invalid_type in ("[]", "{}"):
            with self.subTest(invalid_type=invalid_type):
                line = completion_line({"type": json.loads(invalid_type)})
                entry = ql.latest(self._write([LINE_OLD, line]), now=1793065700)
                self.assertEqual(entry["type"], "exceeded_limit")

    def test_non_finite_and_overflow_resets_are_unknown(self):
        for reset in ("NaN", "Infinity", "1e999"):
            with self.subTest(reset=reset):
                line = completion_line({"type": "rate_limit", "resetsAt": json.loads(reset)})
                entry = ql.latest(self._write([line]), now=1793065700)
                self.assertEqual(entry["type"], "rate_limit")
                self.assertIsNone(entry["resets_at"])
                self.assertIsNone(entry["blocked"])
                self.assertIsNone(entry["expired"])
                self.assertIsNone(ql.observe(self._write([line]), now=1793065700,
                                            zone=PARIS).get("resets_at_local"))

    def test_invalid_timestamp_is_skipped_in_favor_of_prior_quota_event(self):
        entry = ql.latest(self._write([LINE_OLD, LINE_BAD_TIMESTAMP]), now=1793065700)
        self.assertEqual(entry["type"], "exceeded_limit")
        self.assertEqual(entry["resets_at"], 1793065800)

    def test_malformed_outer_json_is_skipped_in_favor_of_prior_quota_event(self):
        huge_integer = "9" * 5000
        recursive_array = "[" * 1500 + "0" + "]" * 1500
        for malformed in (f'{{"resetsAt":{huge_integer}}}', recursive_array):
            with self.subTest(length=len(malformed)):
                line = f"2026-10-26 22:30:03 [error] [COMPLETION] Request failed {malformed}"
                entry = ql.latest(self._write([LINE_OLD, line]), now=1793065700)
                self.assertEqual(entry["type"], "exceeded_limit")

    def test_malformed_nested_json_is_skipped_in_favor_of_prior_quota_event(self):
        nested = "[" * 1500 + "0" + "]" * 1500
        line = completion_line({"message": nested})
        entry = ql.latest(self._write([LINE_OLD, line]), now=1793065700)
        self.assertEqual(entry["type"], "exceeded_limit")

    def test_no_completion_line(self):
        path = self._write(["2026-10-26 22:00:00 [info] autre chose"])
        self.assertIsNone(ql.latest(path, now=0))

    def test_unreadable_file(self):
        self.assertIsNone(ql.latest("/nonexistent/claude.log", now=0))

    def test_observe_without_zone_has_no_local_time(self):
        path = self._write([LINE_OLD])
        self.assertNotIn("resets_at_local", ql.observe(path, now=0))

    def test_observe_local_time_matches_epoch(self):
        path = self._write([LINE_OLD])
        result = ql.observe(path, now=1793065700, zone=PARIS)
        expected = datetime.fromtimestamp(1793065800, PARIS).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(result["resets_at_local"], expected)


class ObservationTests(unittest.TestCase):
    """La forme attendue par quota_knowledge.status()."""

    def _write(self, lines):
        import tempfile
        handle = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        handle.write("\n".join(lines) + "\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_shape_matches_quota_knowledge(self):
        path = self._write([LINE_OLD])
        payload = ql.observation(path, now=1793065700)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["engine"], "claude-desktop")
        window = payload["pools"][0]["windows"][0]
        self.assertEqual(window["resets_at"], 1793065800)
        self.assertIsNone(window["used_percent"])       # inconnu, pas zéro
        self.assertIn("used_percent_unreported", payload["issues"])

    def test_expired_flagged(self):
        path = self._write([LINE_OLD])
        payload = ql.observation(path, now=1793065900)
        self.assertIn("expired", payload["issues"])
        self.assertIsNone(payload["blocked"])

    def test_future_reset_is_only_an_unattributed_observation(self):
        payload = ql.observation(self._write([LINE_OLD]), now=1793065700)
        self.assertTrue(payload["blocked"])
        self.assertIn("attribution_unknown", payload["issues"])
        pool = payload["pools"][0]
        self.assertEqual(pool["scope"], "route_observation")
        self.assertIsNone(pool["account_id"])
        self.assertIsNone(pool["pool_id"])
        self.assertIsNone(pool["quota_pool_key"])

    def test_observe_does_not_include_raw_private_line(self):
        private_line = LINE_OLD.replace("remaining", "private_marker")
        result = ql.observe(self._write([private_line]), now=1793065700)
        serialized = str(result)
        self.assertNotIn("private_marker", serialized)
        self.assertNotIn("evidence\"", serialized)
        self.assertRegex(result["evidence_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(result["attribution"], {"account": None, "pool": None})

    def test_no_event_returns_none(self):
        path = self._write(["2026-10-26 22:00:00 [info] rien"])
        self.assertIsNone(ql.observation(path, now=0))


class CorroborationTests(unittest.TestCase):
    def test_reads_hhmm(self):
        self.assertEqual(ql.hhmm_from_text("Réinitialisation : 14:40."), "14:40")
        self.assertIsNone(ql.hhmm_from_text("aucune heure ici"))

    def test_banner_confirming_reset(self):
        reset = datetime(2026, 10, 27, 14, 40, tzinfo=PARIS).timestamp()
        self.assertTrue(ql.corroborates(reset, "Réinitialisation : 14:40", PARIS))

    def test_banner_contradicting_reset(self):
        reset = datetime(2026, 10, 27, 14, 40, tzinfo=PARIS).timestamp()
        self.assertFalse(ql.corroborates(reset, "Réinitialisation : 02:50", PARIS))

    def test_no_time_in_banner(self):
        self.assertFalse(ql.corroborates(1793065800, "limite atteinte", PARIS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
