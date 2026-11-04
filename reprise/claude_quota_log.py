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

from datetime import datetime, timezone
import json
import math
import os
import re
from hashlib import sha256
import stat
from time import time as _now
from zoneinfo import ZoneInfo

LOG_PATH = os.path.expanduser("~/Library/Logs/Claude/claude.ai-web.log")
MAX_LOG_BYTES = 2_000_000
DEFAULT_EVENT_MAX_AGE = 300

_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+\[error\]\s+\[COMPLETION\]")
_HHMM = re.compile(r'\b([0-2]?\d:[0-5]\d)\b')
_QUOTA_TYPES = {"exceeded_limit", "rate_limit"}

# Blocage client : le bouton est désactivé alors que du texte est présent, sans
# aucune requête émise — donc sans ligne de journal. À croiser avec ce module.
CLIENT_SIDE_NOTE = "bouton d'envoi désactivé avec texte présent, aucun rejet serveur"


def _read_source(path: str, max_bytes: int):
    """Bounded read with an explicit source status; never returns a path or raw text."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError('Positive byte limit required')
    max_bytes = min(max_bytes, MAX_LOG_BYTES)
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_CLOEXEC', 0)
        descriptor = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return 'unreadable', [], False, 'unsupported_file_type'
        handle = os.fdopen(descriptor, "rb")
        descriptor = None  # The file object now owns the descriptor.
        with handle:
            size = os.fstat(handle.fileno()).st_size
            truncated = size > max_bytes
            budget = max_bytes
            if size > max_bytes:
                handle.seek(size - max_bytes)
                skipped = handle.readline(budget)  # Drop a partial first line, still bounded.
                budget -= len(skipped)
            raw = handle.read(budget)
        return 'readable', raw.decode("utf-8", "replace").splitlines(), truncated, None
    except FileNotFoundError:
        return 'absent', [], False, 'source_absent'
    except OSError:
        return 'unreadable', [], False, 'source_unavailable'
    finally:
        if descriptor is not None:
            os.close(descriptor)


def read_tail(path: str = LOG_PATH, max_bytes: int = MAX_LOG_BYTES) -> list[str]:
    """Backwards-compatible bounded tail reader; absent/unreadable maps to []."""
    return _read_source(path, max_bytes)[1]


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


def _valid_source_datetime(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return None


def _source_epoch(value, source_zone):
    """Resolve a timezone-less producer timestamp only with an attested IANA zone."""
    local = _valid_source_datetime(value)
    if local is None:
        return None, 'invalid'
    if not isinstance(source_zone, ZoneInfo):
        return None, 'unknown_timezone'
    candidates = set()
    for fold in (0, 1):
        aware = local.replace(tzinfo=source_zone, fold=fold)
        try:
            epoch = aware.timestamp()
            roundtrip = datetime.fromtimestamp(epoch, source_zone).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            continue
        if roundtrip == local:
            candidates.add(epoch)
    if not candidates:
        return None, 'nonexistent_local_time'
    if len(candidates) != 1:
        return None, 'ambiguous_local_time'
    return next(iter(candidates)), 'known'


def _valid_reset(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        epoch = float(value)
        if not math.isfinite(epoch) or epoch <= 0:
            return None
        datetime.fromtimestamp(epoch, timezone.utc)
        return epoch
    except (OverflowError, OSError, ValueError):
        return None


def _finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _latest_from_lines(lines, current, source_zone=None):
    for line in reversed(lines):
        match = _LINE.match(line)
        if not match:
            continue
        payload = _completion_payload(line, match.end())
        if payload is None:
            continue
        # Invalid wall-clock text is not ordered evidence; an unknown timezone is.
        source_local = _valid_source_datetime(match.group('ts'))
        if source_local is None:
            continue
        source_observed_at, source_timestamp_status = _source_epoch(match.group('ts'), source_zone)
        reset = _valid_reset(payload.get('resetsAt'))
        expired = reset is not None and reset <= current
        return {
            "type": payload.get("type"),
            "resets_at": reset,
            # Legacy alias retained. It is None until the producer timezone is attested.
            "observed_at": source_observed_at,
            "source_observed_at": source_observed_at,
            "source_timestamp_status": source_timestamp_status,
            "expired": expired if reset is not None else None,
            "blocked": True if reset is not None and reset > current else None,
            "evidence_sha256": sha256(line.encode("utf-8", "replace")).hexdigest(),
        }


def latest(path: str = LOG_PATH, now: float | None = None,
           source_zone=None) -> dict | None:
    """Dernier événement quota reconnu, sans exposer le contenu du journal.

    Les erreurs sans type quota ne lèvent pas un événement quota antérieur.
    `blocked` n'est qu'un indice historique avec reset futur; compte, pool et
    état courant restent inconnus.
    """
    current = _now() if now is None else now
    return _latest_from_lines(read_tail(path), current, source_zone)


def source_observation(path: str = LOG_PATH, now: float | None = None,
                       source_zone=None, max_bytes: int = MAX_LOG_BYTES,
                       event_max_age: float = DEFAULT_EVENT_MAX_AGE) -> dict:
    """Explicit, normalized renderer-log observation; never establishes current quota.

    ``source_zone`` is accepted only when the producer's IANA timezone is attested.
    The local machine timezone is deliberately not inferred as the producer timezone.
    ``observed_at`` is this read time; ``source_observed_at`` is the event time, if
    unambiguous. No line text, account, chat, pool, percentage, or current availability
    is returned.
    """
    read_at = _now() if now is None else now
    if not _finite_number(read_at):
        raise ValueError('Finite read timestamp required')
    if not _finite_number(event_max_age) or event_max_age < 1:
        raise ValueError('Positive finite event age required')
    read_status, lines, truncated, failure = _read_source(path, max_bytes)
    zone_name = source_zone.key if isinstance(source_zone, ZoneInfo) else None
    result = {
        'schema_version': 1, 'engine': 'claude-desktop', 'source': 'claude.renderer_log',
        'source_status': read_status, 'event_status': 'not_checked',
        'observation_status': read_status, 'observed_at': float(read_at),
        'source_observed_at': None, 'source_timezone': zone_name,
        'source_timestamp_status': 'unknown_timezone' if zone_name is None else 'unknown',
        'event_type': None, 'event_age_seconds': None, 'event_freshness': 'unknown',
        'event_max_age_seconds': float(event_max_age), 'resets_at': None,
        'reset_relation': 'unknown', 'current_quota_status': 'unknown',
        'current_blocked': None, 'current_available': None,
        'account_id': None, 'account_key': None, 'chat_id': None, 'session_key': None,
        'pool_id': None, 'quota_pool_key': None, 'used_percent': None,
        'remaining_percent': None, 'duration_minutes': None,
        'truncated': truncated,
        'issues': ['account_unattributed', 'chat_unattributed', 'pool_unattributed',
                   'quantity_unreported', 'current_quota_unknown'],
    }
    if read_status != 'readable':
        result['event_status'] = 'not_checked'
        result['failure'] = failure
        return result
    if truncated:
        result['issues'].append('tail_truncated')
    entry = _latest_from_lines(lines, float(read_at), source_zone)
    if entry is None:
        result['event_status'] = 'no_event_in_tail' if truncated else 'no_event'
        result['observation_status'] = result['event_status']
        return result
    source_time = entry['source_observed_at']
    age = float(read_at) - source_time if source_time is not None else None
    if age is None:
        freshness = 'unknown'
    elif age < 0:
        freshness = 'clock_skew'
    elif age > event_max_age:
        freshness = 'stale'
    else:
        freshness = 'recent'
    reset = entry['resets_at']
    result.update(event_status='recognized', observation_status='observed',
        source_observed_at=source_time,
        source_timestamp_status=entry['source_timestamp_status'],
        event_type=entry['type'], event_age_seconds=age, event_freshness=freshness,
        resets_at=reset,
        reset_relation=('future_at_read' if reset > read_at else 'elapsed_at_read') if reset is not None else 'unknown')
    if source_time is None:
        result['issues'].append(entry['source_timestamp_status'])
    if reset is None:
        result['issues'].append('reset_unreported_or_invalid')
    return result


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


def observe(path: str = LOG_PATH, now: float | None = None, zone=None,
            source_zone=None) -> dict:
    """Vue unifiée : ce que le journal permet d'affirmer, et à quelle heure.

    `source` vaut « journal » quand un événement quota reconnu est trouvé.
    Aucune heure n'est déduite d'un « HH:MM » sans date : cette ambiguïté
    (aujourd'hui ou demain ?) est précisément ce que `parse_pause` refusait.
    """
    entry = latest(path, now, source_zone=source_zone)
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
                engine: str = "claude-desktop", source_zone=None) -> dict | None:
    """Legacy event payload retained for compatibility; not a current quota status.

    `used_percent` reste **None** : le journal ne le publie pas, et l'inconnu
    n'est pas zéro. New integrations should use `source_observation`, which also
    separates read time from event time and does not expose a current blocked flag.
    """
    entry = latest(path, now, source_zone=source_zone)
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
    import argparse

    parser = argparse.ArgumentParser(description="Quota Claude Desktop depuis le journal.")
    parser.add_argument("--path", default=LOG_PATH)
    args = parser.parse_args()
    print(json.dumps(source_observation(args.path), ensure_ascii=False, indent=2,
                     default=str))
