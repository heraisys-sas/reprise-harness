"""Observed Claude Desktop free-plan pauses; one durable native admission per pause."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid
from zoneinfo import ZoneInfo
import time

MONTHS = {'janv': 1, 'févr': 2, 'mars': 3, 'avr': 4, 'mai': 5, 'juin': 6,
          'juil': 7, 'août': 8, 'sept': 9, 'oct': 10, 'nov': 11, 'déc': 12}
PAUSE = re.compile(r"En pause\s*:\s*votre utilisation gratuite sera de nouveau disponible\s+(\d{1,2})\s+([\wûé]+)\.?\s+à\s+(\d{2}):(\d{2})\.\s*Vous pourrez continuer à ce moment-là\.", re.IGNORECASE)


def parse_pause(text, now, zone):
    """Return an unambiguous near-future reset, never guess a timezone."""
    if not isinstance(zone, ZoneInfo):
        raise ValueError('Explicit ZoneInfo required')
    match = PAUSE.fullmatch(text.strip()) if isinstance(text, str) else None
    if not match or match[2].lower() not in MONTHS:
        return None
    current = datetime.fromtimestamp(now, zone)
    for year in (current.year, current.year + 1):
        try:
            local = datetime(year, MONTHS[match[2].lower()], int(match[1]), int(match[3]), int(match[4]))
        except ValueError:
            return None
        candidates = set()
        for fold in (0, 1):
            candidate = local.replace(tzinfo=zone, fold=fold).timestamp()
            if datetime.fromtimestamp(candidate, zone).replace(tzinfo=None) == local:
                candidates.add(candidate)
        if len(candidates) != 1:
            return None
        due = candidates.pop()
        if -86400 <= due - now <= 7 * 86400:
            return due
    return None


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS claude_quota_resumes (
        event_key TEXT PRIMARY KEY, chat_id TEXT NOT NULL, observed_at REAL NOT NULL,
        due REAL NOT NULL, fingerprint TEXT NOT NULL, pause_hash TEXT NOT NULL,
        status TEXT NOT NULL)''')
    db.commit()


def identity(observation):
    if not isinstance(observation, dict) or observation.get('status') != 'observed':
        return None
    try:
        chat = str(uuid.UUID(observation.get('chat_id', '')))
    except (ValueError, TypeError, AttributeError):
        return None
    fingerprint = observation.get('evidence_sha256')
    if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
        return None
    return chat, fingerprint


def register(db, observation, now, zone):
    initialize(db)
    ident = identity(observation)
    if ident is None:
        return None
    due = parse_pause(observation.get('pause_text'), now, zone)
    if due is None:
        return None
    chat, fingerprint = ident
    evidence = hashlib.sha256(observation['pause_text'].encode()).hexdigest()
    key = hashlib.sha256(json.dumps([chat, evidence, fingerprint], separators=(',', ':')).encode()).hexdigest()
    with db:
        db.execute('INSERT OR IGNORE INTO claude_quota_resumes VALUES (?,?,?,?,?,?,?)',
                   (key, chat, now, due, fingerprint, evidence, 'waiting'))
    return key



def register_notice(db, chat_id, pause_text, now, zone):
    """Retain an operator/UI notice without inventing native evidence or enabling a click."""
    chat = str(uuid.UUID(chat_id))
    due = parse_pause(pause_text, now, zone)
    if due is None:
        raise ValueError('Unambiguous recent reset required')
    initialize(db)
    pause_hash = hashlib.sha256(pause_text.encode()).hexdigest()
    key = hashlib.sha256(('notice:' + chat + ':' + pause_hash).encode()).hexdigest()
    with db:
        db.execute('INSERT OR IGNORE INTO claude_quota_resumes VALUES (?,?,?,?,?,?,?)',
                   (key, chat, now, due, '', pause_hash, 'awaiting_observation'))
    return key

def tick(db, observer, clicker, now, zone):
    """observer() and clicker(chat_id, fingerprint) must use the native helper."""
    import scheduler
    initialize(db)
    if not scheduler.enabled(db) or scheduler.get(db, 'claude_desktop_auto_enabled', True) is not True:
        return {'status': 'paused', 'submitted': 0}
    # An interrupted dispatch cannot be replayed safely after process restart.
    with db:
        db.execute("UPDATE claude_quota_resumes SET status='uncertain' WHERE status='dispatching'")
    try:
        observation = observer()
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'status': 'unavailable', 'submitted': 0}
    register(db, observation, now, zone)
    ident = identity(observation)
    if ident is None:
        return {'status': 'unavailable', 'submitted': 0}
    chat, fingerprint = ident
    with db:
        db.execute("UPDATE claude_quota_resumes SET status='superseded' WHERE chat_id=? AND status='awaiting_observation'",
                   (chat,))
    rows = db.execute("SELECT event_key,due,fingerprint,pause_hash FROM claude_quota_resumes WHERE chat_id=? AND status='waiting'", (chat,)).fetchall()
    submitted = 0
    for key, due, expected, pause_hash in rows:
        pause = observation.get('pause_text')
        changed_pause = isinstance(pause, str) and hashlib.sha256(pause.encode()).hexdigest() != pause_hash
        if expected != fingerprint or changed_pause:
            with db:
                db.execute("UPDATE claude_quota_resumes SET status='superseded' WHERE event_key=? AND status='waiting'", (key,))
            continue
        if (now < due or observation.get('busy') is not False
                or observation.get('continue_enabled') is not True
                or observation.get('composer_empty') is not True):
            continue
        # Reobserve immediately before reserving; the helper revalidates before clicking.
        try:
            current = observer()
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if (identity(current) != (chat, expected) or current.get('busy') is not False
                or current.get('continue_enabled') is not True
                or current.get('composer_empty') is not True
                or current.get('pause_text') != pause or not scheduler.enabled(db)
                or scheduler.get(db, 'claude_desktop_auto_enabled', True) is not True):
            continue
        with db:
            changed = db.execute("UPDATE claude_quota_resumes SET status='dispatching' WHERE event_key=? AND status='waiting'", (key,)).rowcount
        if not changed:
            continue
        try:
            outcome = clicker(chat, expected)
            state = ('submitted' if isinstance(outcome, dict) and outcome.get('status') == 'clicked'
                     else 'waiting' if isinstance(outcome, dict) and outcome.get('dispatch_attempted') is False
                     else 'uncertain')
        except Exception:
            state = 'uncertain'
        with db:
            db.execute('UPDATE claude_quota_resumes SET status=? WHERE event_key=?', (state, key))
        submitted += state == 'submitted'
    return {'status': 'observed', 'submitted': submitted}


def _resets_from_snapshot(snapshot, db_due=None):
    """Extract the quota deadline defensively from an observation snapshot."""
    found = [db_due] if isinstance(db_due, (int, float)) else []
    def walk(value, depth=0):
        if depth > 6:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'resets_at' and isinstance(item, (int, float)) and not isinstance(item, bool):
                    found.append(item)
                else:
                    walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)
    if isinstance(snapshot, dict):
        walk(snapshot)
    return max(found) if found else None


def admit_queue_continuation(db, root, quick_state, project_root, now=None, snapshot=None, observe=None):
    """Opt-in (#122): at the quota deadline, admit the continuation task into the project queue.

    Deadline sources: the native observation snapshot (run_native) and/or the waiting rows of
    claude_quota_resumes; without either, the session observer (no Accessibility needed) refreshes it.
    Gated by scheduler.enabled + claude_desktop_auto_enabled + claude_queue_channel (opt-in).
    Dedup: one admission per deadline (claude_queue_admitted_for).
    """
    import scheduler
    now = time.time() if now is None else now
    if not scheduler.enabled(db) or scheduler.get(db, 'claude_desktop_auto_enabled', True) is not True:
        return {'status': 'paused'}
    if scheduler.get(db, 'claude_queue_channel') is not True:
        return {'status': 'queue_disabled'}
    if snapshot is None and observe is None:
        try:
            from claude_session_observer import status as observer_status
            def observe():
                return observer_status(root, project_root=project_root, quick_state=quick_state, refresh_now=True)
        except Exception as error:
            return {'status': 'observe_failed', 'error': type(error).__name__}
    if observe is not None:
        try:
            snapshot = observe()
        except Exception as error:
            return {'status': 'observe_failed', 'error': type(error).__name__}
    due_row = db.execute("SELECT MAX(due) FROM claude_quota_resumes WHERE status='waiting'").fetchone()
    resets = _resets_from_snapshot(snapshot if isinstance(snapshot, dict) else None,
                                   due_row[0] if due_row else None)
    if resets is None:
        return {'status': 'no_deadline'}
    if now < resets:
        return {'status': 'waiting', 'resets_at': resets}
    if scheduler.get(db, 'claude_queue_admitted_for') == str(resets):
        return {'status': 'already_admitted'}
    from desktop_task_store import TaskStore
    store = TaskStore(quick_state, project_root)
    try:
        key = f'claude-continuation-{int(resets)}'
        stamp = time.strftime('%Y-%m-%d %H:%M', time.localtime(resets))
        brief = ('Continuation après quota (file opt-in #122) : la pause 5h du chat Desktop est levée '
                 f'(échéance {stamp}). Le travail interrompu vit dans le workspace monté sur /workspace : '
                 'inspecte l’état des fichiers, identifie la dernière étape accomplie, et poursuis la tâche '
                 'restante de manière bornée. Puis submit_result avec le résumé. Si rien n’indique un '
                 'travail en cours, signale-le au lieu d’inventer.')
        r = store.admit(key, brief)
    finally:
        store.close()
    with db:
        scheduler.put(db, 'claude_queue_admitted_for', str(resets))
    return {'status': 'admitted', 'request_key': key, 'resets_at': resets}


def status(db):
    initialize(db)
    return [dict(zip(('event_key', 'chat_id', 'observed_at', 'due', 'fingerprint', 'status'), row))
            for row in db.execute('SELECT event_key,chat_id,observed_at,due,fingerprint,status FROM claude_quota_resumes ORDER BY observed_at DESC LIMIT 100')]



def run_quick(db, now, client_factory=None):
    """Use the same daemon API as delegating harnesses, never treat admission as a turn."""
    from claude_desktop_rpc import Client, REPRISE_DISPATCH_SOCKET
    import scheduler
    initialize(db)
    client = None
    try:
        client = (client_factory or Client)(None, {'socket': str(REPRISE_DISPATCH_SOCKET)}, timeout=5)
        client.call('initialize', {'clientInfo': {'name': 'reprise-scheduler', 'version': '1'}})
        capabilities = client.call('quick_entry/capabilities', {})
        if capabilities.get('dispatch_supported') is not True or capabilities.get('resume_validation_supported') is not True:
            return {'status': 'dispatch_driver_unavailable', 'submitted': 0, 'transport': 'quick-apply'}
        submitted = 0
        with db:
            db.execute("UPDATE claude_quota_resumes SET status='uncertain' WHERE status='dispatching'")
        rows = db.execute("SELECT event_key,chat_id FROM claude_quota_resumes WHERE due<=? AND status='waiting'", (now,)).fetchall()
        for key, chat in rows:
            if not scheduler.enabled(db) or scheduler.get(db, 'claude_desktop_auto_enabled', True) is not True:
                break
            with db:
                changed = db.execute("UPDATE claude_quota_resumes SET status='dispatching' WHERE event_key=? AND status='waiting'", (key,)).rowcount
            if not changed:
                continue
            try:
                receipt = client.call('quick_entry/submit', {'request_key': 'resume-' + key,
                    'prompt': 'retry', 'chatId': chat, 'purpose': 'continuation'})
                state = ('submitted' if receipt.get('dispatch_attempted') is True and receipt.get('admission_only') is False
                         else 'admitted' if receipt.get('admission_only') is True else 'uncertain')
            except Exception:
                state = 'uncertain'
            with db:
                db.execute('UPDATE claude_quota_resumes SET status=? WHERE event_key=?', (state, key))
            submitted += state == 'submitted'
        return {'status': 'observed', 'submitted': submitted, 'transport': 'quick-apply'}
    finally:
        if client:
            client.close()

def run_native(db, root, state, now=None):
    """Serialize native ticks; never interact from concurrent scheduler processes."""
    import fcntl
    import time
    import scheduler
    if not scheduler.enabled(db) or scheduler.get(db, 'claude_desktop_auto_enabled', True) is not True:
        return {'status': 'paused', 'submitted': 0}
    transport = scheduler.get(db, 'claude_desktop_transport', 'quick-apply')
    if transport == 'quick-apply':
        # The daemon owns native delivery and its session preconditions.
        with (Path(state) / 'claude-quota.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy', 'submitted': 0}
            return run_quick(db, time.time() if now is None else now)
    if transport == 'cdp':
        return _run_cdp(db, state, time.time() if now is None else now)
    # Unknown or unmigrated transport — fail closed, never attempt an AX fallback.
    return {'status': 'unknown_transport', 'submitted': 0, 'transport': transport}


def _run_cdp(db, state, now=None):
    """CDP transport: contact the Quick Apply daemon for resume.

    State lives in claude_quota_resumes (event_key, chat_id, status:
    waiting/awaiting_observation -> dispatching -> submitted/uncertain).
    Mirrors run_quick lines 191-211.
    """
    import fcntl
    from claude_desktop_rpc import Client
    from claude_desktop import DEFAULT_SOCKET
    initialize(db)
    with (Path(state) / 'cdp-quota.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'submitted': 0}
        client = Client(str(DEFAULT_SOCKET))
        try:
            client.call('initialize', {'clientInfo': {'name': 'reprise-scheduler', 'version': '1'}})
            status_resp = client.call('cdp/status', {})
            if not isinstance(status_resp, dict) or not status_resp.get('ok'):
                # A non-dict reply (protocol violation) must not crash into the
                # generic 'error' branch and lose the cdp_unavailable verdict.
                detail = (status_resp.get('reason') if isinstance(status_resp, dict) else None) \
                    or 'CDP non disponible'
                out = {'status': 'cdp_unavailable', 'submitted': 0, 'transport': 'cdp-pipe',
                       'detail': detail}
                if isinstance(status_resp, dict) and status_resp.get('status'):
                    out['daemon_status'] = status_resp['status']
                return out
            # Mark any stale dispatching as uncertain first
            with db:
                db.execute("UPDATE claude_quota_resumes SET status='uncertain' WHERE status='dispatching'")
            rows = db.execute(
                "SELECT event_key,chat_id FROM claude_quota_resumes WHERE due<=? AND status='waiting'",
                (now or time.time(),)).fetchall()
            submitted = 0
            for key, chat_id in rows:
                with db:
                    changed = db.execute(
                        "UPDATE claude_quota_resumes SET status='dispatching' WHERE event_key=? AND status='waiting'",
                        (key,)).rowcount
                if not changed:
                    continue
                try:
                    resp = client.call('cdp/resume', {'chat_id': chat_id, 'prompt': 'retry'})
                except Exception:
                    resp = None
                # Trust the daemon's own stage when present: ok=True with
                # stage='incertain' (send click failed) must not read as sent.
                if isinstance(resp, dict) and resp.get('stage') in ('envoyé', 'incertain'):
                    stage = resp['stage']
                else:
                    stage = 'envoyé' if isinstance(resp, dict) and resp.get('ok') else 'incertain'
                with db:
                    db.execute('UPDATE claude_quota_resumes SET status=? WHERE event_key=?',
                               ('submitted' if stage == 'envoyé' else 'uncertain', key))
                submitted += stage == 'envoyé'
            return {'status': 'observed', 'submitted': submitted, 'transport': 'cdp-pipe'}
        except Exception as exc:
            return {'status': 'error', 'submitted': 0, 'transport': 'cdp-pipe',
                    'reason': str(exc)}
        finally:
            client.close()
