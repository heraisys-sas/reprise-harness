#!/usr/bin/env python3
"""Durable ChatGPT admission endpoint (backend 'chatgpt', issue #152).

V1 consultative (expertise/review only): the bridge admits the task durably,
then the delivery to the local ChatGPT app happens over a channel that is
still indeterminate (local plugin/stdio/loopback). Nothing here claims a
delivery proof.

Invariant « arrivé ≠ traité » : an admission only records that a submission
was attempted (delivery='attempted'); the roundtrip is closed solely by a
validated submit_result (attempt_id + claim_key tied to the ChatGPT session)
through desktop_task_store.TaskStore. A submission already attempted is never
resent: idempotence is absolute, recovery goes through reprise_requests or an
explicit operator reconciliation.
"""
import json
import sqlite3
import time
import weakref
from pathlib import Path

from delegation_store import file_evidence
from desktop_task_store import TaskStore, bounded

CHATGPT_TASK_INSTRUCTION = ('A Reprise task is waiting in the configured project. '
                            'Persist a claim_key, then call get_task with request_key={key}. '
                            'Follow your session instructions, perform that scoped task, and call '
                            'submit_result with the returned attempt_id. If already completed, '
                            'retrieve the existing result instead.')

ALLOWED_SUBMISSION_KEYS = {'question', 'kind', 'level', 'paths', 'request_key', 'before_evidence'}


class Endpoint:
    """Channel-independent durable ledger: table chatgpt_submissions + TaskStore roundtrip."""

    def __init__(self, state, project_root):
        root = Path(project_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError('Project directory required')
        self.root = root
        self.scope = str(root)
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(state/'chatgpt.sqlite', timeout=2)
        with self.db:
            self.db.execute('''CREATE TABLE IF NOT EXISTS chatgpt_submissions(
                scope TEXT NOT NULL, request_key TEXT NOT NULL, prompt TEXT NOT NULL,
                delivery TEXT, dispatch_receipt TEXT,
                before_evidence TEXT, after_evidence TEXT,
                needs_review INTEGER NOT NULL DEFAULT 0,
                admitted_at REAL NOT NULL, PRIMARY KEY(scope, request_key))''')
        # Roundtrip registry reused as-is: get_task/submit_result with claim barrier.
        self.tasks = TaskStore(state, root)
        weakref.finalize(self, self._safe_close)

    @staticmethod
    def _safe_close(self):
        for attr in ('tasks', 'db'):
            try:
                getattr(self, attr).close()
            except Exception:
                pass

    def close(self):
        self.tasks, tasks = None, self.tasks
        if tasks is not None:
            tasks.close()
        self.db.close()

    def submit(self, args):
        if not isinstance(args, dict) or args.keys() - ALLOWED_SUBMISSION_KEYS:
            raise ValueError('Invalid ChatGPT submission arguments')
        question = args.get('question')
        if not isinstance(question, str) or not question.strip() or len(question) > 12000:
            raise ValueError('Invalid question')
        request_key = args.get('request_key')
        bounded(request_key, 'request_key', 128)
        kind = args.get('kind', 'expertise')
        level = args.get('level', 'standard')
        paths = args.get('paths')
        row = self.db.execute('SELECT delivery, needs_review FROM chatgpt_submissions WHERE scope=? AND request_key=?',
                              (self.scope, request_key)).fetchone()
        if row:
            # Idempotence: an already-admitted request is never rewritten or resent.
            return {'status': 'finished', 'request_key': request_key,
                    'delivery': row[0] or 'admitted', 'resent': False, 'admission_only': True,
                    'needs_review': bool(row[1])}
        before = args.get('before_evidence')
        if before is None:
            before = file_evidence(self.root, paths)
        prompt = CHATGPT_TASK_INSTRUCTION.format(key=json.dumps(request_key)) + '\n\n' + question
        with self.db:
            self.db.execute('''INSERT INTO chatgpt_submissions VALUES (?,?,?,?,?,?,?,0,?)''',
                            (self.scope, request_key, prompt, 'admitted', None,
                             json.dumps(before), None, time.time()))
        self.tasks.admit(request_key, prompt)
        # Delivery attempt is recorded, never proven; the channel is indeterminate in v1.
        with self.db:
            self.db.execute('''UPDATE chatgpt_submissions SET delivery='attempted',
                dispatch_receipt=? WHERE scope=? AND request_key=?''',
                (json.dumps({'stage': 'admitted', 'channel': 'indeterminate'}), self.scope, request_key))
        after = file_evidence(self.root, paths)
        needs_review = after != before
        if needs_review:
            with self.db:
                self.db.execute('UPDATE chatgpt_submissions SET after_evidence=?, needs_review=1 WHERE scope=? AND request_key=?',
                                (json.dumps(after), self.scope, request_key))
        return {'status': 'finished', 'request_key': request_key, 'delivery': 'attempted',
                'resent': False, 'admission_only': True, 'needs_review': needs_review,
                'kind': kind, 'level': level,
                'return_channel': 'MCP get_task/submit_result; completion only on validated submit_result',
                'channel': 'indeterminate (canal local à déterminer)',
                'automatic_fallback': False}


def review(root, question, *, kind='expertise', level='standard', paths=None,
           socket=None, request_key=None, before_evidence=None):
    """Admit one consultative ChatGPT delegation; returns the durable admission receipt."""
    state = _state_location(root)
    endpoint = Endpoint(state, root)
    try:
        return endpoint.submit({'question': question, 'kind': kind, 'level': level,
                                'paths': paths, 'request_key': request_key,
                                'before_evidence': before_evidence})
    finally:
        endpoint.close()


def _state_location(root, name='chatgpt-state'):
    from app_paths import state_location
    return state_location(root, name)
