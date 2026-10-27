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
import os
import re

LOG_PATH = os.path.expanduser("~/Library/Logs/Claude/claude.ai-web.log")

_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[error\]\s+\[COMPLETION\]")
_TYPE = re.compile(r'"type"\s*:\s*"(?P<type>[a-z_]+)"')
_RESET = re.compile(r'"resetsAt"\s*:\s*(?P<epoch>\d{9,})')
_HHMM = re.compile(r'\b([0-2]?\d:[0-5]\d)\b')

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


def latest(path: str = LOG_PATH, now: float | None = None) -> dict | None:
    """Dernier rejet serveur connu, avec son échéance.

    Retourne None si le journal est illisible ou sans événement. Un événement
    dont `resets_at` est passé n'est pas un blocage : il est marqué `expired`.
    """
    from time import time as _now

    current = _now() if now is None else now
    for line in reversed(read_tail(path)):
        match = _LINE.match(line)
        if not match:
            continue
        # Le message est un JSON sérialisé dans un JSON : \"type\" et non "type".
        body = line[match.end():].replace("\\", "")
        kind = _TYPE.search(body)
        if not kind:
            continue
        reset = _RESET.search(body)
        epoch = float(reset.group("epoch")) if reset else None
        observed = datetime.strptime(match.group("ts"), "%Y-%m-%d %H:%M:%S").timestamp()
        return {
            "type": kind.group("type"),
            "resets_at": epoch,
            "observed_at": observed,
            "expired": epoch is not None and epoch <= current,
            "blocked": kind.group("type") in {"exceeded_limit", "rate_limit", "overloaded"}
                       and not (epoch is not None and epoch <= current),
            "evidence": line[:200],
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

    `source` vaut « journal » quand l'échéance est certaine, « aucun » sinon.
    Aucune heure n'est déduite d'un « HH:MM » sans date : cette ambiguïté
    (aujourd'hui ou demain ?) est précisément ce que `parse_pause` refusait.
    """
    entry = latest(path, now)
    if entry is None:
        return {"source": "aucun", "blocked": None,
                "detail": "journal illisible ou sans rejet de complétion"}
    result = {"source": "journal", **entry}
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
    issues = ["used_percent_unreported", "source_renderer_log"]
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
        "pools": [{
            "limit_id": "claude-chat",
            "scope": "route_observation",
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
