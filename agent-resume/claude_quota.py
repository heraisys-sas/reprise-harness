"""Observed Claude Desktop free-plan pauses; one durable native admission per pause."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import uuid
from zoneinfo import ZoneInfo

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


def status(db):
    initialize(db)
    return [dict(zip(('event_key', 'chat_id', 'observed_at', 'due', 'fingerprint', 'status'), row))
            for row in db.execute('SELECT event_key,chat_id,observed_at,due,fingerprint,status FROM claude_quota_resumes ORDER BY observed_at DESC LIMIT 100')]


def native_observer(helper, chat_id=None):
    def observe():
        reply = subprocess.run([str(helper), 'observe'] + (['--chat', chat_id] if chat_id else []), capture_output=True, text=True, timeout=15)
        return json.loads(reply.stdout)
    return observe


def native_clicker(helper):
    def click(chat_id, fingerprint):
        reply = subprocess.run([str(helper), 'continue', '--chat', chat_id, '--evidence', fingerprint],
                               capture_output=True, text=True, timeout=15)
        return json.loads(reply.stdout)
    return click



def run_quick(db, now, client_factory=None):
    """Use the same daemon API as delegating harnesses, never treat admission as a turn."""
    from claude_desktop_rpc import Client
    import scheduler
    initialize(db)
    client = None
    try:
        client = (client_factory or Client)(None, {'socket': str(Path.home()/'.claude-desktop/quick-entry.sock')}, timeout=5)
        client.call('initialize', {'clientInfo': {'name': 'reprise-scheduler', 'version': '1'}})
        capabilities = client.call('quick_entry/capabilities', {})
        if capabilities.get('dispatch_supported') is not True or capabilities.get('resume_validation_supported') is not True:
            return {'status': 'dispatch_driver_unavailable', 'submitted': 0, 'transport': 'quick-apply'}
        submitted = 0
        with db:
            db.execute("UPDATE claude_quota_resumes SET status='uncertain' WHERE status='dispatching'")
        rows = db.execute("SELECT event_key,chat_id FROM claude_quota_resumes WHERE due<=? AND status IN ('waiting','awaiting_observation')", (now,)).fetchall()
        for key, chat in rows:
            if not scheduler.enabled(db) or scheduler.get(db, 'claude_desktop_auto_enabled', True) is not True:
                break
            with db:
                changed = db.execute("UPDATE claude_quota_resumes SET status='dispatching' WHERE event_key=? AND status IN ('waiting','awaiting_observation')", (key,)).rowcount
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
    if scheduler.get(db, 'claude_desktop_transport', 'quick-apply') == 'quick-apply':
        # The daemon owns native delivery and its session preconditions.
        with (Path(state) / 'claude-quota.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy', 'submitted': 0}
            return run_quick(db, time.time() if now is None else now)
    root = Path(root)
    helper = root.parent.parent / 'MacOS/ClaudeQuota'
    with (Path(state) / 'claude-quota.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'submitted': 0}
        observer = native_observer(helper)
        # Timezone comes from the native desktop, never an implicit UTC daemon locale.
        initial = observer()
        zone_name = initial.get('timezone')
        if not isinstance(zone_name, str):
            return {'status': initial.get('reason', 'unavailable'), 'submitted': 0}
        zone = ZoneInfo(zone_name)
        reads = iter([initial])
        def read():
            return next(reads, None) or observer()
        return tick(db, read, native_clicker(helper), time.time() if now is None else now, zone)
