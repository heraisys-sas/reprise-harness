"""Pure, bounded request-inbox transitions. No storage or system integration."""
import copy
import hashlib
import math
import re
import uuid

VERSION = 1
MAX_TEXT = 1024
MAX_PENDING = 64
MAX_IDENTITIES = 1024
# Unix seconds through 9999-12-31 23:59:59 UTC; compare before float conversion.
MAX_TIMESTAMP = 253402300799


class Conflict(ValueError):
    pass


class NotFound(ValueError):
    pass


class CapacityExceeded(ValueError):
    pass


def empty():
    return {'version': VERSION, 'entries': []}


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError('Canonical identity required')
    try:
        parsed = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError('Canonical identity required') from exc
    if parsed != value:
        raise ValueError('Canonical identity required')
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Nonempty text required')
    try:
        encoded = value.encode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise ValueError('Invalid text encoding') from exc
    if len(encoded) > MAX_TEXT or any((ord(c) < 32 and c not in '\n\t') or ord(c) == 127 for c in value):
        raise ValueError('Invalid or oversized text')
    return encoded


def _time(value):
    if (type(value) not in (int, float) or value < 0 or value > MAX_TIMESTAMP
            or not math.isfinite(value)):
        raise ValueError('Invalid timestamp')
    return value


def validate(state):
    if (not isinstance(state, dict) or set(state) != {'version', 'entries'}
            or state['version'] != VERSION or type(state['version']) is not int
            or not isinstance(state['entries'], list) or len(state['entries']) > MAX_IDENTITIES):
        raise ValueError('Invalid inbox')
    seen, pending = set(), 0
    for row in state['entries']:
        if not isinstance(row, dict) or set(row) != {'id', 'status', 'created_at', 'dismissed_at', 'digest', 'text'}:
            raise ValueError('Invalid entry')
        ident = _uuid(row['id'])
        if ident in seen:
            raise ValueError('Duplicate identity')
        seen.add(ident)
        _time(row['created_at'])
        if not isinstance(row['digest'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['digest']):
            raise ValueError('Invalid fingerprint')
        if row['status'] == 'pending':
            pending += 1
            if row['dismissed_at'] is not None or hashlib.sha256(_text(row['text'])).hexdigest() != row['digest']:
                raise ValueError('Invalid pending entry')
        elif row['status'] == 'dismissed':
            _time(row['dismissed_at'])
            if row['text'] is not None:
                raise ValueError('Dismissed text must be absent')
        else:
            raise ValueError('Unknown entry status')
    if pending > MAX_PENDING:
        raise ValueError('Too many pending requests')
    return state


def put(state, ident, text, *, now):
    validate(state)
    ident, encoded, now = _uuid(ident), _text(text), _time(now)
    digest = hashlib.sha256(encoded).hexdigest()
    for row in state['entries']:
        if row['id'] == ident:
            if row['digest'] != digest:
                raise Conflict('Identity already belongs to different content')
            return copy.deepcopy(state), {'id': ident, 'status': row['status']}
    if len(state['entries']) >= MAX_IDENTITIES or sum(r['status'] == 'pending' for r in state['entries']) >= MAX_PENDING:
        raise CapacityExceeded('Inbox capacity reached')
    result = copy.deepcopy(state)
    result['entries'].append({'id': ident, 'status': 'pending', 'created_at': now,
                              'dismissed_at': None, 'digest': digest, 'text': text})
    return result, {'id': ident, 'status': 'pending'}


def dismiss(state, ident, *, now):
    validate(state)
    ident, now = _uuid(ident), _time(now)
    result = copy.deepcopy(state)
    for row in result['entries']:
        if row['id'] == ident:
            if row['status'] == 'pending':
                row.update(status='dismissed', dismissed_at=now, text=None)
            return result, {'id': ident, 'status': row['status']}
    raise NotFound('Unknown identity')


def list_entries(state):
    validate(state)
    return [{key: row[key] for key in ('id', 'status', 'created_at', 'dismissed_at')} for row in state['entries']]


def read(state, ident):
    validate(state)
    ident = _uuid(ident)
    for row in state['entries']:
        if row['id'] == ident and row['status'] == 'pending':
            return {'id': ident, 'text': row['text'], 'created_at': row['created_at']}
    raise NotFound('Request not available')
