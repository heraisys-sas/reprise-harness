"""Durable admission receipts. No transaction spans an external call."""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path


def file_evidence(root, paths=None, file_paths=None):
    evidence = []
    root = Path(root).resolve(strict=True)
    for project_relative, names in ((True, paths or []), (False, file_paths or [])):
        if not isinstance(names, list) or len(names) > 30:
            raise ValueError("Bounded file path list required")
        for name in names:
            if not isinstance(name, str):
                raise ValueError("File path required")
            if project_relative and Path(name).is_absolute():
                raise ValueError("Project-relative path required")
            path = (root / name) if project_relative else Path(name)
            path = path.resolve(strict=True)
            if project_relative and not path.is_relative_to(root):
                raise ValueError("Project-relative path required")
            if not path.is_file() or path.stat().st_size > 31457280:
                raise ValueError("Regular file at most 30 MB required")
            fingerprint = hashlib.sha256()
            with path.open("rb") as source:
                remaining = 31457281
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        break
                    fingerprint.update(chunk)
                    remaining -= len(chunk)
                if remaining == 0:
                    raise ValueError("File grew beyond 30 MB")
            evidence.append({"path": str(path), "sha256": fingerprint.hexdigest()})
    return evidence


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def validate_key(key):
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise ValueError('Persist a nonempty request_key (at most 128 characters) before sending')


# Statuses that prove no input was delivered (or that a result exists), so an equivalent
# delegation may be requested again under a new key. blocked_owner and blocked_session are only
# emitted before the turn is submitted ("no turn submitted"); blocked_permission is not listed
# because it also covers a human approval requested mid-turn. Anything else stays uncertain.
# `interrupted` is terminal but the work was not achieved: recorded, never finished.
RECORDED_STATUSES = ('finished', 'interrupted', 'quota_blocked', 'blocked_auth', 'billing_unverified',
                     'engine_unavailable', 'busy', 'blocked_owner', 'blocked_session')
# A submitted native turn whose outcome is not captured yet. Not recorded: no acknowledgement,
# no collection, and no equivalent redispatch before reconciliation.
RUNNING_STATUSES = ('running', 'awaiting_approval')
# Native reconciliation phases. `finished` and `interrupted` are terminal; `owner_unavailable`
# keeps the uncertainty instead of inventing a result.
PHASES = ('starting', 'active', 'awaiting_approval', 'finished', 'interrupted',
          'owner_unavailable', 'unknown')
NEXT_ACTIONS = {'starting': 'reconcile', 'active': 'reconcile', 'awaiting_approval': 'reconcile',
                'finished': 'collect', 'interrupted': 'collect', 'owner_unavailable': 'collect',
                'unknown': 'reconcile'}
TURN_FIELDS = ('thread_id', 'turn_id', 'phase', 'work_completed', 'terminal_evidence',
               'pending_method', 'owner', 'started_at', 'deadline_at', 'last_event_at',
               'last_event', 'event_count', 'context')
RECEIPT_FORMAT = 'receipt-json-v1'
LOOKUP_PROPERTIES = {
    'request_key': {'type': 'string', 'minLength': 1, 'maxLength': 128, 'pattern': r'\S'},
    'acknowledge': {'type': 'boolean'},
}
PAGE_PROPERTIES = {
    'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
    'max_chars': {'type': 'integer', 'minimum': 1, 'maximum': 6000, 'default': 6000},
    'result_sha256': {'type': 'string', 'minLength': 64, 'maxLength': 64,
                      'pattern': '^[0-9a-fA-F]{64}$'},
}
LOOKUP_SCHEMA = {
    'type': 'object',
    'oneOf': [
        {'properties': LOOKUP_PROPERTIES | {'view': {'const': 'full', 'default': 'full'}},
         'additionalProperties': False,
         'anyOf': [{'properties': {'acknowledge': {'const': False}}},
                   {'required': ['request_key']}]},
        {'properties': LOOKUP_PROPERTIES | {'view': {'const': 'receipt'}},
         'required': ['view', 'request_key'], 'additionalProperties': False},
        {'properties': LOOKUP_PROPERTIES | PAGE_PROPERTIES | {
            'view': {'const': 'page'}, 'acknowledge': {'const': False}},
         'required': ['view', 'request_key'], 'additionalProperties': False,
         'anyOf': [{'properties': {'offset': {'const': 0}}},
                   {'required': ['result_sha256']}]},
    ],
}


def json_integer(value):
    return type(value) is int or type(value) is float and value.is_integer()


def validate_lookup(args):
    if not isinstance(args, dict) or args.keys() - (
            LOOKUP_PROPERTIES.keys() | PAGE_PROPERTIES.keys() | {'view'}):
        raise ValueError('Invalid lookup arguments')
    view = args.get('view', 'full')
    if view not in ('full', 'receipt', 'page'):
        raise ValueError('Invalid receipt view')
    if 'request_key' in args:
        validate_key(args['request_key'])
    if type(args.get('acknowledge', False)) is not bool:
        raise ValueError('Boolean acknowledge required')
    if (view != 'full' or args.get('acknowledge')) and 'request_key' not in args:
        raise ValueError('A request_key is required')
    if view != 'page':
        if args.keys() & PAGE_PROPERTIES.keys():
            raise ValueError('Pagination arguments require page view')
        return view
    if args.get('acknowledge', False):
        raise ValueError('Pages cannot acknowledge a receipt')
    offset, maximum = args.get('offset', 0), args.get('max_chars', 6000)
    if not json_integer(offset) or offset < 0:
        raise ValueError('Nonnegative integer offset required')
    if not json_integer(maximum) or not 1 <= maximum <= 6000:
        raise ValueError('Integer max_chars from 1 to 6000 required')
    expected = args.get('result_sha256')
    if 'result_sha256' in args and (
            not isinstance(expected, str) or len(expected) != 64
            or any(c not in '0123456789abcdefABCDEF' for c in expected)):
        raise ValueError('SHA-256 must contain 64 hexadecimal characters')
    if offset and expected is None:
        raise ValueError('Continuation requires result_sha256')
    return view


def next_action(state, phase):
    if state == 'recorded':
        return 'collect'
    return NEXT_ACTIONS.get(phase, 'reconcile')


def receipt_view(receipt):
    result = receipt.get('result')
    text = None if result is None else json.dumps(
        result, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    manifest = {name: receipt.get(name) for name in
                ('status', 'request_key', 'state', 'attempt_id', 'phase', 'next_action',
                 'work_completed', 'terminal_evidence', 'thread_id', 'turn_id')}
    manifest.update(
        acknowledged=bool(receipt.get('acknowledged', False)),
        available=text is not None, format=RECEIPT_FORMAT,
        total_length=None if text is None else len(text),
        result_sha256=None if text is None else hashlib.sha256(text.encode('utf-8')).hexdigest())
    return manifest, text


class Store:
    def __init__(self, db, scope, now=None):
        self.db, self.scope = db, scope
        self.now = now or time.time
        self.active_key = None
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS delegation_requests(
                scope TEXT, request_key TEXT, identity TEXT NOT NULL, signature TEXT NOT NULL,
                parent TEXT, root TEXT NOT NULL, depth INTEGER NOT NULL, attempt TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, acknowledged INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(scope,request_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS delegation_turns(
                scope TEXT, request_key TEXT, attempt TEXT NOT NULL, thread_id TEXT, turn_id TEXT,
                phase TEXT NOT NULL, work_completed INTEGER NOT NULL DEFAULT 0,
                terminal_evidence TEXT, pending_method TEXT, owner TEXT, started_at REAL,
                deadline_at REAL, last_event_at REAL, last_event TEXT,
                event_count INTEGER NOT NULL DEFAULT 0, events TEXT NOT NULL DEFAULT '[]',
                updated_at REAL, context TEXT, PRIMARY KEY(scope,request_key))''')
            if 'context' not in {row[1] for row in db.execute('PRAGMA table_info(delegation_turns)')}:
                db.execute('ALTER TABLE delegation_turns ADD COLUMN context TEXT')
            self.reconcile_awaiting_human()

    def reconcile_awaiting_human(self):
        """One-shot, idempotent backfill (issue #53): receipts persisted before the
        awaiting_human state existed are reclassified from indeterminate; replay changes nothing."""
        with self.db:
            self.db.execute("UPDATE delegation_requests SET state='awaiting_human'"
                            " WHERE state='indeterminate' AND json_extract(result,'$.status')='blocked_permission'")

    def lookup(self, args):
        view = validate_lookup(args)
        receipt = self._lookup({k: v for k, v in args.items() if k in LOOKUP_PROPERTIES})
        if view == 'full':
            return receipt
        manifest, text = receipt_view(self._enrich(receipt))
        if view == 'receipt' or text is None:
            return manifest
        expected = args.get('result_sha256')
        if expected is not None and expected.lower() != manifest['result_sha256']:
            return dict(manifest, status='result_changed')
        offset = int(args.get('offset', 0))
        if offset > len(text):
            return dict(manifest, status='invalid_offset')
        fragment = text[offset:offset + int(args.get('max_chars', 6000))]
        end = offset + len(fragment)
        return dict(manifest, offset=offset, text=fragment, returned_length=len(fragment),
                    has_more=end < len(text), next_offset=end if end < len(text) else None)

    def _lookup(self, args):
        if not isinstance(args, dict) or args.keys() - {'request_key', 'acknowledge'}:
            raise ValueError('Invalid lookup arguments')
        key = args.get('request_key')
        if key is None:
            if args.get('acknowledge'):
                raise ValueError('Acknowledgement requires a request_key')
            rows = self.db.execute('SELECT request_key,state,parent,acknowledged FROM delegation_requests WHERE scope=? ORDER BY rowid DESC LIMIT 100', (self.scope,)).fetchall()
            return {'status': 'finished', 'requests': [dict(zip(('request_key', 'state', 'parent_request_key', 'acknowledged'), row)) for row in rows], 'limit': 100}
        validate_key(key)
        with self.db:
            row = self.db.execute('SELECT state,result,attempt,acknowledged FROM delegation_requests WHERE scope=? AND request_key=?', (self.scope, key)).fetchone()
            if not row:
                return {'status': 'not_found', 'request_key': key}
            if args.get('acknowledge'):
                if row[0] == 'awaiting_human':
                    # Acknowledgement is the documented reconciliation act for a human-pending
                    # attempt; the caller attests the observed native state. No other
                    # non-recorded state is ever acknowledged.
                    self.db.execute("UPDATE delegation_requests SET state='recorded', acknowledged=1 WHERE scope=? AND request_key=?", (self.scope, key))
                    state = 'recorded'
                elif row[0] != 'recorded':
                    raise ValueError('Only a recorded receipt can be acknowledged')
                else:
                    self.db.execute('UPDATE delegation_requests SET acknowledged=1 WHERE scope=? AND request_key=?', (self.scope, key))
                    state = row[0]
            else:
                state = row[0]
        receipt = {'status': 'finished', 'request_key': key, 'state': state, 'attempt_id': row[2],
                   'acknowledged': bool(row[3] or args.get('acknowledge')),
                   'result': json.loads(row[1]) if row[1] else None}
        return receipt

    def _enrich(self, receipt):
        """Add the reconciliation fields to compact views only; full receipts stay unchanged."""
        key = receipt.get('request_key')
        if not key or 'state' not in receipt:
            return receipt
        native = self.phase(key)
        if native:
            receipt.update(phase=native['phase'], native=native,
                           thread_id=native['thread_id'], turn_id=native['turn_id'],
                           work_completed=native['work_completed'],
                           terminal_evidence=native['terminal_evidence'],
                           next_action=next_action(receipt['state'], native['phase']))
        else:
            receipt['next_action'] = next_action(receipt['state'], None)
        return receipt

    def _events(self, key):
        row = self.db.execute('SELECT events FROM delegation_turns WHERE scope=? AND request_key=?', (self.scope, key)).fetchone()
        try:
            events = json.loads(row[0]) if row else []
        except (TypeError, ValueError):
            events = []
        return events if isinstance(events, list) else []

    def _count(self, key):
        row = self.db.execute('SELECT event_count FROM delegation_turns WHERE scope=? AND request_key=?', (self.scope, key)).fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def phase(self, key):
        # The read-only collector also supports databases predating native-turn storage.
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='delegation_turns'").fetchone():
            return None
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(delegation_turns)')}
        fields = [field if field in columns else 'NULL AS ' + field for field in TURN_FIELDS]
        row = self.db.execute(
            'SELECT ' + ','.join(fields) + ' FROM delegation_turns WHERE scope=? AND request_key=?',
            (self.scope, key)).fetchone()
        if not row:
            return None
        native = dict(zip(TURN_FIELDS, row))
        native['work_completed'] = bool(native['work_completed'])
        native['context'] = json.loads(native.get('context') or '{}')
        return native

    def attach(self, key, event):
        """Persist the native thread and turn identity before any client wait can expire."""
        validate_key(key)
        if not isinstance(event, dict) or event.get('phase') not in PHASES:
            raise ValueError('Invalid turn event')
        stamp = float(self.now())
        with self.db:
            row = self.db.execute('SELECT attempt FROM delegation_requests WHERE scope=? AND request_key=?', (self.scope, key)).fetchone()
            if not row:
                raise ValueError('Persist the request before attaching a native turn')
            events = (self._events(key) + [{'phase': event['phase'],
                                            'thread_id': event.get('thread_id'),
                                            'turn_id': event.get('turn_id'),
                                            'last_event': event.get('last_event'), 'at': stamp}])[-20:]
            count = self._count(key) + 1
            self.db.execute(
                '''INSERT INTO delegation_turns(scope,request_key,attempt,thread_id,turn_id,phase,
                   work_completed,terminal_evidence,pending_method,owner,started_at,deadline_at,
                   last_event_at,last_event,event_count,events,updated_at,context)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(scope,request_key) DO UPDATE SET
                     thread_id=COALESCE(excluded.thread_id,delegation_turns.thread_id),
                     turn_id=COALESCE(excluded.turn_id,delegation_turns.turn_id),
                     phase=excluded.phase, work_completed=excluded.work_completed,
                     terminal_evidence=COALESCE(excluded.terminal_evidence,delegation_turns.terminal_evidence),
                     pending_method=excluded.pending_method,
                     owner=COALESCE(excluded.owner,delegation_turns.owner),
                     started_at=COALESCE(delegation_turns.started_at,excluded.started_at),
                     deadline_at=COALESCE(delegation_turns.deadline_at,excluded.deadline_at),
                     last_event_at=excluded.last_event_at, last_event=excluded.last_event,
                     event_count=excluded.event_count, events=excluded.events,
                     updated_at=excluded.updated_at,
                     context=COALESCE(delegation_turns.context,excluded.context)''',
                (self.scope, key, row[0], event.get('thread_id'), event.get('turn_id'),
                 event['phase'], int(bool(event.get('work_completed'))),
                 event.get('terminal_evidence'), event.get('pending_method'),
                 event.get('owner'), event.get('started_at'), event.get('deadline_at'),
                 stamp, event.get('last_event') or event['phase'], count,
                 json.dumps(events, ensure_ascii=False), stamp,
                 json.dumps(event['context'], ensure_ascii=False) if event.get('context') is not None else None))
            if event['phase'] == 'active':
                self.db.execute(
                    "UPDATE delegation_requests SET state='running' WHERE scope=? AND request_key=? AND state='indeterminate'",
                    (self.scope, key))

    def set_phase(self, key, phase, **fields):
        """Record a reconciled native state without inventing a delivered result."""
        if phase not in PHASES:
            raise ValueError('Unknown native phase')
        stamp = float(self.now())
        with self.db:
            if not self.db.execute('SELECT 1 FROM delegation_turns WHERE scope=? AND request_key=?',
                                   (self.scope, key)).fetchone():
                raise ValueError('No native turn is persisted for this request')
            assignments, values = ['phase=?', 'updated_at=?'], [phase, stamp]
            for name in ('work_completed', 'terminal_evidence', 'pending_method', 'owner', 'deadline_at'):
                if name in fields:
                    assignments.append(name + '=?')
                    values.append(int(bool(fields[name])) if name == 'work_completed' else fields[name])
            last_event = fields.get('last_event', 'reconciled:' + phase)
            events = (self._events(key) + [{'phase': phase, 'last_event': last_event, 'at': stamp}])[-20:]
            count = self._count(key) + 1
            assignments += ['events=?', 'event_count=?', 'last_event=?', 'last_event_at=?']
            values += [json.dumps(events, ensure_ascii=False), count, last_event, stamp]
            values += [self.scope, key]
            self.db.execute('UPDATE delegation_turns SET ' + ', '.join(assignments) + ' WHERE scope=? AND request_key=?', values)

    def claim(self, key, owner, stale_after=120.0, alive=None):
        """Return owned, held or taken_over. A takeover is recorded, never redispatch."""
        alive = alive or owner_alive
        stamp = float(self.now())
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT owner,updated_at,phase FROM delegation_turns WHERE scope=? AND request_key=?', (self.scope, key)).fetchone()
            if not row:
                return 'unknown'
            if row[0] in (None, owner):
                self.db.execute('UPDATE delegation_turns SET owner=?, updated_at=? WHERE scope=? AND request_key=?', (owner, stamp, self.scope, key))
                return 'owned'
            if self.now() - (row[1] or 0) <= stale_after and alive(row[0]):
                return 'held'
            events = (self._events(key) + [{'phase': row[2], 'takeover_from': row[0],
                                            'owner': owner, 'at': stamp}])[-20:]
            self.db.execute('UPDATE delegation_turns SET owner=?, updated_at=?, events=?, event_count=? WHERE scope=? AND request_key=?',
                            (owner, stamp, json.dumps(events, ensure_ascii=False), len(events), self.scope, key))
            return 'taken_over'

    def release(self, key, owner):
        with self.db:
            self.db.execute('UPDATE delegation_turns SET owner=NULL WHERE scope=? AND request_key=? AND owner=?',
                            (self.scope, key, owner))

    def begin(self, key, payload, parent=None):
        validate_key(key)
        if parent is not None:
            validate_key(parent)
        identity = digest({'payload': payload, 'parent': parent})
        # Session IDs differ on a handoff; they must not conceal an equivalent delegation.
        semantic = {k: v for k, v in payload.items() if k not in ('tool', 'thread_id', 'wait_seconds', 'inactivity_seconds',
                                                                  'execution_seconds', 'stop_on_deadline')}
        semantic.setdefault('backend', 'codex')
        semantic.setdefault('kind', 'review' if payload.get('tool') == 'codex_review' else 'expertise')
        semantic.setdefault('level', 'standard')
        signature = digest(semantic)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT identity,state,result,attempt FROM delegation_requests WHERE scope=? AND request_key=?', (self.scope, key)).fetchone()
            if row:
                if row[0] != identity:
                    raise ValueError('request_key already bound to another immutable request')
                stored = json.loads(row[2]) if row[2] else {'status': 'indeterminate', 'request_key': key, 'attempt_id': row[3], 'reason': 'Attempt may have executed; reconcile without redispatch.'}
                native = self.phase(key)
                if native:
                    stored = dict(stored, phase=native['phase'], native=native,
                                  work_completed=native['work_completed'],
                                  terminal_evidence=native['terminal_evidence'])
                return stored
            root, depth, ancestor = key, 0, parent
            while ancestor is not None:
                row = self.db.execute('SELECT signature,parent,root,depth,state FROM delegation_requests WHERE scope=? AND request_key=?', (self.scope, ancestor)).fetchone()
                if not row:
                    raise ValueError('Parent must be an existing request in this project scope')
                if row[4] in ('indeterminate', 'awaiting_human') + RUNNING_STATUSES:
                    return {'status': 'indeterminate', 'request_key': ancestor, 'reason': 'Ancestor attempt remains uncertain; reconcile before delegating.'}
                if row[0] == signature:
                    return {'status': 'cycle_blocked', 'ancestor_request_key': ancestor, 'reason': 'Equivalent delegation in persistent ancestry; no dispatch.'}
                if ancestor == parent:
                    root, depth = row[2], row[3] + 1
                    # Bound recursive delegation and traversal; independent roots remain unlimited.
                    if depth > 8:
                        return {'status': 'lineage_blocked', 'reason': 'Maximum delegation depth is eight.'}
                ancestor = row[1]
            uncertain = self.db.execute(
                "SELECT request_key FROM delegation_requests WHERE scope=? AND signature=? AND state IN ('indeterminate','running','awaiting_human')",
                (self.scope, signature)).fetchone()
            if uncertain:
                return {'status': 'indeterminate', 'request_key': uncertain[0], 'reason': 'Equivalent attempt remains uncertain; no new execution.'}
            attempt = str(uuid.uuid4())
            self.db.execute('INSERT INTO delegation_requests(scope,request_key,identity,signature,parent,root,depth,attempt,state) VALUES (?,?,?,?,?,?,?,?,?)',
                            (self.scope, key, identity, signature, parent, root, depth, attempt, 'indeterminate'))
        self.active_key = key

    def finish(self, key, result):
        result = dict(result, request_key=key)
        if result.get('status') in RECORDED_STATUSES:
            state = 'recorded'
        elif result.get('status') in RUNNING_STATUSES:
            state = 'running'
        elif result.get('status') == 'blocked_permission':
            # Human approval requested mid-turn: reconcilable, but only by an
            # acknowledgement that attests the observed native state.
            state = 'awaiting_human'
        else:
            state = 'indeterminate'
        with self.db:
            self.db.execute('UPDATE delegation_requests SET state=?,result=? WHERE scope=? AND request_key=?',
                            (state, json.dumps(result, ensure_ascii=False), self.scope, key))
            existing = self.phase(key)
            if existing:
                phase = result.get('phase')
                if phase not in PHASES:
                    phase = existing['phase']
                assignments, values = ['phase=?', 'updated_at=?'], [phase, float(self.now())]
                if 'work_completed' in result:
                    assignments.append('work_completed=?')
                    values.append(int(bool(result['work_completed'])))
                if result.get('terminal_evidence') is not None:
                    assignments.append('terminal_evidence=?')
                    values.append(result['terminal_evidence'])
                if result.get('pending_request'):
                    assignments.append('pending_method=?')
                    values.append((result['pending_request'] or {}).get('method'))
                values += [self.scope, key]
                self.db.execute('UPDATE delegation_turns SET ' + ', '.join(assignments) + ' WHERE scope=? AND request_key=?', values)
        return result


def owner_alive(owner):
    """Best-effort local ownership probe; unknown formats are treated as dead."""
    if not isinstance(owner, str) or ':' not in owner:
        return False
    try:
        os.kill(int(owner.rsplit(':', 1)[-1]), 0)
        return True
    except (OSError, ValueError):
        return False
