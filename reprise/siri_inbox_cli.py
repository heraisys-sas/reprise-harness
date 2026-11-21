"""Local explicit inbox boundary; no service startup, model call, or implicit import."""
import json
import os
import sys
import time

from siri_inbox_acl import ACLPresent, ACLUnavailable
from siri_inbox_model import CapacityExceeded, Conflict, NotFound, _text, _uuid
from siri_inbox_store import Inbox, MissingRegistry, WriteUncertain

MAX_INPUT = 16 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _request(raw):
    if len(raw.encode('utf-8')) > MAX_INPUT:
        raise ValueError('request too large')
    value = json.loads(raw, object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise ValueError('object required')
    action = value.get('action')
    fields = {'put': {'action', 'id', 'text'}, 'list': {'action'},
              'read': {'action', 'id'}, 'dismiss': {'action', 'id'}}
    if not isinstance(action, str) or action not in fields or set(value) != fields[action]:
        raise ValueError('invalid action')
    if action != 'list':
        _uuid(value['id'])
    if action == 'put':
        _text(value['text'])
    return value


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1 or not os.path.isabs(args[0]):
            raise ValueError('absolute state directory required')
        raw = sys.stdin.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('request too large')
        request = _request(raw)
    except (ValueError, UnicodeError, RecursionError):
        print(json.dumps({'status': 'invalid_request'}))
        return 2
    try:
        box = Inbox(args[0])
        action = request['action']
        if action == 'put':
            result = box.put(request['id'], request['text'], now=time.time())
        elif action == 'dismiss':
            result = box.dismiss(request['id'], now=time.time())
        elif action == 'read':
            result = box.read(request['id'])
        else:
            result = box.list()
        print(json.dumps({'status': 'ok', 'result': result}, ensure_ascii=False))
        return 0
    except WriteUncertain:
        status = 'write_uncertain'
    except MissingRegistry:
        status = 'registry_missing'
    except ACLPresent:
        status = 'unsafe_permissions'
    except ACLUnavailable:
        status = 'permissions_unavailable'
    except Conflict:
        status = 'identity_conflict'
    except CapacityExceeded:
        status = 'capacity_exceeded'
    except NotFound:
        status = 'not_found'
    except (ValueError, OSError, TimeoutError):
        status = 'unavailable'
    print(json.dumps({'status': status}))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
