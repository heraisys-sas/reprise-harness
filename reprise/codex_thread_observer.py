"""Read-only observation of recent Codex threads.

The local index establishes that a thread is stored locally.  An optional
``thread/read`` check only establishes readability through the selected Codex
endpoint; it never establishes ownership or permission to control a thread.
"""
from contextlib import closing
import os
from pathlib import Path
import sqlite3

import apple_expert
from codex_session_owners import census, read_proof
from codex_rpc import Client
import engines


CODEX_STATE_DB = Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')).expanduser() / 'state_5.sqlite'
MAX_ROWS = 20
MAX_PROOF_ROWS = 5
RPC_TIMEOUT_SECONDS = 2
SUBJECT_LIMIT = 200


def _bounded_max_rows(max_rows):
    if type(max_rows) is not int or not 1 <= max_rows <= MAX_ROWS:
        raise ValueError(f'max_rows must be between 1 and {MAX_ROWS}')
    return max_rows


def _subject(*values):
    for value in values:
        if isinstance(value, str):
            normalized = ' '.join(value.split())
            if normalized:
                return normalized if len(normalized) <= SUBJECT_LIMIT else normalized[:SUBJECT_LIMIT - 1] + '…'
    return 'Sujet indisponible'


def read_index(*, db_path=CODEX_STATE_DB, max_rows=20):
    """Read a bounded, display-safe projection of the local thread index.

    URI ``mode=ro`` and ``query_only`` deliberately keep this observer from
    creating, migrating, or otherwise changing Codex's state database.
    """
    limit = _bounded_max_rows(max_rows)
    path = Path(db_path).expanduser().absolute()
    query = '''
        SELECT id, title, first_user_message, preview, updated_at_ms, cwd,
               model, archived, source, recency_at_ms
          FROM threads
         WHERE archived = 0
         ORDER BY COALESCE(recency_at_ms, updated_at_ms) DESC, id DESC
         LIMIT ?
    '''
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as database:
        database.execute('PRAGMA query_only=ON')
        records = database.execute(query, (limit,)).fetchall()
    rows = []
    for ident, title, first_message, preview, updated_at_ms, cwd, model, archived, source, recency_at_ms in records:
        if not isinstance(ident, str) or not ident:
            continue
        rows.append({
            'thread_id': ident,
            'subject': _subject(first_message, preview, title),
            'updated_at_ms': updated_at_ms if type(updated_at_ms) is int else None,
            'cwd': cwd if isinstance(cwd, str) else None,
            'model': model if isinstance(model, str) else None,
            'archived': archived if type(archived) is int else None,
            'source': source if isinstance(source, str) else None,
            'recency_at_ms': recency_at_ms if type(recency_at_ms) is int else None,
        })
    return rows


def summarize_thread(*, cwd, excerpt):
    """Ask Foundation Models for an explicit, bounded per-thread summary.

    This is never called while listing threads.  Its availability and every
    local failure are deliberately represented as a non-blocking result.
    """
    try:
        if not apple_expert.configured():
            return {'summary_status': 'unavailable'}
        root = Path(cwd).resolve(strict=True)
        if not root.is_dir():
            return {'summary_status': 'unavailable'}
        text = _subject(excerpt)
        question = ('Résume en une phrase le sujet de cette conversation Codex observée localement. '
                    'Ne déduis ni action, ni propriété, ni contrôle. Extrait observé : ' + text)
        result = apple_expert.review(root, question)
        summary = result.get('response') if isinstance(result, dict) else None
        if result.get('status') == 'finished' and isinstance(summary, str) and summary.strip():
            return {'summary_status': 'available', 'summary': summary.strip()}
    except Exception:
        pass
    return {'summary_status': 'unavailable'}


def _indexed_sessions(limit):
    observation = census([CODEX_STATE_DB.parent], max_threads=limit)
    return {
        session['thread_id']: session
        for profile in observation.get('profiles', [])
        for session in profile.get('sessions', [])
        if isinstance(session, dict) and isinstance(session.get('thread_id'), str)
    }


def _attach_proofs(rows):
    try:
        indexed = _indexed_sessions(len(rows))
        program = engines.executable('codex-cli')
        if not program:
            raise OSError('Codex CLI unavailable')
        client = Client(program, {}, timeout=RPC_TIMEOUT_SECONDS)
    except Exception:
        for row in rows:
            row['rpc_readability'] = 'unavailable'
        return
    try:
        client.call('initialize', {'clientInfo': {'name': 'reprise-thread-observer', 'version': '0.1.0'}})
        client.send({'method': 'initialized'})
        for row in rows[:MAX_PROOF_ROWS]:
            indexed_row = indexed.get(row['thread_id'])
            if indexed_row is None:
                continue
            try:
                response = client.call('thread/read', {'threadId': row['thread_id'], 'includeTurns': False})
                proof = read_proof(indexed_row, response.get('thread') if isinstance(response, dict) else None)
                row.update(proof)
            except Exception:
                # A broken endpoint makes later checks untrustworthy, but keeps
                # every locally indexed row visible.
                for remaining in rows[rows.index(row):]:
                    remaining['rpc_readability'] = 'unavailable'
                return
    except Exception:
        for row in rows:
            if row['rpc_readability'] == 'unverified':
                row['rpc_readability'] = 'unavailable'
    finally:
        client.close()


def inspect_threads(*, max_rows=20, with_proofs=True):
    """Return recent local index rows, optionally with bounded RPC evidence."""
    limit = _bounded_max_rows(max_rows)
    if type(with_proofs) is not bool:
        raise ValueError('with_proofs must be boolean')
    try:
        indexed_rows = read_index(db_path=CODEX_STATE_DB, max_rows=limit)
    except (OSError, sqlite3.Error, ValueError):
        return {'status': 'unavailable', 'rows': []}
    rows = [{
        'thread_id': row['thread_id'],
        'subject': row['subject'],
        'updated_at_ms': row['updated_at_ms'],
        'rpc_readability': 'unverified',
        'owner_state': 'unknown',
        'controllable': False,
        'summary_status': 'not_requested',
    } for row in indexed_rows]
    if with_proofs and rows:
        _attach_proofs(rows)
    return {'status': 'ok', 'rows': rows}


def summarize_indexed_thread(*, thread_id):
    """Summarize one currently indexed thread after an explicit UI request."""
    try:
        row = next((item for item in read_index(db_path=CODEX_STATE_DB, max_rows=MAX_ROWS)
                    if item['thread_id'] == thread_id), None)
        if row is None or not row.get('cwd'):
            return {'status': 'unavailable', 'thread_id': thread_id, 'summary_status': 'unavailable'}
        return {'status': 'ok', 'thread_id': thread_id} | summarize_thread(cwd=row['cwd'], excerpt=row['subject'])
    except Exception:
        return {'status': 'unavailable', 'thread_id': thread_id, 'summary_status': 'unavailable'}
