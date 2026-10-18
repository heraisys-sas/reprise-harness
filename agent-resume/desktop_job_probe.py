#!/usr/bin/env python3
"""One harmless Desktop MCP job, with a durable single-result return channel.

Experimental diagnostic only: no project files, model API, sampling or job loop.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

TOOLS = [
    {'name': 'get_task', 'description': 'Get the single Reprise test job. Solve it, call submit_result once, then stop. No other tools needed.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'submit_result', 'description': 'Return the answer for the claimed Reprise test job. Only one submission is accepted.',
     'inputSchema': {'type': 'object', 'properties': {'job_id': {'type': 'string'},
                      'answer': {'type': 'string', 'maxLength': 120}},
                     'required': ['job_id', 'answer'], 'additionalProperties': False}},
]


class Probe:
    def __init__(self, state):
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(state/'job.sqlite', timeout=2)
        self.db.execute('CREATE TABLE IF NOT EXISTS job(id TEXT PRIMARY KEY,status TEXT NOT NULL,answer TEXT,claims INTEGER NOT NULL)')
        with self.db:
            if not self.db.execute('SELECT 1 FROM job').fetchone():
                self.db.execute('INSERT INTO job VALUES (?,?,NULL,0)', (str(uuid.uuid4()), 'pending'))

    def call(self, name, args):
        if not isinstance(args, dict):
            raise ValueError('Object arguments required')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            ident, status, answer, claims = self.db.execute('SELECT * FROM job').fetchone()
            if name == 'get_task':
                if args:
                    raise ValueError('No arguments expected')
                if status == 'completed':
                    return {'status': 'completed', 'remaining_jobs': 0, 'instruction': 'Stop; no further job exists.'}
                self.db.execute('UPDATE job SET status=?,claims=claims+1 WHERE id=?', ('claimed', ident))
                return {'status': 'claimed', 'job_id': ident,
                        'task': 'Sort the integers [7, 2, 5] in ascending order. Submit only comma-separated digits without spaces.',
                        'instruction': 'Call submit_result exactly once with job_id and your answer, then stop. Use no other tools.'}
            if name != 'submit_result' or args.keys() != {'job_id', 'answer'}:
                raise ValueError('Unknown tool or invalid arguments')
            if (args['job_id'] != ident or status != 'claimed' or not isinstance(args['answer'], str)
                    or not args['answer'].strip() or len(args['answer']) > 120):
                raise ValueError('Only one bounded result for the claimed job is accepted')
            self.db.execute('UPDATE job SET status=?,answer=? WHERE id=?', ('completed', args['answer'], ident))
            return {'status': 'accepted', 'job_id': ident, 'remaining_jobs': 0, 'instruction': 'Stop; the test is complete.'}

    def dispatch(self, message):
        method, params = message.get('method'), message.get('params') or {}
        if method == 'initialize':
            return {'protocolVersion': params.get('protocolVersion', '2024-11-05'),
                    'capabilities': {'tools': {}}, 'serverInfo': {'name': 'reprise-job-probe', 'version': '0.1.0'}}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': TOOLS}
        if method == 'tools/call':
            result = self.call(params.get('name'), params.get('arguments', {}))
            return {'content': [{'type': 'text', 'text': json.dumps(result)}], 'isError': False}
        raise ValueError('No other operation exposed')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    args = parser.parse_args()
    probe = Probe(args.state)
    try:
        while True:
            raw = sys.stdin.buffer.readline(65537)
            if not raw or len(raw) > 65536:
                break
            ident = None
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('Object required')
                if 'id' not in message:
                    continue
                ident = message['id']
                reply = {'jsonrpc': '2.0', 'id': ident, 'result': probe.dispatch(message)}
            except (ValueError, TypeError, KeyError, AttributeError, sqlite3.Error):
                reply = {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': 'Probe request refused; no retry needed.'}}
            print(json.dumps(reply), flush=True)
    finally:
        probe.db.close()


if __name__ == '__main__':
    main()
