"""Durable notification queue for delegation turns (lot C, phase 1).

Events are written inside the SAME SQLite transaction as terminal-state
recording (Store.finish), so a crash between receipt persistence and signal
can never leave a coordinator ignorant. Delivery is at-least-once per
consumer, deduplicated by event_id; retention keeps every event (documented
contract: no pruning in v1). Acknowledgements are per-consumer rows, distinct
from receipt acknowledgement in delegation_requests.
"""

import json
import sqlite3
import time

MAX_BATCH = 100
MAX_WAIT_SECONDS = 600.0
KINDS = ('turn_terminal', 'attention_required')


def install(db):
    """Idempotent schema install; runs inside the caller's transaction when one
    is open, otherwise in its own implicit one (same pattern as routing_events.setup)."""
    db.execute('''CREATE TABLE IF NOT EXISTS notification_events(
        event_id TEXT PRIMARY KEY,
        scope TEXT NOT NULL,
        request_key TEXT NOT NULL,
        attempt_id TEXT,
        thread_id TEXT,
        turn_id TEXT,
        kind TEXT NOT NULL,
        receipt_revision TEXT,
        result_sha256 TEXT,
        terminal_evidence TEXT,
        work_completed INTEGER,
        receipt_ref TEXT,
        created_at REAL NOT NULL)''')
    db.execute('''CREATE TABLE IF NOT EXISTS notification_acks(
        event_id TEXT NOT NULL,
        consumer_id TEXT NOT NULL,
        acknowledged_at REAL NOT NULL,
        PRIMARY KEY(event_id, consumer_id))''')


def record_event(db, scope, key, kind, now=None, **fields):
    """Insert one notification event. MUST be called inside the caller's active
    transaction (Store.finish joins its `with self.db:` block) so receipt and
    event commit or roll back together. Unknown kind or non-dict scope is a
    programming error; duplicates are ignored (at-least-once, dedup by id)."""
    if kind not in KINDS:
        raise ValueError('Unknown notification kind')
    row = {
        'attempt_id': fields.get('attempt_id'),
        'thread_id': fields.get('thread_id'),
        'turn_id': fields.get('turn_id'),
        'receipt_revision': fields.get('receipt_revision'),
        'result_sha256': fields.get('result_sha256'),
        'terminal_evidence': fields.get('terminal_evidence'),
        'work_completed': fields.get('work_completed'),
        'receipt_ref': fields.get('receipt_ref'),
    }
    db.execute('''INSERT OR IGNORE INTO notification_events(
            event_id, scope, request_key, attempt_id, thread_id, turn_id, kind,
            receipt_revision, result_sha256, terminal_evidence, work_completed,
            receipt_ref, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (fields['event_id'], scope, key, row['attempt_id'], row['thread_id'],
         row['turn_id'], kind, row['receipt_revision'], row['result_sha256'],
         row['terminal_evidence'], row['work_completed'], row['receipt_ref'],
         float(now if now is not None else time.time())))


class Queue:
    """Read/wait/ack API over the notification tables. Never dispatches,
    never consumes on read; wait is a bounded poll loop with no open
    transaction (proof is the re-read of pending rows, never a signal)."""

    def __init__(self, db, scope, now=None):
        self.db, self.scope = db, scope
        self.now = now or time.time
        self.wake = None  # optional threading.Event; signal is an optimization only

    def read(self, consumer_id, limit=None, request_keys=None):
        limit = min(int(limit or MAX_BATCH), MAX_BATCH)
        if not isinstance(consumer_id, str) or not consumer_id.strip():
            raise ValueError('Persist a nonempty consumer_id')
        clauses = ['e.scope=?',
                   '''NOT EXISTS (SELECT 1 FROM notification_acks a
                      WHERE a.event_id=e.event_id AND a.consumer_id=?)''']
        params = [self.scope, consumer_id]
        if request_keys is not None:
            # Optional narrowing only; the unfiltered read stays the default so a
            # consumer can never silently miss events outside its filter.
            if (not isinstance(request_keys, list) or not request_keys
                    or len(request_keys) > MAX_BATCH
                    or any(not isinstance(key, str) or not key.strip() for key in request_keys)):
                raise ValueError('Persist a bounded list of nonempty request_keys')
            clauses.append(f'e.request_key IN ({",".join("?" * len(request_keys))})')
            params += list(request_keys)
        rows = self.db.execute(f'''
            SELECT e.event_id, e.request_key, e.attempt_id, e.thread_id, e.turn_id,
                   e.kind, e.receipt_revision, e.result_sha256, e.terminal_evidence,
                   e.work_completed, e.receipt_ref, e.created_at
            FROM notification_events e
            WHERE {' AND '.join(clauses)}
            ORDER BY e.created_at, e.rowid LIMIT ?''',
            params + [limit]).fetchall()
        return [_row_event(row) for row in rows]

    def wait(self, consumer_id, timeout_ms=None, cancel=None, request_keys=None):
        """Bounded blocking wait for pending events. No transaction is held;
        re-checks the queue before sleeping and after each wake, so a wake is
        only an optimization: the returned events are always re-read from the
        queue (at-least-once)."""
        timeout = min(max(float(timeout_ms or 0), 0.0), MAX_WAIT_SECONDS * 1000) / 1000.0
        deadline = time.monotonic() + timeout
        delay = 0.05
        while True:
            events = self.read(consumer_id, request_keys=request_keys)
            if events:
                return {'status': 'notified', 'events': events}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {'status': 'timeout', 'events': []}
            step = min(delay, remaining)
            if self.wake is not None:
                self.wake.wait(step)
                self.wake.clear()
            elif cancel is not None:
                if cancel.wait(step):
                    return {'status': 'timeout', 'events': []}
            else:
                time.sleep(step)
            delay = min(delay * 2, 1.0)

    def ack(self, consumer_id, event_ids):
        """Idempotent per-consumer acknowledgement, distinct from receipt ack."""
        if not isinstance(consumer_id, str) or not consumer_id.strip():
            raise ValueError('Persist a nonempty consumer_id')
        if not isinstance(event_ids, list) or not event_ids or len(event_ids) > MAX_BATCH:
            raise ValueError('Persist a bounded list of event_ids')
        stamp = float(self.now())
        for event_id in event_ids:
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError('Persist nonempty event_ids')
            self.db.execute('INSERT OR IGNORE INTO notification_acks(event_id, consumer_id, acknowledged_at) VALUES (?,?,?)',
                            (event_id, consumer_id, stamp))
        return {'status': 'acknowledged', 'event_ids': list(event_ids)}


def _row_event(row):
    return {
        'event_id': row[0], 'request_key': row[1], 'attempt_id': row[2],
        'thread_id': row[3], 'turn_id': row[4], 'kind': row[5],
        'receipt_revision': row[6], 'result_sha256': row[7],
        'terminal_evidence': row[8], 'work_completed': bool(row[9]) if row[9] is not None else None,
        'receipt_ref': row[10], 'created_at': row[11],
    }
