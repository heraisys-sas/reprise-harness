"""Smoke tests du pont CDP ChatGPT Desktop et du wrapper ChatGPT Reprise (#161).

Tests hors ligne : aucune attache CDP, aucun lancement d'app. La preuve de bout en bout
(dispatch + capture de réponse sur session réelle) est documentée dans l'issue #161.
"""
import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPRISE = Path(__file__).resolve().parents[1]
SCRIPT = REPRISE / "chatgpt_cdp.mjs"
WRAPPER_SOURCES = REPRISE / "wrappers" / "chatgpt-reprise"
BUILDER = WRAPPER_SOURCES / "build_wrapper.py"

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


    @unittest.skipIf(_node() is None, "node indisponible")
    def test_send_rejects_foreign_devtools_target_before_websocket(self):
        fixture = r'''
import {pathToFileURL} from "node:url";
const script = process.argv[1];
process.argv = [process.execPath, script, "send", "synthetic test prompt"];
globalThis.fetch = async (url) => ({
  ok: true,
  json: async () => String(url).endsWith("/json/version")
    ? {Browser: "Chrome/157"} : [{
      type: "page", url: "https://foreign.invalid/",
      webSocketDebuggerUrl: "ws://127.0.0.1:9333/devtools/page/foreign"
    }]
});
globalThis.WebSocket = class {
  constructor() { throw new Error("foreign target must never be connected"); }
};
await import(pathToFileURL(script).href);
'''
        result = subprocess.run(
            [_node(), "--input-type=module", "-e", fixture, str(SCRIPT)],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["status"], "refused")
        self.assertFalse(receipt["dispatch_attempted"])
        self.assertFalse(receipt["dispatch_confirmed"])
        self.assertNotIn("foreign target must never", result.stderr)


    @unittest.skipIf(_node() is None, "node indisponible")
    def test_send_one_click_with_correlated_synthetic_turn(self):
        fixture = r'''
import {pathToFileURL} from "node:url";
import vm from "node:vm";
const script = process.argv[1];
process.argv = [process.execPath, script, "send", "synthetic test prompt"];
let draft = "", clicks = 0;
const users = [], assistants = [];
const editor = {
  tagName: "TEXTAREA", value: "", id: "", className: "",
  isContentEditable: false, offsetParent: {},
  disabled: false, readOnly: false,
  getBoundingClientRect: () => ({width: 300, height: 40}),
  getAttribute: () => "",
  focus() { document.activeElement = this; }
};
const send = {
  disabled: true, readOnly: false, innerText: "Send",
  getBoundingClientRect: () => ({width: 40, height: 25}),
  getAttribute: (name) => name === "aria-label" ? "Send" : "",
  click() {
    clicks++;
    if (clicks > 1) throw new Error("duplicate dispatch");
    users.push({innerText: draft});
    assistants.push({innerText: "synthetic assistant answer"});
  }
};
const document = {
  body: {innerText: "ChatGPT"}, title: "ChatGPT", readyState: "complete",
  activeElement: null,
  querySelectorAll(sel) {
    if (sel.startsWith("textarea")) return [editor];
    if (sel.startsWith("button")) return [send];
    if (sel.includes('data-message-author-role="user"')) return users;
    if (sel.includes('data-message-author-role="assistant"')) return assistants;
    return [];
  }
};
const location = {protocol: "app:", hostname: "-", pathname: "/index.html",
                  href: "app://-/index.html"};
globalThis.fetch = async (url) => ({
  ok: true,
  json: async () => String(url).endsWith("/json/version")
    ? {Browser: "Chrome/157"} : [{
      type: "page", url: location.href,
      webSocketDebuggerUrl: "ws://127.0.0.1:9333/devtools/page/main"
    }]
});
globalThis.WebSocket = class {
  constructor() {
    this.readyState = 1;
    queueMicrotask(() => this.onopen?.());
  }
  send(raw) {
    const msg = JSON.parse(raw);
    let result = {};
    if (msg.method === "Input.insertText") {
      draft = msg.params.text; editor.value = draft; send.disabled = false;
    } else if (msg.method === "Runtime.evaluate") {
      try {
        const value = vm.runInNewContext(msg.params.expression, {
          document, location, getComputedStyle: () => ({visibility: "visible", display: "block"})
        });
        result = {result: {value}};
      } catch (error) {
        result = {exceptionDetails: {text: error.message}};
      }
    } else if (msg.method !== "Runtime.enable") {
      throw new Error("Unexpected CDP command: " + msg.method);
    }
    queueMicrotask(() => this.onmessage?.({
      data: JSON.stringify({id: msg.id, result})
    }));
  }
  close() { this.readyState = 3; this.onclose?.(); }
};
await import(pathToFileURL(script).href);
'''
        result = subprocess.run(
            [_node(), "--input-type=module", "-e", fixture, str(SCRIPT)],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, f'stdout={result.stdout!r}, stderr={result.stderr!r}')
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["status"], "sent_confirmed")
        self.assertTrue(receipt["dispatch_attempted"])
        self.assertTrue(receipt["dispatch_confirmed"])
        self.assertTrue(receipt["response_observed"])
        self.assertFalse(receipt["response_complete"])
        self.assertIn("synthetic assistant answer", receipt["assistant_excerpt"])


class ChatGptRepriseWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("build_chatgpt_wrapper", BUILDER)
        generator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(generator)
        cls.generator = generator
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.wrapper = generator.build(cls.temporary.name)

    def test_build_rejects_existing_wrapper_without_overwrite(self):
        with self.assertRaises(FileExistsError):
            self.generator.build(self.temporary.name)

    def test_plist(self):
        data = plistlib.loads((self.wrapper / "Contents" / "Info.plist").read_bytes())
        self.assertEqual(data["CFBundleExecutable"], "ChatGPT Reprise")
        self.assertEqual(data["CFBundleIdentifier"], "sas.heraisys.reprise.chatgpt-launcher")
        self.assertTrue(data["LSUIElement"])
        self.assertEqual(data["CFBundleShortVersionString"], "1.0")

    def test_launcher_content(self):
        exe = self.wrapper / "Contents" / "MacOS" / "ChatGPT Reprise"
        text = exe.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/bash"))
        self.assertIn("CHATGPT_CDP_PORT", text)
        self.assertIn("--remote-debugging-port", text)
        self.assertIn("9333", text)
        self.assertIn('com.openai.codex', text)
        self.assertIn('signal_main TERM', text)
        self.assertIn('signal_main KILL', text)
        self.assertIn('owned_listener', text)
        self.assertIn('lsof', text)
        self.assertNotIn('killall ', text)
        self.assertNotIn('pkill ', text)
        # le bundle officiel n'est jamais modifié : aucune écriture dans /Applications
        self.assertNotIn("rm -rf /Applications", text)
        if sys.platform == "darwin":
            self.assertTrue(exe.stat().st_mode & 0o111, "bit exécutable manquant")

    def test_launcher_bash_syntax(self):
        if shutil.which("bash") is None:
            self.skipTest("bash indisponible")
        exe = self.wrapper / "Contents" / "MacOS" / "ChatGPT Reprise"
        proc = subprocess.run(["bash", "-n", str(exe)], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()
