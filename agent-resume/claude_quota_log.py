"""Preuves de quota Claude Desktop — source autoritaire : le journal renderer.

`claude_quota.py` s'appuie sur un texte de bandeau (« En pause : … disponible
27 oct. à 14:40 »). La formulation a changé : l'interface affiche désormais
« Réinitialisation : 14:40 », sans date, et `parse_pause` renvoie `None`.

Le journal, lui, donne l'échéance exacte en epoch :

    [error] [COMPLETION] Request failed {"type":"exceeded_limit",
      "resetsAt":1793065800,"remaining":null,…}

Ce module lit cette source, sans écran ni permission, et refuse de deviner une
heure quand seul un « HH:MM » sans date est disponible.

Conventions alignées sur `claude_quota.py` : fonctions pures, `ZoneInfo` exigé,
retour `None` plutôt qu'une valeur approximative.
"""

from __future__ import annotations

from datetime import datetime
import json
import math
import os
import re
from hashlib import sha256

LOG_PATH = os.path.expanduser("~/Library/Logs/Claude/claude.ai-web.log")

_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[error\]\s+\[COMPLETION\]")
_HHMM = re.compile(r'\b([0-2]?\d:[0-5]\d)\b')
_QUOTA_TYPES = {"exceeded_limit", "rate_limit"}

# Blocage client : le bouton est désactivé alors que du texte est présent, sans
# aucune requête émise — donc sans ligne de journal. À croiser avec ce module.
CLIENT_SIDE_NOTE = "bouton d'envoi désactivé avec texte présent, aucun rejet serveur"


def read_tail(path: str = LOG_PATH, max_bytes: int = 2_000_000) -> list[str]:
    """Dernières lignes du journal, sans relire 4 Mo à chaque appel."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()  # ligne tronquée par la fenêtre
            return handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def _is_quota_type(value) -> bool:
    return isinstance(value, str) and value in _QUOTA_TYPES


def _completion_payload(line: str, start: int) -> dict | None:
    """Parse un objet de complétion et, au plus, son message JSON imbriqué."""
    fragment = line[start:].lstrip()
    prefix = "Request failed"
    if fragment.startswith(prefix):
        fragment = fragment[len(prefix):].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(fragment)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return None
    if not isinstance(value, dict):
        return None
    if "type" in value:
        return value if _is_quota_type(value.get("type")) else None
    message = value.get("message")
    if not isinstance(message, str):
        return None
    try:
        nested = json.loads(message)
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        return None
    return nested if isinstance(nested, dict) and _is_quota_type(nested.get("type")) else None


def latest(path: str = LOG_PATH, now: float | None = None) -> dict | None:
    """Dernier événement quota reconnu, sans exposer le contenu du journal.

    Les erreurs sans type quota ne lèvent pas un événement quota antérieur.
    `blocked` n'est qu'un indice historique avec reset futur; compte, pool et
    état courant restent inconnus.
    """
    from time import time as _now

    current = _now() if now is None else now
    for line in reversed(read_tail(path)):
        match = _LINE.match(line)
        if not match:
            continue
        payload = _completion_payload(line, match.end())
        if payload is None:
            continue
        kind = payload.get("type")
        reset = payload.get("resetsAt")
        epoch = None
        if isinstance(reset, (int, float)) and not isinstance(reset, bool):
            try:
                candidate = float(reset)
                if math.isfinite(candidate):
                    # Ensure downstream datetime conversions can represent it.
                    datetime.fromtimestamp(candidate)
                    epoch = candidate
            except (OverflowError, OSError, ValueError):
                pass
        try:
            observed = datetime.strptime(
                match.group("ts"), "%Y-%m-%d %H:%M:%S").timestamp()
        except (OverflowError, OSError, ValueError):
            continue
        expired = epoch is not None and epoch <= current
        return {
            "type": kind,
            "resets_at": epoch,
            "observed_at": observed,
            "expired": expired if epoch is not None else None,
            "blocked": True if epoch is not None and epoch > current else None,
            "evidence_sha256": sha256(line.encode("utf-8", "replace")).hexdigest(),
        }
    return None


def hhmm_from_text(text: str) -> str | None:
    """Heure « HH:MM » d'un bandeau, sans interprétation."""
    match = _HHMM.search(text or "")
    return match.group(1) if match else None


def corroborates(resets_at: float, text: str, zone) -> bool:
    """Le bandeau confirme-t-il l'échéance du journal (même heure locale) ?"""
    if resets_at is None or not text:
        return False
    expected = datetime.fromtimestamp(resets_at, zone).strftime("%H:%M")
    found = hhmm_from_text(text)
    if found is None:
        return False
    hour, minute = (int(part) for part in found.split(":"))
    return f"{hour:02d}:{minute:02d}" == expected


def observe(path: str = LOG_PATH, now: float | None = None, zone=None) -> dict:
    """Vue unifiée : ce que le journal permet d'affirmer, et à quelle heure.

    `source` vaut « journal » quand un événement quota reconnu est trouvé.
    Aucune heure n'est déduite d'un « HH:MM » sans date : cette ambiguïté
    (aujourd'hui ou demain ?) est précisément ce que `parse_pause` refusait.
    """
    entry = latest(path, now)
    if entry is None:
        return {"source": "aucun", "blocked": None,
                "detail": "journal illisible ou sans événement quota reconnu"}
    result = {"source": "journal", **entry}
    result["attribution"] = {"account": None, "pool": None}
    result["attribution_warning"] = (
        "compte et pool non attribués; ne prouve pas le quota du profil Chrome")
    if zone is not None and entry.get("resets_at"):
        result["resets_at_local"] = datetime.fromtimestamp(
            entry["resets_at"], zone).strftime("%Y-%m-%d %H:%M:%S")
    return result


def observation(path: str = LOG_PATH, now: float | None = None,
                engine: str = "claude-desktop") -> dict | None:
    """Événement de quota au format attendu par `quota_knowledge`.

    `used_percent` reste **None** : le journal ne le publie pas, et l'inconnu
    n'est pas zéro. Seule l'échéance est certaine, ce qui est précisément ce qui
    manquait aux fenêtres Codex.
    """
    entry = latest(path, now)
    if entry is None:
        return None
    issues = ["used_percent_unreported", "source_renderer_log", "attribution_unknown"]
    if entry.get("expired"):
        issues.append("expired")
    return {
        "schema_version": 1,
        "engine": engine,
        "source": "claude.ai-web.log",
        "observed_at": entry["observed_at"],
        "observation_status": "observed",
        "coverage": "partial",
        "blocked": entry["blocked"],
        "attribution_warning": (
            "compte et pool non attribués; ne prouve pas le quota du profil Chrome"),
        "pools": [{
            "limit_id": "claude-chat",
            "scope": "route_observation",
            "account_id": None,
            "pool_id": None,
            "quota_pool_key": None,
            "windows": [{
                "name": "primary",
                "used_percent": None,
                "remaining_percent": None,
                "duration_minutes": None,
                "resets_at": entry["resets_at"],
            }],
        }],
        "issues": issues,
    }


if __name__ == "__main__":
    from datetime import timezone
    import argparse

    parser = argparse.ArgumentParser(description="Quota Claude Desktop depuis le journal.")
    parser.add_argument("--path", default=LOG_PATH)
    args = parser.parse_args()
    print(json.dumps(observe(args.path, zone=timezone.utc), ensure_ascii=False, indent=2,
                     default=str))
