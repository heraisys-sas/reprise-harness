#!/usr/bin/env python3
"""Read-only Codex session inventory across explicitly selected CODEX_HOME roots.

The local thread index is evidence of *storage*, not of a process owner or
permission to write. This module never connects to an RPC endpoint, launches
a daemon, reads a rollout, or mutates a thread.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import uuid

MAX_THREADS = 500


def _root(value):
    # Normalize dot segments without following a symlink at the selected root.
    path = Path(os.path.normpath(os.path.abspath(os.path.expanduser(str(value)))))
    return path


def _hash(*parts):
    return hashlib.sha256(b'\0'.join(str(part).encode('utf-8') for part in parts)).hexdigest()


def _store(home, limit):
    profile_id = _hash('codex-home-v1', home)
    result = {'profile_id': profile_id, 'index_state': 'unknown',
              'sessions': [], 'invalid_records': 0, 'truncated': False}
    try:
        info = home.lstat()
        if stat.S_ISLNK(info.st_mode):
            result['index_state'] = 'home_symlink'
            return result
        if not stat.S_ISDIR(info.st_mode):
            result['index_state'] = 'home_not_directory'
            return result
        if info.st_uid != os.getuid():
            result['index_state'] = 'home_not_owned'
            return result
        index = home / 'state_5.sqlite'
        info = index.lstat()
        if stat.S_ISLNK(info.st_mode):
            result['index_state'] = 'index_symlink'
            return result
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            result['index_state'] = 'index_not_owned_regular_file'
            return result
        # mode=ro forbids SQL writes. A live WAL database may still cause
        # SQLite-managed shared-memory side effects; never claim byte-for-byte
        # filesystem immutability for an active index.
        with sqlite3.connect(index.as_uri() + '?mode=ro', uri=True, timeout=2) as connection:
            connection.execute('PRAGMA query_only=ON')
            rows = connection.execute(
                'SELECT id,cwd,updated_at FROM threads WHERE archived=0 '
                'ORDER BY updated_at DESC LIMIT ?', (limit + 1,)).fetchall()
        result['truncated'] = len(rows) > limit
        for ident, cwd, updated in rows[:limit]:
            try:
                thread_id = str(uuid.UUID(ident))
                if not isinstance(cwd, str) or not cwd:
                    raise ValueError('invalid working directory')
            except (ValueError, AttributeError, TypeError):
                result['invalid_records'] += 1
                continue
            result['sessions'].append({
                'session_key': _hash('codex-session-v1', profile_id, thread_id),
                'thread_id': thread_id,
                'profile_id': profile_id,
                'cwd_sha256': _hash('cwd-v1', os.path.normpath(cwd)),
                'updated_at': updated if type(updated) in (int, float) else None,
                'storage_observed': True,
                'owner_state': 'unknown',
                'owner_instance_id': None,
                'rpc_readability': 'unverified',
                'write_ownership': 'unverified',
                'controllable': False,
            })
        result['index_state'] = 'partial' if result['truncated'] or result['invalid_records'] else 'observed'
    except FileNotFoundError:
        result['index_state'] = 'index_missing'
    except PermissionError:
        result['index_state'] = 'access_denied'
    except (sqlite3.Error, OSError, OverflowError):
        result['index_state'] = 'index_unreadable_or_schema_changed'
    return result


def census(homes, *, max_threads=100):
    """Inventory up to max_threads per *explicit* root, with no actor discovery.

    A profile fingerprint is stable across runs; thread UUIDs repeated in two
    roots intentionally receive different session keys. No endpoint or local
    PID is ever promoted to an owner by this storage-only observation.
    """
    if type(max_threads) is not int or not 1 <= max_threads <= MAX_THREADS:
        raise ValueError('max_threads out of range')
    if not isinstance(homes, (tuple, list)) or not homes:
        raise ValueError('at least one CODEX_HOME required')
    roots = [_root(home) for home in homes]
    if len(set(roots)) != len(roots):
        raise ValueError('duplicate CODEX_HOME')
    stores = [_store(root, max_threads) for root in roots]
    complete = all(store['index_state'] == 'observed' for store in stores)
    return {'status': 'observed' if complete else 'partial',
            'source': 'codex-local-index-v1',
            'read_only': True,
            'server_contacted': False,
            'owner_ownership_verified': False,
            'profiles': stores,
            'session_count': sum(len(s['sessions']) for s in stores)}


def read_proof(indexed, observed_thread):
    """Pure read-only evidence comparison; *never* grants writer ownership.

    The caller must have explicitly selected and independently authenticated
    its RPC endpoint. A matching thread/read response proves readability via
    that endpoint only; another app-server may still own the write handle.
    """
    if not isinstance(indexed, dict) or not isinstance(observed_thread, dict):
        return {'rpc_readability': 'unverified', 'owner_state': 'unknown',
                'controllable': False}
    valid = observed_thread.get('id') == indexed.get('thread_id')
    cwd = observed_thread.get('cwd')
    valid = valid and isinstance(cwd, str) and (
        _hash('cwd-v1', os.path.normpath(cwd)) == indexed.get('cwd_sha256'))
    return {'rpc_readability': 'confirmed' if valid else 'identity_mismatch',
            'owner_state': 'unknown', 'controllable': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex-home', action='append', type=Path, dest='homes',
                        help='Explicit CODEX_HOME to inspect; repeat for independent profiles')
    parser.add_argument('--max-threads', type=int, default=100)
    args = parser.parse_args(argv)
    try:
        result = census(args.homes or [Path.home() / '.codex'],
                        max_threads=args.max_threads)
    except (ValueError, OSError, TypeError) as exc:
        result = {'status': 'refused', 'reason': str(exc),
                  'read_only': True, 'server_contacted': False}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result['status'] == 'observed' else 2


if __name__ == '__main__':
    sys.exit(main())
