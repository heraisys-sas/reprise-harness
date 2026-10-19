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

MAX_ANALYSIS = 24000

TOOLS = [
    {'name': 'get_task', 'description': 'Get the single Reprise UX/UI review task and its attached Swift source. Analyze it, call submit_result once with your full UX/UI analysis, then stop.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'submit_result', 'description': 'Return the full UX/UI analysis for the claimed task. One submission only.',
     'inputSchema': {'type': 'object', 'properties': {'job_id': {'type': 'string'}, 'attempt_id': {'type': 'string'},
                                                     'analysis': {'type': 'string', 'maxLength': MAX_ANALYSIS}},
                     'required': ['job_id', 'attempt_id', 'analysis'], 'additionalProperties': False}},
]


class Probe:
    def __init__(self, state, task_prompt, attachment_path):
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.task_prompt = task_prompt
        self.attachment = Path(attachment_path).read_text(errors='replace') if attachment_path else ''
        self.db = sqlite3.connect(state / 'ux_job.sqlite', timeout=2)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self.db.execute('CREATE TABLE IF NOT EXISTS job(id TEXT PRIMARY KEY,status TEXT NOT NULL,analysis TEXT,claims INTEGER NOT NULL,attempt_id TEXT)')
            self.db.execute("UPDATE job SET status='indeterminate' WHERE status='claimed'")
            if not self.db.execute('SELECT 1 FROM job').fetchone():
                self.db.execute('INSERT INTO job VALUES (?,?,NULL,0,NULL)', (str(uuid.uuid4()), 'pending'))

    def call(self, name, args):
        if not isinstance(args, dict):
            raise ValueError('Object arguments required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            ident, status, analysis, claims, attempt = self.db.execute(
                'SELECT id,status,analysis,claims,attempt_id FROM job').fetchone()
            if name == 'get_task':
                if args:
                    raise ValueError('No arguments expected')
                if status == 'completed':
                    return {'status': 'completed', 'remaining_jobs': 0, 'instruction': 'Stop; no further job exists.'}
                if status != 'pending':
                    return {'status': status, 'instruction': 'Already assigned; do not solve, retry or request another task. Stop.'}
                attempt = str(uuid.uuid4())
                self.db.execute('UPDATE job SET status=?,claims=claims+1,attempt_id=? WHERE id=?', ('claimed', attempt, ident))
                return {'status': 'claimed', 'job_id': ident, 'attempt_id': attempt,
                        'task': self.task_prompt,
                        'attachment': self.attachment,
                        'instruction': 'Analyze the attached Swift code for UX/UI. Call submit_result once with job_id, attempt_id and your full analysis in "analysis". Then stop.'}
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
    parser.add_argument('--attachment', help='Path to the Swift source to review')
    parser.add_argument('--task', default='Analyze the attached SwiftUI/AppKit code for UX/UI: clarity, hierarchy, consistency, accessibility, and concrete improvements.')
    args = parser.parse_args()
    probe = Probe(args.state, args.task, args.attachment)
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
