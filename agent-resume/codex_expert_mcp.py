#!/usr/bin/env python3
"""Project-bound MCP expertise tools; explicit backends, no background inference."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import time
from codex_expert import review
from claude_expert import review as claude_review
import claude_desktop
import reprise_logs
from engines import executable, options

SCHEMA = {'type': 'object', 'properties': {'question': {'type': 'string', 'maxLength': 12000}},
          'required': ['question'], 'additionalProperties': False}
TOOLS = [
    {'name': 'reprise_delegate', 'description': 'Delegate expertise, review or a bounded batch. Default Codex: standard Sol, deep Astra, standard batch Luna. Explicit claude-code when configured: Sonnet/medium or Opus/high, no tools, only question and supplied project file snapshots. Returns text/patches to verify. Never silently switches backend or billing. Continue via reprise_followup.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {
         'kind': {'type': 'string', 'enum': ['expertise', 'review', 'batch']},
         'level': {'type': 'string', 'enum': ['standard', 'deep']},
         'backend': {'type': 'string', 'enum': ['codex', 'claude-code', 'claude-desktop']},
         'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}},
         'chatId': {'type': 'string'},
         'filePaths': {'type': 'array', 'maxItems': 10, 'items': {'type': 'string'}}},
         'required': ['question', 'kind']}},
    {'name': 'reprise_backends', 'description': 'List configured delegation capabilities without inference. Availability is not a price or quota guarantee.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'codex_review', 'description': 'Ask Codex for a read-only project review. Uses ChatGPT quota. No automatic retries or approval. Returns its answer and session ID.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}}}}},
    {'name': 'codex_followup', 'description': 'Continue a review created by this project bridge; preserves its Codex conversation.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {'thread_id': {'type': 'string'}}, 'required': ['question', 'thread_id']}},
    {'name': 'reprise_followup', 'description': 'Continue an owned project expertise session, preserving backend, model and effort. No fallback.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {
         'thread_id': {'type': 'string'}, 'backend': {'type': 'string', 'enum': ['codex', 'claude-code', 'claude-desktop']}},
         'required': ['question', 'thread_id', 'backend']}},
    {'name': 'reprise_resume', 'description': 'List interrupted tasks reconstructed from local logs that can be retried (quota, guard or missing dispatch). Read-only; no automatic retry.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'reprise_history', 'description': 'Explain recent harness work and its Reprise interactions from local logs (Quick Entry submissions + MCP calls). Read-only.',
     'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'additionalProperties': False}},
]

MODES = {
    'expertise': 'Analyze the conceptual problem. Return the algorithm, invariants, tradeoffs and a compact implementation contract. Distinguish evidence from assumptions.',
    'review': 'Review for correctness, security and maintainability. Return only actionable findings with severity, file references, evidence and verification steps; say when no finding is established.',
    'batch': 'Produce the requested bounded batch of code, tests or transformations as text or unified patches. Do not apply changes. Preserve existing contracts; include deterministic validation commands and report any unfinished items.',
}


class Bridge:
    def __init__(self, root, values, state, daily_limit=8, *, claude_program=None, claude_subscription_only=False, desktop_socket=None):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir() or not 1 <= daily_limit <= 100:
            raise ValueError('Project directory and daily limit 1..100 required')
        self.values = options('codex-server', values)
        self.program = executable('codex-cli')
        if claude_program and (not Path(claude_program).is_absolute() or not Path(claude_program).is_file()
                               or not os.access(claude_program, os.X_OK)):
            raise ValueError('Absolute executable Claude CLI path required')
        self.claude_program = str(Path(claude_program).resolve()) if claude_program else None
        self.claude_subscription_only = claude_subscription_only
        self.desktop_values = {'socket': desktop_socket} if desktop_socket else {}
        self.desktop_program = executable('claude-desktop')
        self.limit = daily_limit
        self.scope = hashlib.sha256((str(self.root)+'\0'+str(self.values.get('socket', 'default'))).encode()).hexdigest()
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path = state/'expert.lock'
        self.db = sqlite3.connect(state/'expert.sqlite', timeout=2)
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_threads(scope TEXT,id TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_calls(scope TEXT,day INTEGER,count INTEGER,PRIMARY KEY(scope,day))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_routes(scope TEXT,id TEXT,model TEXT,effort TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS claude_expert_routes(scope TEXT,id TEXT,program TEXT,model TEXT,effort TEXT,PRIMARY KEY(scope,id))')
        self.db.commit()

    def call(self, name, args):
        with self.lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy', 'reason': 'Another expertise request is active; no automatic retry.'}
            return self._call(name, args)

    def _call(self, name, args):
        if name not in ('reprise_backends', 'reprise_delegate', 'reprise_followup', 'codex_review', 'codex_followup', 'reprise_resume', 'reprise_history') or not isinstance(args, dict):
            raise ValueError('Unknown tool or invalid arguments')
        if name == 'reprise_resume':
            if args:
                raise ValueError('No arguments expected')
            return {'status': 'finished', 'resumable': reprise_logs.resumable(),
                    'automatic_fallback': False}
        if name == 'reprise_history':
            allowed = {'limit'}
            if args.keys() - allowed:
                raise ValueError('Invalid arguments')
            return {'status': 'finished', 'automatic_fallback': False,
                    **reprise_logs.history(limit=args.get('limit', 20))}
        if name == 'reprise_backends':
            if args:
                raise ValueError('No arguments expected')
            return {'status': 'finished', 'project_root': str(self.root), 'backends': [
                {'id': 'codex', 'configured': bool(self.program), 'billing': 'ChatGPT quota',
                 'kinds': list(MODES), 'levels': ['standard', 'deep'], 'writes': False},
                {'id': 'autoclaw', 'configured': False, 'reason': 'Adapter, billing and fallback behavior not validated.'},
                {'id': 'claude-code', 'configured': bool(self.claude_program and self.claude_subscription_only),
                 'executable_configured': bool(self.claude_program), 'auth_verified': False,
                 'billing': 'Claude subscription; paid credits must be disabled by operator',
                 'quota_remaining': None, 'kinds': list(MODES), 'levels': ['standard', 'deep'],
                 'writes': False, 'context': 'Explicit question and file snapshots only; no tools'},
                {'id': 'claude-desktop', 'configured': bool(self.desktop_program and self.desktop_values),
                 'billing': 'Claude Desktop session; quota not reported',
                 'kinds': list(MODES), 'levels': ['standard', 'deep'], 'writes': False,
                 'dispatch': 'dispatchOnQuickEntrySubmit (full turn, not a prefill)',
                 'context': 'Structured Quick Apply payload; images/files bounded; target chat routing'}],
                'automatic_fallback': False}
        allowed = {'question', 'thread_id'} if name in ('codex_followup', 'reprise_followup') else {'question', 'paths'}
        if name == 'reprise_followup':
            allowed.add('backend')
        if name == 'reprise_delegate':
            allowed |= {'kind', 'level', 'backend', 'chatId', 'filePaths'}
        if (args.keys()-allowed or not isinstance(args.get('question'), str)
                or not args['question'].strip() or len(args['question']) > 12000):
            raise ValueError('Invalid question or arguments')
        routing = {}
        backend = args.get('backend', 'codex')
        if backend not in ('codex', 'claude-code', 'claude-desktop'):
            raise ValueError('Unknown expertise backend')
        question = args['question']
        if name == 'reprise_delegate':
            kind, level = args.get('kind'), args.get('level', 'standard')
            if kind not in MODES or level not in ('standard', 'deep'):
                raise ValueError('Invalid delegation kind or level')
            routing = {'model': 'gpt-6-astra' if level == 'deep' else 'gpt-6-luna' if kind == 'batch' else 'gpt-6.1-sol',
                       'effort': 'high' if level == 'deep' else 'medium'}
            if backend == 'claude-code':
                routing['model'] = 'opus' if level == 'deep' else 'sonnet'
            question = MODES[kind] + '\n\n' + question
            if len(question) > 12000:
                raise ValueError('Task including delegation contract exceeds limit')
        ident = args.get('thread_id')
        if name == 'reprise_followup' and 'backend' not in args:
            raise ValueError('Explicit followup backend required')
        if name == 'reprise_followup' and backend == 'claude-code':
            if not isinstance(ident, str):
                raise ValueError('Owned Claude session required')
            route = self.db.execute('SELECT program,model,effort FROM claude_expert_routes WHERE scope=? AND id=?', (self.scope, ident)).fetchone()
            if not route or route[0] != self.claude_program:
                raise ValueError('This bridge does not own that Claude session and executable route')
            routing = dict(zip(('model', 'effort'), route[1:]))
        elif name == 'reprise_followup':
            name = 'codex_followup'
        if name == 'codex_followup' and (not isinstance(ident, str) or not self.db.execute(
                'SELECT 1 FROM expert_threads WHERE scope=? AND id=?', (self.scope, ident)).fetchone()):
            raise ValueError('This bridge does not own that review thread')
        if ident and backend == 'codex':
            route = self.db.execute('SELECT model,effort FROM expert_routes WHERE scope=? AND id=?', (self.scope, ident)).fetchone()
            if route:
                routing = dict(zip(('model', 'effort'), route))
        if backend != 'claude-desktop' and not (self.program if backend == 'codex' else self.claude_program):
            return {'status': 'engine_unavailable', 'backend': backend, 'reason': 'Requested executable not configured', 'automatic_fallback': False}
        if backend == 'claude-code' and not self.claude_subscription_only:
            return {'status': 'billing_unverified', 'backend': backend, 'reason': 'Operator must confirm paid usage credits disabled before enabling Claude.', 'automatic_fallback': False}
        day = int(time.time()//86400)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            count = self.db.execute('SELECT count FROM expert_calls WHERE scope=? AND day=?', (self.scope, day)).fetchone()
            if count and count[0] >= self.limit:
                return {'status': 'budget_blocked', 'reason': 'Daily expertise call cap reached; no automatic retry.'}
            # Reserve before any transmission; failures are charged to this local cap too.
            self.db.execute('INSERT INTO expert_calls VALUES (?,?,1) ON CONFLICT(scope,day) DO UPDATE SET count=count+1', (self.scope, day))
        if backend == 'claude-desktop':
            result = claude_desktop.review(self.root, question, kind=args.get('kind', 'expertise'),
                                          level=args.get('level', 'standard'), paths=args.get('paths'),
                                          chat_id=args.get('chatId'), file_paths=args.get('filePaths'),
                                          program=self.desktop_program, values=self.desktop_values)
            return result | {'backend': backend, 'automatic_fallback': False}
        if backend == 'claude-code':
            result = claude_review(self.claude_program, self.root, question, paths=args.get('paths'),
                                   thread_id=ident, subscription_only=self.claude_subscription_only, **routing)
            if result.get('status') == 'finished' and result.get('resumable') is True:
                with self.db:
                    self.db.execute('INSERT OR REPLACE INTO claude_expert_routes VALUES (?,?,?,?,?)',
                                    (self.scope, result['thread_id'], self.claude_program, result['model'], routing['effort']))
            return result | {'backend': backend, 'automatic_fallback': False}
        result = review(self.program, self.values, self.root, question, paths=args.get('paths'), thread_id=ident, **routing)
        result.update(backend=backend, automatic_fallback=False)
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
    parser.add_argument('--claude-program', help='Explicit absolute Claude CLI path; disabled by default')
    parser.add_argument('--claude-included-quota-confirmed', action='store_true', help='Operator confirms paid usage credits disabled; CLI cannot verify this')
    parser.add_argument('--desktop-socket', help='Explicit absolute Quick Entry socket path; disabled by default')
    args = parser.parse_args()
    bridge = Bridge(args.project_root, {'socket': args.socket} if args.socket else {}, args.state, args.daily_limit,
                    claude_program=args.claude_program, claude_subscription_only=args.claude_included_quota_confirmed,
                    desktop_socket=args.desktop_socket)
    def terminate(signum, frame):
        # Unwind active CLI cleanup before closing the DB and releasing the lock.
        raise SystemExit(128 + signum)
    previous_sigterm = signal.signal(signal.SIGTERM, terminate)
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
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == '__main__':
    main()
