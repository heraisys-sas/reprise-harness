"""Tests du detecteur de quota Claude Desktop (journal renderer)."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

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


class SourceObservationTests(unittest.TestCase):
    def _write(self, lines):
        handle = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        handle.write("\n".join(lines) + "\n")
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_status_distinguishes_absent_unreadable_and_readable_without_event(self):
        absent = ql.source_observation('/path/that/does/not/exist/claude.log', now=1000)
        self.assertEqual((absent['source_status'], absent['event_status']), ('absent', 'not_checked'))
        self.assertEqual(absent['current_quota_status'], 'unknown')
        with patch('os.open', side_effect=PermissionError('private path')):
            unreadable = ql.source_observation('/private/fixture.log', now=1000)
        self.assertEqual((unreadable['source_status'], unreadable['event_status']), ('unreadable', 'not_checked'))
        self.assertNotIn('private path', str(unreadable))
        no_event = ql.source_observation(self._write(['2026-10-26 22:00:00 [info] unrelated']), now=1000)
        self.assertEqual((no_event['source_status'], no_event['event_status']), ('readable', 'no_event'))
        self.assertEqual(no_event['current_quota_status'], 'unknown')

    def test_event_keeps_read_time_separate_and_unknown_without_attested_zone(self):
        private_line = LINE_OLD.replace('"remaining":null',
            '"remaining":null,"diagnostic":"private diagnostic text"')
        path = self._write([private_line])
        read_at = 1793065700
        result = ql.source_observation(path, now=read_at)
        self.assertEqual(result['source_status'], 'readable')
        self.assertEqual(result['event_status'], 'recognized')
        self.assertEqual(result['observed_at'], read_at)
        self.assertIsNone(result['source_observed_at'])
        self.assertEqual(result['source_timestamp_status'], 'unknown_timezone')
        self.assertEqual(result['event_freshness'], 'unknown')
        self.assertEqual(result['resets_at'], 1793065800)
        self.assertEqual(result['reset_relation'], 'future_at_read')
        self.assertEqual(result['current_quota_status'], 'unknown')
        self.assertIsNone(result['current_blocked'])
        self.assertIsNone(result['current_available'])
        for key in ('account_id', 'account_key', 'chat_id', 'session_key', 'pool_id',
                    'quota_pool_key', 'used_percent', 'remaining_percent', 'duration_minutes'):
            self.assertIsNone(result[key])
        self.assertNotIn('blocked', result)
        self.assertNotIn('evidence_sha256', result)
        self.assertNotIn('private diagnostic text', str(result))

    def test_attested_zone_computes_source_time_and_staleness(self):
        path = self._write([LINE_OLD])
        event_time = datetime(2026, 10, 26, 22, 29, 57, tzinfo=PARIS).timestamp()
        result = ql.source_observation(path, now=event_time + 10, source_zone=PARIS)
        self.assertEqual(result['source_observed_at'], event_time)
        self.assertEqual(result['source_timezone'], 'Europe/Paris')
        self.assertEqual(result['observed_at'], event_time + 10)
        self.assertEqual(result['event_age_seconds'], 10)
        self.assertEqual(result['event_freshness'], 'recent')
        stale = ql.source_observation(path, now=event_time + ql.DEFAULT_EVENT_MAX_AGE + 1,
                                      source_zone=PARIS)
        self.assertEqual(stale['event_freshness'], 'stale')
        self.assertEqual(stale['current_quota_status'], 'unknown')

    def test_ambiguous_and_nonexistent_local_source_times_remain_unknown(self):
        for timestamp, expected in (('2026-10-25 02:30:00', 'ambiguous_local_time'),
                                    ('2026-03-29 02:30:00', 'nonexistent_local_time')):
            with self.subTest(timestamp=timestamp):
                line = completion_line({'type': 'rate_limit', 'resetsAt': 1793065800}, timestamp)
                result = ql.source_observation(self._write([line]), now=1793065700,
                                               source_zone=PARIS)
                self.assertEqual(result['source_timestamp_status'], expected)
                self.assertIsNone(result['source_observed_at'])
                self.assertEqual(result['event_freshness'], 'unknown')
                self.assertIsNone(result['current_blocked'])

    def test_tail_read_is_bounded_even_for_a_long_first_line(self):
        path = self._write(['x' * 5000, LINE_OLD])
        original_fdopen = os.fdopen
        totals = {'bytes': 0, 'calls': []}

        class Reader:
            def __init__(self, handle): self.handle = handle
            def __enter__(self): return self
            def __exit__(self, *args): self.handle.close()
            def fileno(self): return self.handle.fileno()
            def seek(self, *args): return self.handle.seek(*args)
            def readline(self, size=-1):
                totals['calls'].append(size)
                data = self.handle.readline(size)
                totals['bytes'] += len(data)
                return data
            def read(self, size=-1):
                totals['calls'].append(size)
                data = self.handle.read(size)
                totals['bytes'] += len(data)
                return data

        def tracked_fdopen(*args, **kwargs):
            return Reader(original_fdopen(*args, **kwargs))

        with patch('os.fdopen', side_effect=tracked_fdopen):
            result = ql.source_observation(path, now=1000, max_bytes=128)
        self.assertLessEqual(totals['bytes'], 128)
        self.assertTrue(all(size >= 0 for size in totals['calls']))
        self.assertTrue(result['truncated'])
        self.assertEqual(result['event_status'], 'no_event_in_tail')
        self.assertEqual(result['observation_status'], 'no_event_in_tail')
        self.assertIn('tail_truncated', result['issues'])

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'FIFO creation is not supported')
    def test_fifo_path_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = os.path.join(directory, 'quota.pipe')
            os.mkfifo(fifo)
            result = ql.source_observation(fifo, now=1000)
            self.assertEqual(result['source_status'], 'unreadable')
            self.assertEqual(result['failure'], 'unsupported_file_type')
            self.assertEqual(result['event_status'], 'not_checked')


if __name__ == "__main__":
    unittest.main(verbosity=2)
