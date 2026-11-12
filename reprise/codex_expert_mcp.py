#!/usr/bin/env python3
"""Project-bound MCP expertise tools; explicit backends, no background inference."""
from app_paths import state_directory, state_location
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import threading
import time
import weakref
from desktop_task_store import TaskStore
from delegation_store import LOOKUP_SCHEMA, Store, file_evidence, validate_key, validate_lookup, receipt_view
from delegation_reconcile import reconcile as reconcile_turn
from codex_expert import STOP_REASONS, positive, review, review_config
from codex_rpc import Client
from claude_expert import review as claude_review
import claude_desktop
import apple_expert
import ironclaw
import reprise_logs
import reprise_health
import shutil
from engines import Unavailable, executable, options


WORKSPACE_WRITE_ENABLE = ('harness_integration integration-plan --action install --delegate '
                          '--codex-workspace-write, puis integration-apply et redémarrage du pont')


class WorkspaceWriteNotAuthorized(ValueError):
    """Implementation refused: the operator never granted project workspace writes (issue #104)."""


class ThreadNotOwned(ValueError):
    """Followup refused: this bridge never created the requested thread (issue #100)."""

    def __init__(self, backend, thread_id):
        self.backend, self.thread_id = backend, thread_id
        super().__init__(
            f'Followup refused: this bridge does not own that {backend} thread (thread_id={thread_id}). '
            'To resume it, reissue the call with the parent_request_key recorded when the turn was '
            'emitted, or open a new session with reprise_delegate.')



def error_payload(exc):
    detail = f'{type(exc).__name__}: {exc}'[:200]
    kind, code = 'refused', -32602
    if isinstance(exc, DesktopRegistryUnavailable):
        return {'code': -32000, 'message': 'Desktop registry unavailable; no task created.',
                'data': {'kind': 'desktop_registry_unavailable'}}
    if isinstance(exc, ThreadNotOwned):
        # Issue #100: name the reason and the resume path instead of the generic refusal.
        return {'code': code,
                'message': str(exc),
                'data': {'kind': 'thread_not_owned', 'backend': exc.backend, 'thread_id': exc.thread_id,
                         'resume_with': 'parent_request_key',
                         'alternative': 'open a new session with reprise_delegate', 'error': detail}}
    if isinstance(exc, WorkspaceWriteNotAuthorized):
        # Operator configuration, not a secret: say exactly why and how to fix it instead of the generic refusal.
        return {'code': code,
                'message': ('Implementation mode refused: project workspace writes are not authorized '
                            '(workspace_write_authorized=false). Use mode \'expertise\' for a read-only '
                            'patch, or ask the operator to enable --codex-workspace-write. No automatic retry.'),
                'data': {'kind': 'workspace_write_not_authorized', 'reason': 'workspace_write_authorized_false',
                         'error': detail, 'enable_with': WORKSPACE_WRITE_ENABLE}}
    if isinstance(exc, Unavailable):
        # Backend/engine down is an availability failure, not a caller mistake.
        kind, code = 'unavailable', -32000
    elif isinstance(exc, BlockingIOError):
        # Bridge lock held by another active expertise.
        kind, code = 'busy', -32000
    elif isinstance(exc, sqlite3.OperationalError) and ('locked' in str(exc).lower() or 'busy' in str(exc).lower()):
        # Concurrent store write during a lock-free read path; retry later, nothing was refused.
        kind, code = 'busy', -32000
    elif isinstance(exc, sqlite3.Error):
        kind = 'storage_error'
    elif isinstance(exc, OSError):
        # I/O failure (disk, socket path); not a caller mistake.
        kind = 'storage_error'
    return {'code': code, 'message': 'Request refused or unavailable; no automatic retry.',
            'data': {'kind': kind, 'error': detail}}

BACKEND_MODELS = {
    'codex': ('gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra'),
    'claude-code': ('sonnet', 'opus'),
    'ironclaw': (),
    'claude-desktop': (),
    'foundation-models': ('system',),
}
RETURN_MODES = {'type': 'string', 'enum': ['full', 'receipt'], 'default': 'full'}
INFERENCE_TOOLS = ('reprise_delegate', 'reprise_followup', 'codex_review', 'codex_followup')

MODE_SCHEMA = {'type': 'string', 'enum': ['expertise', 'implementation'],
               'description': 'Implementation requires operator-enabled project workspace writes; followup preserves its original mode.'}

MODEL_SCHEMA = {'type': 'string',
                'description': 'Explicit model from reprise_backends.models; omitted preserves the default or thread route.'}
# Four separate budgets. The client wait never interrupts; only an explicitly authorized
# execution deadline may stop the turn, and only when `stop_on_deadline` is set.
POLICY_PROPERTIES = {
    'wait_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'default': 30,
                     'description': 'Client wait only; on expiry the native turn keeps running and the receipt stays running.'},
    'execution_seconds': {'type': 'number', 'exclusiveMinimum': 0,
                          'description': 'Explicitly authorized execution deadline; unused unless stop_on_deadline is true.'},
    'inactivity_seconds': {'type': 'number', 'exclusiveMinimum': 0,
                           'description': 'Diagnostic only: detach with inactivity evidence; never interrupts.'},
    'stop_on_deadline': {'type': 'boolean', 'default': False,
                         'description': 'Authorize turn/interrupt when execution_seconds is reached.'},
}
RECONCILE_SCHEMA = {'type': 'object', 'properties': {
    'request_key': {'type': 'string', 'minLength': 1, 'maxLength': 128, 'pattern': r'\S'},
    'action': {'type': 'string', 'enum': ['observe', 'stop'], 'default': 'observe'},
    'reason': {'type': 'string', 'enum': list(STOP_REASONS)}},
    'required': ['request_key'], 'additionalProperties': False}

SCHEMA = {'type': 'object', 'properties': {'return_mode': RETURN_MODES, 'question': {'type': 'string', 'maxLength': 12000}, 'request_key': {'type': 'string', 'maxLength': 128}, 'parent_request_key': {'type': 'string', 'maxLength': 128}},
          'required': ['question', 'request_key'], 'additionalProperties': False}
TOOLS = [
    {'name': 'wait_result', 'description': 'Observe an existing Claude Desktop response without dispatch. Blocking call for a background job; timeout requires same-key Desktop reconciliation, not native Codex reconciliation.',
     'inputSchema': {'type': 'object', 'properties': {
         'request_key': {'type': 'string', 'minLength': 1, 'maxLength': 128, 'pattern': r'\S'},
         'wait_seconds': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 600, 'default': 600}},
         'required': ['request_key'], 'additionalProperties': False}},
    {'name': 'reprise_quotas', 'description': 'Observe quota knowledge by explicit engine/route, account and pool. Cached by default; refresh reads native metadata only. Unknown is not zero. Never resumes/routes inference.',
     'inputSchema': {'type': 'object', 'properties': {'engine': {'type': 'string', 'enum': ['codex-cli', 'codex-server', 'codex-parent', 'claude-code', 'claude-desktop', 'harness-cli']}, 'refresh': {'type': 'boolean'}}, 'additionalProperties': False}},
    {'name': 'reprise_requests', 'description': 'Recover durable requests/results by caller-persisted request_key, or list requests after a lost acknowledgement. Never dispatches inference.',
     'inputSchema': LOOKUP_SCHEMA},
    {'name': 'reprise_reconcile', 'description': 'Reconcile a persisted request with its native turn: observe active, finished, interrupted, awaiting approval or unavailable owner, and capture a late result. Never starts a new turn. action="stop" is the only interruption path and requires an explicit authorized reason.',
     'inputSchema': RECONCILE_SCHEMA},
    {'name': 'reprise_delegate', 'description': 'Delegate expertise, review or a bounded batch. Codex mode implementation applies project edits only when the integration grants workspace writes. Default Codex: standard Sol, deep Astra, standard batch Luna. Explicit claude-code when configured: Sonnet/medium or Opus/high, no tools, only question and supplied project file snapshots. Returns text/patches to verify. Never silently switches backend or billing. Continue via reprise_followup.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | POLICY_PROPERTIES | {
         'return_mode': {'type': 'string', 'enum': ['full', 'receipt'], 'default': 'full'},
         'mode': MODE_SCHEMA,
         'kind': {'type': 'string', 'enum': ['expertise', 'review', 'batch']},
         'level': {'type': 'string', 'enum': ['standard', 'deep']},
         'model': MODEL_SCHEMA,
         'backend': {'type': 'string', 'enum': ['codex', 'ironclaw', 'claude-code', 'claude-desktop', 'foundation-models']},
         'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}},
         'chatId': {'type': 'string'},
         'filePaths': {'type': 'array', 'maxItems': 10, 'items': {'type': 'string'}}},
         'required': ['question', 'kind', 'request_key']}},
    {'name': 'reprise_backends', 'description': 'List configured delegation capabilities without inference. Availability is not a price or quota guarantee.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'codex_review', 'description': 'Ask Codex for a read-only project review. Uses ChatGPT quota. No automatic retries or approval. Returns its answer and session ID.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | POLICY_PROPERTIES | {'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}}}}},
    {'name': 'codex_followup', 'description': 'Continue a review created by this project bridge; preserves its Codex conversation.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | POLICY_PROPERTIES | {'thread_id': {'type': 'string'}}, 'required': ['question', 'thread_id', 'request_key']}},
    {'name': 'reprise_followup', 'description': 'Continue an owned project session and inherit its execution mode. Omitted model preserves the thread route; explicit model overrides it, preserving effort. No fallback.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | POLICY_PROPERTIES | {
         'model': MODEL_SCHEMA, 'mode': MODE_SCHEMA,
         'thread_id': {'type': 'string'}, 'backend': {'type': 'string', 'enum': ['codex', 'ironclaw', 'claude-code', 'claude-desktop', 'foundation-models']}},
         'required': ['question', 'thread_id', 'backend', 'request_key']}},
    {'name': 'reprise_resume', 'description': 'List interrupted tasks reconstructed from local logs that can be retried (quota, guard or missing dispatch). Read-only; no automatic retry.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'reprise_health', 'description': 'Post-update health: versions, whether supervised servers need relaunch, whether watched log signatures are still present, human-required actions, plus an on-device Foundation Models explanation. Read-only.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'reprise_history', 'description': 'Explain recent harness work and its Reprise interactions from local logs (Quick Entry submissions + MCP calls). Read-only.',
     'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'additionalProperties': False}},
    {'name': 'approval_list', 'description': 'List native Codex approval requests waiting for a human decision. Read-only; never decides, never resumes anything.',
     'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'additionalProperties': False}},
    {'name': 'approval_respond', 'description': 'Answer a native Codex approval request the operator has decided. Reattaches to the owning thread, sends the JSON-RPC response to the stored request id, then marks the request resolved. Explicit decision required; never approves or denies automatically. May be slow (thread reattach).',
     'inputSchema': {'type': 'object', 'properties': {'approval_id': {'type': 'string', 'maxLength': 128},
                     'decision': {'type': 'string', 'enum': ['approved', 'denied']}},
                     'required': ['approval_id', 'decision'], 'additionalProperties': False}},
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
        params.get('name') == 'wait_result'
        or params.get('name') == 'reprise_requests' and args.get('view') in ('receipt', 'page')
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


MAX_RESULT_WAITS = 8
MAX_RESULT_SECONDS = 600


def _close_bridge_databases(desktop_tasks, db):
    """Niveau module : référencé par weakref.finalize sans retenir self."""
    if desktop_tasks is not None:
        TaskStore._safe_close(desktop_tasks.db)
    TaskStore._safe_close(db)


class DesktopRegistryUnavailable(RuntimeError):    """The existing Desktop registry could not be read."""


def wait_arguments(args):
    if not isinstance(args, dict) or args.keys() - {'request_key', 'wait_seconds'}:
        raise ValueError('Exact wait_result arguments required')
    validate_key(args.get('request_key'))
    seconds = args.get('wait_seconds', MAX_RESULT_SECONDS)
    if (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
            or not 0 < seconds <= MAX_RESULT_SECONDS):
        raise ValueError('wait_seconds must be a finite number in (0, 600]')
    return args['request_key'], float(seconds)


def wait_status(key, status, **fields):
    return {'status': status, 'request_key': key, 'backend': 'claude-desktop',
            'completed': status == 'finished', 'idle': status != 'finished',
            'next_action': 'get_result' if status == 'finished' else 'reconcile',
            **fields}


def wait_for_result(state, root, args, cancel=None):
    key, seconds = wait_arguments(args)
    cancel = cancel if cancel is not None else threading.Event()
    started = time.monotonic()
    deadline = started + seconds
    delay = 0.25
    reader = None
    try:
        if cancel.is_set():
            return None
        reader = TaskStore.open_reader(state, root)
        while not cancel.is_set():
            snapshot = reader.observe_result(key)
            if snapshot is None:
                return wait_status(key, 'not_found')
            if snapshot['completed']:
                manifest, _ = receipt_view(snapshot)
                return wait_status(key, 'finished', state='recorded',
                                   result_sha256=manifest['result_sha256'])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return wait_status(key, 'timeout', state='pending',
                                   waited_seconds=time.monotonic() - started)
            cancel.wait(min(delay, remaining))
            delay = min(1.0, delay * 2)
        return None
    except (sqlite3.Error, OSError) as exc:
        raise DesktopRegistryUnavailable('Desktop registry unavailable') from exc
    finally:
        if reader is not None:
            reader.close()


def wait_content(result, ident):
    content = bounded_content(result, ident)
    content['isError'] = False
    return content


class Bridge:
    def __init__(self, root, values, state, daily_limit=8, *, claude_program=None, claude_subscription_only=False, desktop_socket=None, desktop_state=None, allowed_tools=None, ironclaw_values=None, codex_workspace_write=False):
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
        self.owner = 'local:' + str(os.getpid())
        self.lock_path = state/'expert.lock'
        self.db = sqlite3.connect(state/'expert.sqlite', timeout=2)
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_threads(scope TEXT,id TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_calls(scope TEXT,day INTEGER,count INTEGER,PRIMARY KEY(scope,day))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_routes(scope TEXT,id TEXT,model TEXT,effort TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS claude_expert_routes(scope TEXT,id TEXT,program TEXT,model TEXT,effort TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_thread_modes(scope TEXT,id TEXT,mode TEXT NOT NULL,PRIMARY KEY(scope,id))')
        import routing_events
        routing_events.setup(self.db)
        routing_events.seed_local(self.db)
        self.db.commit()
        self.requests = Store(self.db, str(self.root))
        # Canal de retour MCP (get_task/submit_result) : TaskStore durable, ou
        # None si l'état Quick Apply n'est pas initialisable — le pont doit
        # alors refuser l'orchestration plutôt que promettre un aller-retour.
        self.desktop_state = Path(desktop_state) if desktop_state is not None else state_location(self.root, 'quick-state')
        self.desktop_tasks = None
        try:
            from desktop_task_store import TaskStore
            self.desktop_tasks = TaskStore(self.desktop_state, self.root)
        except (OSError, sqlite3.Error, ValueError):
            self.desktop_tasks = None
        # Fonction de niveau module + ressources capturées : une méthode
        # liée retiendrait self (le finalizer ne tournerait qu'à la sortie
        # du processus). _safe_close est idempotent (close après close
        # lève sqlite3.Error, avalé).
        weakref.finalize(self, _close_bridge_databases, self.desktop_tasks, self.db)

    def close(self):
        self.desktop_tasks, tasks = None, self.desktop_tasks
        _close_bridge_databases(tasks, self.db)

    def _ironclaw_configured(self):
        try:
            ironclaw.validate_values(self.ironclaw_values)
        except ValueError:
            return False
        return True

    def _account_key(self, backend):
        """Clé de compte hachée depuis les observations quota (codex seulement,
        source unique quota_knowledge ; sal partagé avec routing_events)."""
        if backend not in ('codex', 'codex-cli', 'codex-server', 'codex-parent'):
            return None
        try:
            import quota_knowledge
            row = self.db.execute("SELECT value FROM quota_identity_meta WHERE key='local_salt'").fetchone()
            if not row:
                return None
            data = quota_knowledge.Registry(self.db, initialize=False).latest(backend, self.values)
        except (ValueError, OSError, KeyError, sqlite3.OperationalError):
            return None
        return (data or {}).get('account_key')

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
                raise WorkspaceWriteNotAuthorized('Project workspace writes not authorized by the integration')
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
        if name == 'wait_result':
            return self.wait_result(args)
        if name == 'reprise_requests':
            return self._lookup_requests(args)
        if name == 'reprise_reconcile':
            return self._reconcile(args)
        if name in ('reprise_backends', 'reprise_health', 'reprise_history', 'reprise_resume'):
            return self._call(name, args)
        if name == 'approval_list':
            return self._approval_list(args)
        if name == 'approval_respond':
            return self._approval_respond(args)
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
            if clean.get('backend') == 'foundation-models':
                validate_key(args.get('request_key'))
                if args.get('parent_request_key') is not None:
                    validate_key(args['parent_request_key'])
            preflight_result = self._call(name, clean, preflight=True)
            if clean.get('backend') == 'foundation-models' and preflight_result is not None:
                known = self.db.execute('SELECT 1 FROM delegation_requests WHERE scope=? AND request_key=?',
                                        (self.requests.scope, args.get('request_key'))).fetchone()
                if not known:
                    return preflight_result
            key = args.get('request_key')
            payload = {'tool': name, **clean}
            payload['file_evidence'] = (file_evidence(self.root, args.get('paths'), args.get('filePaths'))
                                        if mode == 'expertise' else [])
            parent = args.get('parent_request_key')
            if name in ('reprise_followup', 'codex_followup') and parent is None:
                own = self.db.execute('SELECT parent FROM delegation_requests WHERE scope=? AND request_key=?', (self.requests.scope, key)).fetchone()
                row = self.db.execute("SELECT r.request_key FROM delegation_requests r LEFT JOIN delegation_turns t ON r.scope=t.scope AND r.request_key=t.request_key WHERE r.scope=? AND (json_extract(r.result,'$.thread_id')=? OR t.thread_id=?) ORDER BY r.rowid DESC LIMIT 1", (self.requests.scope, args.get('thread_id'), args.get('thread_id'))).fetchone()
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
                completion = (self._desktop_completion(pointer)
                              if existing.get('backend') == 'claude-desktop' and existing.get('admission_only') else None)
                if return_mode == 'receipt':
                    manifest = self.requests.lookup({'request_key': pointer, 'view': 'receipt'})
                    if completion:
                        manifest.update(completion)
                    else:
                        # The lookup status only says the receipt was found, not how the delegation ended.
                        self._tag(manifest, existing)
                    return manifest
                return self._bounded_full(pointer, existing | completion if completion else existing)
            # Exceptions or process exit leave a durable uncertain attempt.
            def attach(event):
                context = {'scope': self.scope, 'backend': clean.get('backend', 'codex'),
                           'mode': mode, 'file_evidence': before_evidence}
                self.requests.attach(key, dict(event, owner=self.owner, context=context))
            try:
                result = self._call(name, clean, on_turn=attach)
            finally:
                self.requests.release(key, self.owner)
            try:
                changed = before_evidence != file_evidence(self.root, args.get('paths'), args.get('filePaths'))
            except (ValueError, OSError):
                changed = True
            if changed and mode == 'implementation':
                result = dict(result, project_files_changed=True)
            elif changed and result.get('backend') == 'foundation-models' and (
                    (result.get('status') == 'apple_input_invalid' and result.get('process_started') is False) or
                    (result.get('status') == 'apple_process_failed' and result.get('process_reaped') is True)):
                # Input changes cannot invalidate proof that no process started or that it was reaped.
                result = dict(result, input_changed=True)
            elif changed:
                result = dict(result, status='needs_review', original_status=result.get('status'),
                              original_reason=result.get('reason'),
                              reason='Input files changed during delegation; result requires reconciliation.', input_changed=True)
            if clean.get('backend') == 'claude-desktop' and result.get('status') == 'finished' \
                    and result.get('response') is None:
                # A claude-desktop submission is only admitted: the MCP round-trip (submit_result) completes it.
                result = dict(result, admission_only=True)
            result = self.requests.finish(key, result)
            if return_mode == 'receipt':
                return self._tag(self.requests.lookup({'request_key': key, 'view': 'receipt'}), result)
            return self._bounded_full(key, result)

    def wait_result(self, args, cancel=None):
        if 'wait_result' not in self.allowed_tools:
            raise ValueError('Tool not enabled for this integration')
        return wait_for_result(self.desktop_state, self.root, args, cancel)

    def _desktop_snapshot(self, key):
        reader = None
        try:
            reader = TaskStore.open_reader(self.desktop_state, self.root)
            return reader.observe_result(key)
        except (sqlite3.Error, OSError) as exc:
            raise DesktopRegistryUnavailable('Desktop registry unavailable') from exc
        finally:
            if reader is not None:
                reader.close()

    def _lookup_requests(self, args):
        view = validate_lookup(args)
        key = args.get('request_key')
        if key is None:
            return self.requests.lookup(args)
        historic = self.requests.lookup({'request_key': key})
        previous = historic.get('result') or {}
        if previous.get('backend') != 'claude-desktop' or not previous.get('admission_only'):
            return self.requests.lookup(args)
        snapshot = self._desktop_snapshot(key)
        if snapshot is None or not snapshot['completed']:
            return self.requests.lookup(args)
        if args.get('acknowledge', False):
            raise ValueError('Acknowledge the Desktop result with get_result on its connector')
        manifest, text = receipt_view(snapshot)
        provenance, _ = receipt_view(historic)
        extra = {'backend': 'claude-desktop', 'source': 'desktop_tasks',
                 'delegation_receipt': provenance, 'completed': True,
                 'next_action': 'get_result'}
        if view == 'full':
            return dict(snapshot, **extra, response=snapshot['result']['answer'],
                        delegation_admission=historic)
        manifest.update(extra)
        if view == 'receipt':
            return manifest
        expected = args.get('result_sha256')
        if expected is not None and expected.lower() != manifest['result_sha256']:
            return dict(manifest, status='result_changed')
        offset, maximum = int(args.get('offset', 0)), int(args.get('max_chars', 6000))
        if offset > len(text):
            return dict(manifest, status='invalid_offset')
        fragment = text[offset:offset + maximum]
        end = offset + len(fragment)
        return dict(manifest, offset=offset, text=fragment,
                    returned_length=len(fragment), has_more=end < len(text),
                    next_offset=end if end < len(text) else None)


    @staticmethod
    def _tag(manifest, result):
        """A receipt manifest reports the delegation status, not the lookup status."""
        manifest['status'] = result['status']
        return manifest

    def _desktop_completion(self, pointer):
        """Project a committed Desktop response without reserving its delivery."""
        snapshot = self._desktop_snapshot(pointer)
        if snapshot is None or not snapshot['completed']:
            return None
        manifest, _ = receipt_view(snapshot)
        return {'status': 'finished', 'response': snapshot['result']['answer'],
                'result_sha256': manifest['result_sha256'], 'completed': True}

    def _bounded_full(self, key, result):
        """Deliver a full-mode result unchanged up to FULL_RESULT_LIMIT, else its durable manifest."""
        if len(json.dumps(result, ensure_ascii=False)) <= FULL_RESULT_LIMIT:
            return result
        manifest = self.requests.lookup({'request_key': key, 'view': 'receipt'})
        if not manifest['available']:
            return result
        return dict(self._tag(manifest, result), return_mode_effective='receipt',
                    reason='Result exceeds the full-mode delivery limit; read it with reprise_requests view=page or delegation_collect.py.')

    def _call(self, name, args, *, preflight=False, on_turn=None):
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
                 **({} if self.codex_workspace_write else {
                     'implementation_unavailable_reason': 'workspace_write_not_authorized',
                     'enable_with': WORKSPACE_WRITE_ENABLE}),
                 'kinds': list(MODES), 'levels': ['standard', 'deep'], 'writes': self.codex_workspace_write},
                {'id': 'foundation-models', 'configured': apple_expert.configured(),
                 'models': ['system'], 'availability': 'unknown', 'inference_verified': False,
                 'billing': 'On-device model; no cloud API cost', 'on_device': True,
                 'modes': ['expertise'], 'writes': False, 'resumable': False,
                 'kinds': list(MODES), 'levels': ['standard'],
                 'context': 'Explicit question and file snapshots only; no tools'},
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
                 'task_return_supported': self.desktop_tasks is not None,
                 'dispatch': 'dispatchOnQuickEntrySubmit (full turn, not a prefill)',
                 'return_channel': 'MCP get_task/submit_result via durable TaskStore; completion only on validated submit_result',
                 'context': 'Structured Quick Apply payload; images/files bounded; target chat routing'},
                {'id': 'ironclaw', 'configured': self._ironclaw_configured(), 'models': [],
                 'reason': None if self._ironclaw_configured() else
                 'Configuration IronClaw requise (--ironclaw-config : host, port, user, key).',
                 'billing': 'Remote IronClaw runtime (paid) — moteur lu via ironclaw.engine()',
                 'kinds': list(MODES), 'levels': ['standard', 'deep'], 'writes': False,
                 'context': 'Experimental CLI retained for audit; use explicit durable ironclaw-submit/poll/result commands.'}],
                'automatic_fallback': False}
        allowed = {'question', 'thread_id'} if name in ('codex_followup', 'reprise_followup') else {'question', 'paths'}
        if name == 'reprise_followup':
            allowed |= {'backend', 'model', 'mode'}
        if name == 'reprise_delegate':
            allowed |= {'kind', 'level', 'backend', 'model', 'chatId', 'filePaths', 'mode'}
        allowed |= set(POLICY_PROPERTIES)
        if (args.keys()-allowed or not isinstance(args.get('question'), str)
                or not args['question'].strip() or len(args['question']) > 12000):
            raise ValueError('Invalid question or arguments')
        mode = self.execution_mode(name, args)
        routing = {}
        backend = args.get('backend', 'codex')
        if backend not in ('codex', 'ironclaw', 'claude-code', 'claude-desktop', 'foundation-models'):
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
            if backend == 'foundation-models' and level != 'standard':
                raise ValueError('Apple local route supports standard level only.')
            routing = {'model': 'gpt-6-astra' if level == 'deep' else 'gpt-6-luna' if kind == 'batch' else 'gpt-6.1-sol',
                       'effort': 'high' if level == 'deep' else 'medium'}
            if backend == 'claude-code':
                routing['model'] = 'opus' if level == 'deep' else 'sonnet'
            elif backend == 'foundation-models':
                routing = {'model': 'system'}
            question = (('Implement the bounded task; apply project changes and run appropriate local checks.'
                         if mode == 'implementation' else MODES[kind]) + '\n\n' + question)
            if len(question) > 12000:
                raise ValueError('Task including delegation contract exceeds limit')
        ident = args.get('thread_id')
        if name == 'reprise_followup' and 'backend' not in args:
            raise ValueError('Explicit followup backend required')
        if name == 'reprise_followup' and backend == 'foundation-models':
            raise ValueError('Apple local expertise has no resumable conversation.')
        if name == 'reprise_followup' and backend == 'ironclaw':
            raise ValueError('Pas de session IronClaw suiveuse ; run -m est sans état.')
        if name == 'reprise_followup' and backend == 'claude-desktop':
            raise ValueError('Pas de session Quick Apply suiveuse ; la soumission est unidirectionnelle.')
        if name == 'reprise_followup' and backend == 'claude-code':
            if not isinstance(ident, str):
                raise ValueError('Owned Claude session required')
            route = self.db.execute('SELECT program,model,effort FROM claude_expert_routes WHERE scope=? AND id=?', (self.scope, ident)).fetchone()
            if not route or route[0] != self.claude_program:
                raise ThreadNotOwned('claude-code', ident)
            routing = dict(zip(('model', 'effort'), route[1:]))
        elif name == 'reprise_followup':
            name = 'codex_followup'
        if name == 'codex_followup' and (not isinstance(ident, str) or not self.db.execute(
                'SELECT 1 FROM expert_threads WHERE scope=? AND id=?', (self.scope, ident)).fetchone()):
            raise ThreadNotOwned('codex', ident)
        if ident and backend == 'codex':
            route = self.db.execute('SELECT model,effort FROM expert_routes WHERE scope=? AND id=?', (self.scope, ident)).fetchone()
            if route:
                routing = dict(zip(('model', 'effort'), route))
        if 'model' in args:
            routing['model'] = args['model']
            if backend != 'foundation-models':
                routing.setdefault('effort', 'medium')
        policy = {}
        for source, target in (('wait_seconds', 'wait_timeout'), ('execution_seconds', 'execution_deadline'),
                               ('inactivity_seconds', 'inactivity')):
            if source in args:
                policy[target] = positive(args[source], source)
        if 'stop_on_deadline' in args:
            if type(args['stop_on_deadline']) is not bool:
                raise ValueError('Boolean stop_on_deadline required')
            policy['stop_on_deadline'] = args['stop_on_deadline']
        if policy.get('stop_on_deadline') and 'execution_deadline' not in policy:
            raise ValueError('execution_seconds is required with stop_on_deadline')
        if policy.get('stop_on_deadline') and (
                policy['execution_deadline'] > policy.get('wait_timeout', 30) or
                policy.get('inactivity', float('inf')) < policy['execution_deadline']):
            raise ValueError('Execution deadline must fit inside the connected wait and inactivity windows')
        if policy and backend not in ('codex', 'claude-desktop'):
            raise ValueError('Wait and execution budgets apply to the Codex backend only')
        if backend == 'claude-desktop' and policy.keys() - {'wait_timeout', 'stop_on_deadline'}:
            raise ValueError('Only wait_seconds applies to the claude-desktop backend')
        if backend == 'ironclaw':
            # Appel explicite uniquement (billing paid, admissibilité automatic=False) ;
            # une configuration absente ou incomplète renvoie un statut actionnable.
            try:
                ironclaw.validate_values(self.ironclaw_values)
            except ValueError as exc:
                return {'status': 'engine_unavailable', 'backend': backend,
                        'reason': f'Configuration IronClaw requise (host, port, user, key) : {exc}',
                        'automatic_fallback': False}
        if backend == 'claude-desktop' and not self.desktop_values:
            return {'status': 'engine_unavailable', 'backend': backend,
                    'reason': 'Quick Apply non configuré sur ce pont ; relancez-le avec --desktop-socket.', 'automatic_fallback': False}
        if backend == 'claude-desktop' and self.desktop_tasks is None:
            return {'status': 'return_channel_unavailable', 'backend': backend,
                    'reason': "Canal de retour MCP indisponible (TaskStore non initialisé) ; aucun aller-retour garanti.",
                    'automatic_fallback': False}
        if backend == 'foundation-models' and not apple_expert.configured():
            return {'status': 'engine_unavailable', 'backend': backend,
                    'reason': 'Apple fm executable unavailable.', 'automatic_fallback': False}
        if backend not in ('claude-desktop', 'foundation-models') and not (self.ironclaw_program if backend == 'ironclaw'
                                                else (self.program if backend == 'codex' else self.claude_program)):
            return {'status': 'engine_unavailable', 'backend': backend, 'reason': 'Requested executable not configured', 'automatic_fallback': False}
        if backend == 'claude-code' and not self.claude_subscription_only:
            return {'status': 'billing_unverified', 'backend': backend, 'reason': 'Operator must confirm paid usage credits disabled before enabling Claude.', 'automatic_fallback': False}
        import routing_events
        # Admissibilité (lot ②) : la route demandée est explicite (consentement
        # porté par l'appel) ; les remplacements automatiques restent gelés, et
        # admissible(automatic=True) n'est jamais appelée ici.
        allowed, billing = routing_events.admissible(self.db, backend, automatic=False)
        if not allowed:
            return {'status': 'admissibility_refused', 'backend': backend, 'billing_class': billing,
                    'reason': f'Route {backend} inadmissible (billing_class={billing}) ; aucune substitution automatique.', 'automatic_fallback': False}
        if preflight:
            return
        # Journal additif de la décision de routage (contrat §7.5 révisé §7.6) ;
        # aucune substitution n'est produite : le type routing_substituted reste réservé.
        import routing_events
        requested = {}
        if isinstance(args.get('model'), str):
            requested['model'] = args['model']
        elif name == 'reprise_followup' and backend == 'claude-code' and routing:
            requested = dict(routing)
        elif ident and backend == 'codex' and self.db.execute(
                'SELECT model,effort FROM expert_routes WHERE scope=? AND id=?', (self.scope, ident)).fetchone():
            requested = dict(routing)
        journal_type = 'explicit' if requested else 'routed'
        served = {k: routing[k] for k in routing} if routing else ({'backend': backend} if backend != 'codex' else None)
        if journal_type == 'explicit':
            served = dict(routing) if routing else served
        event_id = routing_events.record(self.db, journal_type, name, backend,
                                         requested=requested or None, served=served,
                                         level=args.get('level'), request_key=args.get('request_key'),
                                         thread_id=ident,
                                         account_key=self._account_key(backend),
                                         admissibility='granted')
        if backend == 'foundation-models':
            result = apple_expert.review(self.root, question, paths=args.get('paths'))
            routing_events.finalize(self.db, event_id, str(result.get('status') or 'unknown'))
            return result
        if backend == 'ironclaw':
            result = ironclaw.review(self.ironclaw_program, self.ironclaw_values, self.root, question,
                                     kind=args.get('kind', 'expertise'), level=args.get('level', 'standard'),
                                     paths=args.get('paths'), timeout=300)
            routing_events.finalize(self.db, event_id, str(result.get('status') or 'unknown'))
            return result | {'backend': backend, 'automatic_fallback': False}
        if backend == 'claude-desktop':
            max_wait = min(max(policy.get('wait_timeout', 0), 0), 600) if policy else 0
            result = claude_desktop.review(self.root, question, kind=args.get('kind', 'expertise'),
                                          level=args.get('level', 'standard'), paths=args.get('paths'),
                                          chat_id=args.get('chatId'), file_paths=args.get('filePaths'),
                                          program=self.desktop_program, values=self.desktop_values,
                                          request_key=self.requests.active_key, tasks=self.desktop_tasks,
                                          max_wait=max_wait)
            routing_events.finalize(self.db, event_id, str(result.get('status') or 'unknown'))
            return result | {'backend': backend, 'automatic_fallback': False}
        if backend == 'claude-code':
            result = claude_review(self.claude_program, self.root, question, paths=args.get('paths'),
                                   thread_id=ident, subscription_only=self.claude_subscription_only, **routing)
            if result.get('status') == 'finished' and result.get('resumable') is True:
                with self.db:
                    self.db.execute('INSERT OR REPLACE INTO claude_expert_routes VALUES (?,?,?,?,?)',
                                    (self.scope, result['thread_id'], self.claude_program, result['model'], routing['effort']))
            routing_events.finalize(self.db, event_id, str(result.get('status') or 'unknown'))
            return result | {'backend': backend, 'automatic_fallback': False}
        def persist_turn(event):
            if on_turn:
                on_turn(event)
            if event.get('thread_id'):
                with self.db:
                    self.db.execute('INSERT OR IGNORE INTO expert_threads VALUES (?,?)',
                                    (self.scope, event['thread_id']))
                    self.db.execute('INSERT OR IGNORE INTO expert_thread_modes VALUES (?,?,?)',
                                    (self.scope, event['thread_id'], mode))
                    if routing:
                        self.db.execute('INSERT OR REPLACE INTO expert_routes VALUES (?,?,?,?)',
                                        (self.scope, event['thread_id'], routing['model'], routing['effort']))
            approval = event.get('approval')
            if approval:
                # Durable record of a native approval request. Storage failure must not
                # corrupt the wait loop's outcome; the receipt still carries pending_request.
                try:
                    if self.desktop_tasks is not None:
                        self.desktop_tasks.record_approval(
                            approval['approval_id'], approval['method'], rpc_id=approval.get('rpc_id'),
                            thread_id=approval.get('thread_id'), turn_id=approval.get('turn_id'),
                            params=approval.get('params'), requested_at=approval.get('requested_at'),
                            sandbox=approval.get('sandbox'))
                except (OSError, ValueError, sqlite3.Error):
                    pass
        result = review(self.program, self.values, self.root, question, paths=args.get('paths'), thread_id=ident,
                        on_turn=persist_turn, **policy,
                        **({'implementation': True} if mode == 'implementation' else {}), **routing)
        result['mode'] = mode
        result.update(backend=backend, automatic_fallback=False)
        resumable = result.get('status') in ('finished', 'interrupted', 'blocked_permission',
                                             'running', 'awaiting_approval') or (
            result.get('status') == 'needs_review' and result.get('turn_id'))
        if result.get('thread_id') and result.get('resumable') is True and resumable:
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO expert_threads VALUES (?,?)', (self.scope, result['thread_id']))
                self.db.execute('INSERT OR IGNORE INTO expert_thread_modes VALUES (?,?,?)',
                                (self.scope, result['thread_id'], mode))
                if routing:
                    self.db.execute('INSERT OR REPLACE INTO expert_routes VALUES (?,?,?,?)',
                                    (self.scope, result['thread_id'], routing['model'], routing['effort']))
        routing_events.finalize(self.db, event_id, str(result.get('status') or 'unknown'))
        return result

    class ApprovalReattachFailed(Unavailable):
        """Reattach to the owning Codex thread failed; the approval stays pending."""

    def _approval_list(self, args):
        if not isinstance(args, dict) or args.keys() - {'limit'}:
            raise ValueError('Invalid arguments')
        limit = args.get('limit', 50)
        if self.desktop_tasks is None:
            raise Unavailable('storage_error', 'Approval store unavailable; no approvals can be surfaced.')
        approvals = self.desktop_tasks.list_approvals(limit=limit)
        return {'status': 'finished', 'automatic_fallback': False, 'approvals': approvals}

    def _approval_respond(self, args, client_factory=None):
        if not isinstance(args, dict) or set(args) != {'approval_id', 'decision'}:
            raise ValueError('approval_id and an explicit decision are required')
        approval_id, decision = args['approval_id'], args['decision']
        if decision not in ('approved', 'denied'):
            raise ValueError('Decision must be approved or denied')
        if self.desktop_tasks is None:
            raise Unavailable('storage_error', 'Approval store unavailable; approvals cannot be answered.')
        approval = self.desktop_tasks.pending_approval(approval_id)
        if approval is None:
            raise ValueError('Unknown or already resolved approval_id')
        rpc_id = approval.get('rpc_id')
        if rpc_id is None or not approval.get('thread_id'):
            raise ValueError('Stored approval lacks its JSON-RPC identity; cannot respond')
        if str(rpc_id).lstrip('-').isdigit():
            rpc_id = int(rpc_id)
        client = None
        try:
            try:
                client = (client_factory or Client)(self.program, self.values)
                client.call('initialize', {'clientInfo': {'name': 'reprise-approval-respond', 'version': '0.1.0'},
                                           'capabilities': {'experimentalApi': True}})
                client.send({'method': 'initialized'})
                resumed = client.call('thread/resume', {'threadId': approval['thread_id'],
                    'excludeTurns': True, 'config': review_config(client, self.root),
                    'sandbox': approval.get('sandbox') or 'read-only',
                    'approvalPolicy': 'on-request'})
                confirmed = resumed.get('thread') or {}
                if (confirmed.get('id') != approval['thread_id']
                        or (confirmed.get('status') or {}).get('type') != 'idle'):
                    raise Unavailable('blocked_owner', 'Expert identity or idle state changed; approval not sent.')
            except (OSError, ValueError, TimeoutError, Unavailable) as error:
                raise self.ApprovalReattachFailed('owner_unavailable',
                    f'Codex thread reattach failed; approval stays pending. {error}') from error
            try:
                # The native request expects a ReviewDecision as its result. The
                # schema (v0.166.0-alpha.6) wraps it: result = {'decision': …}.
                # Vocabulary depends on the request generation: v2 methods
                # (item/*/requestApproval) use accept/decline; legacy v1
                # (execCommandApproval/applyPatchApproval) use approved/denied,
                # with denied as an object carrying a rejection reason.
                method = approval.get('method') or ''
                if method.startswith('item/'):
                    native_decision = 'accept' if decision == 'approved' else 'decline'
                elif decision == 'approved':
                    native_decision = 'approved'
                else:
                    native_decision = {'denied': {'rejection': 'Denied by operator'}}
                client.send({'id': rpc_id, 'result': {'decision': native_decision}})
            except (OSError, ValueError, TimeoutError, Unavailable) as error:
                raise self.ApprovalReattachFailed('owner_unavailable',
                    f'Sending the approval response failed; approval stays pending. {error}') from error
        finally:
            if client:
                try:
                    client.close()
                except (OSError, ValueError):
                    pass
        resolved = self.desktop_tasks.resolve_approval(approval_id, decision)
        return {'status': 'finished', 'automatic_fallback': False, 'approval_id': approval_id,
                'decision': decision, 'resolved': resolved}

    def _reconcile(self, args):
        if not isinstance(args, dict) or args.keys() - {'request_key', 'action', 'reason'}:
            raise ValueError('Invalid reconciliation arguments')
        key = args.get('request_key')
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise ValueError('Persist a nonempty request_key (at most 128 characters)')
        action = args.get('action', 'observe')
        if action not in ('observe', 'stop'):
            raise ValueError('Unknown reconciliation action')
        if 'reason' in args and args['reason'] not in STOP_REASONS:
            raise ValueError('Explicit authorized stop reason required')
        if action == 'stop' and args.get('reason') not in STOP_REASONS:
            raise ValueError('Explicit authorized stop reason required')
        with self.lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy', 'request_key': key,
                        'reason': 'An active client owns the state; no concurrent reconciliation.'}
            receipt = self.requests.lookup({'request_key': key})
            if receipt.get('status') == 'not_found':
                raise ValueError('Unknown request_key')
            native = self.requests.phase(key)
            previous = receipt.get('result') or {}
            if native is None and previous.get('thread_id') and previous.get('turn_id'):
                # Upgrade only an exact legacy identity owned by this configured route.
                if previous.get('backend', 'codex') != 'codex' or not self.db.execute(
                        'SELECT 1 FROM expert_threads WHERE scope=? AND id=?',
                        (self.scope, previous['thread_id'])).fetchone():
                    raise ValueError('Legacy native thread is not owned by this route')
                self.requests.attach(key, {'phase': 'unknown', 'thread_id': previous['thread_id'],
                                           'turn_id': previous['turn_id'],
                                           'context': {'scope': self.scope, 'backend': 'codex', 'legacy': True}})
                native = self.requests.phase(key)
            if native and native['context'].get('scope') != self.scope:
                raise ValueError('Native turn belongs to another configured route')
            claim = self.requests.claim(key, self.owner, stale_after=120.0)
            if claim == 'held':
                return {'status': 'busy', 'request_key': key,
                        'reason': 'Another live owner holds this request; reconcile later without a new turn.'}
            try:
                result = reconcile_turn(self.requests, self.root, key, action=action, reason=args.get('reason'),
                                        program=self.program, values=self.values)
                return self._bounded_full(key, dict(result, claim=claim, automatic_fallback=False))
            finally:
                self.requests.release(key, self.owner)

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
                content = bounded_content(result, message.get('id'))
                if params.get('name') == 'wait_result':
                    content['isError'] = False
                return content
            return tool_content(result)
        raise ValueError('Unsupported MCP method')


def serve(bridge, source, output):
    """Serve one connection; only wait_result runs on bounded reader workers."""
    active = {}
    workers = set()
    active_lock = threading.Lock()
    send_lock = threading.Lock()
    closed = threading.Event()

    def send(payload):
        with send_lock:
            if not closed.is_set():
                try:
                    output.write(json.dumps(payload, ensure_ascii=False) + '\n')
                    output.flush()
                except OSError:
                    closed.set()
                    with active_lock:
                        for cancel, _ in active.values():
                            cancel.set()
                    raise

    def worker(ident, key, arguments, cancel):
        try:
            result = bridge.wait_result(arguments, cancel)
            if result is not None and not cancel.is_set():
                send({'jsonrpc': '2.0', 'id': ident,
                      'result': wait_content(result, ident)})
        except Exception as exc:
            if not cancel.is_set():
                try:
                    send({'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)})
                except OSError:
                    pass
        finally:
            with active_lock:
                if active.get(key, (None,))[0] is cancel:
                    active.pop(key, None)

    try:
        while True:
            raw = source.readline(1024 * 1024 + 1)
            if not raw or len(raw) > 1024 * 1024:
                break
            ident = None
            message = None
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('Object required')
                if 'id' not in message:
                    params = message.get('params')
                    if message.get('method') == 'notifications/cancelled' and isinstance(params, dict):
                        target = params.get('requestId')
                        if isinstance(target, bool) or not isinstance(target, (str, int, float)):
                            continue
                        try:
                            key = json.dumps(target, ensure_ascii=False, allow_nan=False)
                        except ValueError:
                            continue
                        with active_lock:
                            current = active.get(key)
                            if current is not None:
                                current[0].set()
                    continue
                ident = message['id']
                if isinstance(ident, bool) or not isinstance(ident, (str, int, float)):
                    ident = None
                    raise ValueError('String or numeric JSON-RPC id required')
                try:
                    key = json.dumps(ident, ensure_ascii=False, allow_nan=False)
                except ValueError:
                    ident = None
                    raise
                with active_lock:
                    duplicate = key in active
                if duplicate:
                    send({'jsonrpc': '2.0', 'id': None,
                          'error': {'code': -32600, 'message': 'Duplicate in-flight JSON-RPC id'}})
                    continue
                params = message.get('params') or {}
                if not isinstance(params, dict):
                    raise ValueError('Object params required')
                if message.get('method') == 'tools/call' and params.get('name') == 'wait_result':
                    if 'wait_result' not in bridge.allowed_tools:
                        raise ValueError('Tool not enabled for this integration')
                    arguments = params.get('arguments', {})
                    request_key, _ = wait_arguments(arguments)
                    if len(json.dumps(ident, ensure_ascii=False)) > 256:
                        raise ValueError('Compact calls require a bounded JSON-RPC id')
                    cancel = threading.Event()
                    thread = threading.Thread(target=worker,
                        args=(ident, key, arguments, cancel), daemon=True)
                    with active_lock:
                        workers = {item for item in workers if item.is_alive()}
                        full = len(active) >= MAX_RESULT_WAITS
                        if not full:
                            active[key] = (cancel, thread)
                            workers.add(thread)
                    if full:
                        send({'jsonrpc': '2.0', 'id': ident, 'result': wait_content(
                            wait_status(request_key, 'busy'), ident)})
                    else:
                        try:
                            thread.start()
                        except RuntimeError:
                            with active_lock:
                                active.pop(key, None)
                                workers.discard(thread)
                            raise
                    continue
                send({'jsonrpc': '2.0', 'id': ident, 'result': bridge.dispatch(message)})
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error, RuntimeError) as exc:
                if isinstance(message, dict) and compact_call(message) and len(json.dumps(ident, ensure_ascii=False)) > 256:
                    ident = None
                send({'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)})
    finally:
        closed.set()
        with active_lock:
            for cancel, _ in active.values():
                cancel.set()
        deadline = time.monotonic() + 1.0
        for thread in workers:
            if thread.ident is not None:
                thread.join(max(0.0, deadline - time.monotonic()))


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
        serve(bridge, sys.stdin.buffer, sys.stdout)
    finally:
        bridge.db.close()
        if bridge.desktop_tasks is not None:
            bridge.desktop_tasks.close()
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == '__main__':
    main()
