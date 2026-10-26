"""Project-scoped durable Desktop inbox. Admission is never proof of dispatch."""
import hashlib
from pathlib import Path
import sqlite3
import uuid

from delegation_store import receipt_view, validate_lookup


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
        self.db.execute('''CREATE TABLE IF NOT EXISTS desktop_tasks(
            scope TEXT NOT NULL, request_key TEXT NOT NULL, prompt TEXT NOT NULL,
            chat_id TEXT, claim_key TEXT, attempt_id TEXT, answer TEXT,
            acknowledged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope,request_key))''')
        if 'delivery' not in {row[1] for row in self.db.execute('PRAGMA table_info(desktop_tasks)')}:
            self.db.execute('ALTER TABLE desktop_tasks ADD COLUMN delivery TEXT')  # NULL | reserved | attempted
        self.db.commit()

    def close(self):
        self.db.close()

    def _row(self, key):
        return self.db.execute('SELECT prompt,chat_id,claim_key,attempt_id,answer,acknowledged,delivery FROM desktop_tasks WHERE scope=? AND request_key=?', (self.scope,key)).fetchone()

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
        return {'status': 'finished', 'request_key': request_key, 'admitted': True, 'dispatch_confirmed': False}

    def claim(self, request_key, claim_key):
        bounded(request_key, 'request_key', 128)
        bounded(claim_key, 'claim_key', 128)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self._row(request_key)
            if not row:
                raise ValueError('Unknown project task')
            if row[2] is not None and row[2] != claim_key:
                raise ValueError('Task already claimed')
            attempt = row[3] or str(uuid.uuid4())
            self.db.execute('UPDATE desktop_tasks SET claim_key=?,attempt_id=?,delivery=? WHERE scope=? AND request_key=?', (claim_key,attempt,'attempted' if row[4] is None else row[6],self.scope,request_key))
        return {'status': 'finished', 'request_key': request_key, 'attempt_id': attempt, 'prompt': row[0], 'chat_id': row[1], 'completed': row[4] is not None, 'instruction': 'Already completed; retrieve the result, do not execute again.' if row[4] is not None else 'Perform the scoped task, then call submit_result with this attempt_id.'}

    def reserve(self, request_key, claim_key):
        """Reserve one exact task for claim_key without delivering it.

        States: absent | completed | attempted (prompt already handed over: no payload is returned) |
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
        """Record the delivery intention BEFORE the prompt is written; True for exactly one caller."""
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            changed = self.db.execute("UPDATE desktop_tasks SET delivery='attempted' WHERE scope=? AND request_key=? AND attempt_id=? AND delivery='reserved' AND answer IS NULL",
                                      (self.scope, request_key, attempt_id)).rowcount
        return changed == 1

    def release(self, request_key, attempt_id):
        """Drop a reservation proven never handed over (still 'reserved'); never after an attempt."""
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
