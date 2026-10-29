"""Désactivation des anciens exemples CDP et adaptateur quota local.

Le pilote décrit par la source historique n'est plus exécutable depuis ce
répertoire. Pour le flux produit, voir ``agent-resume/resume desktop-apply``.
``quota_blocked`` reste une lecture locale de fixtures/journal, sans CDP.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
QUOTA_LOG_SOURCE = REPO_ROOT / "agent-resume" / "claude_quota_log.py"
REFUSAL = (
    "Exemple CDP neutralisé : aucune opération navigateur, attente de quota "
    "ou action de fenêtre n'est disponible ici. Utilisez la CLI produit Reprise."
)


class PilotDisabledError(RuntimeError):
    """Le pilote d'exemple est neutralisé sans effet de bord."""


def _refuse() -> None:
    raise PilotDisabledError(REFUSAL)


def apply(text: str, chat_id: str | None = None, *, port: int | None = None,
          wait: int | None = None, timeout: int | None = None) -> dict:
    """Refuse les anciens appels d'envoi, sans ouvrir de navigateur ni socket."""
    _refuse()


def send_staged(port: int | None = None, timeout: int | None = None) -> dict:
    """Refuse l'envoi du composeur existant."""
    _refuse()


def quota_blocked(port: int | None = None, *, log_path: str | None = None,
                  now: float | None = None) -> dict:
    """Adapte localement l'observation quota; l'attribution au profil reste inconnue."""
    try:
        spec = importlib.util.spec_from_file_location(
            "reprise_claude_quota_log", QUOTA_LOG_SOURCE)
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
        "available": None,
        "blocked": observed.get("blocked"),
        "expired": observed.get("expired"),
        "type": observed.get("type"),
        "resetsAtLocal": observed.get("resets_at_local"),
    }


def browser_up(port: int | None = None) -> bool:
    """Refuse toute sonde de port ou de navigateur."""
    _refuse()


def start_browser(port: int | None = None, timeout: float | None = None) -> bool:
    """Refuse le démarrage du Chrome de diagnostic."""
    _refuse()


def stop_browser(port: int | None = None) -> None:
    """Refuse l'arrêt d'un navigateur."""
    _refuse()


def raise_window(port: int | None = None) -> None:
    """Refuse toute demande de mise au premier plan."""
    _refuse()


def status(port: int | None = None) -> dict:
    """Refuse la lecture d'état CDP; utiliser quota_blocked pour le journal."""
    _refuse()


def apply_when_allowed(text: str, chat_id: str | None = None, *,
                       max_wait: int | None = None, poll: int | None = None,
                       port: int | None = None) -> dict:
    """Refuse l'attente, le polling et la reprise d'envoi du pilote historique."""
    _refuse()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ancien pilote CDP neutralisé; aucune commande d'envoi ou de lifecycle.")
    parser.add_argument("text", nargs="?", help="ancien argument d'envoi, refusé")
    parser.add_argument("--chat", metavar="UUID")
    parser.add_argument("--port", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--wait-quota", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--ephemeral", action="store_true")
    parser.add_argument("--linger", type=float)
    parser.add_argument("--raise", "--raise-window", action="store_true", dest="raise_window")
    parser.parse_args(argv)
    print(REFUSAL, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
