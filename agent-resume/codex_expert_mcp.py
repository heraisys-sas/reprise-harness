#!/usr/bin/env python3
"""Project-bound MCP tools for explicit Codex reviews; no background inference."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from codex_expert import review
from engines import executable, options

SCHEMA = {'type': 'object', 'properties': {'question': {'type': 'string', 'maxLength': 12000}},
          'required': ['question'], 'additionalProperties': False}
TOOLS = [
    {'name': 'reprise_delegate', 'description': 'Delegate expertise, code review or a bounded batch to Codex through Reprise. Standard uses Sol; deep uses Astra; standard batches use Luna. Read-only: returns advice, code or patches for the caller to apply and verify. Uses ChatGPT quota, never silently switches billing. Reuse returned thread via codex_followup.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {
         'kind': {'type': 'string', 'enum': ['expertise', 'review', 'batch']},
         'level': {'type': 'string', 'enum': ['standard', 'deep']},
         'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}}},
         'required': ['question', 'kind']}},
    {'name': 'reprise_backends', 'description': 'List configured delegation capabilities without inference. Availability is not a price or quota guarantee.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'codex_review', 'description': 'Ask Codex for a read-only project review. Uses ChatGPT quota. No automatic retries or approval. Returns its answer and session ID.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}}}}},
    {'name': 'codex_followup', 'description': 'Continue a review created by this project bridge; preserves its Codex conversation.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {'thread_id': {'type': 'string'}}, 'required': ['question', 'thread_id']}},
]

MODES = {
    'expertise': 'Analyze the conceptual problem. Return the algorithm, invariants, tradeoffs and a compact implementation contract. Distinguish evidence from assumptions.',
    'review': 'Review for correctness, security and maintainability. Return only actionable findings with severity, file references, evidence and verification steps; say when no finding is established.',
    'batch': 'Produce the requested bounded batch of code, tests or transformations as text or unified patches. Do not apply changes. Preserve existing contracts; include deterministic validation commands and report any unfinished items.',
}


class Bridge:
    def __init__(self, root, values, state, daily_limit=8):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir() or not 1 <= daily_limit <= 100:
            raise ValueError('Project directory and daily limit 1..100 required')
        self.values = options('codex-server', values)
        self.program = executable('codex-cli')
        self.limit = daily_limit
        self.scope = hashlib.sha256((str(self.root)+'\0'+str(self.values.get('socket', 'default'))).encode()).hexdigest()
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path = state/'expert.lock'
        self.db = sqlite3.connect(state/'expert.sqlite', timeout=2)
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_threads(scope TEXT,id TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_calls(scope TEXT,day INTEGER,count INTEGER,PRIMARY KEY(scope,day))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_routes(scope TEXT,id TEXT,model TEXT,effort TEXT,PRIMARY KEY(scope,id))')
        self.db.commit()

    def call(self, name, args):
        with self.lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy', 'reason': 'Another expertise request is active; no automatic retry.'}
            return self._call(name, args)

    def _call(self, name, args):
        if name not in ('reprise_backends', 'reprise_delegate', 'codex_review', 'codex_followup') or not isinstance(args, dict):
            raise ValueError('Unknown tool or invalid arguments')
        if name == 'reprise_backends':
            if args:
                raise ValueError('No arguments expected')
            return {'status': 'finished', 'project_root': str(self.root), 'backends': [
                {'id': 'codex', 'configured': bool(self.program), 'billing': 'ChatGPT quota',
                 'kinds': list(MODES), 'levels': ['standard', 'deep'], 'writes': False},
                {'id': 'autoclaw', 'configured': False, 'reason': 'Adapter, billing and fallback behavior not validated.'}],
                'automatic_fallback': False}
        allowed = {'question', 'thread_id'} if name == 'codex_followup' else {'question', 'paths'}
        if name == 'reprise_delegate':
            allowed |= {'kind', 'level'}
        if args.keys()-allowed or not isinstance(args.get('question'), str) or not 0 < len(args['question'].strip()) <= 12000:
            raise ValueError('Invalid question or arguments')
        routing = {}
        question = args['question']
        if name == 'reprise_delegate':
            kind, level = args.get('kind'), args.get('level', 'standard')
            if kind not in MODES or level not in ('standard', 'deep'):
                raise ValueError('Invalid delegation kind or level')
            routing = {'model': 'gpt-6-astra' if level == 'deep' else 'gpt-6-luna' if kind == 'batch' else 'gpt-6.1-sol',
                       'effort': 'high' if level == 'deep' else 'medium'}
            question = MODES[kind] + '\n\n' + question
            if len(question) > 12000:
                raise ValueError('Task including delegation contract exceeds limit')
        ident = args.get('thread_id')
        if name == 'codex_followup' and (not isinstance(ident, str) or not self.db.execute(
                'SELECT 1 FROM expert_threads WHERE scope=? AND id=?', (self.scope, ident)).fetchone()):
            raise ValueError('This bridge does not own that review thread')
        if ident:
            route = self.db.execute('SELECT model,effort FROM expert_routes WHERE scope=? AND id=?', (self.scope, ident)).fetchone()
            if route:
                routing = dict(zip(('model', 'effort'), route))
        if not self.program:
            return {'status': 'engine_unavailable', 'reason': 'Codex executable unavailable'}
        day = int(time.time()//86400)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            count = self.db.execute('SELECT count FROM expert_calls WHERE scope=? AND day=?', (self.scope, day)).fetchone()
            if count and count[0] >= self.limit:
                return {'status': 'budget_blocked', 'reason': 'Daily expertise call cap reached; no automatic retry.'}
            # Reserve before any transmission; failures are charged to this local cap too.
            self.db.execute('INSERT INTO expert_calls VALUES (?,?,1) ON CONFLICT(scope,day) DO UPDATE SET count=count+1', (self.scope, day))
        result = review(self.program, self.values, self.root, question, paths=args.get('paths'), thread_id=ident, **routing)
        result.update(backend='codex', automatic_fallback=False)
        resumable = result.get('status') in ('finished', 'blocked_permission', 'timed_out') or (
            result.get('status') == 'needs_review' and result.get('turn_id'))
        if result.get('thread_id') and result.get('resumable') is True and resumable:
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO expert_threads VALUES (?,?)', (self.scope, result['thread_id']))
                if routing:
                    self.db.execute('INSERT OR REPLACE INTO expert_routes VALUES (?,?,?,?)',
                                    (self.scope, result['thread_id'], routing['model'], routing['effort']))
        return result

    def dispatch(self, message):
        method, params = message.get('method'), message.get('params') or {}
        if method == 'initialize':
            return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'reprise-codex-expert', 'version': '0.1.0'}}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': TOOLS}
        if method == 'tools/call':
            result = self.call(params.get('name'), params.get('arguments', {}))
            return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}],
                    'isError': result.get('status') != 'finished'}
        raise ValueError('Unsupported MCP method')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--socket')
    parser.add_argument('--state', default=str(Path(__file__).parent/'state'))
    parser.add_argument('--daily-limit', type=int, default=8)
    args = parser.parse_args()
    bridge = Bridge(args.project_root, {'socket': args.socket} if args.socket else {}, args.state, args.daily_limit)
    try:
        while True:
            raw = sys.stdin.buffer.readline(1024*1024+1)
            if not raw:
                break
            if len(raw) > 1024*1024:
                break
            ident = None
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('Object required')
                ident = message.get('id')
                if 'id' not in message:
                    continue
                result = bridge.dispatch(message)
                reply = {'jsonrpc': '2.0', 'id': ident, 'result': result}
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error):
                reply = {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': 'Request refused or unavailable; no automatic retry.'}}
            print(json.dumps(reply, ensure_ascii=False), flush=True)
    finally:
        bridge.db.close()


if __name__ == '__main__':
    main()
