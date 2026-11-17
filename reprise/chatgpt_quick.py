#!/usr/bin/env python3
"""Durable ChatGPT admission endpoint (backend 'chatgpt', issue #152).

V1 consultative (expertise/review only): the bridge admits the task durably,
then the delivery to the local ChatGPT app happens over a channel that is
still indeterminate (local plugin/stdio/loopback). Nothing here claims a
delivery proof.

Invariant « arrivé ≠ traité » : an admission only records that a submission
was attempted (delivery='attempted'); the roundtrip is closed by the holder's
response capture in its durable receipt (Network/SSE, AXTree fallback —
chatgpt_desktop_pipe), not by any ChatGPT-side action: ChatGPT Desktop has no
Reprise MCP tools. The TaskStore keeps the submission registry and may record
an attribution complement, never a loop condition. A submission already
attempted is never resent: idempotence is absolute, recovery goes through
reprise_requests or an explicit operator reconciliation.
"""
import hashlib
import json
import sqlite3
import time
import weakref
from pathlib import Path

from delegation_store import file_evidence
from desktop_task_store import TaskStore, bounded

CHATGPT_TASK_INSTRUCTION = ('Reprise consultative question (request_key={key}). '
                            'ChatGPT has no Reprise tools: answer directly in this '
                            'conversation — the reply is captured from it by the Reprise '
                            'holder. Question:\n\n')

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

    def _replay_admission(self, request_key, prompt):
        """Return an immutable prior receipt without redispatching a prompt.

        Historical 'admitted' ledger rows represent a crash between two local
        projection writes, not proof that a model was ever invoked.
        """
        row = self.db.execute('''SELECT delivery, needs_review, prompt
            FROM chatgpt_submissions WHERE scope=? AND request_key=?''',
            (self.scope, request_key)).fetchone()
        if row is None:
            raise ValueError('Missing ChatGPT admission ledger')
        if row[2] != prompt:
            raise ValueError('Request identity conflict')
        task = self.tasks._row(request_key)
        if task is None or task[0] != prompt:
            raise ValueError('Admission ledger conflicts with TaskStore')
        if row[0] == 'admitted':
            # Repair a pre-v2 split write. This is a local projection only: no
            # prompt is delivered, no new attempt is made, no result is erased.
            with self.db:
                self.db.execute('''UPDATE chatgpt_submissions SET delivery='attempted',
                    dispatch_receipt=COALESCE(dispatch_receipt, ?)
                    WHERE scope=? AND request_key=? AND delivery='admitted' ''',
                    (json.dumps({'stage': 'admitted', 'channel': 'indeterminate'}),
                     self.scope, request_key))
            row = self.db.execute('''SELECT delivery,needs_review,prompt
                FROM chatgpt_submissions WHERE scope=? AND request_key=?''',
                (self.scope, request_key)).fetchone()
        return {'status': 'finished', 'request_key': request_key,
                'delivery': row[0] or 'admitted', 'resent': False,
                'admission_only': True, 'needs_review': bool(row[1])}

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
        prompt = CHATGPT_TASK_INSTRUCTION.format(key=json.dumps(request_key)) + '\n\n' + question
        row = self.db.execute('SELECT 1 FROM chatgpt_submissions WHERE scope=? AND request_key=?',
                              (self.scope, request_key)).fetchone()
        if row:
            return self._replay_admission(request_key, prompt)
        before = args.get('before_evidence')
        if before is None:
            before = file_evidence(self.root, paths)
        # Validate the ledger's JSON payload before the TaskStore commit.
        # Otherwise a serialization failure leaves a ghost admitted task.
        before_json = json.dumps(before)
        # The TaskStore commits first and is idempotent on (request_key,prompt).
        # A crash before the ledger insert can be retried without redispatch;
        # a reused key with a different prompt is a hard identity conflict.
        self.tasks.admit(request_key, prompt)
        # Both admission and its "attempted" projection are one SQLite write:
        # there is no crash window leaving a new row stuck in "admitted".
        # Attempted means durable local admission, NOT transport delivery.
        with self.db:
            inserted = self.db.execute(
                '''INSERT OR IGNORE INTO chatgpt_submissions VALUES (?,?,?,?,?,?,?,0,?)''',
                (self.scope, request_key, prompt, 'attempted',
                 json.dumps({'stage': 'admitted', 'channel': 'indeterminate'}),
                 before_json, None, time.time())).rowcount
        if not inserted:
            # A second process may have won the insertion after our SELECT.
            # Its prompt is immutable; never report success for a different one.
            return self._replay_admission(request_key, prompt)
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
        if task[4] is None:
            raise ValueError('Task result is not recorded')
        if hashlib.sha256(task[4].encode()).hexdigest() != answer_sha256:
            raise ValueError('Answer sha256 does not match recorded task result')
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
                WHERE scope=? AND request_key=? AND delivery IN ('admitted','attempted') ''',
                (json.dumps({'stage': 'acknowledged', 'attempt_id': attempt_id,
                             'answer_sha256': answer_sha256}), self.scope, request_key))
        if cursor.rowcount:
            return {'request_key': request_key, 'delivery': 'acknowledged', 'changed': True}
        raise ValueError('Delivery is not attempted')


def review(root, question, *, kind='expertise', level='standard', paths=None,
           socket=None, request_key=None, before_evidence=None):
    """Admit one consultative ChatGPT delegation; returns the durable admission receipt."""
    # In this integration "socket" is an explicitly configured state directory,
    # not a network transport. Bridge and Endpoint must share its TaskStore.
    state = Path(socket).resolve() if socket is not None else _state_location(root)
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
