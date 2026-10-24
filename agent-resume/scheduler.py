"""Automatic retries of confirmed Codex quota interruptions, once per event."""
import json
from collections import Counter
import math
from pathlib import Path
import time
import uuid

RECENT = 86400
POLL = 300
PROMPT = ('Ton travail a été interrompu par une limite d’usage. Vérifie où tu en étais dans les derniers échanges. '
          'Vérifie aussi l’état de tes sous-agents et récupère leurs résultats disponibles ; '
          'ne relance que les tâches encore nécessaires. Reprends le travail restant. '
          'Vérifie les changements et résultats déjà présents avant toute action pour éviter '
          'les doublons. Respecte les instructions, autorisations et limites de la session. '
          'Ne change pas de fournisseur et ne bascule pas vers une facturation API. '
          'Si la tâche est déjà terminée ou attend une réponse humaine, arrête-toi.')


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS scheduler_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS automation_events ('
               'event_key TEXT PRIMARY KEY, task_id TEXT UNIQUE NOT NULL, session TEXT NOT NULL, '
               'interrupted_at REAL NOT NULL, source_updated REAL NOT NULL, source_cwd TEXT NOT NULL, '
               'created REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0)')
    db.commit()


def get(db, key, default=None):
    row = db.execute('SELECT value FROM scheduler_meta WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db, key, value):
    db.execute('INSERT OR REPLACE INTO scheduler_meta VALUES (?,?)', (key, json.dumps(value)))


def enabled(db):
    return get(db, 'auto_enabled', True) is True


def snapshot():
    from detect import scan
    return scan()


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def confirmed(row, now, max_age=RECENT):
    try:
        uuid.UUID(row['id'])
        return (row.get('state') == 'quota_confirmed' and not row.get('active', False)
                and not row.get('is_subagent', False) and row.get('automatic_resume_supported') is True
                and number(row.get('interrupted_at')) and number(row.get('updated'))
                and 0 <= now - row['interrupted_at'] <= max_age
                and isinstance(row.get('cwd'), str) and Path(row['cwd']).is_absolute()
                and Path(row['cwd']).is_dir()
                and (row.get('retry_at') is None or number(row['retry_at'])))
    except (ValueError, KeyError, TypeError, OSError):
        return False


def same(event, row, now):
    return (row is not None and confirmed(row, now, max_age=30 * 86400)
            and row['interrupted_at'] == event['interrupted_at']
            and row['updated'] == event['source_updated'] and row['cwd'] == event['source_cwd'])


def manual_conflict(db, session):
    return db.execute("SELECT 1 FROM tasks t LEFT JOIN automation_events e ON e.task_id=t.id "
                      "WHERE t.session=? AND e.task_id IS NULL AND t.status IN ('paused','ready','running') LIMIT 1",
                      (session,)).fetchone() is not None


def coverage_proof(data):
    """A bounded successful DB scan can isolate individual rollout failures."""
    evidence = data.get('session_coverage')
    counts = data.get('coverage')
    if (data.get('scan_complete') is not True or not isinstance(evidence, dict)
            or not isinstance(counts, dict) or len(evidence) > 100
            or counts.get('total') != len(evidence)
            or any(not isinstance(key, str) or value not in
                   ('recognized', 'unknown_format', 'unreadable')
                   for key, value in evidence.items())):
        return None
    return evidence


def session_uncertain(data, session):
    evidence = coverage_proof(data)
    return ((bool(data.get('error')) and evidence is None)
            or (evidence is not None and evidence.get(session) != 'recognized'))


def reconcile(db, data, now=None):
    now = time.time() if now is None else now
    if data.get('error') and coverage_proof(data) is None:
        raise ValueError('Lecture des sessions Codex indisponible ; reprise différée')
    rows = {row['id']: row for row in data.get('sessions', [])}
    with db:
        db.execute('BEGIN IMMEDIATE')
        for event in db.execute("SELECT e.* FROM automation_events e JOIN tasks t ON t.id=e.task_id "
                                "WHERE t.status IN ('ready','waiting_quota')").fetchall():
            if session_uncertain(data, event['session']):
                continue
            if not same(event, rows.get(event['session']), now) or manual_conflict(db, event['session']):
                db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (event['task_id'],))
            elif enabled(db) and rows[event['session']].get('retry_at') is not None:
                # Recover waits postponed by the former mandatory quota probe. Never
                # touch consumed attempts or adapter deferrals already marked ready.
                db.execute("UPDATE tasks SET status='ready',due=? WHERE id=? "
                           "AND status='waiting_quota' AND attempts=0 AND ?=0",
                           (rows[event['session']]['retry_at'], event['task_id'], event['consumed']))
        if not enabled(db):
            return
        for row in rows.values():
            if session_uncertain(data, row['id']) or not confirmed(row, now) or manual_conflict(db, row['id']):
                continue
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, f"reprise:quota:{row['id']}:{float(row['interrupted_at'])}"))
            if db.execute('SELECT 1 FROM automation_events WHERE event_key=?', (key,)).fetchone():
                continue
            if db.execute("SELECT 1 FROM tasks WHERE session=? AND status='running'", (row['id'],)).fetchone():
                continue
            due = row.get('retry_at')
            db.execute('INSERT INTO automation_events VALUES (?,?,?,?,?,?,?,0)',
                       (key, key, row['id'], row['interrupted_at'], row['updated'], row['cwd'], now))
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,0)',
                       (key, row['id'], row['cwd'], PROMPT, due if due is not None else now,
                        'ready' if due is not None else 'waiting_quota'))
            put(db, 'quota_checked_at', None)
            put(db, 'quota_allowed', False)


def quota_state(db, executable, now=None, probe=None, deadline=None, force=False):
    now = time.time() if now is None else now
    checked = get(db, 'quota_checked_at')
    if not force and checked is not None and 0 <= now - checked < POLL:
        return get(db, 'quota_allowed', False), get(db, 'quota_retry_at')
    if probe is None or deadline is None:
        from quota import read_limits, next_retry
        probe = probe or read_limits
        deadline = deadline or next_retry
    try:
        result = probe(executable)
        allowed = isinstance(result, dict) and result.get('ordinaryUsageAllowed') is True
        retry = deadline(result, now) if isinstance(result, dict) else None
        if not number(retry):
            retry = None
    except Exception:
        allowed, retry = False, None
    with db:
        put(db, 'quota_checked_at', now)
        put(db, 'quota_allowed', allowed)
        put(db, 'quota_retry_at', retry)
    return allowed, retry


def refresh_waiting(db, executable, now=None):
    now = time.time() if now is None else now
    if not enabled(db) or not db.execute("SELECT 1 FROM automation_events e JOIN tasks t ON t.id=e.task_id "
                                        "WHERE t.status='waiting_quota' LIMIT 1").fetchone():
        return
    allowed, retry = quota_state(db, executable, now)
    with db:
        if allowed:
            db.execute("UPDATE tasks SET status='ready',due=? WHERE status='waiting_quota' "
                       "AND id IN (SELECT task_id FROM automation_events)",
                       (max(now, retry or now),))
        else:
            db.execute("UPDATE tasks SET status='waiting_quota',due=? WHERE status='waiting_quota' "
                       "AND id IN (SELECT task_id FROM automation_events)",
                       (max(now + POLL, retry or now),))


def session_unchanged(db, event, now, read):
    data = read()
    if session_uncertain(data, event['session']):
        return False
    row = next((r for r in data.get('sessions', []) if r['id'] == event['session']), None)
    if not same(event, row, now) or manual_conflict(db, event['session']):
        with db:
            db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (event['task_id'],))
        return False
    return True


def preflight(db, task, executable, now=None, read=None):
    """Return False without running a model if any prerequisite is uncertain."""
    event = db.execute('SELECT * FROM automation_events WHERE task_id=?', (task['id'],)).fetchone()
    if event is None:
        return True
    now = time.time() if now is None else now
    if not enabled(db) or event['consumed'] or task['attempts'] or task['due'] > now:
        return False
    read = read or snapshot
    if not session_unchanged(db, event, now, read):
        return False
    # An announced deadline authorizes a single attempt, not a claim that
    # quota has recovered. Measure only when the interruption had no deadline.
    data = read()
    if session_uncertain(data, event['session']):
        return False
    row = next((r for r in data.get('sessions', []) if r['id'] == event['session']), None)
    if not same(event, row, now) or manual_conflict(db, event['session']):
        with db:
            db.execute("UPDATE tasks SET status='cancelled' WHERE id=?", (task['id'],))
        return False
    due = row.get('retry_at')
    if due is not None:
        return due <= now
    allowed, retry = quota_state(db, executable, now, force=True)
    # A useful quota measurement may be slow; recheck session ownership after it.
    if not session_unchanged(db, event, now, read):
        return False
    if not allowed:
        with db:
            db.execute("UPDATE tasks SET status='waiting_quota',due=? WHERE id=?",
                       (max(now + POLL, retry or now), task['id']))
        return False
    return True


def status(db, state_dir=None):
    from run_status import rows, worker_active
    from engines import decorate
    tasks = decorate(db, rows(db, state_dir or Path(".")))
    active_worker = worker_active(state_dir or Path("."))
    automatic = enabled(db)
    for task in tasks:
        if task['status'] in ('running', 'launching') and not active_worker:
            task.update(status='needs_review', label='Exécution non confirmée · worker absent', reason='Le processus de reprise n’est plus détecté ; aucun succès déduit.')
        elif task['status'] in ('ready', 'waiting_quota') and not automatic:
            task['label'] = 'Automatisation en pause · aucune relance prévue'
    import claude_quota
    claude_events = claude_quota.status(db)
    counts = Counter(task['status'] for task in tasks)
    next_due = min((task['due'] for task in tasks if task['status'] in ('ready', 'waiting_quota')), default=None)
    from presentation import summarize
    return {'claude_quota': {'enabled': get(db, 'claude_desktop_auto_enabled', True), 'events': claude_events}, 'tasks': tasks, 'summary': summarize(tasks), 'launching': counts['launching'], 'auto_enabled': automatic, 'pending': counts.get('ready', 0) + counts.get('waiting_quota', 0),
            'ready': counts.get('ready', 0), 'waiting_quota': counts.get('waiting_quota', 0),
            'running': counts['running'], 'needs_review': sum(t['status'] in ('needs_review', 'blocked_trust', 'blocked_auth', 'quota_blocked', 'failed', 'timed_out', 'blocked_owner', 'blocked_parent', 'blocked_session', 'blocked_permission', 'needs_input', 'engine_unavailable', 'stopped', 'queued_parent', 'delegated_parent') for t in tasks),
            'next_due': next_due, 'last_tick': get(db, 'last_tick'), 'last_error': get(db, 'last_error'),
            'quota_checked_at': get(db, 'quota_checked_at'), 'quota_allowed': get(db, 'quota_allowed'), 'quota_retry_at': get(db, 'quota_retry_at')}
