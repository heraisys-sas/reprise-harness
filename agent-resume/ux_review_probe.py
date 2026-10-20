#!/usr/bin/env python3
"""One bounded UX/UI review job with a durable long-form return channel.

Mirrors desktop_job_probe.py (validated round-trip get_task / submit_result),
but carries an attached Swift source file and accepts a long analysis instead
of a 120-char answer. Claude Desktop reads the task + attached code and returns
its UX/UI analysis through submit_result; Reprise reads it back from SQLite.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid
from delegation_store import digest, validate_key

MAX_ANALYSIS = 24000

TOOLS = [
    {'name': 'get_result', 'description': 'Retrieve the durable result or attempt state, optionally acknowledge consumption. Does not dispatch work.',
     'inputSchema': {'type': 'object', 'properties': {'request_key': {'type': 'string'}, 'acknowledge': {'type': 'boolean'}}, 'required': ['request_key'], 'additionalProperties': False}},
    {'name': 'get_task', 'description': 'Get the single Reprise UX/UI review task and its attached Swift source. Analyze it, call submit_result once with your full UX/UI analysis, then stop.',
     'inputSchema': {'type': 'object', 'properties': {'claim_key': {'type': 'string'}}, 'required': ['claim_key'], 'additionalProperties': False}},
    {'name': 'submit_result', 'description': 'Return the full UX/UI analysis for the claimed task. One submission only.',
     'inputSchema': {'type': 'object', 'properties': {'job_id': {'type': 'string'}, 'attempt_id': {'type': 'string'},
                                                     'analysis': {'type': 'string', 'maxLength': MAX_ANALYSIS}},
                     'required': ['job_id', 'attempt_id', 'analysis'], 'additionalProperties': False}},
]


class Probe:
    def __init__(self, state, task_prompt, attachment_path, request_key="ux-review"):
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        validate_key(request_key)
        self.request_key = request_key
        self.task_prompt = task_prompt
        self.attachment = Path(attachment_path).read_text(errors='replace') if attachment_path and task_prompt is not None else ''
        self.db = sqlite3.connect(state / 'ux_job.sqlite', timeout=2)
        try:
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('CREATE TABLE IF NOT EXISTS job(id TEXT PRIMARY KEY,status TEXT NOT NULL,analysis TEXT,claims INTEGER NOT NULL,attempt_id TEXT)')
                self.db.execute('CREATE TABLE IF NOT EXISTS manifest(request_key TEXT PRIMARY KEY, task TEXT, attachment TEXT, request_hash TEXT, claim_key TEXT, acknowledged INTEGER DEFAULT 0)')
                row = self.db.execute('SELECT request_key,request_hash,task,attachment FROM manifest').fetchone()
                if task_prompt is None:
                    if not row or row[0] != request_key:
                        raise ValueError('No persisted manifest for recovery')
                    self.task_prompt, self.attachment = row[2:]
                row = row[:2] if row else None
                request_hash = digest({'task': self.task_prompt, 'attachment': self.attachment})
                self.request_hash = request_hash
                if row and row != (request_key, request_hash):
                    raise ValueError('State already bound to another immutable task/attachment; use a new state directory')
                if not row:
                    if self.db.execute('SELECT 1 FROM job').fetchone():
                        raise ValueError('Legacy job has no immutable manifest; reconcile it before migration')
                    self.db.execute('INSERT INTO manifest(request_key,task,attachment,request_hash) VALUES (?,?,?,?)', (request_key, self.task_prompt, self.attachment, request_hash))
                if not self.db.execute('SELECT 1 FROM job').fetchone():
                    self.db.execute('INSERT INTO job VALUES (?,?,NULL,0,NULL)', (str(uuid.uuid4()), 'pending'))
        except BaseException:
            self.db.close()
            raise

    def call(self, name, args):
        if not isinstance(args, dict):
            raise ValueError('Object arguments required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            ident, status, analysis, claims, attempt = self.db.execute(
                'SELECT id,status,analysis,claims,attempt_id FROM job').fetchone()
            if name == 'get_result':
                if args.keys() - {'request_key', 'acknowledge'} or args.get('request_key') != self.request_key:
                    raise ValueError('Matching request_key required')
                if args.get('acknowledge'):
                    if status != 'completed':
                        raise ValueError('No accepted result to acknowledge')
                    self.db.execute('UPDATE manifest SET acknowledged=1')
                acknowledged = self.db.execute('SELECT acknowledged FROM manifest').fetchone()[0]
                return {'status': status, 'request_key': self.request_key, 'request_hash': self.request_hash, 'job_id': ident,
                        'attempt_id': attempt, 'analysis': analysis, 'acknowledged': bool(acknowledged)}
            if name == 'get_task':
                if args.keys() != {'claim_key'}:
                    raise ValueError('Persist claim_key before reserving')
                validate_key(args['claim_key'])
                if status == 'completed':
                    return {'status': 'completed', 'remaining_jobs': 0, 'instruction': 'Stop; no further job exists.'}
                owner = self.db.execute('SELECT claim_key FROM manifest').fetchone()[0]
                if status != 'pending' and owner != args['claim_key']:
                    return {'status': status, 'instruction': 'Already assigned; do not solve, retry or request another task. Stop.'}
                recovered = status != 'pending'
                if not recovered:
                    attempt = str(uuid.uuid4())
                    self.db.execute('UPDATE job SET status=?,claims=claims+1,attempt_id=? WHERE id=?', ('claimed', attempt, ident))
                    self.db.execute('UPDATE manifest SET claim_key=?', (args['claim_key'],))
                return {'status': 'claimed', 'job_id': ident, 'attempt_id': attempt,
                        'request_key': self.request_key, 'request_hash': self.request_hash, 'recovered_reservation': recovered,
                        'task': self.task_prompt,
                        'attachment': self.attachment,
                        'instruction': 'Recovered reservations are not permission to start a second inference. Reconcile existing execution first. Analyze the attached Swift code for UX/UI. Call submit_result once with job_id, attempt_id and your full analysis in "analysis". Then stop.'}
            if name != 'submit_result' or args.keys() != {'job_id', 'attempt_id', 'analysis'}:
                raise ValueError('Unknown tool or invalid arguments')
            text = args.get('analysis')
            if (args['job_id'] != ident or not attempt or args['attempt_id'] != attempt
                    or status not in ('claimed', 'indeterminate', 'completed')
                    or not isinstance(text, str) or not text.strip() or len(text) > MAX_ANALYSIS):
                raise ValueError('Only one bounded analysis for the claimed task is accepted')
            if status == 'completed' and analysis != text:
                raise ValueError('Conflicting analysis for completed attempt')
            self.db.execute('UPDATE job SET status=?,analysis=? WHERE id=?', ('completed', text, ident))
            return {'status': 'accepted', 'job_id': ident, 'receipt_id': attempt,
                    'remaining_jobs': 0, 'instruction': 'Stop; the review is complete.'}

    def dispatch(self, message):
        method, params = message.get('method'), message.get('params') or {}
        if method == 'initialize':
            return {'protocolVersion': params.get('protocolVersion', '2024-11-05'),
                    'capabilities': {'tools': {}}, 'serverInfo': {'name': 'reprise-ux-review', 'version': '0.1.0'}}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': TOOLS}
        if method == 'tools/call':
            result = self.call(params.get('name'), params.get('arguments') or {})
            return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}],
                    'isError': result.get('status') not in ('claimed', 'accepted', 'completed')}
        raise ValueError('Unsupported MCP method')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--recover-existing', action='store_true', help='Load persisted task and attachment without reading source files')
    parser.add_argument('--request-key', default='ux-review', help='Persist this key before triggering Claude')
    parser.add_argument('--attachment', help='Path to the Swift source to review')
    parser.add_argument('--task', default='Analyze the attached SwiftUI/AppKit code for UX/UI: clarity, hierarchy, consistency, accessibility, and concrete improvements.')
    args = parser.parse_args()
    probe = Probe(args.state, None if args.recover_existing else args.task, args.attachment, args.request_key)
    try:
        for raw in sys.stdin.buffer:
            if len(raw) > 1024 * 1024:
                break
            ident = None
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('Object required')
                ident = message.get('id')
                if 'id' not in message:
                    continue
                reply = {'jsonrpc': '2.0', 'id': ident, 'result': probe.dispatch(message)}
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
                reply = {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': str(error) or 'Request refused.'}}
            print(json.dumps(reply, ensure_ascii=False), flush=True)
    finally:
        probe.db.close()


if __name__ == '__main__':
    main()
