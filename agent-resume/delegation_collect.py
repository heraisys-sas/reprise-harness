"""Recover a persisted expert result without inference or acknowledgement."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys

from delegation_store import Store, validate_key


def collect(state, project_root, request_key, field='result'):
    """Read and verify all result pages from read-only SQLite; reject changes between pages."""
    validate_key(request_key)
    if field not in ('result', 'response'):
        raise ValueError('Unsupported result field')
    root = Path(project_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Project root must be a directory')
    database = (Path(state) / 'expert.sqlite').resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('PRAGMA query_only=ON')
        # Store.__init__ creates tables; a collector must never migrate the database.
        store = Store.__new__(Store)
        store.db, store.scope = db, str(root)
        manifest = store.lookup({'request_key': request_key, 'view': 'receipt'})
        if manifest.get('state') != 'recorded' or not manifest.get('available'):
            raise ValueError('No recorded result is available')
        fingerprint, total = manifest.get('result_sha256'), manifest.get('total_length')
        if not isinstance(fingerprint, str) or type(total) is not int or total <= 0:
            raise ValueError('Invalid result manifest')
        parts, offset = [], 0
        while offset < total:
            page = store.lookup({'request_key': request_key, 'view': 'page',
                                 'offset': offset, 'max_chars': 6000,
                                 'result_sha256': fingerprint})
            fragment = page.get('text')
            if (page.get('status') != 'finished' or page.get('state') != 'recorded'
                    or page.get('result_sha256') != fingerprint
                    or page.get('total_length') != total
                    or page.get('offset') != offset or not isinstance(fragment, str)
                    or not fragment or len(fragment) > 6000
                    or page.get('returned_length') != len(fragment)):
                raise ValueError('Invalid result page')
            end = offset + len(fragment)
            if (end > total or page.get('has_more') is not (end < total)
                    or page.get('next_offset') != (end if end < total else None)):
                raise ValueError('Invalid result continuation')
            parts.append(fragment)
            offset = end
        text = ''.join(parts)
        if len(text) != total or hashlib.sha256(text.encode('utf-8')).hexdigest() != fingerprint:
            raise ValueError('Result integrity verification failed')
        result = json.loads(text)
        if not isinstance(result, dict) or result.get('output_truncated') or result.get('output_complete') is False:
            raise ValueError('Result is incomplete')
        if result.get('status') != 'finished':
            raise ValueError('Result did not finish successfully')
        if field == 'response':
            response = result.get('response')
            if not isinstance(response, str):
                raise ValueError('No text response is available')
            return response
        return text


def write_output(path, text):
    """Create a private new file atomically; never follow, replace or leave a partial entry."""
    path = Path(path)
    nofollow = getattr(os, 'O_NOFOLLOW', 0)
    temporary = path.with_name(f'.{path.name}.{secrets.token_hex(8)}.part')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        # link() fails if the target exists (including a dangling symlink) and never replaces it.
        os.link(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, help='Directory containing expert.sqlite')
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--request-key', required=True)
    parser.add_argument('--output', required=True, help='New output file (never overwritten)')
    parser.add_argument('--field', choices=('result', 'response'), default='result')
    args = parser.parse_args(argv)
    try:
        text = collect(args.state, args.project_root, args.request_key, args.field)
        write_output(args.output, text)
    except (ValueError, OSError, sqlite3.Error, TypeError):
        # Do not print stored content, credentials, paths, or database errors.
        print('Collection failed: verify the recorded result and output destination.', file=sys.stderr)
        return 1
    print('Result collected and verified.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
