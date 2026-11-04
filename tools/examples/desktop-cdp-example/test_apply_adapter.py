"""Offline tests for the quota log adapter; never reads the user's log."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

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
        self.assertIsNone(result["available"])
        self.assertTrue(result["blocked"])
        self.assertFalse(result["expired"])
        self.assertEqual(result["type"], "exceeded_limit")
        self.assertIsInstance(result["resetsAtLocal"], str)

    def test_expired_event_does_not_prove_current_availability(self):
        result = apply_module.quota_blocked(
            log_path=self.log(LINE), now=1793065900)
        self.assertIsNone(result["available"])
        self.assertIsNone(result["blocked"])
        self.assertTrue(result["expired"])
        self.assertEqual(result["type"], "exceeded_limit")

    def test_event_without_reset_does_not_prove_current_availability(self):
        line = ('2026-10-26 22:29:57 [error] [COMPLETION] Request failed '
                '{"name":"Error","message":"{\\"type\\":\\"rate_limit\\"}"}')
        result = apply_module.quota_blocked(
            log_path=self.log(line), now=1793065700)
        self.assertIsNone(result["available"])
        self.assertIsNone(result["blocked"])
        self.assertIsNone(result["expired"])
        self.assertEqual(result["type"], "rate_limit")

    def test_missing_log_keeps_quota_unknown(self):
        path = str(HERE / "does-not-exist.synthetic.log")
        result = apply_module.quota_blocked(log_path=path, now=1793065700)
        self.assertIsNone(result["available"])
        self.assertIsNone(result["blocked"])
        self.assertIsNone(result["expired"])
        self.assertIsNone(result["type"])
        self.assertIsNone(result["resetsAtLocal"])


class PilotRefusalTests(unittest.TestCase):
    def test_public_entrypoints_refuse_without_external_effects(self):
        operations = (
            lambda: apply_module.apply("message"),
            apply_module.send_staged,
            lambda: apply_module.apply_when_allowed("message"),
            apply_module.browser_up,
            apply_module.start_browser,
            apply_module.stop_browser,
            apply_module.raise_window,
            apply_module.status,
        )
        with patch("subprocess.run") as run, \
             patch("subprocess.Popen") as popen, \
             patch("urllib.request.urlopen") as urlopen, \
             patch("socket.create_connection") as connect, \
             patch("time.sleep") as sleep, \
             patch("os.makedirs") as makedirs:
            for operation in operations:
                with self.subTest(operation=operation):
                    with self.assertRaises(apply_module.PilotDisabledError):
                        operation()
            for mock in (run, popen, urlopen, connect, sleep, makedirs):
                mock.assert_not_called()

    def test_cli_mutating_wait_status_and_raise_paths_exit_nonzero(self):
        with patch("subprocess.run") as run, \
             patch("subprocess.Popen") as popen, \
             patch("urllib.request.urlopen") as urlopen, \
             patch("socket.create_connection") as connect:
            for args in (["message"], ["--wait-quota"], ["--status"],
                         ["--raise"], ["--ephemeral", "--resume"]):
                with self.subTest(args=args), redirect_stderr(StringIO()) as stderr:
                    self.assertEqual(apply_module.main(args), 2)
                    self.assertIn("neutralisé", stderr.getvalue())
            for mock in (run, popen, urlopen, connect):
                mock.assert_not_called()

    def test_node_and_shell_entrypoints_are_inert_refusal_stubs(self):
        node = (HERE / "cdp.mjs").read_text(encoding="utf-8")
        self.assertIn("process.exitCode = 2", node)
        for forbidden in ("fetch(", "WebSocket", "Page.navigate", "Input.dispatch",
                          "child_process", "spawn("):
            self.assertNotIn(forbidden, node)
        for name in ("launch.sh", "stop.sh"):
            script = (HERE / name).read_text(encoding="utf-8")
            self.assertIn("exit 2", script)
            for forbidden in ("curl", "pkill", "pgrep", "nohup", "mkdir", "Chrome"):
                self.assertNotIn(forbidden, script)


if __name__ == "__main__":
    unittest.main()
