"""Smoke tests du pont CDP ChatGPT Desktop et du wrapper ChatGPT Reprise (#161).

Tests hors ligne : aucune attache CDP, aucun lancement d'app. La preuve de bout en bout
(dispatch + capture de réponse sur session réelle) est documentée dans l'issue #161.
"""
import os
import plistlib
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPRISE = Path(__file__).resolve().parents[1]
SCRIPT = REPRISE / "chatgpt_cdp.mjs"
WRAPPER = REPRISE / "wrappers" / "chatgpt-reprise" / "ChatGPT Reprise.app"

NODE_CANDIDATES = [
    os.environ.get("CHATGPT_CDP_TEST_NODE"),
    shutil.which("node"),
    "/Applications/ChatGPT.app/Contents/Resources/cua_node/bin/node",
]


def _node():
    for cand in NODE_CANDIDATES:
        if cand and Path(cand).exists():
            return cand
    return None


class ChatGptCdpScriptTests(unittest.TestCase):
    def test_script_present_with_modes(self):
        text = SCRIPT.read_text(encoding="utf-8")
        for mode in ("targets", "discover", "newchat", "send"):
            self.assertIn(f"'{mode}'", text)

    @unittest.skipIf(_node() is None, "node indisponible")
    def test_node_syntax(self):
        proc = subprocess.run(
            [_node(), "--check", str(SCRIPT)], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    @unittest.skipIf(_node() is None, "node indisponible")
    def test_send_requires_prompt(self):
        proc = subprocess.run(
            [_node(), str(SCRIPT), "send"], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 64)
        self.assertIn("usage", proc.stderr.lower())

    @unittest.skipIf(_node() is None, "node indisponible")
    def test_unknown_mode_refused(self):
        proc = subprocess.run(
            [_node(), str(SCRIPT), "mode-inconnu"], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(proc.returncode, 64)


class ChatGptRepriseWrapperTests(unittest.TestCase):
    def test_plist(self):
        data = plistlib.loads((WRAPPER / "Contents" / "Info.plist").read_bytes())
        self.assertEqual(data["CFBundleExecutable"], "ChatGPT Reprise")
        self.assertEqual(data["CFBundleIdentifier"], "sas.heraisys.reprise.chatgpt-launcher")
        self.assertTrue(data["LSUIElement"])
        self.assertEqual(data["CFBundleShortVersionString"], "1.0")

    def test_launcher_content(self):
        exe = WRAPPER / "Contents" / "MacOS" / "ChatGPT Reprise"
        text = exe.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/bash"))
        self.assertIn("CHATGPT_CDP_PORT", text)
        self.assertIn("--remote-debugging-port", text)
        self.assertIn("9333", text)
        # le bundle officiel n'est jamais modifié : aucune écriture dans /Applications
        self.assertNotIn("rm -rf /Applications", text)
        if sys.platform == "darwin":
            self.assertTrue(exe.stat().st_mode & 0o111, "bit exécutable manquant")

    def test_launcher_bash_syntax(self):
        if shutil.which("bash") is None:
            self.skipTest("bash indisponible")
        exe = WRAPPER / "Contents" / "MacOS" / "ChatGPT Reprise"
        proc = subprocess.run(["bash", "-n", str(exe)], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
