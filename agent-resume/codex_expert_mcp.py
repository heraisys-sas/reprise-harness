#!/usr/bin/env python3
"""Project-bound MCP expertise tools; explicit backends, no background inference."""
from app_paths import state_directory
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
from delegation_store import LOOKUP_SCHEMA, Store, file_evidence
from codex_expert import review
from claude_expert import review as claude_review
import claude_desktop
import ironclaw
import reprise_logs
import reprise_health
import shutil
from engines import executable, options


def error_payload(exc):
    detail = f'{type(exc).__name__}: {exc}'[:200]
    return {'code': -32602, 'message': 'Request refused or unavailable; no automatic retry.',
            'data': {'error': detail}}

BACKEND_MODELS = {
    'codex': ('gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra'),
    'claude-code': ('sonnet', 'opus'),
    'ironclaw': (),
    'claude-desktop': (),
}
RETURN_MODES = {'type': 'string', 'enum': ['full', 'receipt'], 'default': 'full'}
INFERENCE_TOOLS = ('reprise_delegate', 'reprise_followup', 'codex_review', 'codex_followup')

MODE_SCHEMA = {'type': 'string', 'enum': ['expertise', 'implementation'],
               'description': 'Implementation requires operator-enabled project workspace writes; followup preserves its original mode.'}

MODEL_SCHEMA = {'type': 'string',
                'description': 'Explicit model from reprise_backends.models; omitted preserves the default or thread route.'}

SCHEMA = {'type': 'object', 'properties': {'return_mode': RETURN_MODES, 'question': {'type': 'string', 'maxLength': 12000}, 'request_key': {'type': 'string', 'maxLength': 128}, 'parent_request_key': {'type': 'string', 'maxLength': 128}},
          'required': ['question', 'request_key'], 'additionalProperties': False}
TOOLS = [
    {'name': 'reprise_quotas', 'description': 'Observe quota knowledge by explicit engine/route, account and pool. Cached by default; refresh reads native metadata only. Unknown is not zero. Never resumes/routes inference.',
     'inputSchema': {'type': 'object', 'properties': {'engine': {'type': 'string', 'enum': ['codex-cli', 'codex-server', 'codex-parent', 'claude-code', 'claude-desktop', 'harness-cli']}, 'refresh': {'type': 'boolean'}}, 'additionalProperties': False}},
    {'name': 'reprise_requests', 'description': 'Recover durable requests/results by caller-persisted request_key, or list requests after a lost acknowledgement. Never dispatches inference.',
     'inputSchema': LOOKUP_SCHEMA},
    {'name': 'reprise_delegate', 'description': 'Delegate expertise, review or a bounded batch. Codex mode implementation applies project edits only when the integration grants workspace writes. Default Codex: standard Sol, deep Astra, standard batch Luna. Explicit claude-code when configured: Sonnet/medium or Opus/high, no tools, only question and supplied project file snapshots. Returns text/patches to verify. Never silently switches backend or billing. Continue via reprise_followup.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {
         'return_mode': {'type': 'string', 'enum': ['full', 'receipt'], 'default': 'full'},
         'mode': MODE_SCHEMA,
         'kind': {'type': 'string', 'enum': ['expertise', 'review', 'batch']},
         'level': {'type': 'string', 'enum': ['standard', 'deep']},
         'model': MODEL_SCHEMA,
         'backend': {'type': 'string', 'enum': ['codex', 'ironclaw', 'claude-code', 'claude-desktop']},
         'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}},
         'chatId': {'type': 'string'},
         'filePaths': {'type': 'array', 'maxItems': 10, 'items': {'type': 'string'}}},
         'required': ['question', 'kind', 'request_key']}},
    {'name': 'reprise_backends', 'description': 'List configured delegation capabilities without inference. Availability is not a price or quota guarantee.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'codex_review', 'description': 'Ask Codex for a read-only project review. Uses ChatGPT quota. No automatic retries or approval. Returns its answer and session ID.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}}}}},
    {'name': 'codex_followup', 'description': 'Continue a review created by this project bridge; preserves its Codex conversation.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {'thread_id': {'type': 'string'}}, 'required': ['question', 'thread_id', 'request_key']}},
    {'name': 'reprise_followup', 'description': 'Continue an owned project session and inherit its execution mode. Omitted model preserves the thread route; explicit model overrides it, preserving effort. No fallback.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | {
         'model': MODEL_SCHEMA, 'mode': MODE_SCHEMA,
         'thread_id': {'type': 'string'}, 'backend': {'type': 'string', 'enum': ['codex', 'ironclaw', 'claude-code', 'claude-desktop']}},
         'required': ['question', 'thread_id', 'backend', 'request_key']}},
    {'name': 'reprise_resume', 'description': 'List interrupted tasks reconstructed from local logs that can be retried (quota, guard or missing dispatch). Read-only; no automatic retry.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'reprise_health', 'description': 'Post-update health: versions, whether supervised servers need relaunch, whether watched log signatures are still present, human-required actions, plus an on-device Foundation Models explanation. Read-only.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'reprise_history', 'description': 'Explain recent harness work and its Reprise interactions from local logs (Quick Entry submissions + MCP calls). Read-only.',
     'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'additionalProperties': False}},
]

MODES = {
    'expertise': 'Analyze the conceptual problem. Return the algorithm, invariants, tradeoffs and a compact implementation contract. Distinguish evidence from assumptions.',
    'review': 'Review for correctness, security and maintainability. Return only actionable findings with severity, file references, evidence and verification steps; say when no finding is established.',
    'batch': 'Produce the requested bounded batch of code, tests or transformations as text or unified patches. Do not apply changes. Preserve existing contracts; include deterministic validation commands and report any unfinished items.',
}


TRANSPORT_LIMIT = 7000
# Largest response a backend could capture before #32 (48,000 characters). A full-mode result up to
# this size is delivered unchanged; a larger one is replaced by its durable receipt manifest.
FULL_RESULT_LIMIT = 48000


def compact_call(message):
    params = message.get('params') or {}
    if message.get('method') != 'tools/call' or not isinstance(params, dict):
        return False
    args = params.get('arguments', {})
    return isinstance(args, dict) and (
        params.get('name') == 'reprise_requests' and args.get('view') in ('receipt', 'page')
        or params.get('name') in INFERENCE_TOOLS and args.get('return_mode') == 'receipt')


def tool_content(result):
    return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}],
            'isError': result.get('status') != 'finished'}


def bounded_content(result, ident):
    result = dict(result)
    while True:
        content = tool_content(result)
        # Reserve the largest accepted id and acknowledgement representation.
        budget = dict(result, acknowledged=False) if 'acknowledged' in result else result
        envelope = {'jsonrpc': '2.0', 'id': 'x' * 254, 'result': tool_content(budget)}
        if len(json.dumps(envelope, ensure_ascii=False)) <= TRANSPORT_LIMIT:
            actual = {'jsonrpc': '2.0', 'id': ident, 'result': content}
            if len(json.dumps(actual, ensure_ascii=False)) > TRANSPORT_LIMIT:
                raise ValueError('Compact response exceeds transport limit')
            return content
        fragment = result.get('text')
        if not fragment:
            raise ValueError('Compact response exceeds transport limit')
        # Halving is deterministic and bounds the number of serializations.
        fragment = fragment[:len(fragment) // 2]
        end = result['offset'] + len(fragment)
        result.update(text=fragment, returned_length=len(fragment),
                      has_more=end < result['total_length'],
                      next_offset=end if end < result['total_length'] else None)
        if result['has_more'] and not fragment:
            raise ValueError('Transport limit prevents positive progress')


class Bridge:
    def __init__(self, root, values, state, daily_limit=8, *, claude_program=None, claude_subscription_only=False, desktop_socket=None, allowed_tools=None, ironclaw_values=None, codex_workspace_write=False):
        if type(codex_workspace_write) is not bool:
            raise ValueError('Explicit workspace-write grant required')
        self.codex_workspace_write = codex_workspace_write
        known_tools = {item['name'] for item in TOOLS}
        if allowed_tools is not None and (not allowed_tools or set(allowed_tools) - known_tools):
            raise ValueError('Unknown or empty tool selection')
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else known_tools
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Project directory required')
        self.values = options('codex-server', values)
        self.program = executable('codex-cli')
        if claude_program and (not Path(claude_program).is_absolute() or not Path(claude_program).is_file()
                               or not os.access(claude_program, os.X_OK)):
            raise ValueError('Absolute executable Claude CLI path required')
        self.claude_program = str(Path(claude_program).resolve()) if claude_program else None
        self.claude_subscription_only = claude_subscription_only
        self.desktop_values = {'socket': desktop_socket} if desktop_socket else {}
        self.desktop_program = executable('claude-desktop')
        self.ironclaw_program = shutil.which('ssh') or 'ssh'
        self.ironclaw_values = ironclaw_values or {}
        self.scope = hashlib.sha256((str(self.root)+'\0'+str(self.values.get('socket', 'default'))).encode()).hexdigest()
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path = state/'expert.lock'
        self.db = sqlite3.connect(state/'expert.sqlite', timeout=2)
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_threads(scope TEXT,id TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_calls(scope TEXT,day INTEGER,count INTEGER,PRIMARY KEY(scope,day))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_routes(scope TEXT,id TEXT,model TEXT,effort TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS claude_expert_routes(scope TEXT,id TEXT,program TEXT,model TEXT,effort TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_thread_modes(scope TEXT,id TEXT,mode TEXT NOT NULL,PRIMARY KEY(scope,id))')
        self.db.commit()
        self.requests = Store(self.db, str(self.root))

    def execution_mode(self, name, args):
        mode = args.get('mode', 'expertise')
        if not isinstance(mode, str) or mode not in ('expertise', 'implementation'):
            raise ValueError('Unknown delegation mode')
        if name in ('reprise_followup', 'codex_followup'):
            if not isinstance(args.get('thread_id'), str):
                raise ValueError('Owned Codex session required')
            row = self.db.execute('SELECT mode FROM expert_thread_modes WHERE scope=? AND id=?',
                                  (self.scope, args.get('thread_id'))).fetchone()
            stored = row[0] if row else 'expertise'
            if 'mode' in args and mode != stored:
                raise ValueError('Followup cannot change the original execution mode')
            mode = stored
        if mode == 'implementation':
            if name not in ('reprise_delegate', 'reprise_followup') or args.get('backend', 'codex') != 'codex':
                raise ValueError('Implementation requires generic Codex delegation tools')
            if not self.codex_workspace_write:
                raise ValueError('Project workspace writes not authorized by the integration')
        return mode

    def call(self, name, args):
        if name not in self.allowed_tools:
            raise ValueError('Tool not enabled for this integration')
        if name == 'reprise_quotas':
            if not isinstance(args, dict) or args.keys() - {'engine', 'refresh'} or not isinstance(args.get('refresh', False), bool):
                raise ValueError('Invalid quota observation arguments')
            import quota_knowledge
            engine = args.get('engine', 'codex-server')
            settings = self.values if engine in ('codex-server', 'codex-parent') else self.desktop_values if engine == 'claude-desktop' else {}
            return {'status': 'finished', 'automatic_fallback': False, 'quota_knowledge': quota_knowledge.status(self.db, engine, settings, refresh=args.get('refresh', False))}
        if name == 'reprise_requests':
            return self.requests.lookup(args)
        if name in ('reprise_backends', 'reprise_health', 'reprise_history', 'reprise_resume'):
            return self._call(name, args)
        if not isinstance(args, dict):
            raise ValueError('Object arguments required')
        return_mode = args.get('return_mode', 'full')
        if name in INFERENCE_TOOLS:
            if return_mode not in ('full', 'receipt'):
                raise ValueError('Invalid return_mode')
        elif 'return_mode' in args:
            raise ValueError('return_mode requires an inference tool')
        with self.lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy', 'reason': 'One active expertise per state directory; no automatic retry.'}
            clean = {k: v for k, v in args.items() if k not in ('request_key', 'parent_request_key', 'return_mode')}
            mode = self.execution_mode(name, clean)
            self._call(name, clean, preflight=True)
            key = args.get('request_key')
            payload = {'tool': name, **clean}
            payload['file_evidence'] = (file_evidence(self.root, args.get('paths'), args.get('filePaths'))
                                        if mode == 'expertise' else [])
            parent = args.get('parent_request_key')
            if name in ('reprise_followup', 'codex_followup') and parent is None:
                own = self.db.execute('SELECT parent FROM delegation_requests WHERE scope=? AND request_key=?', (self.requests.scope, key)).fetchone()
                row = self.db.execute("SELECT request_key FROM delegation_requests WHERE scope=? AND json_extract(result,'$.thread_id')=? ORDER BY rowid DESC LIMIT 1", (self.requests.scope, args.get('thread_id'))).fetchone()
                parent = own[0] if own else row[0] if row else None
            before_evidence = payload['file_evidence']
            if mode == 'implementation':
                known = self.db.execute('SELECT 1 FROM delegation_requests WHERE scope=? AND request_key=?',
                                        (self.requests.scope, key)).fetchone()
                if not known:
                    try:
                        before_evidence = file_evidence(self.root, args.get('paths'), args.get('filePaths'))
                    except OSError as error:
                        raise ValueError('Implementation input unavailable; no request admitted') from error
            existing = self.requests.begin(key, payload, parent)
            if existing:
                pointer = existing.get('request_key', key)
                if return_mode == 'receipt':
                    manifest = self.requests.lookup({'request_key': pointer, 'view': 'receipt'})
                    # The lookup status only says the receipt was found, not how the delegation ended.
                    manifest['status'] = existing['status']
                    return manifest
                return self._bounded_full(pointer, existing)
            # Exceptions or process exit leave a durable uncertain attempt.
            result = self._call(name, clean)
            try:
                changed = before_evidence != file_evidence(self.root, args.get('paths'), args.get('filePaths'))
            except (ValueError, OSError):
                changed = True
            if changed and mode == 'implementation':
                result = dict(result, project_files_changed=True)
            elif changed:
                result = dict(result, status='needs_review', reason='Input files changed during delegation; result requires reconciliation.', input_changed=True)
            result = self.requests.finish(key, result)
            if return_mode == 'receipt':
                return dict(self.requests.lookup({'request_key': key, 'view': 'receipt'}), status=result['status'])
            return self._bounded_full(key, result)

    def _bounded_full(self, key, result):
        """Deliver a full-mode result unchanged up to FULL_RESULT_LIMIT, else its durable manifest."""
        if len(json.dumps(result, ensure_ascii=False)) <= FULL_RESULT_LIMIT:
            return result
        manifest = self.requests.lookup({'request_key': key, 'view': 'receipt'})
        if not manifest['available']:
            return result
        return dict(manifest, status=result['status'], return_mode_effective='receipt',
                    reason='Result exceeds the full-mode delivery limit; read it with reprise_requests view=page or delegation_collect.py.')

    def _call(self, name, args, *, preflight=False):
        if name not in ('reprise_backends', 'reprise_delegate', 'reprise_followup', 'codex_review', 'codex_followup', 'reprise_resume', 'reprise_history', 'reprise_health') or not isinstance(args, dict):
            raise ValueError('Unknown tool or invalid arguments')
        if name == 'reprise_health':
            if args:
                raise ValueError('No arguments expected')
            return {'status': 'finished', 'automatic_fallback': False,
                    **reprise_health.summarize(self.root)}
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
            return {'status': 'finished', 'project_root': str(self.root), 'result_transport': {
                    'receipt': True, 'pagination': True, 'followup_receipt': True,
                    'format': 'receipt-json-v1', 'transport_limit': TRANSPORT_LIMIT}, 'backends': [
                {'id': 'codex', 'configured': bool(self.program), 'billing': 'ChatGPT quota',
                 'models': list(BACKEND_MODELS['codex']),
                 'modes': ['expertise'] + (['implementation'] if self.codex_workspace_write else []),
                 'workspace_write_authorized': self.codex_workspace_write,
                 'implementation_scope': str(self.root) if self.codex_workspace_write else None,
                 'kinds': list(MODES), 'levels': ['standard', 'deep'], 'writes': self.codex_workspace_write},
                {'id': 'autoclaw', 'configured': False, 'models': [], 'reason': 'Adapter, billing and fallback behavior not validated.'},
                {'id': 'claude-code', 'configured': bool(self.claude_program and self.claude_subscription_only),
                 'models': list(BACKEND_MODELS['claude-code']),
                 'executable_configured': bool(self.claude_program), 'auth_verified': False,
                 'billing': 'Claude subscription; paid credits must be disabled by operator',
                 'quota_remaining': None, 'kinds': list(MODES), 'levels': ['standard', 'deep'],
                 'writes': False, 'context': 'Explicit question and file snapshots only; no tools'},
                {'id': 'claude-desktop', 'configured': bool(self.desktop_values), 'models': [],
                 'billing': 'Claude Desktop session; quota not reported',
                 'kinds': list(MODES), 'levels': ['standard', 'deep'], 'writes': False,
                 'dispatch': 'dispatchOnQuickEntrySubmit (full turn, not a prefill)',
                 'context': 'Structured Quick Apply payload; images/files bounded; target chat routing'},
                {'id': 'ironclaw', 'configured': False, 'models': [],
                 'reason': 'CLI prototype not enabled: durable Tasks/Endpoint MCP binding pending.',
                 'billing': 'Remote IronClaw runtime (default model observed: morph-dsv41flash-1017)',
                 'kinds': list(MODES), 'levels': ['standard', 'deep'], 'writes': False,
                 'context': 'Experimental CLI retained for audit; use explicit durable ironclaw-submit/poll/result commands.'}],
                'automatic_fallback': False}
        allowed = {'question', 'thread_id'} if name in ('codex_followup', 'reprise_followup') else {'question', 'paths'}
        if name == 'reprise_followup':
            allowed |= {'backend', 'model', 'mode'}
        if name == 'reprise_delegate':
            allowed |= {'kind', 'level', 'backend', 'model', 'chatId', 'filePaths', 'mode'}
        if (args.keys()-allowed or not isinstance(args.get('question'), str)
                or not args['question'].strip() or len(args['question']) > 12000):
            raise ValueError('Invalid question or arguments')
        mode = self.execution_mode(name, args)
        routing = {}
        backend = args.get('backend', 'codex')
        if backend not in ('codex', 'ironclaw', 'claude-code', 'claude-desktop'):
            raise ValueError('Unknown expertise backend')
        if 'model' in args:
            if not isinstance(args['model'], str) or args['model'] not in BACKEND_MODELS[backend]:
                raise ValueError(f'Unknown or unsupported model for backend {backend}')
        if name == 'reprise_delegate' and backend != 'claude-desktop' and args.keys() & {'chatId', 'filePaths'}:
            raise ValueError('chatId and filePaths are only valid with backend claude-desktop')
        question = args['question']
        if name == 'reprise_delegate':
            kind, level = args.get('kind'), args.get('level', 'standard')
            if kind not in MODES or level not in ('standard', 'deep'):
                raise ValueError('Invalid delegation kind or level')
            routing = {'model': 'gpt-6-astra' if level == 'deep' else 'gpt-6-luna' if kind == 'batch' else 'gpt-6.1-sol',
                       'effort': 'high' if level == 'deep' else 'medium'}
            if backend == 'claude-code':
                routing['model'] = 'opus' if level == 'deep' else 'sonnet'
            question = (('Implement the bounded task; apply project changes and run appropriate local checks.'
                         if mode == 'implementation' else MODES[kind]) + '\n\n' + question)
            if len(question) > 12000:
                raise ValueError('Task including delegation contract exceeds limit')
        ident = args.get('thread_id')
        if name == 'reprise_followup' and 'backend' not in args:
            raise ValueError('Explicit followup backend required')
        if name == 'reprise_followup' and backend == 'ironclaw':
            raise ValueError('Pas de session IronClaw suiveuse ; run -m est sans état.')
        if name == 'reprise_followup' and backend == 'claude-desktop':
            raise ValueError('Pas de session Quick Apply suiveuse ; la soumission est unidirectionnelle.')
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
        if 'model' in args:
            routing['model'] = args['model']
            routing.setdefault('effort', 'medium')
        if backend == 'ironclaw':
            return {'status': 'engine_unavailable', 'backend': backend,
                    'reason': 'Prototype CLI IronClaw désactivé : raccordement MCP au registre durable Tasks/Endpoint requis.', 'automatic_fallback': False}
        if backend == 'claude-desktop' and not self.desktop_values:
            return {'status': 'engine_unavailable', 'backend': backend,
                    'reason': 'Quick Apply non configuré sur ce pont ; relancez-le avec --desktop-socket.', 'automatic_fallback': False}
        if backend != 'claude-desktop' and not (self.ironclaw_program if backend == 'ironclaw'
                                                else (self.program if backend == 'codex' else self.claude_program)):
            return {'status': 'engine_unavailable', 'backend': backend, 'reason': 'Requested executable not configured', 'automatic_fallback': False}
        if backend == 'claude-code' and not self.claude_subscription_only:
            return {'status': 'billing_unverified', 'backend': backend, 'reason': 'Operator must confirm paid usage credits disabled before enabling Claude.', 'automatic_fallback': False}
        if preflight:
            return
        if backend == 'ironclaw':
            result = ironclaw.review(self.ironclaw_program, self.ironclaw_values, self.root, question,
                                     kind=args.get('kind', 'expertise'), level=args.get('level', 'standard'),
                                     paths=args.get('paths'), timeout=300)
            return result | {'backend': backend, 'automatic_fallback': False}
        if backend == 'claude-desktop':
            result = claude_desktop.review(self.root, question, kind=args.get('kind', 'expertise'),
                                          level=args.get('level', 'standard'), paths=args.get('paths'),
                                          chat_id=args.get('chatId'), file_paths=args.get('filePaths'),
                                          program=self.desktop_program, values=self.desktop_values, request_key=self.requests.active_key)
            return result | {'backend': backend, 'automatic_fallback': False, 'admission_only': True, 'response': None}
        if backend == 'claude-code':
            result = claude_review(self.claude_program, self.root, question, paths=args.get('paths'),
                                   thread_id=ident, subscription_only=self.claude_subscription_only, **routing)
            if result.get('status') == 'finished' and result.get('resumable') is True:
                with self.db:
                    self.db.execute('INSERT OR REPLACE INTO claude_expert_routes VALUES (?,?,?,?,?)',
                                    (self.scope, result['thread_id'], self.claude_program, result['model'], routing['effort']))
            return result | {'backend': backend, 'automatic_fallback': False}
        result = review(self.program, self.values, self.root, question, paths=args.get('paths'), thread_id=ident,
                        **({'implementation': True} if mode == 'implementation' else {}), **routing)
        result['mode'] = mode
        result.update(backend=backend, automatic_fallback=False)
        resumable = result.get('status') in ('finished', 'blocked_permission', 'timed_out') or (
            result.get('status') == 'needs_review' and result.get('turn_id'))
        if result.get('thread_id') and result.get('resumable') is True and resumable:
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO expert_threads VALUES (?,?)', (self.scope, result['thread_id']))
                self.db.execute('INSERT OR IGNORE INTO expert_thread_modes VALUES (?,?,?)',
                                (self.scope, result['thread_id'], mode))
                if routing:
                    self.db.execute('INSERT OR REPLACE INTO expert_routes VALUES (?,?,?,?)',
                                    (self.scope, result['thread_id'], routing['model'], routing['effort']))
        return result

    def dispatch(self, message):
        method, params = message.get('method'), message.get('params') or {}
        if method == 'initialize':
            return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'reprise-codex-expert', 'version': Path(__file__).with_name('VERSION').read_text().strip(),
                                   'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': [item for item in TOOLS if item['name'] in self.allowed_tools]}
        if method == 'tools/call':
            compact = compact_call(message)
            if compact and len(json.dumps(message.get('id'), ensure_ascii=False)) > 256:
                raise ValueError('Compact calls require a bounded JSON-RPC id')
            result = self.call(params.get('name'), params.get('arguments', {}))
            if compact:
                return bounded_content(result, message.get('id'))
            return tool_content(result)
        raise ValueError('Unsupported MCP method')


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--socket')
    parser.add_argument('--state')
    parser.add_argument('--tools', help='Explicit comma-separated exposed tool allowlist; legacy default exposes all')
    parser.add_argument('--daily-limit', type=int, default=8, help='Deprecated compatibility option; ignored for all backends')
    parser.add_argument('--claude-program', help='Explicit absolute Claude CLI path; disabled by default')
    parser.add_argument('--claude-included-quota-confirmed', action='store_true', help='Operator confirms paid usage credits disabled; CLI cannot verify this')
    parser.add_argument('--codex-workspace-write', action='store_true', help='Operator grant: implementation may write this project; native approvals remain required')
    parser.add_argument('--desktop-socket', help='Explicit absolute Quick Entry socket path; disabled by default')
    parser.add_argument('--ironclaw-config', help='JSON 0600: {host,port,user,key} du point d’acceptation IronClaw')
    args = parser.parse_args()
    ironclaw_values = {}
    if args.ironclaw_config:
        try:
            ironclaw_values = json.loads(Path(args.ironclaw_config).read_text())
        except (OSError, ValueError):
            raise SystemExit('--ironclaw-config doit être un JSON 0600 lisible')
    bridge = Bridge(args.project_root, {'socket': args.socket} if args.socket else {}, args.state or str(state_directory(Path(__file__).parent)), args.daily_limit,
                    claude_program=args.claude_program, claude_subscription_only=args.claude_included_quota_confirmed,
                    desktop_socket=args.desktop_socket, allowed_tools=args.tools.split(',') if args.tools is not None else None,
                    ironclaw_values=ironclaw_values, codex_workspace_write=args.codex_workspace_write)
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
            message = None
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('Object required')
                ident = message.get('id')
                if 'id' not in message:
                    continue
                result = bridge.dispatch(message)
                reply = {'jsonrpc': '2.0', 'id': ident, 'result': result}
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
                if isinstance(message, dict) and compact_call(message) and len(json.dumps(ident, ensure_ascii=False)) > 256:
                    ident = None
                reply = {'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)}
            print(json.dumps(reply, ensure_ascii=False), flush=True)
    finally:
        bridge.db.close()
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == '__main__':
    main()
