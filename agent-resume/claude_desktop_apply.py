"""Transport `apply` pour le démon claude-desktop de Reprise.

But : remplacer le « supervised driver » resté en attente dans
`claude_desktop_quick.py` par un envoi réel, sans permission macOS.

Principe : Claude Desktop refuse tout commutateur de debug (jeton Ed25519
`CLAUDE_CDP_AUTH`), mais claude.ai s'ouvre dans un Chrome qu'on contrôle. On
pilote cette page par le Chrome DevTools Protocol : navigation vers la
conversation, insertion du texte, clic sur « Envoyer ».

Aucune dépendance : client WebSocket minimal inclus (stdlib uniquement).

Contrat de retour, aligné sur le reste du démon :

    {"ok": bool, "stage": str, "staged": bool, "detail": {...}}

`stage` : "envoyé" | "posé" | "quota" | "session" | "composeur" | "frappe" |
"connexion" | "vide".
Un `stage == "quota"` avec `staged: true` signifie que le texte est **posé** dans
le composeur : `send_staged()` l'enverra sans le retaper dès la réouverture.
"""

from __future__ import annotations

import base64
import json
import os
import random
import socket
import struct
import time

DEFAULT_PORT = int(os.environ.get("CLAUDE_CDP_PORT", "9333"))
# Même borne que claude_desktop.review() : au-delà, la soumission est refusée en
# amont. Aligner ici évite un transport qui accepte ce que le démon rejette.
MAX_PROMPT_CHARS = 12000
CHROME_BIN = os.environ.get(
    "CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PROFILE = os.environ.get("CLAUDE_CDP_PROFILE",
                         os.path.expanduser("~/.local/share/claude-cdp/chrome"))
VISIBLE_POSITION = (120, 120)
SEND_TESTID = 'chat-input-send'


# ---------------------------------------------------------------------------
# WebSocket minimal (client, texte uniquement)
# ---------------------------------------------------------------------------
class WSError(RuntimeError):
    pass


class WebSocket:
    def __init__(self, host: str, port: int, path: str, timeout: float = 10.0) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(bytes(random.getrandbits(8) for _ in range(16))).decode()
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(request.encode())
        header = self._read_until(b"\r\n\r\n")
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            raise WSError(f"handshake refusé : {header[:80]!r}")

    def _read_until(self, marker: bytes) -> bytes:
        buffer = b""
        while marker not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WSError("connexion fermée pendant le handshake")
            buffer += chunk
        return buffer

    def _recv_exact(self, size: int) -> bytes:
        buffer = b""
        while len(buffer) < size:
            chunk = self.sock.recv(size - len(buffer))
            if not chunk:
                raise WSError("connexion fermée")
            buffer += chunk
        return buffer

    def send_text(self, text: str) -> None:
        payload = text.encode()
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = bytes(random.getrandbits(8) for _ in range(4))
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def recv_text(self) -> str:
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            if second & 0x80:  # masque improbable côté serveur
                mask = self._recv_exact(4)
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(self._recv_exact(length)))
            else:
                data = self._recv_exact(length)

            if opcode == 0x8:
                raise WSError("fermé par le pair")
            if opcode == 0x9:  # ping
                self.sock.sendall(b"\x8a" + bytes([0x80]) + b"\x00\x00\x00\x00")
                continue
            if opcode == 0xA:  # pong
                continue
            if opcode in (0x1, 0x0):
                return data.decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Client CDP
# ---------------------------------------------------------------------------
class CDP:
    def __init__(self, port: int = DEFAULT_PORT, timeout: float = 10.0) -> None:
        self.port = port
        self._id = 0
        targets = self._targets()
        page = self._pick_page(targets)
        if page is None:
            raise WSError("aucune page exposée — launch.sh est-il lancé ?")
        path = page["webSocketDebuggerUrl"].split(str(port), 1)[-1]
        self.ws = WebSocket("127.0.0.1", port, path, timeout=timeout)

    def _targets(self) -> list[dict]:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=5) as response:
            return json.loads(response.read().decode())

    @staticmethod
    def _pick_page(targets: list[dict]) -> dict | None:
        pages = [t for t in targets if t.get("type") == "page"]
        for target in pages:
            if "claude.ai" in (target.get("url") or ""):
                return target
        return pages[0] if pages else None

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        message_id = self._id
        self.ws.send_text(json.dumps({"id": message_id, "method": method,
                                      "params": params or {}}))
        while True:
            payload = json.loads(self.ws.recv_text())
            if payload.get("id") != message_id:
                continue  # événement, pas notre réponse
            if "error" in payload:
                raise WSError(f"{method}: {payload['error']}")
            return payload.get("result", {})

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True})
        if result.get("exceptionDetails"):
            raise WSError(result["exceptionDetails"].get("text", "exception page"))
        return (result.get("result") or {}).get("value")

    def navigate(self, url: str) -> None:
        self.call("Page.navigate", {"url": url})

    def close(self) -> None:
        self.ws.close()


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------
JS_COMPOSER_BOX = """(() => {
  const el = document.querySelector('div[contenteditable="true"], textarea');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (r.width < 100 || r.height < 10) return null;
  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
})()"""

JS_STATE = """(() => {
  const NL = String.fromCharCode(10);
  const send = document.querySelector('[data-testid="chat-input-send"]');
  const input = document.querySelector('div[contenteditable="true"], textarea');
  const text = ((input ? (input.innerText ?? input.value) : '') || '');
  const dialogs = [...document.querySelectorAll('[role="dialog"], [data-testid*="modal"]')]
    .map(d => (d.innerText || '').split(NL).join(' ').trim()).filter(Boolean);
  const joined = dialogs.join(' ').toLowerCase();
  const time = dialogs.join(' ').match(new RegExp('([0-9]{1,2}:[0-9]{2})'));
  return {
    url: location.href,
    sendDisabled: send ? send.disabled : null,
    hasText: Boolean(text.trim()),
    text: text.slice(0, 120),
    blocked: joined.includes('limite') || joined.includes('limit'),
    resetAt: time ? time[1] : null,
    dialogs,
  };
})()"""


def browser_up(port: int = DEFAULT_PORT) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2):
            return True
    except OSError:
        return False


def shutdown(port: int = DEFAULT_PORT, wait: float = 8.0) -> None:
    """Arrête uniquement le navigateur de diagnostic (repéré par son profil)."""
    import subprocess
    from time import time, sleep

    subprocess.run(["pkill", "-f", f"user-data-dir={PROFILE}"], check=False)
    deadline = time() + wait
    while time() < deadline:
        if not browser_up(port):
            return
        sleep(0.3)


def launch(port: int = DEFAULT_PORT, visible: bool = False, timeout: float = 20.0,
           chromeless: bool = True) -> bool:
    """Démarre le navigateur de diagnostic ; fenêtré, hors écran par défaut.

    Le headless est exclu : claude.ai renvoie un interstitiel anti-bot.
    """
    import subprocess
    from time import time, sleep

    if browser_up(port):
        return True
    if not os.path.isfile(CHROME_BIN):
        return False
    os.makedirs(PROFILE, exist_ok=True)
    position = f"--window-position={VISIBLE_POSITION[0]},{VISIBLE_POSITION[1]}" if visible \
        else "--window-position=-3000,-3000"
    subprocess.Popen(
        [CHROME_BIN, f"--remote-debugging-port={port}", f"--user-data-dir={PROFILE}",
         position, "--window-size=1200,900", "--no-first-run",
         "--no-default-browser-check"]
        + (["--app=about:blank"] if chromeless else ["about:blank"]),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time() + timeout
    while time() < deadline:
        if browser_up(port):
            return True
        sleep(0.4)
    return False


def raise_for_login(port: int = DEFAULT_PORT) -> bool:
    """Remet une fenêtre visible au premier plan pour laisser l'humain se connecter.

    Chrome ne permet pas de repositionner une fenêtre existante sans
    Accessibilité : on relance l'instance hors écran en fenêtre visible.
    """
    shutdown(port)
    if not launch(port, visible=True):
        return False
    try:
        import subprocess

        subprocess.run(["open", "-a", "Google Chrome"], check=False)
    except OSError:
        pass
    return True


def _select_all(cdp: CDP) -> None:
    cdp.evaluate("""(() => {
      const el = document.querySelector('div[contenteditable="true"], textarea');
      if (!el || el.tagName === 'TEXTAREA') { el && el.focus(); return true; }
      el.focus();
      const range = document.createRange();
      range.selectNodeContents(el);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      return true;
    })()""")


def _click_point(cdp: CDP, box: dict) -> None:
    for kind in ("mousePressed", "mouseReleased"):
        cdp.call("Input.dispatchMouseEvent", {
            "type": kind, "x": box["x"], "y": box["y"],
            "button": "left", "clickCount": 1})


def wait_composer(cdp: CDP, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cdp.evaluate(JS_COMPOSER_BOX):
            return True
        time.sleep(0.25)
    return False


def state(cdp: CDP) -> dict:
    return cdp.evaluate(JS_STATE)


# ---------------------------------------------------------------------------
# API du transport
# ---------------------------------------------------------------------------
def quota(port: int = DEFAULT_PORT) -> dict:
    """État de quota tel que la page le montre (blocage client inclus)."""
    cdp = CDP(port)
    try:
        info = state(cdp)
        return {
            "blocked": bool(info.get("sendDisabled") and info.get("hasText")) or bool(info.get("blocked")),
            "sendDisabled": info.get("sendDisabled"),
            "hasText": info.get("hasText"),
            "resetAt": info.get("resetAt"),
        }
    finally:
        cdp.close()


def to_submission(result: dict) -> dict:
    """Projette un résultat de transport sur le contrat de soumission du dépôt.

    `claude_desktop.review()` rend {status, resumable, model, admission_only,…}.
    Un appelant ne doit pas avoir à connaître deux vocabulaires.
    """
    stage = result.get("stage")
    base = {"status": "needs_review", "resumable": False, "model": "claude-desktop",
            "automatic_fallback": False, "response": None, "admission_only": True,
            "transport": {"stage": stage, "detail": result.get("detail")}}
    if result.get("ok") and stage in ("envoyé", "posé"):
        base.update(status="finished", dispatch="cdp-web")
        if stage == "posé":
            base["admission_only"] = False
        return base
    if stage == "quota":
        base.update(status="blocked_session", staged=True, reason="Quota Claude épuisé")
    elif stage == "session":
        base.update(status="blocked_auth", reason="Connexion claude.ai requise")
    else:
        base.update(status="engine_unavailable", reason=str(stage))
    return base


def apply(prompt: str, chat_id: str | None = None, *, port: int = DEFAULT_PORT,
          settle: float = 3.0, submit: bool = True,
          on_login: str = "fail") -> dict:
    """Pose `prompt` dans la conversation visée et l'envoie si possible."""
    prompt = (prompt or "").strip()
    if chat_id:
        try:
            from claude_desktop import validate_chat_id
            chat_id = validate_chat_id(chat_id)
        except ImportError:
            pass
        except ValueError as exc:
            return {"ok": False, "stage": "chat_id", "staged": False, "detail": str(exc)}
    if not prompt:
        return {"ok": False, "stage": "vide", "staged": False}
    if len(prompt) > MAX_PROMPT_CHARS:
        return {"ok": False, "stage": "trop-long", "staged": False,
                "detail": {"length": len(prompt), "max": MAX_PROMPT_CHARS}}

    try:
        cdp = CDP(port)
    except (OSError, WSError) as exc:
        return {"ok": False, "stage": "connexion", "staged": False, "detail": str(exc)}

    try:
        target = (f"https://claude.ai/chat/{chat_id}" if chat_id
                  else "https://claude.ai/new")
        cdp.navigate(target)
        time.sleep(settle)
        if not wait_composer(cdp):
            current = state(cdp)
            url = str(current.get("url") or target)
            # Une session absente n'est pas une page cassée : c'est une action
            # humaine attendue, à signaler comme telle.
            if "/login" not in url:
                return {"ok": False, "stage": "composeur", "staged": False,
                        "detail": {"url": url, "title": current.get("title")}}
            # Session absente : action humaine attendue, pas une panne.
            raised = False
            if on_login == "raise":
                cdp.close()
                raised = raise_for_login(port)
            return {"ok": False, "stage": "session", "staged": False, "raised": raised,
                    "detail": {"url": url, "title": current.get("title"),
                               "action": "connexion claude.ai requise"}}

        box = cdp.evaluate(JS_COMPOSER_BOX)
        _click_point(cdp, box)
        _select_all(cdp)
        cdp.call("Input.insertText", {"text": prompt})
        time.sleep(0.2)

        info = state(cdp)
        if not info.get("hasText"):
            return {"ok": False, "stage": "frappe", "staged": False, "detail": info}

        if info.get("sendDisabled"):
            return {"ok": False, "stage": "quota", "staged": True,
                    "detail": {"resetAt": info.get("resetAt"), "url": info.get("url")}}

        if not submit:
            return {"ok": True, "stage": "posé", "staged": True, "detail": {"url": info.get("url")}}

        clicked = cdp.evaluate("""(() => {
          const b = document.querySelector('[data-testid="chat-input-send"]');
          if (!b || b.disabled) return false;
          b.click();
          return true;
        })()""")
        if not clicked:
            return {"ok": False, "stage": "envoi", "staged": True}

        return {"ok": True, "stage": "envoyé", "staged": False,
                "detail": {"url": info.get("url"), "length": len(prompt)}}
    finally:
        cdp.close()


def send_staged(port: int = DEFAULT_PORT) -> dict:
    """Envoie le texte déjà présent dans le composeur (aucune frappe rejouée)."""
    try:
        cdp = CDP(port)
    except (OSError, WSError) as exc:
        return {"ok": False, "stage": "connexion", "detail": str(exc)}

    try:
        info = state(cdp)
        if not info.get("hasText"):
            return {"ok": False, "stage": "vide", "detail": "aucun texte en attente"}
        if info.get("sendDisabled"):
            return {"ok": False, "stage": "quota", "detail": {"resetAt": info.get("resetAt")}}

        clicked = cdp.evaluate("""(() => {
          const b = document.querySelector('[data-testid="chat-input-send"]');
          if (!b || b.disabled) return false;
          b.click();
          return true;
        })()""")
        return {"ok": bool(clicked), "stage": "envoyé" if clicked else "envoi"}
    finally:
        cdp.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Transport apply (CDP) pour le démon claude-desktop.")
    parser.add_argument("text", nargs="?")
    parser.add_argument("--chat")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--quota", action="store_true")
    parser.add_argument("--send-staged", action="store_true")
    args = parser.parse_args()

    if args.quota:
        payload = quota(args.port)
    elif args.send_staged:
        payload = send_staged(args.port)
    else:
        payload = apply(args.text or "", args.chat, port=args.port)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
