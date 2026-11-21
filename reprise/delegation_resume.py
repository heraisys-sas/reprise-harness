"""Automatic resume of delegation turns at their due date (lot C, issue #239).

The resume engine is a tick-driven, observe-only scheduler over persisted
timestamped jobs. It NEVER re-dispatches: the only side effect on a native
turn is delegation_reconcile.reconcile(..., action='observe'), which may
collect an already-terminal result but can never start a new turn.
expert_active_turns is never touched by this module.
"""

import json
import sqlite3
import time
from pathlib import Path

from delegation_notifications import record_event
from delegation_store import Store

SCHEMA = '''CREATE TABLE IF NOT EXISTS delegation_resume_jobs(
    request_key TEXT PRIMARY KEY,
    backend TEXT NOT NULL,
    root TEXT NOT NULL DEFAULT '',
    due_at REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    last_error TEXT,
    scheduled_at REAL NOT NULL,
    updated_at REAL NOT NULL)'''

# Kill-switch lives in scheduler_meta (same store as the automation flags).
ENABLED_KEY = 'delegation_resume_enabled'
DEFAULT_ENABLED = True

MAX_ATTEMPTS = 8
MAX_LIMIT = 2
DEFAULT_BUDGET_SECONDS = 2.0


def enabled(db, default=DEFAULT_ENABLED):
    """Kill-switch: scheduler_meta['delegation_resume_enabled'] ('0'/'false'
    disables). A missing key keeps resumption enabled by default."""
    try:
        import scheduler
        return scheduler.get(db, ENABLED_KEY, default) not in (False, 0, '0', 'false', 'False')
    except sqlite3.OperationalError:
        # Pas encore de scheduler_meta (table installee par reprise) : la cle
        # est absente, le contrat s'applique (defaut = reprise activee).
        return default
    except (sqlite3.Error, ValueError, TypeError):
        return False


def backoff_delay(attempts):
    """Exponential backoff in seconds: 60s, 120s, ... capped at 1h."""
    return min(60 * (2 ** max(int(attempts), 0)), 3600)


def schedule(db, request_key, backend, root, due_at=None, now=None):
    """Persist one resume job (idempotent upsert); a re-scheduled key restarts
    at attempts=0 with a fresh due date. `root` is the delegation project root
    (Store scope = str(root), same value the bridge persisted the receipt with)."""
    now = time.time() if now is None else float(now)
    due = float(now if due_at is None else due_at)
    with db:
        db.execute(SCHEMA)
        db.execute('''INSERT INTO delegation_resume_jobs(
                request_key, backend, root, due_at, attempts, status, scheduled_at, updated_at)
            VALUES (?,?,?,?,0,'scheduled',?,?)
            ON CONFLICT(request_key) DO UPDATE SET
                backend=excluded.backend, root=excluded.root, due_at=excluded.due_at,
                status='scheduled', attempts=0, last_error=NULL, updated_at=excluded.updated_at''',
            (request_key, backend, str(root), due, now, now))
    return {'request_key': request_key, 'backend': backend, 'root': str(root), 'due_at': due,
            'attempts': 0, 'status': 'scheduled', 'last_error': None,
            'scheduled_at': now, 'updated_at': now}


def _defer(db, request_key, due_at, now):
    with db:
        db.execute('UPDATE delegation_resume_jobs SET status=?, due_at=?, updated_at=? WHERE request_key=?',
                   ('scheduled' if due_at > now else 'done', due_at, now, request_key))


def _backoff(db, request_key, attempts, reason, now):
    """Reschedule after a failed attempt; abandoned past MAX_ATTEMPTS.
    Delay is computed from the pre-increment attempts: first retry at 60s,
    then 120s, ... (plan formula 60*2**attempts)."""
    next_attempts = int(attempts) + 1
    status = 'abandoned' if next_attempts >= MAX_ATTEMPTS else 'scheduled'
    with db:
        db.execute('UPDATE delegation_resume_jobs SET status=?, attempts=?, last_error=?, due_at=?, updated_at=? WHERE request_key=?',
                   (status, next_attempts, reason, now + backoff_delay(int(attempts)), now, request_key))
    return status


def _abandon_notice(db, scope, request_key, reason, now):
    """Idempotent attention_required event for an abandoned job; exactly one
    event per abandoned job (deduplicated by event_id)."""
    record_event(db, scope, request_key, 'attention_required', now=now,
                 event_id='attention_required:' + request_key + ':resume-abandoned',
                 receipt_ref=json.dumps({'reason': reason}))


def quota_gate(db, backend, now=None):
    """Read-only quota pause for claude-desktop resume jobs, sourced ONLY from
    the persisted banner table (status IN ('waiting','awaiting_observation') AND
    due > now) — the same query contract as claude_quota.quota_state. It never
    blocks admission (admission happens in the bridge and ignores this gate)
    and never invents a due date: codex backends and unknown dues are 'ok'."""
    now = time.time() if now is None else float(now)
    if backend not in ('claude-desktop', 'claude'):
        return {'status': 'ok', 'reason': None, 'resume_at': None}
    try:
        row = db.execute("SELECT MAX(due) FROM claude_quota_resumes WHERE status IN ('waiting','awaiting_observation') AND due>?",
                         (now,)).fetchone()
    except sqlite3.Error:
        return {'status': 'ok', 'reason': None, 'resume_at': None}
    if row and row[0]:
        return {'status': 'blocked', 'reason': 'quota', 'resume_at': row[0]}
    return {'status': 'ok', 'reason': None, 'resume_at': None}


LAZY_START_KEY = 'delegation_lazy_start'


def lazy_start_enabled(db, default=False):
    """Opt-in lazy daemon start (plan lot C #239): scheduler key, default False."""
    try:
        import scheduler
        value = scheduler.get(db, LAZY_START_KEY, default)
    except sqlite3.OperationalError:
        return default  # scheduler_meta absente : cle non posee, defaut
    except (sqlite3.Error, ValueError, TypeError):
        return False
    return value not in (False, 0, '0', 'false', 'False', '')


def lazy_start(root, values, *, connect_timeout=2.0):
    """Verify daemon liveness by socket connect() only (never file existence —
    an orphan socket file means 'stopped'). Bounded by connect_timeout.
    Returns {'status': 'running'|'unavailable', 'socket': path, ...}. The
    daemon spawn itself is intentionally NOT implemented (plan escape hatch):
    no process is started from the resume engine in v1; callers treat
    'unavailable' as lazy_start_attempted=False evidence."""
    import socket as _socket
    socket_path = (values or {}).get('socket')
    if not socket_path:
        try:
            import claude_desktop_rpc
            socket_path = claude_desktop_rpc.DEFAULT_SOCKET
        except (ImportError, AttributeError):
            return {'status': 'unavailable', 'socket': None, 'reason': 'no socket configured',
                    'lazy_start_attempted': False}
    deadline = time.monotonic() + max(float(connect_timeout), 0.0)
    last_error = None
    while True:
        probe = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        probe.settimeout(max(deadline - time.monotonic(), 0.05))
        try:
            probe.connect(socket_path)
            return {'status': 'running', 'socket': socket_path, 'lazy_start_attempted': False}
        except OSError as error:
            last_error = error
        finally:
            probe.close()
        if time.monotonic() >= deadline:
            break
        time.sleep(0.1)
    return {'status': 'unavailable', 'socket': socket_path,
            'reason': 'connect failed: ' + (str(last_error) or 'unknown'),
            'lazy_start_attempted': False}


def run_due(db, *, budget_seconds=DEFAULT_BUDGET_SECONDS, limit=MAX_LIMIT, now=None):
    """Tick entry point: observe-only resume of due delegation jobs.

    Contract (issue #239):
    - at most `limit` due jobs claimed per call, atomically (UPDATE + rowcount);
    - every per-job exception is absorbed into last_error (never raises);
    - terminal receipts (recorded/awaiting_human/canceled) -> job done;
    - owner_unavailable/unknown -> exponential backoff, abandoned after
      MAX_ATTEMPTS with exactly one attention_required notification (idempotent);
    - claude-desktop jobs wait behind the persisted quota banner (quota_gate);
    - the shared wall-clock budget bounds the whole call;
    - no path to codex_expert.review / claude_review / chatgpt_quick:
      delegation_reconcile is called with action='observe' only.
    """
    now = time.time() if now is None else float(now)
    summary = {'status': 'finished', 'enabled': True, 'claimed': 0, 'processed': [],
               'budget_seconds': float(budget_seconds)}
    try:
        if not enabled(db):
            summary['enabled'] = False
            return summary
        db.execute(SCHEMA)
        rows = db.execute('''SELECT request_key, backend, root, attempts FROM delegation_resume_jobs
                             WHERE status='scheduled' AND due_at<=? ORDER BY due_at LIMIT ?''',
                          (now, int(limit))).fetchall()
        deadline = time.monotonic() + max(float(budget_seconds), 0.0)
        for request_key, backend, root, attempts in rows:
            if time.monotonic() >= deadline:
                summary['status'] = 'budget_exhausted'
                break
            # Atomic claim: only one scheduler may own this attempt.
            with db:
                claimed = db.execute('''UPDATE delegation_resume_jobs SET status='running', updated_at=?
                                        WHERE request_key=? AND status='scheduled' AND due_at<=?''',
                                     (now, request_key, now)).rowcount
            if not claimed:
                continue
            summary['claimed'] += 1
            outcome = {'request_key': request_key, 'status': 'backoff'}
            # Quota pause is checked BEFORE any native contact: observing still
            # connects to the endpoint, which a persisted banner forbids. This
            # is not a failed attempt (no attempts increment): wait for the
            # persisted due date, or a generic backoff slot when none is known.
            gate = quota_gate(db, backend, now)
            if gate['status'] == 'blocked':
                resume_at = gate.get('resume_at')
                _defer(db, request_key, resume_at if resume_at else now + backoff_delay(attempts), now)
                outcome['status'] = 'quota_wait'
                summary['processed'].append(outcome)
                continue
            try:
                outcome.update(_resume_one(db, root, request_key, now))
            except Exception as error:  # absorbed: the tick must survive anything
                outcome.update(status='backoff', reason=type(error).__name__ + ': ' + str(error))
            if outcome['status'] == 'done':
                _defer(db, request_key, now, now)  # terminal: due_at<=now keeps status 'done'
            else:
                final = _backoff(db, request_key, attempts, outcome.get('reason', 'observe incomplete'), now)
                if final == 'abandoned':
                    try:
                        with db:
                            _abandon_notice(db, root, request_key,
                                            outcome.get('reason', 'resume abandoned'), now)
                    except sqlite3.Error:
                        pass
            summary['processed'].append(outcome)
    except Exception as error:  # never break the caller's tick
        summary.update(status='error', reason=type(error).__name__ + ': ' + str(error))
    return summary


def _resume_one(db, root, request_key, now):
    """Observe one job. Returns {'status': 'done'|'backoff'|'abandoned', ...}.
    Only delegation_reconcile.reconcile(action='observe') touches the native
    turn; no dispatch path exists in this module."""
    import delegation_reconcile
    store = Store(db, root)
    receipt = store.lookup({'request_key': request_key})
    if receipt.get('status') == 'not_found':
        return {'status': 'done', 'reason': 'receipt no longer exists'}
    state = receipt.get('state')
    if state in ('recorded', 'awaiting_human'):
        # Terminal: the result is already persisted by Store.finish; collecting
        # it is a read of the receipt, not a new native call.
        return {'status': 'done', 'receipt_state': state,
                'result_status': (receipt.get('result') or {}).get('status')}
    if state == 'canceled':
        return {'status': 'done', 'reason': 'receipt canceled'}
    response = delegation_reconcile.reconcile(store, root, request_key, action='observe',
                                              reason='automatic resume at due date')
    status = response.get('status')
    if status in ('finished', 'interrupted', 'needs_review'):
        return {'status': 'done', 'receipt_state': state, 'observe_status': status}
    if status == 'owner_unavailable':
        row = db.execute('SELECT attempts FROM delegation_resume_jobs WHERE request_key=?', (request_key,)).fetchone()
        if row and int(row[0]) + 1 >= MAX_ATTEMPTS:
            return {'status': 'abandoned', 'observe_status': status,
                    'reason': 'owner_unavailable after ' + str(MAX_ATTEMPTS) + ' attempts'}
        return {'status': 'backoff', 'observe_status': status}
    return {'status': 'backoff', 'observe_status': status}
