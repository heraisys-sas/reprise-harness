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
    """Admit one advisory queue task per verified waiting quota event (#208).

    The queue is not a native same-chat resume. Its prompt carries the durable
    chat/event identity so an executor can reconcile before any work. A legacy
    deadline-only admission is uncertain: never silently emit a replacement.
    """
    import scheduler
    now = time.time() if now is None else now
    if not scheduler.enabled(db) or scheduler.get(db, 'claude_desktop_auto_enabled', True) is not True:
        return {'status': 'paused'}
    if scheduler.get(db, 'claude_queue_channel') is not True:
        return {'status': 'queue_disabled'}
    initialize(db)
    # A durable verified event is sufficient to admit the advisory task at its
    # deadline. A new observer measurement is not a prerequisite: it may be
    # unavailable precisely because the original process was interrupted.
    rows = db.execute("""SELECT event_key,chat_id,due FROM claude_quota_resumes
                         WHERE status='waiting' ORDER BY due,event_key""").fetchall()
    if not rows and snapshot is None and observe is None:
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
            if not rows:
                return {'status': 'observe_failed', 'error': type(error).__name__}
            # Existing durable event identities remain usable without a fresh
            # observation. Never manufacture a new event from the failed read.
            snapshot = None

    # Snapshot-derived reset dates alone are not permission to create a task.
    if not rows:
        return {'status': 'no_verified_session' if _resets_from_snapshot(snapshot) is not None
                else 'no_deadline'}
    due_rows = [(key, chat, due) for key, chat, due in rows if due <= now]
    if not due_rows:
        return {'status': 'waiting', 'resets_at': min(due for _, _, due in rows)}

    legacy_deadline = scheduler.get(db, 'claude_queue_admitted_for')
    try:
        # Earlier versions wrote str(resets) where resets could be an int from
        # an observation or a float from SQLite (e.g. "5000" vs "5000.0").
        legacy_due = float(legacy_deadline) if legacy_deadline is not None else None
    except (ValueError, TypeError, OverflowError):
        legacy_due = None
    from desktop_task_store import TaskStore
    store = TaskStore(quick_state, project_root)
    admitted = []
    already = []
    legacy_uncertain = []
    try:
        for event_key, chat_id, due in due_rows:
            # Older versions admitted a generic task keyed only by the reset
            # timestamp. We cannot assign it to one of several chats; preserve
            # that uncertainty rather than silently duplicating the work.
            if legacy_due is not None and legacy_due == due:
                legacy_uncertain.append(event_key)
                continue
            key = f'claude-continuation-{event_key}'
            stamp = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(due))
            brief = ('Continuation après quota (file opt-in #122, événement #208). '
                     f'Identité observée : chat_id={chat_id}, event_key={event_key}, '
                     f'échéance={stamp}. Admission en file ≠ reprise du chat natif. '
                     'Vérifie explicitement le propriétaire de la session et les '
                     'travaux déjà réalisés ; ne suppose pas avoir accès à son '
                     'historique. Si la route vers cette session est indisponible '
                     'ou ambiguë, signale needs_review sans déclencher de nouvelle '
                     'inférence. Ne change pas de fournisseur ni de mode de facturation. '
                     'Sinon, reprends seulement les tâches restantes et retourne '
                     'un résumé via submit_result.')
            if store._row(key) is not None:
                already.append(key)
                continue
            store.admit(key, brief, chat_id=chat_id)
            admitted.append(key)
    finally:
        store.close()
    if admitted:
        return {'status': 'admitted', 'request_key': admitted[0],
                'request_keys': admitted, 'count': len(admitted),
                'legacy_uncertain_events': legacy_uncertain}
    if legacy_uncertain:
        return {'status': 'legacy_uncertain', 'event_keys': legacy_uncertain,
                'instruction': 'Reconcile legacy deadline-only queue admission before new delivery.'}
    return {'status': 'already_admitted', 'request_keys': already}


def quota_state(db, now=None):
    """Readable quota state (#132): blocked while an unexpired native resume is waiting.

    The only deadline the module knows lives in claude_quota_resumes (status 'waiting');
    no field is invented beyond it.
    """
    now = time.time() if now is None else now
    initialize(db)
    row = db.execute(
        "SELECT MAX(due) FROM claude_quota_resumes WHERE status IN ('waiting','awaiting_observation') AND due>?",
        (now,)).fetchone()
    if row and row[0] is not None:
        return {'status': 'blocked', 'reason': 'quota', 'resume_at': row[0]}
    return {'status': 'ok', 'reason': None, 'resume_at': None}


def status(db):
    initialize(db)
    return [dict(zip(('event_key', 'chat_id', 'observed_at', 'due', 'fingerprint', 'status'), row))
            for row in db.execute('SELECT event_key,chat_id,observed_at,due,fingerprint,status FROM claude_quota_resumes ORDER BY observed_at DESC LIMIT 100')]


BANNER_MIN_INTERVAL = 600.0  # (#135) au plus un cycle Chrome / 10 min
BANNER_LAST_ATTEMPT = 'banner_cdp_last_attempt'


def register_banner_resume(db, resets_at, pause_hash, now):
    """(#135) Alimente claude_quota_state via le chemin #132 (lignes
    'awaiting_observation') : la bannière CDP n'a pas de chat_id ni de
    preuve native, donc chat_id/fingerprint restent vides."""
    initialize(db)
    key = hashlib.sha256(('banner:' + str(int(resets_at))).encode()).hexdigest()
    with db:
        db.execute('INSERT OR IGNORE INTO claude_quota_resumes VALUES (?,?,?,?,?,?,?)',
                   (key, '', now, resets_at, '', pause_hash, 'awaiting_observation'))
    return key


def refresh_from_banner(db, now=None, zone=None, banner_reader=None):
    """(#135) Repli bannière CDP : uniquement si bloqué sans échéance connue.

    En état ok ou avec un resume_at futur du journal (#133) : zéro lancement
    Chrome. Sinon, garde anti-répétition (≤ 1 cycle Chrome / 10 min, persisté
    en scheduler_meta), lecture read-only de la bannière dans le Chrome
    dédié, puis enregistrement de l'échéance via register_banner_resume.
    Ne lève jamais : le tick reste non bloquant.
    """
    import scheduler
    now = time.time() if now is None else now
    state = quota_state(db, now=now)
    # L'état courant du tick (#132, scheduler_meta 'claude_quota_state') porte
    # le « bloqué sans échéance » / « indéterminé » (unavailable) que
    # quota_state seul ne peut pas exprimer.
    cached = scheduler.get(db, 'claude_quota_state')
    cached_status = cached.get('status') if isinstance(cached, dict) else None
    if state.get('resume_at') is not None:
        return {'status': 'skipped', 'reason': 'deadline_known_or_ok'}
    if state.get('status') == 'ok' and cached_status not in ('blocked', 'unavailable'):
        return {'status': 'skipped', 'reason': 'deadline_known_or_ok'}
    if state.get('status') == 'ok' and isinstance(cached, dict) and cached.get('resume_at') is not None:
        return {'status': 'skipped', 'reason': 'deadline_known_or_ok'}
    if not isinstance(zone, ZoneInfo):
        return {'status': 'skipped', 'reason': 'zone_required'}
    last = scheduler.get(db, BANNER_LAST_ATTEMPT)
    if isinstance(last, (int, float)) and 0 <= now - last < BANNER_MIN_INTERVAL:
        return {'status': 'throttled'}
    with db:
        scheduler.put(db, BANNER_LAST_ATTEMPT, now)
    if banner_reader is None:
        from claude_quota_banner import read_banner as banner_reader
    try:
        result = banner_reader(now=now, zone=zone)
    except Exception as error:
        return {'status': 'banner_failed', 'error': type(error).__name__}
    if not isinstance(result, dict):
        return {'status': 'banner_failed', 'error': 'TypeError'}
    resets = result.get('resets_at')
    if result.get('deadline_status') == 'future' and isinstance(resets, (int, float)) \
            and not isinstance(resets, bool) and resets > now:
        register_banner_resume(
            db, float(resets),
            hashlib.sha256(str(result.get('raw_text', '')).encode()).hexdigest(), now)
        return {'status': 'deadline_recorded', 'resets_at': float(resets)}
    return {'status': 'no_deadline', 'detail': result.get('status')}



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
    waiting/awaiting_observation (en attente, non dispatché) -> dispatching -> submitted/uncertain).
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
