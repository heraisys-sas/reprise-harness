"""Offline tests for the quota log adapter; never reads the user's log."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("cdp_example_apply", HERE / "apply.py")
apply_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apply_module)

LINE = ('2026-10-26 22:29:57 [error] [COMPLETION] Request failed '
        '{"name":"Error","message":"{\\"type\\":\\"exceeded_limit\\",'
        '\\"resetsAt\\":1793065800,\\"remaining\\":null}"}')


class QuotaAdapterTests(unittest.TestCase):
    def log(self, content):
        handle = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        handle.write(content + "\n")
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_active_event_maps_quota_fields(self):
        result = apply_module.quota_blocked(
            log_path=self.log(LINE), now=1793065700)
        self.assertTrue(result["available"])
        self.assertTrue(result["blocked"])
        self.assertFalse(result["expired"])
        self.assertEqual(result["type"], "exceeded_limit")
        self.assertIsInstance(result["resetsAtLocal"], str)

    def test_expired_event_stays_available_but_unblocked(self):
        result = apply_module.quota_blocked(
            log_path=self.log(LINE), now=1793065900)
        self.assertTrue(result["available"])
        self.assertFalse(result["blocked"])
        self.assertTrue(result["expired"])
        self.assertEqual(result["type"], "exceeded_limit")

    def test_missing_log_keeps_quota_unknown(self):
        path = str(HERE / "does-not-exist.synthetic.log")
        result = apply_module.quota_blocked(log_path=path, now=1793065700)
        self.assertIsNone(result["available"])
        self.assertIsNone(result["blocked"])
        self.assertIsNone(result["expired"])
        self.assertIsNone(result["type"])
        self.assertIsNone(result["resetsAtLocal"])


if __name__ == "__main__":
    unittest.main()
