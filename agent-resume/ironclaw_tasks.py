"""Opt-in durable IronClaw tasks. An uncertain POST is never automatically replayed."""
import hashlib
import json
import re
import uuid
from urllib.parse import quote
from ironclaw_ssh import request


class TaskError(Exception):
    pass


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise TaskError('Invalid API identifier')
    return value


class Tasks:
    def __init__(self, db, endpoint=None, transport=request):
        self.db, self.endpoint, self.transport = db, endpoint, transport
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS ironclaw_tasks(
              request_key TEXT PRIMARY KEY, identity TEXT NOT NULL, content TEXT NOT NULL,
              thread_action TEXT NOT NULL, message_action TEXT NOT NULL,
              state TEXT NOT NULL, thread_id TEXT, run_id TEXT, result TEXT,
              acknowledged INTEGER NOT NULL DEFAULT 0)''')

    def get(self, key, *, acknowledge=False):
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise TaskError('Invalid request key')
        row = self.db.execute('SELECT state,thread_id,run_id,result,acknowledged,identity,content FROM ironclaw_tasks WHERE request_key=?', (key,)).fetchone()
        if not row:
            return {'state': 'not_found'}
        if acknowledge:
            if row[0] != 'finished':
                raise TaskError('Only finished results can be acknowledged')
            with self.db:
                self.db.execute('UPDATE ironclaw_tasks SET acknowledged=1 WHERE request_key=?', (key,))
        return dict(state=row[0], thread_id=row[1], run_id=row[2],
                    result=json.loads(row[3]) if row[3] else None,
                    acknowledged=bool(row[4] or acknowledge))

    def _require_endpoint(self):
        if self.endpoint is None:
            raise TaskError('Endpoint configuration required')
        self.endpoint.validate()

    def _ensure_binding(self, key):
        self._require_endpoint()
        saved = self.db.execute('SELECT identity,content FROM ironclaw_tasks WHERE request_key=?', (key,)).fetchone()
        if saved and saved[0] != self._identity(saved[1]):
            raise TaskError('Task belongs to a different endpoint configuration')

    def list_requests(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TaskError('Limit must be between 1 and 100')
        rows = self.db.execute('SELECT request_key,state,acknowledged FROM ironclaw_tasks ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
        return [{'request_key': row[0], 'state': row[1], 'acknowledged': bool(row[2])} for row in rows]

    def _identity(self, content):
        self._require_endpoint()
        return hashlib.sha256(json.dumps({'content': content, 'endpoint': [
            self.endpoint.host, self.endpoint.user, self.endpoint.port, self.endpoint.http_port,
            self.endpoint.identity_file, self.endpoint.token_url_file]}, sort_keys=True).encode()).hexdigest()

    def submit(self, key, content, *, allow_submit=False):
        if allow_submit is not True:
            raise TaskError('Submission requires explicit opt-in')
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise TaskError('Invalid request key')
        if not isinstance(content, str) or not content.strip() or len(content.encode()) > 60000:
            raise TaskError('Invalid content')
        identity = self._identity(content)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            duplicate = self.db.execute("SELECT request_key FROM ironclaw_tasks WHERE identity=? AND request_key<>? AND state IN ('prepared','dispatching','ambiguous','pending','needs_operator','needs_review','deferred','blocked_busy') LIMIT 1", (identity, key)).fetchone()
            if duplicate:
                raise TaskError('Equivalent task already admitted; use its existing request key')
            self.db.execute('INSERT OR IGNORE INTO ironclaw_tasks VALUES(?,?,?,?,?,?,NULL,NULL,NULL,0)',
                            (key, identity, content, uuid.uuid4().hex, uuid.uuid4().hex, 'prepared'))
        row = self.db.execute('SELECT identity,thread_action,message_action,state FROM ironclaw_tasks WHERE request_key=?', (key,)).fetchone()
        if row[0] != identity:
            raise TaskError('Request key reused with different identity')
        if row[3] != 'prepared':
            return self.get(key)
        # Claim commits before any network operation; concurrent calls cannot dispatch twice.
        with self.db:
            changed = self.db.execute("UPDATE ironclaw_tasks SET state='dispatching' WHERE request_key=? AND state='prepared'", (key,)).rowcount
        if not changed:
            return self.get(key)
        try:
            session = self.transport(self.endpoint, 'GET', '/api/webchat/v2/session')
            channel = _id(session.get('session_channel_extension_id'))
            created = self.transport(self.endpoint, 'POST', '/api/webchat/v2/threads',
                                     {'client_action_id': row[1]}, allow_submit=True)
            thread = _id(created.get('thread', {}).get('thread_id'))
            with self.db:
                self.db.execute('UPDATE ironclaw_tasks SET thread_id=? WHERE request_key=?', (thread, key))
            ack = self.transport(self.endpoint, 'POST', f'/api/webchat/v2/channels/{channel}/messages',
                                 {'client_action_id': row[2], 'thread_id': thread, 'content': content}, allow_submit=True)
            if ack.get('thread_id') != thread:
                raise TaskError('Submission thread mismatch')
            outcome = ack.get('outcome')
            if outcome == 'rejected_busy':
                state, run = 'blocked_busy', None
            elif outcome == 'deferred_busy':
                state = 'deferred'
                run = _id(ack['run_id']) if ack.get('run_id') else None
            elif ack.get('status') in ('queued', 'running') and outcome in (None, 'queued', 'running'):
                state, run = 'pending', _id(ack.get('run_id'))
            else:
                state, run = 'needs_review', None
            with self.db:
                self.db.execute('UPDATE ironclaw_tasks SET state=?,run_id=? WHERE request_key=?', (state, run, key))
        except Exception:
            with self.db:
                self.db.execute("UPDATE ironclaw_tasks SET state='ambiguous' WHERE request_key=?", (key,))
            raise TaskError('Dispatch uncertain; inspect remotely before any new request') from None
        return self.get(key)

    def poll(self, key):
        task = self.get(key)
        self._ensure_binding(key)
        if task['state'] not in ('pending', 'needs_operator', 'deferred') or not task['run_id']:
            return task
        thread, run = _id(task['thread_id']), _id(task['run_id'])
        cursor, seen, final = None, set(), None
        for _ in range(20):
            path = f'/api/webchat/v2/threads/{thread}/timeline?limit=100'
            if cursor:
                path += '&cursor=' + quote(cursor, safe='')
            page = self.transport(self.endpoint, 'GET', path)
            messages = page.get('messages')
            if not isinstance(messages, list):
                raise TaskError('Invalid timeline messages')
            for message in messages:
                if not isinstance(message, dict) or message.get('turn_run_id') != run:
                    continue
                if message.get('kind') in ('assistant', 'assistant_message') and message.get('status') == 'finalized':
                    if not isinstance(message.get('content'), str):
                        raise TaskError('Invalid final content')
                    final = message['content']
            cursor = page.get('next_cursor')
            if cursor is None:
                break
            if not isinstance(cursor, str) or not cursor or len(cursor) > 512 or cursor in seen:
                raise TaskError('Invalid timeline cursor')
            seen.add(cursor)
        else:
            raise TaskError('Timeline page limit reached')
        with self.db:
            if final is not None:
                self.db.execute("UPDATE ironclaw_tasks SET state='finished',result=? WHERE request_key=? AND state IN ('pending','needs_operator','deferred')", (json.dumps(final), key))

        return self.get(key)

    def observe_event(self, key, event_type, frame):
        """Consume a verified SSE frame; no event stream connection is made here."""
        task = self.get(key)
        self._ensure_binding(key)
        if task['state'] not in ('pending', 'needs_operator', 'deferred') or not task['run_id']:
            return task
        if event_type in ('gate', 'auth_required'):
            prompt = frame.get('prompt', {})
            if prompt.get('turn_run_id') == task['run_id'] and isinstance(prompt.get('gate_ref'), str):
                with self.db:
                    self.db.execute("UPDATE ironclaw_tasks SET state='needs_operator' WHERE request_key=? AND state IN ('pending','deferred')", (key,))
        return self.get(key)
