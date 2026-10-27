"""Tests du detecteur de quota Claude Desktop (journal renderer)."""

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

    def test_past_reset_is_not_a_block(self):
        path = self._write([LINE_OLD])
        entry = ql.latest(path, now=1793065900)  # après l'échéance
        self.assertFalse(entry["blocked"])
        self.assertTrue(entry["expired"])

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
        self.assertFalse(payload["blocked"])

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
