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


def _close_endpoint_resources(tasks, db):
    """Niveau module : idempotent, avalé si déjà fermé."""
    for resource in (tasks, db):
        try:
            resource.close()
        except Exception:
            pass


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
        # Niveau module + ressources capturées (ni self ni méthode liée, qui
        # retiendraient l'Endpoint et empêcheraient le finalizer de tourner).
        weakref.finalize(self, _close_endpoint_resources, self.tasks, self.db)

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
        # Crash-safe admission: validate EVERYTHING against the roundtrip store
        # BEFORE the first durable write. A failure after the INSERT below would
        # leave an admitted row with no TaskStore entry; recovery would then rely
        # on replay alone instead of a clean retry. admit() is idempotent on the
        # same (request_key, prompt); a DIFFERENT prompt is a real conflict.
        self.tasks.admit(request_key, prompt)
        with self.db:
            self.db.execute('''INSERT INTO chatgpt_submissions VALUES (?,?,?,?,?,?,?,0,?)''',
                            (self.scope, request_key, prompt, 'admitted', None,
                             json.dumps(before), None, time.time()))
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

    def mark_acknowledged(self, request_key, attempt_id, answer_sha256):
        """One-way delivery projection attempted→acknowledged (issue #156).

        Seule écriture de livraison venant de l'Endpoint ; le résultat immuable
        vit dans le TaskStore (preuve de complétion), ici on ne projette que la
        livraison. Idempotent : un acknowledged n'est jamais rétrogradé, un
        re-acknowledged identique est un no-op.
        """
        from desktop_task_store import bounded
        bounded(request_key, 'request_key', 128)
        bounded(attempt_id, 'attempt_id', 128)
        if not isinstance(answer_sha256, str) or len(answer_sha256) != 64:
            raise ValueError('Invalid answer sha256')
        # attempt_id must be the one the TaskStore actually issued (logical
        # possession proof): never trust a caller-supplied attempt blindly.
        task = self.tasks._row(request_key)
        if task is None or task[3] != attempt_id:
            raise ValueError('Attempt does not own project task')
        # Idempotence check BEFORE any write: identical acknowledged receipt is a
        # no-op; a DIFFERENT attempt on an acknowledged delivery is refused.
        row = self.db.execute('SELECT delivery, dispatch_receipt FROM chatgpt_submissions WHERE scope=? AND request_key=?',
                              (self.scope, request_key)).fetchone()
        if row and row[0] == 'acknowledged':
            receipt = json.loads(row[1]) if row[1] else {}
            if receipt.get('attempt_id') != attempt_id or receipt.get('answer_sha256') != answer_sha256:
                raise ValueError('Acknowledged with a different receipt')
            return {'request_key': request_key, 'delivery': 'acknowledged', 'changed': False}
        with self.db:
            cursor = self.db.execute(
                '''UPDATE chatgpt_submissions SET delivery='acknowledged', dispatch_receipt=?
                WHERE scope=? AND request_key=? AND delivery='attempted' ''',
                (json.dumps({'stage': 'acknowledged', 'attempt_id': attempt_id,
                             'answer_sha256': answer_sha256}), self.scope, request_key))
        if cursor.rowcount:
            return {'request_key': request_key, 'delivery': 'acknowledged', 'changed': True}
        raise ValueError('Delivery is not attempted')


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
