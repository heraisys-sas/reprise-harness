"""Project-scoped durable Desktop inbox. Admission is never proof of dispatch."""
import hashlib
import base64
import binascii
import json
from pathlib import Path
import sqlite3
import time
import uuid

from delegation_store import receipt_view, validate_lookup

SQLITE_INT_MAX = (1 << 63) - 1


def bounded(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f'Invalid {name}')
    return value


class TaskStore:
    def __init__(self, state, project_root):
        root = Path(project_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Project directory required')
        self.scope = str(root)
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(state/'quick.sqlite', timeout=2)
        try:
            # Serialize schema discovery and migration. A concurrent opener must not observe
            # the new column before the legacy rows have been classified.
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('''CREATE TABLE IF NOT EXISTS desktop_tasks(
                scope TEXT NOT NULL, request_key TEXT NOT NULL, prompt TEXT NOT NULL,
                chat_id TEXT, claim_key TEXT, attempt_id TEXT, answer TEXT,
                acknowledged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope,request_key))''')
            dispatch_guard_exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='desktop_dispatch_guard'").fetchone() is not None
            self.db.execute('''CREATE TABLE IF NOT EXISTS desktop_dispatch_guard(
                scope TEXT NOT NULL, request_key TEXT NOT NULL,
                legacy_unknown INTEGER NOT NULL DEFAULT 0,
                review_started INTEGER NOT NULL DEFAULT 0,
                dispatch_started INTEGER NOT NULL DEFAULT 0,
                dispatch_receipt TEXT,
                PRIMARY KEY(scope,request_key))''')
            if not dispatch_guard_exists:
                # Old direct dispatches left no durable trace, even when delivery is NULL.
                self.db.execute('''INSERT INTO desktop_dispatch_guard(scope,request_key,legacy_unknown)
                                   SELECT scope,request_key,1 FROM desktop_tasks''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS desktop_approvals(
                scope TEXT NOT NULL, approval_id TEXT NOT NULL, method TEXT NOT NULL,
                rpc_id TEXT, thread_id TEXT, turn_id TEXT, params TEXT,
                requested_at REAL NOT NULL, resolved INTEGER NOT NULL DEFAULT 0,
                decision TEXT, resolved_at REAL, sandbox TEXT, PRIMARY KEY(scope,approval_id))''')
            approval_columns = {row[1] for row in self.db.execute('PRAGMA table_info(desktop_approvals)')}
            if 'sandbox' not in approval_columns:
                self.db.execute('ALTER TABLE desktop_approvals ADD COLUMN sandbox TEXT')
            columns = {row[1] for row in self.db.execute('PRAGMA table_info(desktop_tasks)')}
            if 'delivery' not in columns:
                self.db.execute('ALTER TABLE desktop_tasks ADD COLUMN delivery TEXT')  # NULL | reserved | attempted
                # Old rows do not record whether a claim was actually handed over. Treat
                # any persisted claim/attempt/result as uncertain and never redeliver it.
                self.db.execute("UPDATE desktop_tasks SET delivery='attempted' WHERE claim_key IS NOT NULL OR attempt_id IS NOT NULL OR answer IS NOT NULL")
            order_table_exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='desktop_task_order'").fetchone() is not None
            order_trigger_exists = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='desktop_tasks_order_after_insert'").fetchone() is not None
            self.db.execute('''CREATE TABLE IF NOT EXISTS desktop_task_order(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL, request_key TEXT NOT NULL,
                UNIQUE(scope,request_key))''')
            self.db.execute('CREATE INDEX IF NOT EXISTS desktop_task_order_scope_seq ON desktop_task_order(scope,seq)')
            if not order_table_exists or not order_trigger_exists:
                # Legacy tasks have no trustworthy creation timestamp. Give them a
                # deterministic registry order; this does not claim historical chronology.
                self.db.execute('''INSERT OR IGNORE INTO desktop_task_order(scope,request_key)
                    SELECT scope,request_key FROM desktop_tasks ORDER BY scope,request_key''')
            # Older Reprise daemons still write desktop_tasks directly. Keep their
            # inserts visible in the registry after the migration has committed.
            self.db.execute('''CREATE TRIGGER IF NOT EXISTS desktop_tasks_order_after_insert
                AFTER INSERT ON desktop_tasks
                BEGIN
                    INSERT OR IGNORE INTO desktop_task_order(scope,request_key)
                    VALUES(NEW.scope,NEW.request_key);
                END''')
            self.db.commit()
        except Exception:
            self.db.rollback()
            self.db.close()
            raise

    def register_session(self, chat_id):
        from claude_session_store import SessionStore
        session = SessionStore(self.db, self.scope).register(chat_id, source='desktop_mcp_declared')
        return {'status': 'registered', **session}

    def record_approval(self, approval_id, method, rpc_id=None, thread_id=None,
                        turn_id=None, params=None, requested_at=None, sandbox=None):
        """Durable record of a native approval request; inserting twice keeps the first."""
        bounded(approval_id, 'approval_id', 128)
        bounded(method, 'method', 128)
        if rpc_id is not None and not isinstance(rpc_id, (str, int)):
            raise ValueError('Invalid rpc_id')
        if thread_id is not None and not isinstance(thread_id, str):
            raise ValueError('Invalid thread_id')
        if turn_id is not None and not isinstance(turn_id, str):
            raise ValueError('Invalid turn_id')
        if params is not None and not isinstance(params, (dict, list)):
            raise ValueError('Invalid params')
        if sandbox is not None and sandbox not in ('read-only', 'workspace-write'):
            raise ValueError('Invalid sandbox')
        with self.db:
            self.db.execute('''INSERT OR IGNORE INTO desktop_approvals
                (scope,approval_id,method,rpc_id,thread_id,turn_id,params,requested_at,sandbox)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (self.scope, approval_id, method, str(rpc_id) if rpc_id is not None else None,
                 thread_id, turn_id,
                 json.dumps(params, ensure_ascii=False) if params is not None else None,
                 float(requested_at) if requested_at is not None else time.time(), sandbox))

    @staticmethod
    def _approval_row(row):
        return {'approval_id': row[0], 'method': row[1], 'rpc_id': row[2], 'thread_id': row[3],
                'turn_id': row[4], 'params': json.loads(row[5]) if row[5] else None,
                'requested_at': row[6], 'sandbox': row[7]}

    def list_approvals(self, limit=50):
        """Pending approval requests for this project, oldest first. Read-only."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('Limit must be an integer from 1 to 100')
        rows = self.db.execute('''SELECT approval_id,method,rpc_id,thread_id,turn_id,params,requested_at,sandbox
            FROM desktop_approvals WHERE scope=? AND resolved=0 ORDER BY requested_at LIMIT ?''',
            (self.scope, limit)).fetchall()
        return [self._approval_row(row) for row in rows]

    def pending_approval(self, approval_id):
        row = self.db.execute('''SELECT approval_id,method,rpc_id,thread_id,turn_id,params,requested_at,sandbox
            FROM desktop_approvals WHERE scope=? AND approval_id=? AND resolved=0''',
            (self.scope, bounded(approval_id, 'approval_id', 128))).fetchone()
        return self._approval_row(row) if row else None

    def resolve_approval(self, approval_id, decision, resolved_at=None):
        """Mark a pending approval resolved; return False when nothing was pending."""
        if decision not in ('approved', 'denied'):
            raise ValueError('Decision must be approved or denied')
        with self.db:
            changed = self.db.execute('''UPDATE desktop_approvals
                SET resolved=1, decision=?, resolved_at=?
                WHERE scope=? AND approval_id=? AND resolved=0''',
                (decision, float(resolved_at) if resolved_at is not None else time.time(),
                 self.scope, bounded(approval_id, 'approval_id', 128))).rowcount
        return changed == 1

    def close(self):
        self.db.close()

    def _row(self, key):
        return self.db.execute('SELECT prompt,chat_id,claim_key,attempt_id,answer,acknowledged,delivery FROM desktop_tasks WHERE scope=? AND request_key=?', (self.scope,key)).fetchone()

    def list_tasks(self, limit=50, cursor=None):
        """List metadata for this project using a stable insertion watermark.

        Cursors are opaque pagination tokens, not authenticated security boundaries.
        Project authorization comes from the bound TaskStore scope and private socket.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('Limit must be an integer from 1 to 100')
        scope_hash = hashlib.sha256(self.scope.encode()).hexdigest()
        high_water = None
        before_seq = None
        if cursor is not None:
            if not isinstance(cursor, str) or len(cursor) > 1024:
                raise ValueError('Invalid task cursor')
            try:
                token = cursor.encode('ascii')
                padded = token + b'=' * (-len(token) % 4)
                raw = base64.b64decode(padded, altchars=b'-_', validate=True)
                if base64.urlsafe_b64encode(raw).rstrip(b'=') != token:
                    raise ValueError('Non-canonical task cursor')
                def unique_object(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError('Duplicate task cursor key')
                        result[key] = value
                    return result
                decoded = json.loads(raw, object_pairs_hook=unique_object)
                if (not isinstance(decoded, dict) or set(decoded) != {'v', 'scope', 'high', 'before'} or
                        type(decoded.get('v')) is not int or decoded.get('v') != 1 or
                        decoded.get('scope') != scope_hash or
                        isinstance(decoded.get('high'), bool) or not isinstance(decoded.get('high'), int) or
                        isinstance(decoded.get('before'), bool) or not isinstance(decoded.get('before'), int) or
                        decoded['high'] < 0 or decoded['high'] > SQLITE_INT_MAX or
                        decoded['before'] < 1 or decoded['before'] > decoded['high']):
                    raise ValueError
                high_water, before_seq = decoded['high'], decoded['before']
            except (ValueError, TypeError, UnicodeError, binascii.Error) as error:
                raise ValueError('Invalid or foreign task cursor') from error
        try:
            # A deferred read transaction preserves one high-water snapshot without
            # taking SQLite's writer reservation while tasks are being listed.
            self.db.execute('BEGIN')
            if high_water is None:
                high_water = self.db.execute('SELECT COALESCE(MAX(seq),0) FROM desktop_task_order WHERE scope=?', (self.scope,)).fetchone()[0]
                before_seq = None
            rows = self.db.execute('''SELECT o.seq,o.request_key,t.delivery,(t.answer IS NOT NULL),t.acknowledged
                FROM desktop_task_order o JOIN desktop_tasks t
                ON t.scope=o.scope AND t.request_key=o.request_key
                WHERE o.scope=? AND o.seq<=? AND (? IS NULL OR o.seq<?)
                ORDER BY o.seq DESC LIMIT ?''', (self.scope,high_water,before_seq,before_seq,limit + 1)).fetchall()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        has_more = len(rows) > limit
        rows = rows[:limit]
        items = []
        for seq, request_key, delivery, result_available, acknowledged in rows:
            state = 'recorded' if result_available else (delivery or 'admitted')
            items.append({'request_key': request_key, 'state': state,
                          'result_available': bool(result_available),
                          'acknowledged': bool(acknowledged)})
        next_cursor = None
        if has_more:
            payload = json.dumps({'v': 1, 'scope': scope_hash, 'high': high_water,
                                  'before': rows[-1][0]}, separators=(',', ':')).encode()
            next_cursor = base64.urlsafe_b64encode(payload).decode().rstrip('=')
        return {'tasks': items, 'next_cursor': next_cursor, 'high_water': high_water}

    def admit(self, request_key, prompt, chat_id=None):
        bounded(request_key, 'request_key', 128)
        bounded(prompt, 'prompt', 12000)
        if chat_id is not None:
            bounded(chat_id, 'chat_id', 256)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self._row(request_key)
            if row and row[:2] != (prompt, chat_id):
                raise ValueError('Request identity conflict')
            self.db.execute('INSERT OR IGNORE INTO desktop_tasks(scope,request_key,prompt,chat_id) VALUES(?,?,?,?)', (self.scope,request_key,prompt,chat_id))
            if not row:
                self.db.execute('INSERT INTO desktop_dispatch_guard(scope,request_key) VALUES(?,?)',
                                (self.scope, request_key))
        return {'status': 'finished', 'request_key': request_key, 'admitted': True, 'dispatch_confirmed': False}

    def reserve_review_dispatch(self, request_key):
        """Reserve one orchestrated submission without touching MCP delivery state."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            changed = self.db.execute('''UPDATE desktop_dispatch_guard SET review_started=1
                WHERE scope=? AND request_key=? AND legacy_unknown=0 AND review_started=0
                AND dispatch_started=0 AND EXISTS (SELECT 1 FROM desktop_tasks t
                    WHERE t.scope=desktop_dispatch_guard.scope AND t.request_key=desktop_dispatch_guard.request_key
                    AND t.claim_key IS NULL AND t.attempt_id IS NULL AND t.delivery IS NULL AND t.answer IS NULL)''',
                (self.scope, request_key)).rowcount
        return changed == 1

    def reserve_socket_dispatch(self, request_key):
        """Reserve the only socket send; return a stored receipt or uncertainty on replay."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('''SELECT legacy_unknown,dispatch_started,dispatch_receipt
                FROM desktop_dispatch_guard WHERE scope=? AND request_key=?''',
                (self.scope, request_key)).fetchone()
            if row is None or row[0]:
                return {'state': 'legacy_unknown'}
            if row[1]:
                return {'state': 'recorded', 'receipt': json.loads(row[2])} if row[2] else {'state': 'attempted'}
            changed = self.db.execute('''UPDATE desktop_dispatch_guard SET dispatch_started=1
                WHERE scope=? AND request_key=? AND dispatch_started=0 AND EXISTS
                  (SELECT 1 FROM desktop_tasks t WHERE t.scope=desktop_dispatch_guard.scope
                   AND t.request_key=desktop_dispatch_guard.request_key AND t.claim_key IS NULL
                   AND t.attempt_id IS NULL AND t.delivery IS NULL AND t.answer IS NULL)''',
                (self.scope, request_key)).rowcount
            return {'state': 'reserved' if changed == 1 else 'attempted'}

    def record_socket_dispatch(self, request_key, receipt):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            changed = self.db.execute('''UPDATE desktop_dispatch_guard SET dispatch_receipt=?
                WHERE scope=? AND request_key=? AND dispatch_started=1 AND dispatch_receipt IS NULL''',
                (json.dumps(receipt, ensure_ascii=False), self.scope, request_key)).rowcount
        return changed == 1

    def claim(self, request_key, claim_key):
        """Explicit pull (get_task), sharing await_task's one-attempt delivery barrier.

        The barrier records intent before writing, so a replay returns a receipt with
        unconfirmed delivery, never another prompt.
        """
        bounded(request_key, 'request_key', 128)
        bounded(claim_key, 'claim_key', 128)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self._row(request_key)
            if not row:
                raise ValueError('Unknown project task')
            if row[2] is not None and row[2] != claim_key:
                raise ValueError('Task already claimed')
            if row[6] == 'attempted':
                return {'status': 'finished', 'request_key': request_key, 'attempt_id': row[3], 'completed': row[4] is not None,
                        'delivery': 'attempted',
                        'instruction': 'A delivery attempt was recorded, but receipt is unconfirmed. Do not execute unless you actually hold the prompt from that attempt. If you do not hold it, ask the operator to reconcile the state before any new admission; do not create a replacement key to bypass uncertainty.'}
            attempt = row[3] or str(uuid.uuid4())
            self.db.execute('UPDATE desktop_tasks SET claim_key=?,attempt_id=?,delivery=? WHERE scope=? AND request_key=?',
                            (claim_key, attempt, 'attempted' if row[4] is None else row[6], self.scope, request_key))
        return {'status': 'finished', 'request_key': request_key, 'attempt_id': attempt, 'prompt': row[0], 'chat_id': row[1],
                'completed': row[4] is not None, 'delivery': 'handed_over',
                'instruction': 'Already completed; retrieve the result with get_result.' if row[4] is not None else 'Perform the scoped task, then call submit_result with this attempt_id.'}

    def claim_next(self, claim_key):
        """Opt-in queue: claim the oldest fresh task in scope. One claim at a time.

        Fresh = never claimed, never attempted, no answer. Uncertain deliveries are
        skipped and stay awaiting operator reconciliation; they are never redelivered.
        Execute and submit the claimed task before claiming another.
        """
        bounded(claim_key, 'claim_key', 128)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('''SELECT t.request_key,t.prompt,t.chat_id
                FROM desktop_task_order o JOIN desktop_tasks t
                  ON t.scope=o.scope AND t.request_key=o.request_key
                WHERE o.scope=? AND t.claim_key IS NULL AND t.delivery IS NULL AND t.answer IS NULL
                ORDER BY o.seq LIMIT 1''', (self.scope,)).fetchone()
            if row is None:
                return {'status': 'finished', 'state': 'empty',
                        'instruction': 'Aucune tâche en file pour ce projet. Rien à réclamer.'}
            attempt = str(uuid.uuid4())
            self.db.execute('UPDATE desktop_tasks SET claim_key=?,attempt_id=?,delivery=? WHERE scope=? AND request_key=?',
                            (claim_key, attempt, 'attempted', self.scope, row[0]))
        return {'status': 'finished', 'request_key': row[0], 'attempt_id': attempt, 'prompt': row[1],
                'chat_id': row[2], 'completed': False, 'delivery': 'handed_over',
                'instruction': 'Perform the scoped task, then call submit_result with this attempt_id. Execute and submit before claiming another.'}

    def reserve(self, request_key, claim_key):
        """Reserve one exact task for claim_key without delivering it.

        States: absent | completed | attempted (delivery intent recorded; receipt unconfirmed, no payload returned) |
        reserved (fresh=True when this call created the reservation). A task reserved by another
        claim_key raises ValueError.
        """
        bounded(request_key, 'request_key', 128)
        bounded(claim_key, 'claim_key', 128)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self._row(request_key)
            if not row:
                return {'state': 'absent'}
            if row[2] is not None and row[2] != claim_key:
                raise ValueError('Task already claimed')
            if row[4] is not None:
                return {'state': 'completed', 'attempt_id': row[3]}
            if row[6] == 'attempted':
                return {'state': 'attempted', 'attempt_id': row[3]}
            fresh = row[2] is None
            attempt = row[3] or str(uuid.uuid4())
            self.db.execute('UPDATE desktop_tasks SET claim_key=?,attempt_id=?,delivery=? WHERE scope=? AND request_key=?',
                            (claim_key, attempt, 'reserved', self.scope, request_key))
        return {'state': 'reserved', 'fresh': fresh, 'attempt_id': attempt, 'prompt': row[0], 'chat_id': row[1]}

    def mark_attempted(self, request_key, attempt_id):
        """Record intent before writing; True for one caller. This does not confirm receipt."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            changed = self.db.execute("UPDATE desktop_tasks SET delivery='attempted' WHERE scope=? AND request_key=? AND attempt_id=? AND delivery='reserved' AND answer IS NULL",
                                      (self.scope, request_key, attempt_id)).rowcount
        return changed == 1

    def release(self, request_key, attempt_id):
        """Drop a reservation while no write attempt is recorded; never after attempted."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            changed = self.db.execute("UPDATE desktop_tasks SET claim_key=NULL,attempt_id=NULL,delivery=NULL WHERE scope=? AND request_key=? AND attempt_id=? AND delivery='reserved' AND answer IS NULL",
                                      (self.scope, request_key, attempt_id)).rowcount
        return changed == 1

    def submit(self, request_key, attempt_id, answer):
        bounded(request_key, 'request_key', 128)
        bounded(attempt_id, 'attempt_id', 128)
        bounded(answer, 'answer', 2_000_000)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self._row(request_key)
            if not row or not row[3] or row[3] != attempt_id:
                raise ValueError('Attempt does not own project task')
            if row[4] is not None and row[4] != answer:
                raise ValueError('Result conflict')
            self.db.execute('UPDATE desktop_tasks SET answer=? WHERE scope=? AND request_key=?', (answer,self.scope,request_key))
        return {'status': 'finished', 'request_key': request_key, 'completed': True, 'sha256': hashlib.sha256(answer.encode()).hexdigest()}

    def result(self, request_key, acknowledge=False, view='receipt', offset=0, max_chars=6000, result_sha256=None):
        lookup = {'request_key': request_key, 'acknowledge': acknowledge, 'view': view}
        if view == 'page' or offset != 0 or max_chars != 6000 or result_sha256 is not None:
            lookup.update(offset=offset, max_chars=max_chars)
            if result_sha256 is not None:
                lookup['result_sha256'] = result_sha256
        validate_lookup(lookup)
        bounded(request_key, 'request_key', 128)
        if not isinstance(acknowledge, bool):
            raise ValueError('Boolean acknowledge required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self._row(request_key)
            if not row:
                raise ValueError('Unknown project task')
            if acknowledge and row[4] is None:
                raise ValueError('Cannot acknowledge unfinished result')
            if acknowledge:
                self.db.execute('UPDATE desktop_tasks SET acknowledged=1 WHERE scope=? AND request_key=?', (self.scope,request_key))
        full = {'status': 'finished', 'request_key': request_key, 'completed': row[4] is not None, 'answer': row[4], 'acknowledged': bool(row[5] or acknowledge)}
        if view == 'full':
            return full
        manifest, text = receipt_view(dict(full, state='recorded' if row[4] is not None else 'pending',
            attempt_id=row[3], result={'answer': row[4]} if row[4] is not None else None))
        if view == 'receipt' or text is None:
            return manifest
        if result_sha256 is not None and result_sha256.lower() != manifest['result_sha256']:
            return dict(manifest, status='result_changed')
        offset, max_chars = int(offset), int(max_chars)
        if offset > len(text):
            return dict(manifest, status='invalid_offset')
        fragment = text[offset:offset+max_chars]
        end = offset + len(fragment)
        return dict(manifest, offset=offset, text=fragment, returned_length=len(fragment),
            has_more=end < len(text), next_offset=end if end < len(text) else None)
