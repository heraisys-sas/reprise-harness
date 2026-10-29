"""Opt-in durable IronClaw tasks. An uncertain POST is never automatically replayed."""
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from urllib.parse import quote
from ironclaw_ssh import request

# Schéma des identités de tâche. Toute évolution de `_identity()` doit incrémenter ce
# numéro : `_ensure_binding()` compare l'identité recalculée à celle stockée, et sans
# version un simple changement d'algorithme serait pris pour un changement d'endpoint
# (« Task belongs to a different endpoint configuration »), rendant les tâches illisibles.
IDENTITY_SCHEME = 'v1'

# Sortie automatique d'un refus `blocked_busy` : jamais de rejeu du POST, seulement des
# lectures espacées, puis une décision opérateur. Constantes ajustables.
BUSY_MAX_ATTEMPTS = 5
BUSY_BACKOFF_SECONDS = 300
# Balayage déclenché à l'entrée de `poll()` : borné pour ne pas multiplier les timelines.
POLL_SWEEP_LIMIT = 5
# Pages lues au plus par appel de `poll()` ; au-delà, le curseur atteint est persisté et la
# reprise se fait à l'appel suivant.
MAX_TIMELINE_PAGES = 20
# Rescans complets consécutifs tolérés quand le curseur de reprise est invalidé ; au-delà,
# dégradation en erreur plutôt que boucle coût à chaque tour.
MAX_TIMELINE_RESCANS = 2


class TaskError(Exception):
    pass


def _fingerprint(messages):
    return hashlib.sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise TaskError('Invalid API identifier')
    return value


class Tasks:
    def __init__(self, db, endpoint=None, transport=request, expire_after=None):
        self.db, self.endpoint, self.transport = db, endpoint, transport
        self.expire_after = expire_after
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS ironclaw_tasks(
              request_key TEXT PRIMARY KEY, identity TEXT NOT NULL, content TEXT NOT NULL,
              thread_action TEXT NOT NULL, message_action TEXT NOT NULL,
              state TEXT NOT NULL, thread_id TEXT, run_id TEXT, result TEXT,
              acknowledged INTEGER NOT NULL DEFAULT 0)''')
            columns = {row[1] for row in db.execute('PRAGMA table_info(ironclaw_tasks)')}
            if 'ack_json' not in columns:
                db.execute('ALTER TABLE ironclaw_tasks ADD COLUMN ack_json TEXT')
            if 'parent_request_key' not in columns:
                db.execute('ALTER TABLE ironclaw_tasks ADD COLUMN parent_request_key TEXT')
            if 'admitted_at' not in columns:
                db.execute('ALTER TABLE ironclaw_tasks ADD COLUMN admitted_at REAL')
            if 'last_error' not in columns:
                db.execute('ALTER TABLE ironclaw_tasks ADD COLUMN last_error TEXT')
            if 'busy_attempts' not in columns:
                db.execute('ALTER TABLE ironclaw_tasks ADD COLUMN busy_attempts INTEGER NOT NULL DEFAULT 0')
            if 'next_attempt_at' not in columns:
                db.execute('ALTER TABLE ironclaw_tasks ADD COLUMN next_attempt_at REAL')
            # Reprise incrémentale de la timeline : curseur opaque par (thread, run).
            db.execute('''CREATE TABLE IF NOT EXISTS ironclaw_timeline_cursors(
              thread_id TEXT NOT NULL, run_id TEXT NOT NULL, page_cursor TEXT, boundary TEXT,
              folded TEXT, updated_at REAL, PRIMARY KEY(thread_id, run_id))''')

    def get(self, key, *, acknowledge=False):
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise TaskError('Invalid request key')
        row = self.db.execute('SELECT state,thread_id,run_id,result,acknowledged,identity,content,last_error FROM ironclaw_tasks WHERE request_key=?', (key,)).fetchone()
        if not row:
            return {'state': 'not_found'}
        if acknowledge:
            if row[0] != 'finished':
                raise TaskError('Only finished results can be acknowledged')
            with self.db:
                self.db.execute('UPDATE ironclaw_tasks SET acknowledged=1 WHERE request_key=?', (key,))
        return dict(state=row[0], thread_id=row[1], run_id=row[2],
                    result=json.loads(row[3]) if row[3] else None,
                    acknowledged=bool(row[4] or acknowledge), last_error=row[7])

    def _require_endpoint(self):
        if self.endpoint is None:
            raise TaskError('Endpoint configuration required')
        self.endpoint.validate()

    def _ensure_binding(self, key, connection=None):
        self._require_endpoint()
        connection = self.db if connection is None else connection
        saved = connection.execute('SELECT identity,content,parent_request_key FROM ironclaw_tasks WHERE request_key=?', (key,)).fetchone()
        if not saved:
            return
        stored, content, parent_key = saved
        expected = self._identity(content, parent_key)
        if stored == expected:
            return
        # Ligne écrite avant la version des identités : on migre au lieu de crier à un
        # changement d'endpoint, qui serait un faux diagnostic.
        if ':' not in stored and stored == self._identity_legacy(content, parent_key):
            if connection is self.db:
                # Hors section d'écriture ouverte, la migration est sa propre section ; dans
                # une section ouverte, `_write_transaction()` la laisse au propriétaire.
                with self._write_transaction() as owned:
                    owned.execute('UPDATE ironclaw_tasks SET identity=? WHERE request_key=?', (expected, key))
            else:
                connection.execute('UPDATE ironclaw_tasks SET identity=? WHERE request_key=?', (expected, key))
            return
        raise TaskError('Task belongs to a different endpoint configuration')

    def list_requests(self, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TaskError('Limit must be between 1 and 100')
        rows = self.db.execute('SELECT request_key,state,acknowledged FROM ironclaw_tasks ORDER BY rowid DESC LIMIT ?', (limit,)).fetchall()
        return [{'request_key': row[0], 'state': row[1], 'acknowledged': bool(row[2])} for row in rows]

    def _identity(self, content, parent_key=None):
        self._require_endpoint()
        base = hashlib.sha256(json.dumps({'content': content, 'scheme': IDENTITY_SCHEME, 'endpoint': [
            self.endpoint.host, self.endpoint.user, self.endpoint.port, self.endpoint.http_port,
            self.endpoint.identity_file, self.endpoint.token_url_file]}, sort_keys=True).encode()).hexdigest()
        if parent_key is None:
            return f'{IDENTITY_SCHEME}:{base}'
        linked = hashlib.sha256(json.dumps({'base': base, 'parent': parent_key}, sort_keys=True).encode()).hexdigest()
        return f'{IDENTITY_SCHEME}:{linked}'

    def _identity_legacy(self, content, parent_key=None):
        """Identité pré-versionnée, conservée uniquement pour migrer les lignes existantes."""
        self._require_endpoint()
        base = hashlib.sha256(json.dumps({'content': content, 'endpoint': [
            self.endpoint.host, self.endpoint.user, self.endpoint.port, self.endpoint.http_port,
            self.endpoint.identity_file, self.endpoint.token_url_file]}, sort_keys=True).encode()).hexdigest()
        if parent_key is None:
            return base
        return hashlib.sha256(json.dumps({'base': base, 'parent': parent_key}, sort_keys=True).encode()).hexdigest()

    @contextmanager
    def _write_transaction(self):
        """Seul propriétaire d'une section d'écriture : un seul BEGIN IMMEDIATE, émis ici.

        Sur une base fichier la section tourne sur une connexion dédiée dont le verrou est
        validé puis rendu avant la phase réseau ; une connexion mémoire partagée n'en
        héberge pas de seconde, elle garde donc le chemin direct et n'ouvre une transaction
        que si l'appelant n'en a pas déjà une.
        """
        info = self.db.execute('PRAGMA database_list').fetchall()
        filename = next((row[2] for row in info if row[1] == 'main'), '')
        if not filename:
            owned = not self.db.in_transaction
            if not owned:
                yield self.db
                return
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            return
        conn = sqlite3.connect(filename, isolation_level=None)
        # Parité avec la connexion appelante : `sqlite3` n'expose pas `detect_types`.
        conn.row_factory = self.db.row_factory
        conn.text_factory = self.db.text_factory
        timeout = self.db.execute('PRAGMA busy_timeout').fetchone()[0]
        try:
            conn.execute(f'PRAGMA busy_timeout={int(timeout)}')
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def submit(self, key, content, *, allow_submit=False):
        return self._submit(key, content, allow_submit=allow_submit)

    def followup(self, key, parent_key, content, *, allow_submit=False):
        if not isinstance(parent_key, str) or not parent_key.strip() or len(parent_key) > 128 or key == parent_key:
            raise TaskError('Invalid parent request key')
        return self._submit(key, content, parent_key=parent_key, allow_submit=allow_submit)

    def _submit(self, key, content, *, parent_key=None, allow_submit=False):
        if allow_submit is not True:
            raise TaskError('Submission requires explicit opt-in')
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise TaskError('Invalid request key')
        if not isinstance(content, str) or not content.strip() or len(content.encode()) > 60000:
            raise TaskError('Invalid content')
        # Validate the actual ASCII JSON bound before admitting or creating a remote thread.
        if len(json.dumps({'client_action_id': 'x' * 32, 'thread_id': 'x' * 128, 'content': content}, ensure_ascii=True)) > 65536:
            raise TaskError('Encoded submission too large')
        identity = self._identity(content, parent_key)
        thread = None
        with self._write_transaction() as admission:
            self._ensure_binding(key, admission)
            if parent_key is not None:
                self._ensure_binding(parent_key, admission)
                parent = self.get(parent_key)
                if parent['state'] != 'finished' or not parent['thread_id']:
                    raise TaskError('Followup requires a finished parent with an owned thread')
                thread = _id(parent['thread_id'])
                ancestor, visited = parent_key, set()
                while ancestor is not None:
                    if ancestor in visited or len(visited) >= 8:
                        raise TaskError('Followup lineage exceeds eight ancestors or contains a cycle')
                    visited.add(ancestor)
                    link = admission.execute('SELECT parent_request_key FROM ironclaw_tasks WHERE request_key=?', (ancestor,)).fetchone()
                    if link is None:
                        raise TaskError('Missing parent lineage')
                    ancestor = link[0]
                active = admission.execute("SELECT request_key FROM ironclaw_tasks WHERE thread_id=? AND request_key<>? AND state<>'finished' LIMIT 1", (thread, key)).fetchone()
                if active:
                    raise TaskError('Another unfinished task owns this thread')
            duplicate = admission.execute("SELECT request_key FROM ironclaw_tasks WHERE identity IN (?,?) AND request_key<>? AND state IN ('prepared','dispatching','ambiguous','pending','needs_operator','needs_review','deferred','blocked_busy') LIMIT 1", (identity, self._identity_legacy(content, parent_key), key)).fetchone()
            if duplicate:
                raise TaskError('Equivalent task already admitted; use its existing request key')
            admission.execute('INSERT OR IGNORE INTO ironclaw_tasks(request_key,identity,content,thread_action,message_action,state,thread_id,run_id,result,acknowledged,parent_request_key,admitted_at) VALUES(?,?,?,?,?,?,?,NULL,NULL,0,?,?)',
                              (key, identity, content, uuid.uuid4().hex, uuid.uuid4().hex, 'prepared', thread, parent_key, time.time()))
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
        post_attempted = False
        try:
            session = self.transport(self.endpoint, 'GET', '/api/webchat/v2/session')
            channel = _id(session.get('session_channel_extension_id'))
            if parent_key is None:
                post_attempted = True
                created = self.transport(self.endpoint, 'POST', '/api/webchat/v2/threads',
                                         {'client_action_id': row[1]}, allow_submit=True)
                thread = _id(created.get('thread', {}).get('thread_id'))
                with self.db:
                    self.db.execute('UPDATE ironclaw_tasks SET thread_id=? WHERE request_key=?', (thread, key))
            post_attempted = True
            ack = self.transport(self.endpoint, 'POST', f'/api/webchat/v2/channels/{channel}/messages',
                                 {'client_action_id': row[2], 'thread_id': thread, 'content': content}, allow_submit=True)
            if ack.get('thread_id') != thread:
                raise TaskError('Submission thread mismatch')
            projection = {field: value for field in ('thread_id', 'run_id', 'status', 'outcome', 'accepted_message_ref')
                          if isinstance((value := ack.get(field)), str)
                          and re.fullmatch(r'[A-Za-z0-9_:.-]{1,256}', value)}
            with self.db:
                self.db.execute('UPDATE ironclaw_tasks SET ack_json=? WHERE request_key=?', (json.dumps(projection), key))
            outcome = ack.get('outcome')
            if outcome == 'rejected_busy':
                state, run = 'blocked_busy', None
            elif outcome == 'deferred_busy':
                state = 'deferred'
                run = _id(ack['run_id']) if ack.get('run_id') else None
            elif ack.get('status') in ('queued', 'running') and outcome in (None, 'queued', 'running'):
                state, run = 'pending', _id(ack.get('run_id'))
            else:
                state = 'needs_review'
                try:
                    run = _id(ack.get('run_id'))
                except TaskError:
                    run = None
            with self.db:
                self.db.execute('UPDATE ironclaw_tasks SET state=?,run_id=? WHERE request_key=?', (state, run, key))
        except (TaskError, OSError, TimeoutError, ValueError) as exc:
            state = 'ambiguous' if post_attempted else 'prepared'
            detail = f'{type(exc).__name__}: {exc}'[:500]
            with self.db:
                self.db.execute('UPDATE ironclaw_tasks SET state=?,last_error=? WHERE request_key=?', (state, detail, key))
            raise TaskError('Dispatch uncertain; inspect remotely before any new request') from exc
        return self.get(key)

    def poll(self, key, *, sweep=True):
        # Une lecture humaine ou un tick de suivi fait aussi avancer le stock incertain ;
        # le balayage est borné et n'ajoute aucun POST.
        if sweep:
            self.reconcile_due(limit=POLL_SWEEP_LIMIT, exclude=key)
        task = self.get(key)
        self._ensure_binding(key)
        if task['state'] not in ('pending', 'needs_operator', 'deferred') or not task['run_id']:
            return task
        thread, run = _id(task['thread_id']), _id(task['run_id'])
        state, complete = self._scan(thread, run)
        if not complete:
            # Budget de pages épuisé : le curseur atteint est persisté, l'état reste inchangé.
            return self.get(key)
        final = None if state['activity_after'] else state['final']
        if final is None and self._expired(key):
            with self.db:
                self.db.execute("UPDATE ironclaw_tasks SET state='expired' WHERE request_key=? AND state IN ('pending','deferred')", (key,))
                self.db.execute('DELETE FROM ironclaw_timeline_cursors WHERE thread_id=? AND run_id=?', (thread, run))
            return self.get(key)
        with self.db:
            if final is not None:
                self.db.execute("UPDATE ironclaw_tasks SET state='finished',result=? WHERE request_key=? AND state IN ('pending','needs_operator','deferred')", (json.dumps(final), key))
                self.db.execute('DELETE FROM ironclaw_timeline_cursors WHERE thread_id=? AND run_id=?', (thread, run))

        return self.get(key)

    def fail(self, key, reason):
        """Operator exit for gated, ambiguous, review or busy tasks. Local-only; no replay."""
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
            raise TaskError('Invalid failure reason')
        with self.db:
            changed = self.db.execute("UPDATE ironclaw_tasks SET state='failed',result=? WHERE request_key=? AND state IN ('needs_operator','ambiguous','needs_review','blocked_busy')", (json.dumps({'operator': reason}, ensure_ascii=False), key)).rowcount
        if not changed:
            raise TaskError('Only gated, ambiguous, review or busy tasks can be marked failed')
        return self.get(key)

    def _expired(self, key):
        if self.expire_after is None:
            return False
        row = self.db.execute('SELECT admitted_at,state FROM ironclaw_tasks WHERE request_key=?', (key,)).fetchone()
        return bool(row and row[0] is not None and row[1] in ('pending', 'deferred') and time.time() - row[0] > self.expire_after)

    def _timeline_page(self, thread, cursor=None):
        path = f'/api/webchat/v2/threads/{_id(thread)}/timeline?limit=100'
        if cursor:
            path += '&cursor=' + quote(cursor, safe='')
        page = self.transport(self.endpoint, 'GET', path)
        messages = page.get('messages')
        if not isinstance(messages, list):
            raise TaskError('Invalid timeline messages')
        next_cursor = page.get('next_cursor')
        if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor or len(next_cursor) > 512):
            raise TaskError('Invalid timeline cursor')
        return messages, next_cursor

    def _timeline_matches(self, thread, key, content):
        """Budgeted fold of the matching user messages; the cursor is persisted so the
        next call resumes instead of failing, like `_scan()`. `complete` is False when
        the page budget ran out."""
        row = self.db.execute('SELECT page_cursor,folded FROM ironclaw_timeline_cursors '
                              'WHERE thread_id=? AND run_id=?', (thread, key)).fetchone()
        cursor = row[0] if row else None
        folded = json.loads(row[1]) if row and row[1] else {'matches': []}
        budget, seen = MAX_TIMELINE_PAGES, set()
        while budget:
            messages, next_cursor = self._timeline_page(thread, cursor)
            budget -= 1
            folded['matches'] += [_id(m['turn_run_id']) for m in messages
                                  if isinstance(m, dict) and m.get('kind') in ('user', 'user_message')
                                  and m.get('content') == content]
            # Le curseur du tour suivant est le `next_cursor` : aucune page de recouvrement,
            # les correspondances s'accumulent sans double comptage.
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO ironclaw_timeline_cursors'
                                '(thread_id,run_id,page_cursor,boundary,folded,updated_at) VALUES(?,?,?,?,?,?)',
                                (thread, key, next_cursor, None, json.dumps(folded), time.time()))
            if next_cursor is None:
                return folded['matches'], True
            if next_cursor in seen:
                raise TaskError('Invalid timeline cursor')
            seen.add(next_cursor)
            cursor = next_cursor
        return folded['matches'], False

    def _load_scan(self, thread, run):
        row = self.db.execute('SELECT page_cursor,boundary,folded FROM ironclaw_timeline_cursors '
                              'WHERE thread_id=? AND run_id=?', (thread, run)).fetchone()
        if not row:
            return {'page_cursor': None, 'boundary': None, 'final': None, 'activity_after': False, 'rescans': 0}
        folded = json.loads(row[2]) if row[2] else {}
        return {'page_cursor': row[0], 'boundary': row[1], 'rescans': folded.get('rescans', 0),
                'final': folded.get('final'), 'activity_after': bool(folded.get('activity_after'))}

    def _save_scan(self, thread, run, state):
        folded = json.dumps({'final': state['final'], 'activity_after': state['activity_after'],
                             'rescans': state['rescans']}, ensure_ascii=False)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO ironclaw_timeline_cursors'
                            '(thread_id,run_id,page_cursor,boundary,folded,updated_at) VALUES(?,?,?,?,?,?)',
                            (thread, run, state['page_cursor'], state['boundary'], folded, time.time()))

    def _fold(self, state, messages, run):
        final, activity_after = state['final'], state['activity_after']
        for message in messages:
            if not isinstance(message, dict) or message.get('turn_run_id') != run:
                continue
            if message.get('kind') in ('assistant', 'assistant_message') and message.get('status') == 'finalized':
                if not isinstance(message.get('content'), str):
                    raise TaskError('Invalid final content')
                final, activity_after = message['content'], False
            elif final is not None:
                # A finalized message followed by later run activity (tools, streaming,
                # user turn) was intermediate, not the end of the run.
                activity_after = True
        return {'page_cursor': state['page_cursor'], 'boundary': state['boundary'],
                'final': final, 'activity_after': activity_after, 'rescans': state['rescans']}

    def _scan(self, thread, run):
        """Folds the timeline incrementally; `complete` is False when the page budget ran out."""
        state = self._load_scan(thread, run)
        cursor, budget, seen = state['page_cursor'], MAX_TIMELINE_PAGES, set()
        while budget:
            messages, next_cursor = self._timeline_page(thread, cursor)
            fingerprint = _fingerprint(messages)
            if cursor is not None and cursor == state['page_cursor'] and state['boundary'] is not None \
                    and fingerprint != state['boundary']:
                # Curseur opaque invalidé au point de reprise : rescan complet, borné et
                # répétable d'un tour à l'autre tant que le plafond n'est pas atteint.
                if state['rescans'] >= MAX_TIMELINE_RESCANS:
                    raise TaskError('Invalid timeline cursor')
                state = {'page_cursor': None, 'boundary': None, 'final': None,
                         'activity_after': False, 'rescans': state['rescans'] + 1}
                cursor, seen = None, set()
                continue
            budget -= 1
            if cursor is None:
                # Lecture depuis la tête : l'état repart de zéro, sans page de recouvrement.
                state = dict(state, final=None, activity_after=False)
            state = self._fold(state, messages, run)
            state['page_cursor'], state['boundary'] = cursor, fingerprint
            if next_cursor is None:
                state['rescans'] = 0
            self._save_scan(thread, run, state)
            if next_cursor is None:
                return state, True
            if next_cursor in seen:
                raise TaskError('Invalid timeline cursor')
            seen.add(next_cursor)
            cursor = next_cursor
        return state, False

    def reconcile(self, key, now=None):
        """Read-only recovery of a lost ACK in the thread created for this task."""
        task = self.get(key)
        if task['state'] in ('not_found', 'finished'):
            return task
        self._ensure_binding(key)
        if task['state'] not in ('needs_review', 'ambiguous', 'dispatching', 'blocked_busy') or not task['thread_id']:
            raise TaskError('Task is not eligible for read-only reconciliation')
        content = self.db.execute('SELECT content FROM ironclaw_tasks WHERE request_key=?', (key,)).fetchone()[0]
        matches, complete = self._timeline_matches(task['thread_id'], key, content)
        if not complete:
            # Budget de pages épuisé : curseur persisté, la réconciliation reprendra au tour suivant.
            return self.get(key)
        with self.db:
            self.db.execute('DELETE FROM ironclaw_timeline_cursors WHERE thread_id=? AND run_id=?',
                            (task['thread_id'], key))
        if task['state'] == 'blocked_busy' and not matches:
            return self._busy_backoff(key, now)
        if len(matches) != 1:
            if task['state'] == 'blocked_busy':
                # Même politique que busy : retest espacé au lieu d'un retraitement à chaque sweep.
                self._busy_backoff(key, now)
            raise TaskError('Reconciliation requires exactly one matching user message')
        if task['run_id'] is not None and task['run_id'] != matches[0]:
            raise TaskError('Reconciliation run conflicts with saved acknowledgement')
        with self.db:
            self.db.execute("UPDATE ironclaw_tasks SET state='pending',run_id=? WHERE request_key=? AND state IN ('needs_review','ambiguous','dispatching','blocked_busy')", (matches[0], key))
        return self.poll(key, sweep=False)

    def _busy_backoff(self, key, now=None):
        """Un refus `blocked_busy` absent du thread ne se rejoue pas : lectures espacées, puis opérateur."""
        now = time.time() if now is None else now
        row = self.db.execute('SELECT busy_attempts FROM ironclaw_tasks WHERE request_key=?', (key,)).fetchone()
        attempts = (row[0] if row else 0) + 1
        with self.db:
            if attempts >= BUSY_MAX_ATTEMPTS:
                self.db.execute("UPDATE ironclaw_tasks SET state='needs_operator',busy_attempts=?,next_attempt_at=NULL,"
                                "last_error='busy: never accepted' WHERE request_key=? AND state='blocked_busy'", (attempts, key))
            else:
                self.db.execute("UPDATE ironclaw_tasks SET busy_attempts=?,next_attempt_at=? "
                                "WHERE request_key=? AND state='blocked_busy'",
                                (attempts, now + BUSY_BACKOFF_SECONDS, key))
        return self.get(key)

    def reconcile_due(self, limit=10, now=None, min_age=300, exclude=None):
        '''Read-only sweep of every task left uncertain by a lost acknowledgement.

        `reconcile()` handles one key and raises when the evidence is inconclusive, so
        nothing ever swept the backlog: a task left `needs_review`, `ambiguous`,
        `dispatching` or `blocked_busy` stayed that way until a human asked. This sweep is
        bounded -- at most `limit` candidates older than `min_age` seconds -- honours the
        per-task backoff (`next_attempt_at`) and never raises on a single task: each
        failure is reported per key instead of turning the whole sweep into an exception.
        '''
        if type(limit) is not int or not 1 <= limit <= 100:
            raise TaskError('Limit must be between 1 and 100')
        if type(min_age) not in (int, float) or min_age < 0:
            raise TaskError('Minimum age must be a non-negative number')
        now = time.time() if now is None else now
        rows = self.db.execute(
            "SELECT request_key FROM ironclaw_tasks WHERE state IN "
            "('needs_review','ambiguous','dispatching','blocked_busy') AND thread_id IS NOT NULL "
            "AND admitted_at IS NOT NULL AND admitted_at<=? "
            "AND (next_attempt_at IS NULL OR next_attempt_at<=?) AND request_key<>? "
            "ORDER BY admitted_at LIMIT ?",
            (now - min_age, now, exclude or '', limit)).fetchall()
        results = []
        for (key,) in rows:
            try:
                task = self.reconcile(key, now=now)
            except (TaskError, OSError, TimeoutError, ValueError) as exc:
                results.append({'request_key': key, 'state': 'unreconciled',
                                'reason': f'{type(exc).__name__}: {exc}'[:500]})
                continue
            results.append({'request_key': key, 'state': task['state'],
                            'run_id': task.get('run_id')})
        return results

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
