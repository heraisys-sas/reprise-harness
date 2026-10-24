"""Durable admission receipts. No transaction spans an external call."""
import hashlib
import json
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


def receipt_view(receipt):
    result = receipt.get('result')
    text = None if result is None else json.dumps(
        result, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    manifest = {name: receipt.get(name) for name in
                ('status', 'request_key', 'state', 'attempt_id')}
    manifest.update(
        acknowledged=bool(receipt.get('acknowledged', False)),
        available=text is not None, format=RECEIPT_FORMAT,
        total_length=None if text is None else len(text),
        result_sha256=None if text is None else hashlib.sha256(text.encode('utf-8')).hexdigest())
    return manifest, text


class Store:
    def __init__(self, db, scope):
        self.db, self.scope = db, scope
        self.active_key = None
        with db:
            db.execute('''CREATE TABLE IF NOT EXISTS delegation_requests(
                scope TEXT, request_key TEXT, identity TEXT NOT NULL, signature TEXT NOT NULL,
                parent TEXT, root TEXT NOT NULL, depth INTEGER NOT NULL, attempt TEXT NOT NULL,
                state TEXT NOT NULL, result TEXT, acknowledged INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(scope,request_key))''')

    def lookup(self, args):
        view = validate_lookup(args)
        receipt = self._lookup({k: v for k, v in args.items() if k in LOOKUP_PROPERTIES})
        if view == 'full':
            return receipt
        manifest, text = receipt_view(receipt)
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
                if row[0] != 'recorded':
                    raise ValueError('Only a recorded receipt can be acknowledged')
                self.db.execute('UPDATE delegation_requests SET acknowledged=1 WHERE scope=? AND request_key=?', (self.scope, key))
        return {'status': 'finished', 'request_key': key, 'state': row[0], 'attempt_id': row[2],
                'acknowledged': bool(row[3] or args.get('acknowledge')), 'result': json.loads(row[1]) if row[1] else None}

    def begin(self, key, payload, parent=None):
        validate_key(key)
        if parent is not None:
            validate_key(parent)
        identity = digest({'payload': payload, 'parent': parent})
        # Session IDs differ on a handoff; they must not conceal an equivalent delegation.
        semantic = {k: v for k, v in payload.items() if k not in ('tool', 'thread_id')}
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
                return (json.loads(row[2]) if row[2] else {'status': 'indeterminate', 'request_key': key, 'attempt_id': row[3], 'reason': 'Attempt may have executed; reconcile without redispatch.'})
            root, depth, ancestor = key, 0, parent
            while ancestor is not None:
                row = self.db.execute('SELECT signature,parent,root,depth,state FROM delegation_requests WHERE scope=? AND request_key=?', (self.scope, ancestor)).fetchone()
                if not row:
                    raise ValueError('Parent must be an existing request in this project scope')
                if row[4] == 'indeterminate':
                    return {'status': 'indeterminate', 'request_key': ancestor, 'reason': 'Ancestor attempt remains uncertain; reconcile before delegating.'}
                if row[0] == signature:
                    return {'status': 'cycle_blocked', 'ancestor_request_key': ancestor, 'reason': 'Equivalent delegation in persistent ancestry; no dispatch.'}
                if ancestor == parent:
                    root, depth = row[2], row[3] + 1
                    # Bound recursive delegation and traversal; independent roots remain unlimited.
                    if depth > 8:
                        return {'status': 'lineage_blocked', 'reason': 'Maximum delegation depth is eight.'}
                ancestor = row[1]
            uncertain = self.db.execute("SELECT request_key FROM delegation_requests WHERE scope=? AND signature=? AND state='indeterminate'", (self.scope, signature)).fetchone()
            if uncertain:
                return {'status': 'indeterminate', 'request_key': uncertain[0], 'reason': 'Equivalent attempt remains uncertain; no new execution.'}
            attempt = str(uuid.uuid4())
            self.db.execute('INSERT INTO delegation_requests(scope,request_key,identity,signature,parent,root,depth,attempt,state) VALUES (?,?,?,?,?,?,?,?,?)',
                            (self.scope, key, identity, signature, parent, root, depth, attempt, 'indeterminate'))
        self.active_key = key

    def finish(self, key, result):
        result = dict(result, request_key=key)
        state = 'recorded' if result.get('status') in ('finished', 'quota_blocked', 'blocked_auth', 'billing_unverified', 'engine_unavailable', 'busy') else 'indeterminate'
        with self.db:
            self.db.execute('UPDATE delegation_requests SET state=?,result=? WHERE scope=? AND request_key=?', (state, json.dumps(result, ensure_ascii=False), self.scope, key))
        return result
