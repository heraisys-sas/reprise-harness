"""Experimental claude.ai transport; not connected to the Quick Apply daemon.

A click is neither admission nor a model result. The adapter changes only the
explicitly selected page and never launches or stops a browser during apply.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from claude_desktop import validate_chat_id
from websocket_transport import WebSocketTransport

DEFAULT_PORT = int(os.environ.get("CLAUDE_CDP_PORT", "9333"))
MAX_PROMPT_CHARS = 12000
CHROME_BIN = os.environ.get(
    "CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PROFILE = os.environ.get("CLAUDE_CDP_PROFILE",
                         os.path.expanduser("~/.local/share/claude-cdp/chrome"))
VISIBLE_POSITION = (120, 120)
SEND_TESTID = "chat-input-send"


class WSError(RuntimeError):
    pass


def _page_path(url):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "claude.ai"
                or parsed.query or parsed.fragment):
            return None
        return parsed.path
    except (TypeError, ValueError):
        return None


def _port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError("Port CDP local invalide")
    return value


class WebSocket(WebSocketTransport):
    def __init__(self, host, port, path, timeout=10.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buffer, self.timeout = bytearray(), timeout
        try:
            self._handshake(f"{host}:{port}", path, timeout)
        except BaseException:
            self.close()
            raise

    def _write(self, data):
        self.sock.sendall(data)

    def _read(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Délai CDP dépassé")
        self.sock.settimeout(remaining)
        chunk = self.sock.recv(65536)
        if not chunk:
            self._peer_closed()
        self.buffer.extend(chunk)

    def _peer_closed(self):
        raise WSError("Connexion CDP fermée")

    def close(self):
        self.sock.close()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise WSError("Redirection CDP refusée")


class CDP:
    def __init__(self, port=DEFAULT_PORT, timeout=10.0, *, chat_id=None):
        self.port, self.timeout, self._id = _port(port), timeout, 0
        page = self._pick_page(self._targets(), chat_id)
        if page is None:
            raise WSError("Page claude.ai unique requise")
        self.target_id = page.get("id")
        if not isinstance(self.target_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.target_id):
            raise WSError("Identité CDP invalide")
        try:
            endpoint = urlsplit(page.get("webSocketDebuggerUrl", ""))
            valid = (endpoint.scheme == "ws" and endpoint.hostname in ("127.0.0.1", "localhost")
                     and endpoint.port == self.port and not endpoint.username and not endpoint.password
                     and not endpoint.query and not endpoint.fragment
                     and endpoint.path == "/devtools/page/" + self.target_id)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise WSError("Endpoint CDP local invalide")
        self.ws = WebSocket("127.0.0.1", self.port, endpoint.path, timeout=timeout)

    def _targets(self):
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(f"http://127.0.0.1:{self.port}/json/list", timeout=self.timeout) as response:
            data = response.read(WebSocketTransport.LIMIT + 1)
        if len(data) > WebSocketTransport.LIMIT:
            raise WSError("Liste CDP trop volumineuse")
        targets = json.loads(data)
        if not isinstance(targets, list) or len(targets) > 100 or not all(isinstance(t, dict) for t in targets):
            raise WSError("Liste CDP invalide")
        return targets

    @staticmethod
    def _pick_page(targets, chat_id=None):
        pages = [t for t in targets if t.get("type") == "page" and _page_path(t.get("url")) is not None]
        if chat_id is not None:
            matching = [t for t in pages if _page_path(t.get("url")) == "/chat/" + chat_id]
            if matching:
                pages = matching
        return pages[0] if len(pages) == 1 else None

    def call(self, method, params=None):
        self._id += 1
        message_id = self._id
        self.ws.send({"id": message_id, "method": method, "params": params or {}})
        deadline = time.monotonic() + self.timeout
        for _ in range(201):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Délai CDP dépassé")
            payload = self.ws.receive(remaining)
            if payload.get("id") != message_id:
                continue
            if "error" in payload or not isinstance(payload.get("result", {}), dict):
                raise WSError("Commande CDP refusée")
            return payload.get("result", {})
        raise WSError("Trop de notifications CDP")

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise WSError("Évaluation CDP refusée")
        value = result.get("result") or {}
        if not isinstance(value, dict):
            raise WSError("Résultat CDP invalide")
        return value.get("value")

    def navigate(self, url):
        if _page_path(url) is None:
            raise WSError("Destination claude.ai requise")
        if self.call("Page.navigate", {"url": url}).get("errorText"):
            raise WSError("Navigation CDP refusée")

    def close(self):
        self.ws.close()


# All page actions use the same composer lookup and full-text fingerprint.
JS_HELPERS = r"""
  const composer = () => {
    const inputs = [...document.querySelectorAll('div[contenteditable="true"], textarea')]
      .filter(el => {
        const box = el.getBoundingClientRect();
        return box.width >= 100 && box.height >= 10 && !el.disabled && !el.readOnly;
      });
    return inputs.length === 1 ? inputs[0] : null;
  };
  const readText = el => {
    if (!el) return '';
    if (el.tagName === 'TEXTAREA') return el.value;
    const text = el.innerText;
    const paragraph = el.childNodes.length === 1 ? el.childNodes[0] : null;
    const br = paragraph?.childNodes.length === 1 ? paragraph.childNodes[0] : null;
    // ProseMirror's sole trailing break renders a newline without draft content.
    const placeholder = el.tagName === 'DIV' && el.isContentEditable === true
      && text === '\n' && el.textContent === ''
      && paragraph?.nodeType === 1 && paragraph.tagName === 'P'
      && br?.nodeType === 1 && br.tagName === 'BR' && br.childNodes.length === 0
      && br.classList.length === 1 && br.classList.contains('ProseMirror-trailingBreak');
    return placeholder ? '' : text;
  };
  const sendButton = () => {
    const buttons = [...document.querySelectorAll('[data-testid="chat-input-send"]')];
    return buttons.length === 1 ? buttons[0] : null;
  };
  const digest = async text => [...new Uint8Array(await crypto.subtle.digest(
    'SHA-256', new TextEncoder().encode(text)))].map(b => b.toString(16).padStart(2, '0')).join('');
  const atTarget = path => location.origin === 'https://claude.ai'
    && location.pathname === path && !location.search && !location.hash;
"""

JS_STATE = "(async () => {" + JS_HELPERS + """
  const doc = document, el = composer(), text = readText(el), url = location.href;
  const sha = el ? await digest(text) : null;
  if (doc !== document || el !== composer() || text !== readText(el) || url !== location.href)
    return {changed: true};
  const send = sendButton();
  return {url, composerPresent: Boolean(el), text_sha256: sha,
    text_length: el ? text.length : null, hasText: Boolean(text.length),
    sendDisabled: send ? Boolean(send.disabled) : null};
})()"""


def state(cdp):
    result = cdp.evaluate(JS_STATE)
    if not isinstance(result, dict):
        raise WSError("État du composeur indisponible")
    return result


def _prepare(cdp, path):
    return cdp.evaluate("(async () => {" + JS_HELPERS + "\nconst path = " + json.dumps(path) + ";" + """
      if (!atTarget(path)) return false;
      const el = composer();
      if (!el || readText(el).length !== 0 || !sendButton()) return false;
      el.focus();
      if (el.tagName === 'TEXTAREA') {
        el.setSelectionRange(0, el.value.length);
      } else {
        const range = document.createRange();
        range.selectNodeContents(el);
        const selection = window.getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
      }
      return atTarget(path) && el === composer() && readText(el).length === 0;
    })()""") is True


def _send(cdp, path, expected_sha256):
    return cdp.evaluate("(async () => {" + JS_HELPERS + "\nconst path = " + json.dumps(path)
                        + ", expected = " + json.dumps(expected_sha256) + ";" + """
      if (!atTarget(path)) return {stage: 'cible', clicked: false};
      const doc = document, el = composer(), text = readText(el);
      if (!el || !text.length) return {stage: 'composeur', clicked: false};
      const sha = await digest(text);
      // Recheck synchronously after hashing, before the only click primitive.
      if (doc !== document || !atTarget(path)) return {stage: 'cible', clicked: false};
      if (el !== composer() || readText(el) !== text || sha !== expected)
        return {stage: 'empreinte', clicked: false};
      const button = sendButton();
      if (!button) return {stage: 'composeur', clicked: false};
      if (button.disabled) return {stage: 'bloqué', clicked: false};
      button.click();
      return {stage: 'envoyé', clicked: true};
    })()""")


def _result(stage, *, ok=False, staged=False, dispatch_attempted=False,
            click_observed=False, **detail):
    return {"ok": ok, "stage": stage, "staged": staged,
            "dispatch_attempted": dispatch_attempted, "click_observed": click_observed,
            "admission_confirmed": False, "response_received": False,
            "lifecycle_supported": False, "lifecycle_reason": "lifecycle_unverified", "detail": detail}


def _check(info, path):
    if info.get("changed") or _page_path(info.get("url")) != path:
        return "session" if _page_path(info.get("url")) == "/login" else "cible"
    if info.get("composerPresent") is not True or not isinstance(info.get("sendDisabled"), bool):
        return "composeur"
    return None


def wait_composer(cdp, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = state(cdp)
        if info.get("composerPresent") is True and info.get("sendDisabled") is not None:
            return True
        if _page_path(info.get("url")) == "/login":
            return False
        time.sleep(0.25)
    return False


def _dispatch(cdp, path, fingerprint):
    # A lost response to this evaluation may conceal a click, never retry it.
    try:
        outcome = _send(cdp, path, fingerprint)
    except (OSError, ValueError, WSError, TimeoutError):
        return _result("incertain", staged=None, dispatch_attempted=True)
    if not isinstance(outcome, dict):
        return _result("incertain", staged=None, dispatch_attempted=True)
    if outcome.get("clicked") is True and outcome.get("stage") == "envoyé":
        return _result("envoyé", ok=True, staged=None, dispatch_attempted=True, click_observed=True,
                       expected_sha256=fingerprint)
    if outcome.get("clicked") is False and outcome.get("stage") in ("cible", "composeur", "empreinte", "bloqué"):
        stage = outcome["stage"]
        return _result(stage, staged=True if stage == "bloqué" else None,
                       expected_sha256=fingerprint)
    return _result("incertain", staged=None, dispatch_attempted=True)


def _close(cdp):
    if cdp:
        try:
            cdp.close()
        except (OSError, ValueError, WSError):
            pass


def quota(port=DEFAULT_PORT):
    """An interface observation is not an attributed provider quota."""
    cdp = None
    try:
        cdp = CDP(port)
        info = state(cdp)
        available = (not info.get("changed") and info.get("composerPresent") is True
                     and isinstance(info.get("sendDisabled"), bool))
        return {"available": bool(available), "blocked": None,
                "sendDisabled": info.get("sendDisabled") if available else None,
                "hasText": info.get("hasText") if available else None,
                "source": "interface", "quota_confirmed": False,
                "lifecycle_supported": False, "lifecycle_reason": "lifecycle_unverified"}
    except (OSError, ValueError, WSError, TimeoutError):
        return {"available": False, "blocked": None, "source": "interface", "quota_confirmed": False,
                "lifecycle_supported": False, "lifecycle_reason": "lifecycle_unverified"}
    finally:
        _close(cdp)


def to_submission(result):
    """Preserve uncertainty; a draft or click is never a completed task."""
    stage = result.get("stage")
    attempted = result.get("dispatch_attempted") is True or stage in ("envoyé", "incertain")
    return {"status": "blocked_auth" if stage == "session" and not attempted else "needs_review",
            "resumable": False, "model": "claude-desktop", "automatic_fallback": False,
            "response": None, "admission_only": True, "dispatch": "cdp-web",
            "dispatch_attempted": attempted, "click_observed": result.get("click_observed") is True,
            "admission_confirmed": False, "response_received": False,
            "staged": result.get("staged"), "transport": {"stage": stage},
            "reason": "Connexion claude.ai requise" if stage == "session" else
                      "Transport expérimental ; admission et résultat non confirmés"}


def apply(prompt, chat_id=None, *, port=DEFAULT_PORT, settle=3.0, submit=True, on_login="fail"):
    """Stage into an empty, exact target; optionally attempt one guarded click."""
    if not isinstance(prompt, str) or not prompt.strip():
        return _result("vide")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        return _result("trop-long", length=len(prompt), max=MAX_PROMPT_CHARS)
    try:
        chat_id = validate_chat_id(chat_id)
        _port(port)
        if (not isinstance(settle, (int, float)) or isinstance(settle, bool)
                or not math.isfinite(settle) or not 0 <= settle <= 30
                or type(submit) is not bool or on_login not in ("fail", "raise")):
            raise ValueError("Options invalides")
    except (ValueError, TypeError):
        return _result("paramètres")
    path = "/chat/" + chat_id if chat_id else "/new"
    try:
        fingerprint = hashlib.sha256(prompt.encode()).hexdigest()
    except UnicodeError:
        return _result("paramètres")
    cdp, staged = None, False
    try:
        cdp = CDP(port, chat_id=chat_id)
        initial = state(cdp)
        if _page_path(initial.get("url")) == "/login":
            return _result("session")
        if initial.get("changed") or _page_path(initial.get("url")) is None:
            return _result("cible")
        if initial.get("composerPresent") is not True or initial.get("text_length") is None:
            return _result("composeur")
        if initial["text_length"] != 0:
            return _result("brouillon")
        # Do not reload the target unnecessarily or navigate away from a draft.
        if _page_path(initial["url"]) != path:
            cdp.navigate("https://claude.ai" + path)
        time.sleep(settle)
        wait_composer(cdp)
        info = state(cdp)
        problem = _check(info, path)
        if problem:
            return _result(problem)
        if info.get("text_length") != 0:
            return _result("brouillon")
        if not _prepare(cdp, path):
            return _result("cible")
        staged = None  # A lost insertion response can conceal an existing draft.
        cdp.call("Input.insertText", {"text": prompt})
        info = state(cdp)
        problem = _check(info, path)
        if problem:
            return _result(problem, staged=staged)
        if info.get("text_sha256") != fingerprint:
            return _result("empreinte", staged=staged)
        staged = True
        if not submit:
            return _result("posé", ok=True, staged=True, expected_sha256=fingerprint)
        return _dispatch(cdp, path, fingerprint)
    except (OSError, ValueError, WSError, TimeoutError):
        return _result("connexion", staged=staged)
    finally:
        _close(cdp)


def send_staged(port=DEFAULT_PORT, *, chat_id, expected_sha256):
    """Attempt one click only for the supplied target and full composer hash."""
    try:
        chat_id = validate_chat_id(chat_id)
        _port(port)
        if chat_id is None or not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
            raise ValueError("Cible et empreinte requises")
    except (ValueError, TypeError):
        return _result("paramètres")
    path = "/chat/" + chat_id
    cdp = None
    try:
        cdp = CDP(port, chat_id=chat_id)
        info = state(cdp)
        problem = _check(info, path)
        if problem:
            return _result(problem)
        if info.get("text_length") == 0:
            return _result("vide")
        if info.get("text_sha256") != expected_sha256:
            return _result("empreinte")
        return _dispatch(cdp, path, expected_sha256)
    except (OSError, ValueError, WSError, TimeoutError):
        return _result("connexion", staged=None)
    finally:
        _close(cdp)


def browser_up(port=DEFAULT_PORT):
    """HTTP liveness only; does not establish browser ownership or identity."""
    try:
        _port(port)
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=2):
            return True
    except (OSError, ValueError, WSError):
        return False


def shutdown(port=DEFAULT_PORT, wait=8.0):
    """Refuse shutdown: neither a port nor a profile prefix proves ownership."""
    return False


def launch(port=DEFAULT_PORT, visible=False, timeout=20.0, chromeless=True):
    """Refuse launch/adoption until an owned browser lifecycle is implemented."""
    return False


def raise_for_login(port=DEFAULT_PORT):
    """Human action required; never stop or relaunch an unverified instance."""
    return False


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Transport apply expérimental (CDP).")
    parser.add_argument("text", nargs="?")
    parser.add_argument("--chat")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--quota", action="store_true")
    parser.add_argument("--send-staged", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    if args.quota:
        payload = quota(args.port)
    elif args.send_staged:
        if not args.chat or not args.expected_sha256:
            parser.error("--send-staged exige --chat et --expected-sha256")
        payload = send_staged(args.port, chat_id=args.chat, expected_sha256=args.expected_sha256)
    else:
        payload = apply(args.text or "", args.chat, port=args.port)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
