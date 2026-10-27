"""apply — déposer un message dans une conversation Claude et l'envoyer.

Sert au cas d'usage « l'agent a été interrompu par les quotas, on le relance » :

    from apply import apply, RESUME
    apply(RESUME, chat_id="<uuid>")      # ou sans chat_id : nouvelle conversation

Le message est écrit puis envoyé en un seul appel — pas de seconde validation.
Aucune permission macOS : on pilote claude.ai dans le Chrome de diagnostic
(`launch.sh`), via le Chrome DevTools Protocol.

Prérequis : `./launch.sh` (Chrome dédié, port 9333) et une session claude.ai
connectée dans ce profil.
"""

from __future__ import annotations

import argparse
import base64
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = Path(HERE).parents[1]
QUOTA_LOG_SOURCE = REPO_ROOT / "agent-resume" / "claude_quota_log.py"
CHROME_BIN = os.environ.get(
    "CHROME_BIN", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
PROFILE = os.environ.get("CLAUDE_CDP_PROFILE",
                         os.path.expanduser("~/.local/share/claude-cdp/chrome"))
CDP_SCRIPT = os.path.join(HERE, "cdp.mjs")
DEFAULT_PORT = int(os.environ.get("CDP_PORT", "9333"))

# Message de reprise après une interruption par quota.
RESUME = ("Continue : tu as été interrompu par les quotas. Reprends exactement "
          "là où tu t'es arrêté, sans recommencer ce qui est déjà fait.")

NODE_CANDIDATES = [
    os.environ.get("NODE_BIN"),
    shutil.which("node"),
]


def _node() -> str:
    for candidate in NODE_CANDIDATES:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("node introuvable — définissez NODE_BIN")


def apply(text: str, chat_id: str | None = None, *, port: int = DEFAULT_PORT,
          wait: int = 3500, timeout: int = 90) -> dict:
    """Écrit `text` dans la conversation ciblée et l'envoie.

    Retourne le JSON de l'outil : {ok, stage, target, ...}.
    `stage` vaut « envoyé », « quota », « frappe » ou « envoi ».
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "stage": "validation", "detail": "texte vide"}

    argv = [_node(), CDP_SCRIPT, "apply", "--port", str(port),
            "--text-b64", base64.b64encode(text.encode()).decode(),
            "--wait", str(wait)]
    if chat_id:
        argv += ["--chat", chat_id]

    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "timeout", "detail": f"{timeout}s"}

    raw = (completed.stdout or "").strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "stage": "sortie", "detail": (raw or completed.stderr)[:300]}
    return result


def send_staged(port: int = DEFAULT_PORT, timeout: int = 30) -> dict:
    """Envoie le texte déjà présent dans le composeur (sans le retaper)."""
    argv = [_node(), CDP_SCRIPT, "send-staged", "--port", str(port)]
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "timeout"}
    try:
        return json.loads((completed.stdout or "").strip())
    except json.JSONDecodeError:
        return {"ok": False, "stage": "sortie", "detail": (completed.stdout or completed.stderr)[:200]}


def quota_blocked(port: int = DEFAULT_PORT, *, log_path: str | None = None,
                  now: float | None = None) -> dict:
    """Adapte le lecteur de quota du dépôt; une source absente reste inconnue."""
    try:
        spec = importlib.util.spec_from_file_location("reprise_claude_quota_log", QUOTA_LOG_SOURCE)
        if spec is None or spec.loader is None:
            raise ImportError("lecteur de quota introuvable dans agent-resume")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        observed = module.observe(
            path=str(log_path or module.LOG_PATH),
            now=now,
            zone=datetime.now().astimezone().tzinfo,
        )
    except (ImportError, OSError, ValueError, AttributeError) as exc:
        return {"available": None, "blocked": None, "expired": None, "type": None,
                "resetsAtLocal": None, "detail": str(exc)}

    if observed.get("source") != "journal":
        return {"available": None, "blocked": None, "expired": None, "type": None,
                "resetsAtLocal": None, "detail": observed.get("detail")}
    return {
        # Une observation renderer ne prouve pas que le profil Chrome utilise
        # le même compte/pool; la disponibilité pour ce profil reste inconnue.
        "available": None,
        "blocked": observed.get("blocked"),
        "expired": observed.get("expired"),
        "type": observed.get("type"),
        "resetsAtLocal": observed.get("resets_at_local"),
    }


def browser_up(port: int = DEFAULT_PORT) -> bool:
    """Le port de diagnostic répond-il ?"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2):
            return True
    except OSError:
        return False


def start_browser(port: int = DEFAULT_PORT, timeout: float = 20.0) -> bool:
    """Lance le Chrome de diagnostic (fenêtré : le headless est refusé par claude.ai)."""
    if browser_up(port):
        return True
    if not os.path.isfile(CHROME_BIN):
        return False
    os.makedirs(PROFILE, exist_ok=True)
    subprocess.Popen(
        [CHROME_BIN, f"--remote-debugging-port={port}", f"--user-data-dir={PROFILE}",
         "--no-first-run", "--no-default-browser-check", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if browser_up(port):
            return True
        time.sleep(0.4)
    return False


def stop_browser(port: int = DEFAULT_PORT) -> None:
    """Arrête uniquement le navigateur de diagnostic (repéré par son profil)."""
    subprocess.run(["pkill", "-f", f"user-data-dir={PROFILE}"], check=False)
    deadline = time.time() + 8
    while time.time() < deadline:
        if not browser_up(port):
            return
        time.sleep(0.3)


def status(port: int = DEFAULT_PORT) -> dict:
    """État de quota, en croisant deux sources indépendantes.

    - journal : rejets serveur (`exceeded_limit`), datés ;
    - page   : bouton désactivé avec du texte, sans trace au journal.
    """
    from_log = quota_blocked(port)
    dom = None
    try:
        argv = [_node(), CDP_SCRIPT, "quota", "--port", str(port)]
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        dom = json.loads((completed.stdout or "").strip())
    except Exception:
        dom = None

    signals = []
    if from_log.get("available") is True and from_log.get("blocked") is not None:
        signals.append(bool(from_log["blocked"]))
    if dom and dom.get("sendDisabled") is not None:
        signals.append(bool(dom.get("sendDisabled") and dom.get("hasText")))
    blocked = True if True in signals else False if signals else None
    return {
        "blocked": blocked,
        "log": {k: from_log.get(k) for k in ("available", "blocked", "expired", "type", "resetsAtLocal")},
        "page": {"sendDisabled": dom.get("sendDisabled"), "hasText": dom.get("hasText"),
                 "resetAt": dom.get("resetAt")} if dom else None,
    }


def apply_when_allowed(text: str, chat_id: str | None = None, *,
                       max_wait: int = 6 * 3600, poll: int = 60,
                       port: int = DEFAULT_PORT) -> dict:
    """Pose le message une seule fois, puis l'envoie dès que le quota le permet.

    Le texte reste dans le composeur : aucune frappe n'est rejouée à chaque
    tentative, donc aucun quota n'est consommé en vain.
    """
    first = apply(text, chat_id, port=port)
    if first.get("ok"):
        return first
    if first.get("stage") != "quota" or not first.get("staged"):
        return first

    info = first
    remaining = info.get("resetsIn")
    deadline = time.time() + (remaining if isinstance(remaining, int) and remaining > 0
                              else max_wait)
    deadline = min(deadline, time.time() + max_wait)

    while time.time() < deadline:
        time.sleep(min(poll, max(5, deadline - time.time())))
        attempt = send_staged(port=port)
        if attempt.get("ok"):
            return attempt
        if attempt.get("stage") not in {"quota", "envoi"}:
            return attempt
    return {"ok": False, "stage": "quota", "staged": True,
            "detail": "toujours bloqué à l'échéance — le texte reste dans le composeur"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Envoyer un message dans Claude (claude.ai via CDP).")
    parser.add_argument("text", nargs="?", help="message à envoyer")
    parser.add_argument("--chat", metavar="UUID", help="conversation existante")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--resume", action="store_true",
                        help="utiliser le message de reprise après quota")
    parser.add_argument("--wait-quota", action="store_true",
                        help="attendre la réouverture du quota avant d'envoyer")
    parser.add_argument("--status", action="store_true", help="état du quota seulement")
    parser.add_argument("--ephemeral", action="store_true",
                        help="démarrer le navigateur si absent, puis l'arrêter")
    parser.add_argument("--linger", type=float, default=6.0,
                        help="secondes d'attente avant fermeture (défaut 6)")
    args = parser.parse_args(argv)

    if args.status:
        print(json.dumps(status(args.port), ensure_ascii=False, indent=2))
        return 0

    text = args.text or (RESUME if args.resume else None)
    if not text:
        parser.print_help()
        return 2

    started = False
    if args.ephemeral and not browser_up(args.port):
        if not start_browser(args.port):
            print(json.dumps({"ok": False, "stage": "navigateur",
                              "detail": "démarrage impossible"}, ensure_ascii=False, indent=2))
            return 1
        started = True

    result = (apply_when_allowed(text, args.chat, port=args.port)
              if args.wait_quota else apply(text, args.chat, port=args.port))

    # Session absente : la fenêtre est laissée ouverte pour que l'humain puisse
    # se connecter — la refermer rendrait la connexion impossible.
    if started and result.get("stage") != "session":
        # On laisse le tour partir avant de fermer : couper au clic risquerait
        # d'interrompre la requête côté client.
        time.sleep(max(0.0, args.linger))
        stop_browser(args.port)

    if started:
        result["browser"] = ("laissé ouvert (connexion requise)"
                             if result.get("stage") == "session" else "arrêté")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
