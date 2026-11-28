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
import delegation_notifications
from delegation_reconcile import reconcile as reconcile_turn
from codex_expert import STOP_REASONS, positive, review, review_config
from codex_rpc import Client
from claude_expert import review as claude_review
import claude_desktop
import chatgpt_quick
import metaai_taskstore
import apple_expert
import ironclaw
import reprise_logs
import reprise_health
import shutil
import mcp_transport
from mcp_transport import StdioHandler
from datetime import datetime, timezone
from engines import Unavailable, executable, options


class RequestNotFound(ValueError):
    """An exact request_key does not exist in this bridge scope."""


BUDGET_ARGUMENTS = {'wait_timeout': 'wait_seconds', 'execution_deadline': 'execution_seconds',
                    'inactivity': 'inactivity_seconds', 'stop_on_deadline': 'stop_on_deadline'}
BACKEND_BUDGETS = {'codex': ('wait_seconds', 'execution_seconds', 'inactivity_seconds', 'stop_on_deadline'),
                   'claude-desktop': ('wait_seconds', 'stop_on_deadline'),
                   'metaai': ('wait_seconds',), 'chatgpt': ('wait_seconds',)}


class BudgetNotSupported(ValueError):
    """Budget refused for this backend, named for the caller (issue #279)."""

    def __init__(self, backend, budgets, allowed):
        self.backend, self.budgets, self.allowed = backend, sorted(budgets), list(allowed)
        accepted = ', '.join(self.allowed) if self.allowed else 'none'
        super().__init__(
            f'Budget not supported for backend {backend}: {", ".join(self.budgets)}. '
            f'Accepted budgets: {accepted}. Resubmit without them; nothing was admitted.')


class ThreadNotOwned(ValueError):
    """Followup refused: this bridge never created the requested thread (issue #100)."""

    def __init__(self, backend, thread_id):
        self.backend, self.thread_id = backend, thread_id
        super().__init__(
            f'Followup refused: this bridge does not own that {backend} thread (thread_id={thread_id}). '
            'To resume it, reissue the call with the parent_request_key recorded when the turn was '
            'emitted, or open a new session with reprise_delegate.')


METAAI_RETIRED_KEYS = ('dav_root',)


def metaai_retired_keys(values):
    """Clés de connexion Meta AI de l'ère WebDAV, refusées depuis #276."""
    return [key for key in METAAI_RETIRED_KEYS if key in values]


def metaai_bridge_root(values):
    """Répertoire pont du canal metaai (clé bridge_root) ; None si absent ou vide."""
    value = values.get('bridge_root')
    return value if isinstance(value, str) and value.strip() else None


def error_payload(exc):
    detail = f'{type(exc).__name__}: {exc}'[:200]
    kind, code = 'refused', -32602
    if isinstance(exc, DesktopRegistryUnavailable):
        return {'code': -32000, 'message': 'Desktop registry unavailable; no task created.',
                'data': {'kind': 'desktop_registry_unavailable'}}
    if isinstance(exc, RequestNotFound):
        return {'code': -32004, 'message': 'Request key not found in this bridge scope; no automatic retry.',
                'data': {'kind': 'not_found', 'error': detail}}
    if isinstance(exc, ThreadNotOwned):
        # Issue #100: name the reason and the resume path instead of the generic refusal.
        return {'code': code,
                'message': str(exc),
                'data': {'kind': 'thread_not_owned', 'backend': exc.backend, 'thread_id': exc.thread_id,
                         'resume_with': 'parent_request_key',
                         'alternative': 'open a new session with reprise_delegate', 'error': detail}}
    if isinstance(exc, BudgetNotSupported):
        return {'code': code,
                'message': str(exc),
                'data': {'kind': 'budget_not_supported', 'backend': exc.backend, 'budgets': exc.budgets,
                         'allowed': exc.allowed, 'error': detail}}
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
    messages = {
        'busy': 'Bridge busy: one active request per state directory; no automatic retry.',
        'unavailable': 'Requested backend unavailable; no automatic fallback or retry.',
        'refused': 'Request refused or invalid; no automatic retry.',
        'storage_error': 'Bridge storage unavailable; no automatic retry.',
    }
    # Preserve the original top-level message for existing MCP clients; structured
    # consumers use data.kind / reason_code, humans may inspect human_reason.
    return {'code': code, 'message': 'Request refused or unavailable; no automatic retry.',
            'data': {'kind': kind, 'reason_code': 'bridge_busy' if kind == 'busy' else kind,
                     'human_reason': messages[kind], 'error': detail}}

BACKEND_MODELS = {
    # Déprécié (#253) : la lineup codex vit uniquement dans le catalogue
    # dynamique (live ou last-known persisté). Cette clé n'est plus lue par
    # le code de production — elle n'est conservée que pour les imports des
    # tests existants (test_expert_active_turns, test_delegation_persistence),
    # PAS comme catalogue.
    'codex': ('gpt-6.2-sol', 'gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra', 'gpt-5.6-terra'),
    'claude-code': ('sonnet', 'opus'),
    'ironclaw': (),
    'claude-desktop': (),
    'chatgpt': (),
    'metaai': (),
    'foundation-models': ('system',),
}
# Champ `writes` de reprise_backends (#258 lot 3, directive opérateur) :
# capacité DÉCLARÉE d'écrire dans un espace de travail pendant une délégation,
# constante unique par backend — jamais un grant CLI. Les grants CLI gardent
# leur rôle propre (déblocage du mode implementation codex, dispatch ChatGPT)
# et restent annoncés par enable_with ; ils ne nourrissent plus ce champ.
WORKSPACE_WRITE_BACKENDS = frozenset(('codex', 'chatgpt', 'claude-desktop', 'metaai'))


def workspace_writes(backend):
    return backend in WORKSPACE_WRITE_BACKENDS


RETURN_MODES = {'type': 'string', 'enum': ['full', 'receipt'], 'default': 'full'}
# Catalogue de modèles Codex dynamique (#242, #253) : préférences ordonnées
# résolues contre le catalogue (live ou last-known) — ce sont des préférences
# de routage, PAS un catalogue de modèles ; aucune substitution silencieuse
# hors catalogue.
CODEX_ROUTE_PREFERENCES = {
    'deep': ('gpt-6-astra', 'gpt-5.6-terra', 'gpt-6.1-sol'),
    'batch': ('gpt-6-luna', 'gpt-5.6-terra', 'gpt-6.1-sol'),
    'default': ('gpt-6.1-sol', 'gpt-6-luna', 'gpt-5.6-terra'),
}
CODEX_CATALOG_TTL = 30.0
CODEX_CATALOG_VERSION = 1
# Persistance last-known-good du catalogue codex (#253), même convention que
# MANIFEST_PATH d'app_clones.py ; écriture atomique à la sauvegarde live.
CATALOG_PATH = Path.home() / 'Library/Application Support/Reprise/codex-models.json'
CODEX_EFFORT_PREFERENCES = {'deep': 'high', 'batch': 'medium', 'default': 'medium'}
# Revue (MINEUR S6) : ordre canonique des efforts codex — « le plus bas » se
# juge sur cet ordre, pas sur l'ordre lexicographique ('high' < 'medium' en ASCII).
CODEX_EFFORT_ORDER = {'minimal': 0, 'low': 1, 'medium': 2, 'high': 3, 'xhigh': 4}
INFERENCE_TOOLS = ('reprise_delegate', 'reprise_followup', 'codex_review', 'codex_followup')
# Manifeste expert déclaratif (#258 lot 2) : le manifeste décrit les backends
# (capacité, resumable, sémantique de session) ; la disponibilité, elle, se
# mesure (programmes, sockets, TaskStores). Erreur de schéma/type = échec de
# construction du bridge ; backend déclaré sans handler de dispatch = démarré
# quand même, marqué unavailable (jamais routé vers un autre backend).
EXPERTS_MANIFEST_VERSION = 1
EXPERTS_MANIFEST_PATH = Path(__file__).resolve().parent / 'experts-manifest.json'
EXPERTS_SESSION_MODES = ('stdio_thread', 'durable_taskstore', 'push_only', 'window_continuation')
EXPERTS_HANDLED_BACKENDS = ('codex', 'ironclaw', 'claude-code', 'claude-desktop',
                            'foundation-models', 'chatgpt', 'metaai')


def _read_manifest(path=None):
    """experts-manifest.json est livré dans le bundle : son absence est une installation incomplète."""
    manifest_path = Path(path) if path is not None else EXPERTS_MANIFEST_PATH
    try:
        with open(manifest_path, encoding='utf-8') as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise ValueError(f'experts-manifest.json absent ({manifest_path}) : bundle incomplet, reconstruire Reprise.') from None


def load_experts_manifest(path=None):
    """Charge experts-manifest.json avec validation stricte ; absent ou invalide :
    ValueError, le bridge refuse de démarrer."""
    document = _read_manifest(path)
    if not isinstance(document, dict) or document.get('schema_version') != EXPERTS_MANIFEST_VERSION:
        raise ValueError(f'experts manifest requires schema_version {EXPERTS_MANIFEST_VERSION}')
    backends = document.get('backends')
    if not isinstance(backends, dict) or not backends:
        raise ValueError('experts manifest requires a nonempty backends object')
    if 'automatic_fallback' in document and type(document['automatic_fallback']) is not bool:
        raise ValueError('automatic_fallback must be a boolean')
    validated = {}
    for backend, entry in backends.items():
        if not isinstance(backend, str) or not backend or not isinstance(entry, dict):
            raise ValueError(f'invalid manifest entry for backend {backend!r}')
        unknown = entry.keys() - {'enabled', 'capacity', 'resumable', 'session'}
        if unknown:
            raise ValueError(f'unknown manifest fields for {backend}: {sorted(unknown)}')
        capacity = entry.get('capacity', 1)
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError(f'capacity must be a positive integer for {backend}')
        session = entry.get('session')
        if session not in EXPERTS_SESSION_MODES:
            raise ValueError(f'session must be one of {EXPERTS_SESSION_MODES} for {backend}')
        validated[backend] = {'enabled': bool(entry.get('enabled', True)),
                              'capacity': capacity,
                              'resumable': bool(entry.get('resumable', False)),
                              'session': session}
    return validated

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
    'action': {'type': 'string', 'enum': ['observe', 'stop', 'release_undispatched'], 'default': 'observe'},
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
         'backend': {'type': 'string', 'enum': ['codex', 'ironclaw', 'claude-code', 'claude-desktop', 'foundation-models', 'chatgpt', 'metaai']},
         'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}},
         'chatId': {'type': 'string'},
         'filePaths': {'type': 'array', 'maxItems': 10, 'items': {'type': 'string'},
                       'description': 'claude-desktop attachments: project files only (relative to the project root, or absolute under it).'}},
         'required': ['question', 'kind', 'request_key']}},
    {'name': 'reprise_backends', 'description': 'List configured delegation capabilities without inference. Availability is not a price or quota guarantee.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'reprise_experts_list', 'description': 'List expert backends from the declarative manifest (declaration) side by side with measured availability (handlers, programs, TaskStores). Declaration is not availability; read-only, no inference.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'reprise_notifications', 'description': 'Read, wait for or acknowledge durable delegation notifications (lot C, issue #237). read/wait never consume and never mark; ack is idempotent per (event_id, consumer_id); after a wait, events are re-read (at-least-once). No redispatch logic here: redispatching is decided by the caller, never by this tool.',
     'inputSchema': {'type': 'object', 'properties': {
         'action': {'type': 'string', 'enum': ['read', 'wait', 'ack']},
         'consumer_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
         'request_keys': {'type': 'array', 'maxItems': 100, 'items': {'type': 'string', 'minLength': 1, 'maxLength': 128}},
         'event_ids': {'type': 'array', 'maxItems': 100, 'items': {'type': 'string', 'minLength': 1}},
         'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100},
         'timeout_ms': {'type': 'number', 'minimum': 0, 'maximum': 600000}},
         'required': ['action', 'consumer_id'], 'additionalProperties': False}},
    {'name': 'codex_review', 'description': 'Ask Codex for a read-only project review. Uses ChatGPT quota. No automatic retries or approval. Returns its answer and session ID.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | POLICY_PROPERTIES | {'paths': {'type': 'array', 'maxItems': 30, 'items': {'type': 'string'}}}}},
    {'name': 'codex_followup', 'description': 'Continue a review created by this project bridge; preserves its Codex conversation.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | POLICY_PROPERTIES | {'thread_id': {'type': 'string'}}, 'required': ['question', 'thread_id', 'request_key']}},
    {'name': 'reprise_followup', 'description': 'Continue an owned project session and inherit its execution mode. Omitted model preserves the thread route; explicit model overrides it, preserving effort. No fallback.',
     'inputSchema': SCHEMA | {'properties': SCHEMA['properties'] | POLICY_PROPERTIES | {
         'model': MODEL_SCHEMA, 'mode': MODE_SCHEMA,
         'thread_id': {'type': 'string'}, 'backend': {'type': 'string', 'enum': ['codex', 'ironclaw', 'claude-code', 'claude-desktop', 'foundation-models', 'chatgpt', 'metaai']}},
         'required': ['question', 'thread_id', 'backend', 'request_key']}},
    {'name': 'reprise_resume', 'description': 'List interrupted tasks reconstructed from local logs that can be retried (quota, guard or missing dispatch). Read-only; no automatic retry.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'reprise_health', 'description': 'Post-update health: versions, whether supervised servers need relaunch, whether watched log signatures are still present, human-required actions, plus an on-device Foundation Models explanation. Read-only.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'reprise_history', 'description': 'Explain recent harness work and its Reprise interactions from local logs (Quick Entry submissions + MCP calls). Read-only.',
     'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'additionalProperties': False}},
    {'name': 'approval_list', 'description': 'List native Codex approval requests waiting for a human decision. Read-only; never decides, never resumes anything.',
     'inputSchema': {'type': 'object', 'properties': {'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, 'additionalProperties': False}},
    {'name': 'approval_respond', 'description': 'Disabled since #117 T2a: operator decision is required. Returns a typed refusal and the approval request stays pending. Will be replaced by a token-gated delegation path in #117 T2b.',
     'inputSchema': {'type': 'object', 'properties': {'approval_id': {'type': 'string', 'maxLength': 128},
                     'decision': {'type': 'string', 'enum': ['approved', 'denied']}},
                     'required': ['approval_id', 'decision'], 'additionalProperties': False}},
]

MODES = {
    'expertise': 'Analyze the conceptual problem. Return the algorithm, invariants, tradeoffs and a compact implementation contract. Distinguish evidence from assumptions.',
    'review': 'Review for correctness, security and maintainability. Return only actionable findings with severity, file references, evidence and verification steps; say when no finding is established.',
    'batch': 'Produce the requested bounded batch of code, tests or transformations as text or unified patches. Do not apply changes. Preserve existing contracts; include deterministic validation commands and report any unfinished items.',
}


META_AI_RETURN_CONTRACT = (
    'CONTRAT DE RETOUR (obligatoire) : pour tout changement de fichier, réponds '
    'uniquement par blocs\n'
    'FICHIER: <chemin relatif du dépôt>\n'
    'CHERCHER (ligne exacte, unique dans le fichier):\n'
    '<copie exacte des lignes actuelles à remplacer>\n'
    'REMPLACER PAR:\n'
    '<texte complet de remplacement, copiable tel quel>\n'
    'Une fonction touchée = la fonction entière réécrite en CHERCHER/REMPLACER. '
    'NE JAMAIS écrire de diff unifié (compteurs de hunk faux). Ne paraphrase pas.')

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


def _close_bridge_databases(desktop_tasks, db, chatgpt_tasks=None, metaai_tasks=None):
    """Niveau module : référencé par weakref.finalize sans retenir self."""
    if desktop_tasks is not None:
        TaskStore._safe_close(desktop_tasks.db)
    if chatgpt_tasks is not None:
        TaskStore._safe_close(chatgpt_tasks.db)
    if metaai_tasks is not None:
        try:
            metaai_tasks._conn.close()
        except sqlite3.Error:
            pass
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


def _ledger_backend(db_path, scope, key):
    """Backend enregistré d'un reçu, lu depuis une connexion read-only dédiée
    (revue MAJEUR 6) : le worker de wait_result tourne hors du thread
    principal et la connexion partagée du Bridge (check_same_thread=True)
    refuserait sqlite3.ProgrammingError. Même motif que TaskStore.open_reader :
    connexion mode=ro courte, fermée avant retour. Retourne None si absent ou
    illisible (routage par défaut claude-desktop conservé)."""
    validate_key(key)
    try:
        db = sqlite3.connect(Path(db_path).as_uri() + '?mode=ro', timeout=0,
                             isolation_level=None, uri=True)
    except sqlite3.Error:
        return None
    try:
        row = db.execute('SELECT result FROM delegation_requests WHERE scope=? AND request_key=?',
                         (scope, key)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    if not row or not row[0]:
        return None
    try:
        backend = (json.loads(row[0]) or {}).get('backend')
    except ValueError:
        return None
    return backend if isinstance(backend, str) else None


def wait_status(key, status, backend='claude-desktop', **fields):
    return {'status': status, 'request_key': key, 'backend': backend,
            'completed': status == 'finished', 'idle': status != 'finished',
            'next_action': 'get_result' if status == 'finished' else 'reconcile',
            **fields}


def wait_for_result(state, root, args, cancel=None, backend='claude-desktop'):
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
                return wait_status(key, 'not_found', backend=backend)
            if snapshot['completed']:
                manifest, _ = receipt_view(snapshot)
                return wait_status(key, 'finished', backend=backend, state='recorded',
                                   result_sha256=manifest['result_sha256'])
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return wait_status(key, 'timeout', backend=backend, state='pending',
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


def load_default_chain(path=None):
    """Charge le champ optionnel `default_chain` (liste ordonnée d'ids de
    backends) du manifeste. Champ absent → [] ; ids invalides, non uniques ou
    inconnus des backends déclarés → ValueError."""
    document = _read_manifest(path)
    chain = document.get('default_chain')
    if chain is None:
        return []
    if not isinstance(chain, list) or not chain or not all(isinstance(b, str) and b for b in chain):
        raise ValueError('default_chain must be a nonempty list of backend ids')
    if len(set(chain)) != len(chain):
        raise ValueError('default_chain must not repeat a backend')
    declared = set(document.get('backends') or {})
    unknown = [b for b in chain if b not in declared]
    if unknown:
        raise ValueError(f'default_chain references unknown backends: {unknown}')
    return list(chain)


def load_fallback_flag(path=None):
    """Charge le champ optionnel `automatic_fallback` (bool) du manifeste.
    Champ absent → False ; type non-bool → ValueError (fail-fast, même contrat
    que le manifeste)."""
    document = _read_manifest(path)
    flag = document.get('automatic_fallback')
    if flag is None:
        return False
    if type(flag) is not bool:
        raise ValueError('automatic_fallback must be a boolean')
    return flag


def project_file_paths(root, names):
    """Pièces jointes Claude Desktop (#297) : fichiers du projet seulement.

    Relatif à la racine (comme `paths`) ou absolu sous elle ; rendus absolus et résolus.
    La forme est vérifiée avant toute résolution : un chemin hors projet est refusé
    sans révéler s'il existe.
    """
    if not isinstance(names, list) or len(names) > 10:
        raise ValueError('filePaths : liste de 10 chemins au plus requise')
    root = Path(root).resolve(strict=True)
    refusal = 'filePaths : fichier du projet requis (chemin relatif à la racine, ou absolu sous elle) ; copiez le fichier dans le projet.'
    resolved = []
    for name in names:
        if not isinstance(name, str) or not name or '\0' in name:
            raise ValueError(refusal)
        candidate = Path(os.path.normpath(name if os.path.isabs(name) else os.path.join(root, name)))
        if not candidate.is_relative_to(root):
            raise ValueError(refusal)
        try:
            target = candidate.resolve(strict=True)
        except OSError:
            raise ValueError('filePaths : fichier introuvable dans le projet')
        if not target.is_relative_to(root):
            raise ValueError(refusal)
        resolved.append(str(target))
    return resolved


class Bridge:
    def __init__(self, root, values, state, *, claude_program=None, claude_subscription_only=False, desktop_socket=None, desktop_state=None, allowed_tools=None, ironclaw_values=None, chatgpt_state=None, chatgpt_dispatch=False, metaai_values=None, automatic_fallback_enabled=None):
        if automatic_fallback_enabled is not None and type(automatic_fallback_enabled) is not bool:
            raise ValueError('Explicit automatic-fallback grant required')
        if type(chatgpt_dispatch) is not bool:
            raise ValueError('Explicit ChatGPT dispatch grant required')
        if chatgpt_state is not None and (not isinstance(chatgpt_state, str) or not Path(chatgpt_state).is_absolute()):
            raise ValueError('Absolute ChatGPT state directory required')
        self.chatgpt_configured = chatgpt_state is not None
        self.chatgpt_dispatch = chatgpt_dispatch
        known_tools = {item['name'] for item in TOOLS}
        if allowed_tools is not None and (not allowed_tools or set(allowed_tools) - known_tools):
            raise ValueError('Unknown or empty tool selection')
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else known_tools
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError('Project directory required')
        self.values = options('codex-server', values)
        self.program = executable('codex-cli')
        # #266 : un chemin Claude invalide désactive seulement claude-code (raison
        # exposée par reprise_backends) ; le reste du pont démarre normalement.
        self.claude_program = None
        self.claude_program_error = None
        if claude_program:
            if (not Path(claude_program).is_absolute() or not Path(claude_program).is_file()
                    or not os.access(claude_program, os.X_OK)):
                self.claude_program_error = 'Chemin Claude CLI absolu et exécutable requis : ' + str(claude_program)
                print('Warning: ' + self.claude_program_error + ' ; backend claude-code non configuré', file=sys.stderr)
            else:
                self.claude_program = str(Path(claude_program).resolve())
        self.claude_subscription_only = claude_subscription_only
        self.desktop_values = {'socket': desktop_socket} if desktop_socket else {}
        self.desktop_program = executable('claude-desktop')
        self.ironclaw_program = shutil.which('ssh') or 'ssh'
        self.ironclaw_values = ironclaw_values or {}
        self.scope = hashlib.sha256((str(self.root)+'\0'+str(self.values.get('socket', 'default'))).encode()).hexdigest()
        state = Path(state)
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.owner = 'local:' + str(os.getpid())
        self.db_path = state / 'expert.sqlite'
        self.lock_path = state/'expert.lock'
        self.db = sqlite3.connect(state/'expert.sqlite', timeout=2)
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_threads(scope TEXT,id TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_calls(scope TEXT,day INTEGER,count INTEGER,PRIMARY KEY(scope,day))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_routes(scope TEXT,id TEXT,model TEXT,effort TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS claude_expert_routes(scope TEXT,id TEXT,program TEXT,model TEXT,effort TEXT,PRIMARY KEY(scope,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS expert_thread_modes(scope TEXT,id TEXT,mode TEXT NOT NULL,PRIMARY KEY(scope,id))')
        # Lot C / issue #238 : une ligne par tour d'inférence actif, autant de slots
        # que la capacité déclarée par experts-manifest.json.
        # Une ligne ne meurt que par observation d'un reçu terminal, jamais par âge.
        self.db.execute('''CREATE TABLE IF NOT EXISTS expert_active_turns(
            backend TEXT, slot TEXT, thread_id TEXT, request_key TEXT,
            started_at REAL, heartbeat_at REAL, PRIMARY KEY(backend, slot))''')
        import routing_events
        routing_events.setup(self.db)
        routing_events.seed_local(self.db)
        self.db.commit()
        self.requests = Store(self.db, str(self.root))
        # Catalogue de modèles Codex dynamique (#242, #253) : cache TTL strictement
        # par instance ; repli last-known uniquement via le fichier CATALOG_PATH
        # (écrit en live, jamais réécrit depuis un contenu last-known).
        self._codex_catalog_cache = None
        self._codex_catalog_time = None
        self._release_terminal_slots()
        # Manifeste expert déclaratif (#258 lot 2) : échec de schéma = bridge
        # ou fichier absent = bridge refusé au démarrage.
        self.experts = load_experts_manifest()
        self.default_chain = load_default_chain()
        # Flag automatic_fallback : kwarg explicite (True/False) gagne ; None
        # (sentinel, kwarg omis) → dérivation depuis le manifeste, re-dérivée
        # au rechargement sauf kwarg explicite (automatic_fallback_explicit).
        self.automatic_fallback_explicit = automatic_fallback_enabled is not None
        self.automatic_fallback_enabled = automatic_fallback_enabled if automatic_fallback_enabled is not None else load_fallback_flag()
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
        # ChatGPT roundtrip: same TaskStore contract over a dedicated state directory
        # (channel independence), opened only when the local channel is configured.
        # Admission and the return-channel TaskStore must use that exact path,
        # including isolated integration-verification and nondefault installs.
        self.chatgpt_state = (Path(chatgpt_state).resolve() if chatgpt_state
                              else state_location(self.root, 'chatgpt-state'))
        self.chatgpt_tasks = None
        if self.chatgpt_configured:
            try:
                self.chatgpt_tasks = TaskStore(self.chatgpt_state, self.root)
            except (OSError, sqlite3.Error, ValueError):
                self.chatgpt_tasks = None
        # Pont Meta AI (#258 lot 3) : TaskStore partagé avec le service metaaid
        # + répertoire inbox du pont fichiers, lus depuis la connexion Reprise
        # (connections/metaai.json, clé bridge_root —
        # issue #267). Sans valeurs ou avec enabled:false → backend non
        # configuré, déclaré honnête, jamais routé ailleurs ; une connexion
        # incomplète est ignorée ici (le CLI affiche l'avertissement, pas de
        # repli silencieux de routage).
        self.metaai_values = metaai_values if isinstance(metaai_values, dict) else {}
        if metaai_retired_keys(self.metaai_values):
            raise ValueError('Connexion Meta AI : clé dav_root supprimée (#276) ; la renommer bridge_root.')
        self.metaai_tasks = None
        self.metaai_inbox = None
        if self.metaai_values and self.metaai_values.get('enabled', True) is not False:
            store_path = self.metaai_values.get('taskstore')
            bridge_root = metaai_bridge_root(self.metaai_values)
            if isinstance(store_path, str) and store_path.strip() and bridge_root:
                try:
                    self.metaai_tasks = metaai_taskstore.TaskStore(store_path)
                    self.metaai_inbox = Path(bridge_root).expanduser() / 'inbox'
                except (OSError, sqlite3.Error, ValueError):
                    self.metaai_tasks = None
                    self.metaai_inbox = None
        # Fonction de niveau module + ressources capturées : une méthode
        # liée retiendrait self (le finalizer ne tournerait qu'à la sortie
        # du processus). _safe_close est idempotent (close après close
        # lève sqlite3.Error, avalé).
        weakref.finalize(self, _close_bridge_databases, self.desktop_tasks, self.db, self.chatgpt_tasks, self.metaai_tasks)

    def reload_experts_manifest(self):
        """Recharge experts-manifest.json (validation stricte identique au
        démarrage ; lève ValueError sur schéma/type invalide) ainsi que la
        default_chain du routeur et le flag automatic_fallback (re-dérivé
        du manifeste sauf kwarg explicite)."""
        self.experts = load_experts_manifest()
        self.default_chain = load_default_chain()
        if not self.automatic_fallback_explicit:
            self.automatic_fallback_enabled = load_fallback_flag()
        return self.experts

    def close(self):
        self.desktop_tasks, tasks = None, self.desktop_tasks
        self.chatgpt_tasks, chatgpt = None, self.chatgpt_tasks
        self.metaai_tasks, metaai = None, self.metaai_tasks
        _close_bridge_databases(tasks, self.db, chatgpt, metaai)
        if chatgpt is not None:
            chatgpt.close()

    def _codex_catalog(self, force=False):
        """Catalogue de modèles Codex (#242, #253) : `model/list` via le proxy RPC.

        Cache TTL 30 s strictement par instance Bridge. Succès live → écriture
        du fichier last-known-good (`CATALOG_PATH`, atomique) ; un échec
        d'écriture est journalisé dans `error` sans invalider le catalogue
        live. Échec live → chargement du fichier : lisible avec au moins un
        modèle valide → `source='last_known'` (models et updated_at du
        fichier, erreur live préservée) ; absent/corrompu/vide →
        `source='unavailable'`, models vide. Le fichier n'est jamais réécrit
        depuis un contenu last-known (pas de réécriture circulaire).
        """
        now = time.monotonic()
        if not force and self._codex_catalog_cache is not None \
                and now - (self._codex_catalog_time or 0.0) < CODEX_CATALOG_TTL:
            return self._codex_catalog_cache
        models, error = [], None
        client = None
        try:
            client = Client(self.program, self.values)
            client.call('initialize', {'clientInfo': {'name': 'reprise-expert', 'version': '0.1.0'},
                                       'capabilities': {'experimentalApi': True}})
            client.send({'method': 'initialized'})
            raw = client.call('model/list', {}).get('data') or []
            for item in raw:
                if isinstance(item, dict) and isinstance(item.get('model'), str) and item['model']:
                    models.append({'model': item['model'],
                                   'displayName': item.get('displayName'),
                                   'supportedReasoningEfforts': [
                                       effort.get('reasoningEffort') for effort in
                                       (item.get('supportedReasoningEfforts') or [])
                                       if isinstance(effort, dict) and effort.get('reasoningEffort')]})
        except (OSError, ValueError, TimeoutError, Unavailable) as exc:
            models, error = [], str(exc) or exc.__class__.__name__
        finally:
            if client is not None:
                client.close()
        if models:
            error = self._codex_catalog_save(models) or error
            entry = {'models': models, 'source': 'live',
                     'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'error': error}
        else:
            entry = self._codex_catalog_load(error) or {
                'models': [], 'source': 'unavailable',
                'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'error': error}
        self._codex_catalog_cache, self._codex_catalog_time = entry, now
        return entry

    @staticmethod
    def _codex_catalog_save(models):
        """Écriture atomique last-known-good (#253) ; retourne None ou un message d'erreur."""
        try:
            CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Revue (MINEUR S6) : suffixe unique par processus — deux ponts
            # concurrents n'écrasent plus le même temporaire.
            tmp = CATALOG_PATH.with_name(f'{CATALOG_PATH.name}.{os.getpid()}.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump({'models': models,
                           'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                           'version': CODEX_CATALOG_VERSION}, handle, ensure_ascii=False, indent=1)
            os.replace(tmp, CATALOG_PATH)
        except OSError as exc:
            return f'catalog persistence failed: {exc}'
        return None

    @staticmethod
    def _codex_catalog_load(live_error=None):
        """Chargement last-known-good (#253) ; None si absent, corrompu ou sans modèle valide."""
        try:
            with open(CATALOG_PATH, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            models = [item for item in (data.get('models') or [])
                      if isinstance(item, dict) and isinstance(item.get('model'), str) and item['model']]
        except (OSError, ValueError):
            return None
        if not models:
            return None
        return {'models': models, 'source': 'last_known',
                'updated_at': data.get('updated_at'), 'error': live_error}

    def _codex_admit_model(self, catalog, model):
        """Admission d'un modèle codex explicite contre le catalogue (#242, #253).

        Modèle absent d'un cache valide → revalidation forcée unique ; toujours
        absent → ValueError AVANT toute création de requête ou insertion en
        base. Compromis #253 : contre un catalogue last-known, un modèle
        explicite de l'opérateur est admis alors que le compte actuel peut ne
        plus l'autoriser — le dispatch échouera alors clairement avec
        `engine_unavailable` au moment de l'inférence.
        """
        if not isinstance(model, str) or not model:
            raise ValueError('Unknown or unsupported model for backend codex')
        known = {item['model'] for item in catalog['models']}
        if model not in known:
            catalog = self._codex_catalog(force=True)
            known = {item['model'] for item in catalog['models']}
            if model not in known:
                raise ValueError(f'Unknown or unsupported model for backend codex: {model!r} not in live catalog'
                                 + (f' ({catalog["error"]})' if catalog['error'] else ''))

    def _codex_route(self, catalog, preference):
        """Résolution ordonnée d'une route codex contre le catalogue (#242, #253).

        Premier candidat présent dans le catalogue ; effort prescrit validé
        contre `supportedReasoningEfforts` (repli medium, sinon le plus bas) ;
        accepte les sources 'live' et 'last_known', refuse 'unavailable' ;
        aucun candidat présent → ValueError, jamais de substitution silencieuse.
        """
        if catalog['source'] not in ('live', 'last_known'):
            raise ValueError(f'Codex model catalog unavailable: {catalog["error"] or "model/list failed"}')
        available = {item['model']: item for item in catalog['models']}
        for candidate in CODEX_ROUTE_PREFERENCES[preference]:
            if candidate in available:
                return self._codex_route_effort(available[candidate], preference)
        raise ValueError('No preferred Codex model in live catalog: '
                         + ', '.join(CODEX_ROUTE_PREFERENCES[preference]))

    @staticmethod
    def _codex_route_effort(item, preference):
        efforts = item.get('supportedReasoningEfforts') or []
        prescribed = CODEX_EFFORT_PREFERENCES[preference]
        if not efforts:
            return {'model': item['model'], 'effort': prescribed}
        if prescribed in efforts:
            return {'model': item['model'], 'effort': prescribed}
        if 'medium' in efforts:
            return {'model': item['model'], 'effort': 'medium'}
        ranked = sorted(efforts, key=lambda effort: (CODEX_EFFORT_ORDER.get(effort, len(CODEX_EFFORT_ORDER)), effort))
        return {'model': item['model'], 'effort': ranked[0]}

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

    def _retry_task_store(self, which):
        """Une seule tentative de reconstruction du TaskStore par appel (lot C
        #239) : un état Quick Apply/ChatGPT initialisable depuis l'init du pont
        doit redevenir utilisable sans redémarrer le bridge. Retourne True si
        le canal de retour est disponible après la tentative."""
        if which == 'desktop':
            if self.desktop_tasks is not None:
                return True
            try:
                self.desktop_tasks = TaskStore(self.desktop_state, self.root)
                return True
            except (OSError, sqlite3.Error, ValueError):
                self.desktop_tasks = None
                return False
        if which == 'metaai':
            if self.metaai_tasks is not None and self.metaai_inbox is not None:
                return True
            if not self.metaai_values or self.metaai_values.get('enabled', True) is False:
                return False
            try:
                self.metaai_tasks = metaai_taskstore.TaskStore(self.metaai_values['taskstore'])
                bridge_root = metaai_bridge_root(self.metaai_values)
                if not bridge_root:
                    raise KeyError('bridge_root')
                self.metaai_inbox = Path(bridge_root).expanduser() / 'inbox'
                return True
            except (OSError, sqlite3.Error, ValueError, KeyError):
                self.metaai_tasks = None
                self.metaai_inbox = None
                return False
        if self.chatgpt_tasks is not None:
            return True
        if not self.chatgpt_configured:
            return False
        try:
            self.chatgpt_tasks = TaskStore(self.chatgpt_state, self.root)
            return True
        except (OSError, sqlite3.Error, ValueError):
            self.chatgpt_tasks = None
            return False

    def _chatgpt_thread_owned(self, request_key):
        """Issue #260 : possession d'un thread ChatGPT pour un followup.

        Critère (validé) : une admission de CE projet dans le registre durable
        chatgpt_submissions (scope = str(root), même convention que
        chatgpt_quick.Endpoint) dont le reçu de dispatch est présent. Lecture
        seule, best effort : registre absent, base vide ou illisible = non
        possédé (ThreadNotOwned côté appelant), jamais d'exception. La
        correspondance de conversation reste prouvée au tour 2 par l'écho du
        prompt ; un id divergent observé est signalé dans le reçu, sans refus.
        """
        database = self.chatgpt_state / 'chatgpt.sqlite'
        if not database.is_file():
            return False
        try:
            ledger = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)
        except sqlite3.Error:
            return False
        try:
            row = ledger.execute(
                'SELECT 1 FROM chatgpt_submissions'
                ' WHERE scope=? AND request_key=? AND dispatch_receipt IS NOT NULL',
                (str(self.root), request_key)).fetchone()
        except sqlite3.Error:
            return False
        finally:
            ledger.close()
        return row is not None

    def _lazy_claude_start(self):
        """Départ paresseux optionnel du daemon Quick Apply (lot C #239) :
        opt-in via la clé scheduler delegation_lazy_start (défaut False) ;
        sonde connect() seule ≤ 2 s, jamais de spawn (TODO : lancement daemon
        hors contrat pour l'instant). Retourne le dict de delegation_resume.
        lazy_start, ou None quand la sonde est désactivée ou impossible."""
        try:
            import delegation_resume
            if not delegation_resume.lazy_start_enabled(self.db):
                return None
            return delegation_resume.lazy_start(self.root, self.desktop_values)
        except Exception:
            return None

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
            if name in ('reprise_delegate', 'reprise_followup') and args.get('backend') == 'chatgpt':
                raise ValueError('Backend chatgpt refused in v1: consultative expertise/review only, no implementation mode.')
            if name not in ('reprise_delegate', 'reprise_followup') or args.get('backend', 'codex') != 'codex':
                raise ValueError('Implementation requires generic Codex delegation tools')
        return mode

    def _acquire_slot(self, name, args):
        """Acquiert un slot d'inférence par INSERT atomique (PK backend+slot).
        Retourne (backend, slot, clé de libération) ; clé None quand le slot
        appartient à un autre tour (followup réutilisant un thread déjà actif)."""
        backend = args.get('backend', 'codex')
        if not isinstance(backend, str) or not backend:
            backend = 'codex'
        now = time.time()
        thread_id = args.get('thread_id')
        if name in ('reprise_followup', 'codex_followup') and isinstance(thread_id, str) and thread_id:
            row = self.db.execute('SELECT slot, request_key FROM expert_active_turns WHERE backend=? AND thread_id=?',
                                  (backend, thread_id)).fetchone()
            if row:
                # Le thread_id actif est propriétaire légitime du slot : réutilisation, pas refus.
                self.db.execute('UPDATE expert_active_turns SET heartbeat_at=? WHERE backend=? AND slot=?',
                                (now, backend, row[0]))
                self.db.commit()
                key = args.get('request_key')
                return (backend, row[0], key if row[1] == key else None)
        # La capacité vient du manifeste ; un backend non déclaré a un seul slot.
        entry = self.experts.get(backend)
        capacity = entry['capacity'] if entry else 1
        for index in range(capacity):
            slot = str(index)
            try:
                self.db.execute('INSERT INTO expert_active_turns(backend, slot, thread_id, request_key, started_at, heartbeat_at) VALUES (?,?,?,?,?,?)',
                                (backend, slot, thread_id if isinstance(thread_id, str) else None,
                                 args.get('request_key'), now, now))
                self.db.commit()
            except sqlite3.IntegrityError:
                # Slot pris : la tentative d'INSERT a ouvert une transaction
                # implicite — la fermer pour laisser la connexion propre.
                self.db.rollback()
                continue
            return (backend, slot, args.get('request_key'))
        return None

    def _release_slot(self, slot):
        backend, name, key = slot
        if key is None:
            # #244 : clé None signifie slot inséré avant un échec d'admission
            # (INSERT avec request_key NULL) — sans suppression dédiée, la ligne
            # fuit et verrouille le backend (bridge_busy définitif). Une ligne
            # AVEC clé appartient à un tour tiers (réutilisation followup) et
            # ne doit jamais être touchée par ce chemin.
            self.db.execute('DELETE FROM expert_active_turns WHERE backend=? AND slot=? AND request_key IS NULL',
                            (backend, name))
        else:
            self.db.execute('DELETE FROM expert_active_turns WHERE backend=? AND slot=? AND request_key=?',
                            (backend, name, key))
        self.db.commit()

    def _release_terminal_slots(self):
        """Ne libère que sur observation d'un reçu terminal (cycle reconcile ou
        redémarrage) ; jamais par âge : un long tour Codex ne doit jamais être
        invalidé par un tick."""
        scope = self.requests.scope
        self.db.execute('''DELETE FROM expert_active_turns WHERE request_key IN (
            SELECT t.request_key FROM expert_active_turns t
            JOIN delegation_turns d ON d.scope=? AND d.request_key=t.request_key
            WHERE d.phase IN ('finished','interrupted'))''', (scope,))
        self.db.execute('''DELETE FROM expert_active_turns WHERE request_key IN (
            SELECT t.request_key FROM expert_active_turns t
            JOIN delegation_requests r ON r.scope=? AND r.request_key=t.request_key
            WHERE r.state IN ('recorded','indeterminate'))''', (scope,))
        self.db.commit()

    def _record_observed_refusal(self, backend, result):
        """Lot 3 #258 : tout refus déclencheur final (hors portes manifeste
        définitives) est journalisé via routing_events.record_refusal →
        veto refus-recent. Opt-in automatique ou non : l'observation est
        un fait, pas un choix de routage. Borné try/except : ne lève jamais."""
        try:
            import expert_router
            import routing_events
            if expert_router.is_trigger(result):
                resolved = backend or result.get('backend') or 'codex'
                routing_events.record_refusal(self.db, resolved, str(result.get('status')))
        except Exception:
            pass

    def attempt_substitution(self, name, args, mode, attach, result, key=None):
        """Substitution automatique de délégation (opt-in, flag OFF par
        défaut). Sur un refus déclencheur (quota_blocked, billing_unverified,
        engine_unavailable — hors portes manifeste définitives, jamais
        substituées), route vers le meilleur backend admissible, configuré et
        non exclu : cible explicite défaillante → external_rankings.route_choice ;
        aucune cible appelante → default_chain du manifeste, résolue au temps T.
        served par attach() sous le backend D'ORIGINE (limitation connue :
        le contexte de l'événement n'est pas re-threadé vers le backend servi).
        Aucun candidat → refus d'origine retourné INCHANGÉ (de même qu'une
        exception du backend servi : le refus d'origine est retourné inchangé
        pour que l'appelant termine la requête proprement). Une substitution
        effective journalise routing_substituted (requested_route ET
        served_route non nuls) et porte automatic_fallback/requested_route/
        served_route sur le résultat. Une seule substitution par délégation :
        un échec du backend servi n'est jamais re-substitué ici."""
        if not self.automatic_fallback_enabled or not isinstance(result, dict):
            return result
        import expert_router
        if not expert_router.is_trigger(result):
            return result
        explicit = args.get('backend') is not None
        failed = args.get('backend') or result.get('backend') or 'codex'
        # L'implémentation est réservée à codex (execution_mode) : la cible
        # défaillante étant exclue, aucun candidat ne peut la servir.
        if mode == 'implementation':
            return result
        if explicit:
            served = expert_router.substitute_for_target(self, failed)
        else:
            served = expert_router.chain_candidates(self, self.default_chain, exclude=failed)
        if served is None or served == failed:
            return result
        dispatch_args = dict(args)
        dispatch_args['backend'] = served
        # Le modèle demandé appartient au catalogue de la cible défaillante :
        # retiré sans condition (un élu codex refuserait un modèle claude-code).
        dispatch_args.pop('model', None)
        try:
            served_result = self._call(name, dispatch_args, on_turn=attach)
        except Exception:
            # L'appelant finit la requête avec le refus d'origine inchangé.
            return result
        if isinstance(served_result, dict) and served_result.get('status') == 'busy':
            return result
        # Lot 3 #258 : le refus du backend servi est observé lui aussi (attribué
        # au servi, pas à la cible d'origine) — un échec du servi n'est jamais
        # réessayé silencieusement au tour suivant (veto refus-recent).
        self._record_observed_refusal(served, served_result)
        expert_router.trace_substitution(
            self, name, failed, served, level=args.get('level'),
            request_key=key or args.get('request_key'), thread_id=args.get('thread_id'),
            reason=f'automatic fallback: {failed} refused ({result.get("status")})')
        return expert_router.annotate(served_result, failed, served)

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
        if name in ('reprise_backends', 'reprise_experts_list', 'reprise_health', 'reprise_history', 'reprise_resume'):
            return self._call(name, args)
        if name == 'reprise_notifications':
            return self._notifications(args)
        if name == 'approval_list':
            return self._approval_list(args)
        if name == 'approval_respond':
            return self._approval_respond(args)
        if not isinstance(args, dict):
            raise ValueError('Object arguments required')
        if name == 'reprise_delegate' and 'filePaths' in args:
            args = dict(args, filePaths=project_file_paths(self.root, args['filePaths']))
        return_mode = args.get('return_mode', 'full')
        if name in INFERENCE_TOOLS:
            if return_mode not in ('full', 'receipt'):
                raise ValueError('Invalid return_mode')
        elif 'return_mode' in args:
            raise ValueError('return_mode requires an inference tool')
        slot = self._acquire_slot(name, args)
        if slot is None:
            return {'status': 'busy', 'reason_code': 'bridge_busy',
                    'automatic_retry': False, 'dispatched': False,
                    'reason': 'Bridge busy: one active expertise per state directory; no automatic retry.'}
        # Porte opportuniste (lot C #239) : l'appel admis sert de point de
        # rendez-vous pour la reprise observe-only des jobs dus. Borne interne
        # de run_due (budget 2 s), total try/except : ne lève jamais.
        try:
            import delegation_resume
            delegation_resume.run_due(self.db)
        except Exception:
            pass
        try:
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
                    # Lot 3 #258 : ce refus ne passe jamais par le tour réel
                    # (court-circuit preflight) — observation ici, avant retour.
                    self._record_observed_refusal(clean.get('backend'), preflight_result)
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
            existing = self.requests.begin(key, payload, parent, owner=self.owner)
            if existing:
                pointer = existing.get('request_key', key)
                completion = (self._desktop_completion(pointer, existing.get('backend'))
                              if existing.get('backend') in ('claude-desktop', 'chatgpt')
                              and existing.get('admission_only') else None)
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
            self._record_observed_refusal(clean.get('backend'), result)
            result = self.attempt_substitution(name, clean, mode, attach, result, key)
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
            if (result.get('backend') or clean.get('backend')) in ('claude-desktop', 'chatgpt', 'metaai') \
                    and result.get('status') == 'finished' \
                    and result.get('response') is None:
                # A desktop submission is only admitted: the MCP round-trip (submit_result) completes it.
                # Backend SERVI (posé par le handler) : une admission d'un
                # backend atteint par substitution doit aussi être marquée.
                result = dict(result, admission_only=True)
            result = self.requests.finish(key, result)
            if return_mode == 'receipt':
                return self._tag(self.requests.lookup({'request_key': key, 'view': 'receipt'}), result)
            return self._bounded_full(key, result)
        finally:
            self._release_slot(slot)

    def wait_result(self, args, cancel=None):
        if 'wait_result' not in self.allowed_tools:
            raise ValueError('Tool not enabled for this integration')
        key = args.get('request_key')
        backend = None
        if key is not None:
            # Revue MAJEUR 6 : lecture thread-safe (connexion read-only dédiée)
            # et non self.requests, lié à la connexion du thread principal.
            # Partition identique à l'écrivain : Store(scope=str(self.root)),
            # PAS self.scope (digest sha256 réservé aux tables expert_*).
            previous = _ledger_backend(self.db_path, str(self.root), key)
            if previous == 'metaai':
                return self._metaai_wait_result(args, cancel)
            backend = 'chatgpt' if previous == 'chatgpt' else 'claude-desktop'
        state = self.chatgpt_state if backend == 'chatgpt' else self.desktop_state
        return wait_for_result(state, self.root, args, cancel, backend=backend or 'claude-desktop')

    def _metaai_wait_result(self, args, cancel=None):
        """Attente d'une réponse Meta AI (#258 lot 3) : sondage du TaskStore
        partagé jusqu'à answered/failed (record_result du watcher metaaid)
        ou timeout. Le reçu du delegate n'est qu'une admission ; la réponse
        finale vit dans le store, elle voyage dans le payload d'attente."""
        key = args.get('request_key')
        validate_key(key)
        _, seconds = wait_arguments(args)
        cancel = cancel if cancel is not None else threading.Event()
        # Lecture hors thread principal : même motif que _ledger_backend,
        # connexion mode=ro courte au lieu du db partagé (check_same_thread).
        task_id = None
        try:
            db = sqlite3.connect(Path(self.db_path).as_uri() + '?mode=ro', timeout=0,
                                 isolation_level=None, uri=True)
        except sqlite3.Error:
            db = None
        if db is not None:
            try:
                row = db.execute('SELECT result FROM delegation_requests WHERE scope=? AND request_key=?',
                                 (str(self.root), key)).fetchone()
                if row and row[0]:
                    try:
                        task_id = (json.loads(row[0]) or {}).get('task_id')
                    except ValueError:
                        task_id = None
            except sqlite3.Error:
                task_id = None
            finally:
                db.close()
        if not isinstance(task_id, str) or not task_id:
            return wait_status(key, 'not_found', backend='metaai')
        started = time.monotonic()
        deadline = started + seconds
        delay = 0.25
        while not cancel.is_set():
            if self.metaai_tasks is None and not self._retry_task_store('metaai'):
                return wait_status(key, 'timeout', backend='metaai', state='channel_unavailable',
                                   waited_seconds=time.monotonic() - started)
            task = None
            try:
                task = self.metaai_tasks.get(task_id)
            except sqlite3.Error:
                task = None
            if task is not None and task.get('status') in ('answered', 'failed'):
                try:
                    result = json.loads(task['result']) if task.get('result') else {}
                except ValueError:
                    result = {'raw': task.get('result')}
                digest = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                return wait_status(key, 'finished', backend='metaai', state='recorded',
                                   result_sha256=digest, task_id=task_id,
                                   task_status=task['status'], result=result)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return wait_status(key, 'timeout', backend='metaai', state='pending',
                                   waited_seconds=time.monotonic() - started)
            cancel.wait(min(delay, remaining))
            delay = min(1.0, delay * 2)
        return None

    def _task_snapshot(self, key, backend='claude-desktop'):
        state = self.desktop_state if backend != 'chatgpt' else self.chatgpt_state
        reader = None
        try:
            reader = TaskStore.open_reader(state, self.root)
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
        backend = previous.get('backend')
        if backend not in ('claude-desktop', 'chatgpt') or not previous.get('admission_only'):
            return self.requests.lookup(args)
        snapshot = self._task_snapshot(key, backend)
        if snapshot is None or not snapshot['completed']:
            return self.requests.lookup(args)
        if args.get('acknowledge', False):
            raise ValueError('Acknowledge the result with get_result on its connector')
        manifest, text = receipt_view(snapshot)
        provenance, _ = receipt_view(historic)
        source = 'desktop_tasks' if backend == 'claude-desktop' else 'chatgpt_tasks'
        extra = {'backend': backend, 'source': source,
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

    def _notifications(self, args):
        """Phase 1 of #237: durable notification queue over the delegation
        receipt store. read/wait never consume or mark anything; ack writes
        per-(event_id, consumer_id) rows, idempotently. No redispatch logic
        lives here — the caller decides what to do with an event."""
        if not isinstance(args, dict) or args.keys() - {'action', 'consumer_id', 'request_keys', 'event_ids', 'limit', 'timeout_ms'}:
            raise ValueError('Invalid notification arguments')
        action = args.get('action')
        if action not in ('read', 'wait', 'ack'):
            raise ValueError('Invalid notification action')
        if not isinstance(args.get('consumer_id'), str) or not args.get('consumer_id').strip():
            raise ValueError('Persist a nonempty consumer_id')
        request_keys = args.get('request_keys')
        if request_keys is not None and (
                not isinstance(request_keys, list) or not request_keys
                or len(request_keys) > delegation_notifications.MAX_BATCH
                or any(not isinstance(key, str) or not key.strip() or len(key) > 128 for key in request_keys)):
            raise ValueError('Bounded list of nonempty request_keys required')
        queue = delegation_notifications.Queue(self.db, self.requests.scope)
        if action == 'ack':
            return queue.ack(args.get('consumer_id'), args.get('event_ids'))
        if action == 'wait':
            timeout = args.get('timeout_ms')
            if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                                        or timeout < 0 or timeout > delegation_notifications.MAX_WAIT_SECONDS * 1000):
                raise ValueError('Bounded nonnegative timeout_ms required')
            return queue.wait(args.get('consumer_id'), timeout_ms=timeout, request_keys=request_keys)
        limit = args.get('limit')
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            raise ValueError('Positive integer limit required')
        return {'status': 'read', 'events': queue.read(args.get('consumer_id'), limit=limit, request_keys=request_keys)}


    @staticmethod
    def _tag(manifest, result):
        """A receipt manifest reports the delegation status, not the lookup status."""
        manifest['status'] = result['status']
        return manifest

    def _desktop_completion(self, pointer, backend='claude-desktop'):
        """Project a committed connector response without reserving its delivery."""
        snapshot = self._task_snapshot(pointer, backend)
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
        if name not in ('reprise_backends', 'reprise_experts_list', 'reprise_delegate', 'reprise_followup', 'codex_review', 'codex_followup', 'reprise_resume', 'reprise_history', 'reprise_health') or not isinstance(args, dict):
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
            catalog = self._codex_catalog()
            codex_entry = {'id': 'codex', 'configured': bool(self.program),
                 'models': list(catalog['models']),
                 'models_source': catalog['source'],
                 'models_updated_at': catalog['updated_at'],
                 'billing': 'ChatGPT quota',
                 # #252 : écriture du projet permise par défaut (réseau coupé,
                 # approbations natives conservées) ; expertise reste en lecture seule.
                 'modes': ['expertise', 'implementation'],
                 'workspace_write_authorized': True,
                 'implementation_scope': str(self.root),
                 'kinds': list(MODES), 'levels': ['standard', 'deep'], 'writes': workspace_writes('codex')}
            if catalog['error']:
                codex_entry['models_error'] = catalog['error']
            return {'status': 'finished', 'project_root': str(self.root),
                    'automatic_fallback_enabled': bool(self.automatic_fallback_enabled),
                    'result_transport': {
                    'receipt': True, 'pagination': True, 'followup_receipt': True,
                    'format': 'receipt-json-v1', 'transport_limit': TRANSPORT_LIMIT}, 'backends': [
                codex_entry,
                {'id': 'foundation-models', 'configured': apple_expert.configured(),
                 'models': ['system'], 'availability': 'unknown', 'inference_verified': False,
                 'billing': 'On-device model; no cloud API cost', 'on_device': True,
                 'modes': ['expertise'], 'writes': workspace_writes('foundation-models'), 'resumable': False,
                 'kinds': list(MODES), 'levels': ['standard'],
                 'context': 'Explicit question and file snapshots only; no tools'},
                {'id': 'autoclaw', 'configured': False, 'models': [], 'reason': 'Adapter, billing and fallback behavior not validated.'},
                {'id': 'claude-code', 'configured': bool(self.claude_program and self.claude_subscription_only),
                 'models': list(BACKEND_MODELS['claude-code']),
                 'executable_configured': bool(self.claude_program), 'auth_verified': False,
                 'billing': 'Claude subscription; paid credits must be disabled by operator',
                 'quota_remaining': None, 'kinds': list(MODES), 'levels': ['standard', 'deep'],
                 'writes': workspace_writes('claude-code'), 'context': 'Explicit question and file snapshots only; no tools'}
                | ({'reason': self.claude_program_error} if self.claude_program_error else {}),
                {'id': 'claude-desktop', 'configured': bool(self.desktop_values), 'models': [],
                 'billing': 'Claude Desktop session; quota not reported',
                 'kinds': list(MODES), 'levels': ['standard', 'deep'],
                 'writes': workspace_writes('claude-desktop'),
                 'task_return_supported': self.desktop_tasks is not None,
                 'dispatch': 'dispatchOnQuickEntrySubmit (full turn, not a prefill)',
                 'return_channel': 'MCP get_task/submit_result via durable TaskStore; completion only on validated submit_result',
                 'context': 'Structured Quick Apply payload; images/files bounded; target chat routing'},
                {'id': 'ironclaw', 'configured': self._ironclaw_configured(), 'models': [],
                 'reason': None if self._ironclaw_configured() else
                 'Configuration IronClaw requise (--ironclaw-config : host, port, user, key).',
                 'billing': 'Remote IronClaw runtime (paid) — moteur lu via ironclaw.engine()',
                 'kinds': list(MODES), 'levels': ['standard', 'deep'],
                 'writes': workspace_writes('ironclaw'),
                 'context': 'Experimental CLI retained for audit; use explicit durable ironclaw-submit/poll/result commands.'},
                {'id': 'chatgpt', 'configured': self.chatgpt_tasks is not None, 'models': [],
                  'billing': 'Compte ChatGPT existant ; pas de fallback API payante',
                  'kinds': ['expertise', 'review'], 'levels': ['standard', 'deep'],
                  'writes': workspace_writes('chatgpt'), 'transport_verified': False, 'availability': 'unverified',
                  'dispatch': 'holder_capture_sync', 'task_return_supported': False,
                  'context': 'Admission SQLite durable ; dispatch holder CDP profil dédié, retour = capture de réponse (WS fin de tour + lecture rendue ancrée)'},
                {'id': 'metaai', 'configured': self.metaai_tasks is not None, 'models': [],
                 'billing': 'Meta AI session; quota not reported',
                 'kinds': list(MODES), 'levels': ['standard', 'deep'],
                 'writes': workspace_writes('metaai'),
                 'task_return_supported': self.metaai_tasks is not None,
                 'dispatch': 'enqueue + dispatch_write_inbox (pont fichiers, inbox JSON, outbox clé/valeur)',
                 'return_channel': 'TaskStore Meta AI partagé ; answered via record_result du watcher metaaid',
                 'availability': 'unverified',
                 'context': 'Tâche asynchrone par pont fichiers (dossier partagé, plus de WebDAV depuis le 24/11 — issue #277) ; pas de session suiveuse (reprise_followup refusé). Réponse finale lue via reprise_wait_result.'}],
                'automatic_fallback': False}
        if name == 'reprise_experts_list':
            if args:
                raise ValueError('No arguments expected')
            measured = {'codex': bool(self.program),
                        'ironclaw': self._ironclaw_configured(),
                        'claude-code': bool(self.claude_program and self.claude_subscription_only),
                        'claude-desktop': bool(self.desktop_values),
                        'foundation-models': apple_expert.configured(),
                        'chatgpt': self.chatgpt_tasks is not None,
                        'metaai': self.metaai_tasks is not None}
            listed = [{'id': backend, 'enabled': entry['enabled'], 'capacity': entry['capacity'],
                       'resumable': entry['resumable'], 'session': entry['session'],
                       'handler': backend in EXPERTS_HANDLED_BACKENDS,
                       'measured_available': measured.get(backend, False),
                       'availability': 'available' if measured.get(backend, False) else 'unavailable'}
                      for backend, entry in self.experts.items()]
            return {'status': 'finished', 'automatic_fallback': False,
                    'manifest_path': str(EXPERTS_MANIFEST_PATH),
                    'manifest_loaded': bool(self.experts), 'backends': listed}
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
        if backend not in ('codex', 'ironclaw', 'claude-code', 'claude-desktop', 'foundation-models', 'chatgpt', 'metaai'):
            raise ValueError('Unknown expertise backend')
        if 'model' in args:
            if backend == 'codex':
                # Admission dynamique (#242, #253) : source de vérité = catalogue
                # live ou last-known persisté ; jamais de liste figée.
                self._codex_admit_model(self._codex_catalog(), args['model'])
            elif backend not in BACKEND_MODELS:
                raise ValueError(f'Backend {backend} does not accept a model argument')
            elif not isinstance(args['model'], str) or args['model'] not in BACKEND_MODELS[backend]:
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
            preference = 'deep' if level == 'deep' else 'batch' if kind == 'batch' else 'default'
            if backend == 'claude-code':
                routing = {'model': 'opus' if level == 'deep' else 'sonnet',
                           'effort': 'high' if level == 'deep' else 'medium'}
            elif backend == 'codex':
                routing = self._codex_route(self._codex_catalog(), preference)
            elif backend == 'foundation-models':
                routing = {'model': 'system'}
            question = (('Implement the bounded task; apply project changes and run appropriate local checks.'
                         if mode == 'implementation' else MODES[kind]) + '\n\n' + question)
            if backend == 'metaai':
                # Contrat de retour v2 par ancres (#194) : jamais de diff unifié
                # sur le canal clé/valeur Meta AI (compteurs de hunk faux 2/2).
                question = META_AI_RETURN_CONTRACT + '\n\n' + question
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
        if name == 'reprise_followup' and backend == 'chatgpt':
            # Issue #260 : la levée conditionnelle exige une possession
            # prouvée — admission chatgpt de ce scope dont le reçu durable de
            # dispatch est présent. Sinon, refus typé (jamais un repli).
            if not isinstance(ident, str) or not self._chatgpt_thread_owned(ident):
                raise ThreadNotOwned('chatgpt', ident)
        if name == 'reprise_followup' and backend == 'metaai':
            raise ValueError('Pas de session metaai suiveuse (#258 lot 2) ; le pont TaskStore fichiers couvre les tâches, pas une session de délégation expert.')
        if name == 'reprise_followup' and backend == 'claude-code':
            if not isinstance(ident, str):
                raise ValueError('Owned Claude session required')
            route = self.db.execute('SELECT program,model,effort FROM claude_expert_routes WHERE scope=? AND id=?', (self.scope, ident)).fetchone()
            if not route or route[0] != self.claude_program:
                raise ThreadNotOwned('claude-code', ident)
            routing = dict(zip(('model', 'effort'), route[1:]))
        elif name == 'reprise_followup' and backend != 'chatgpt':
            # Issue #260 : un followup chatgpt gardé sous son propre nom — le
            # rename codex_followup entraînerait le contrôle expert_threads
            # (scope codex), inapplicable à un thread_id d'admission chatgpt.
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
        if backend == 'codex' and routing.get('model'):
            # Validation du modèle codex FINAL résolu (argument explicite ou route
            # restaurée d'expert_routes) contre le catalogue live ou last-known :
            # refus avant toute insertion en base (#242, #253).
            self._codex_admit_model(self._codex_catalog(), routing['model'])
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
        # #277 : wait_seconds est envoyé par défaut par les clients MCP (attente
        # connectée) ; l'admission metaai/chatgpt est asynchrone, il est ignoré.
        # #279 : un budget non admis est un refus typé, nommé pour l'appelant.
        allowed = BACKEND_BUDGETS.get(backend, ())
        refused = {BUDGET_ARGUMENTS[key] for key in policy} - set(allowed)
        if refused:
            raise BudgetNotSupported(backend, refused, allowed)
        # Portes manifeste déterministes (#258 lot 2, lot 3) : évaluées une
        # seule fois AVANT les portes de connexion, pour le preflight comme
        # pour le tour réel — un backend désactivé, déclaré sans handler ou
        # absent du manifeste est refusé sur sa déclaration, jamais routé vers
        # un autre backend, même sans connexion configurée.
        entry = self.experts.get(backend)
        if entry is not None and not entry['enabled']:
            return {'status': 'engine_unavailable', 'backend': backend,
                    'reason': f'Backend {backend} désactivé dans experts-manifest.json (#258 lot 2).',
                    'automatic_fallback': False}
        if backend not in EXPERTS_HANDLED_BACKENDS:
            return {'status': 'engine_unavailable', 'backend': backend,
                    'reason': (f'Backend {backend} déclaré dans experts-manifest.json mais sans handler de dispatch (#258 lot 2).'
                               if entry is not None else
                               f'Backend {backend} inconnu du manifeste experts ; aucun handler de dispatch.'),
                    'automatic_fallback': False}
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
            if self._retry_task_store('desktop'):
                pass  # reconstruit : on poursuit le parcours normal de l'appel
            else:
                payload = {'status': 'return_channel_unavailable', 'backend': backend,
                           'reason': "Canal de retour MCP indisponible (TaskStore non initialisé) ; aucun aller-retour garanti.",
                           'automatic_fallback': False, 'lazy_start_attempted': False}
                lazy = self._lazy_claude_start()
                if lazy is not None:
                    payload['lazy_start_attempted'] = True
                    payload['lazy_start'] = lazy
                return payload
        if backend == 'foundation-models' and not apple_expert.configured():
            return {'status': 'engine_unavailable', 'backend': backend,
                    'reason': 'Apple fm executable unavailable.', 'automatic_fallback': False}
        if backend == 'chatgpt' and not self.chatgpt_configured:
            return {'status': 'engine_unavailable',
                    'reason': '--chatgpt-state non configuré'}
        if backend == 'chatgpt' and self.chatgpt_tasks is None and not self._retry_task_store('chatgpt'):
            return {'status': 'return_channel_unavailable', 'backend': backend,
                    'reason': 'Canal de retour MCP indisponible (TaskStore non initialisé) ; aucun aller-retour garanti.',
                    'automatic_fallback': False}
        if backend == 'metaai' and (not self.metaai_values or self.metaai_values.get('enabled', True) is False):
            return {'status': 'engine_unavailable', 'backend': backend,
                    'reason': ('Connexion Meta AI désactivée (enabled: false) ; réactivez la connexion Reprise pour router vers metaai.'
                               if self.metaai_values else
                               'Connexion Meta AI non configurée ; relancez le pont avec --metaai-config ou la connexion Reprise standard.'),
                    'automatic_fallback': False}
        if backend == 'metaai' and self.metaai_values and \
                self.metaai_values.get('enabled', True) is not False and \
                metaai_bridge_root(self.metaai_values) is None:
            # Connexion avec taskstore mais sans répertoire pont : l'inbox ne
            # peut pas être matérialisée, refus AVANT enqueue (issue #267) pour
            # ne plus laisser de tâches bloquées en dispatched sans réponse.
            return {'status': 'return_channel_unavailable', 'backend': backend,
                    'reason': "Connexion Meta AI sans répertoire pont (clé bridge_root) ; l'inbox ne peut pas être écrite. Corrigez connections/metaai.json ou relancez metaai-install.py.",
                    'automatic_fallback': False}
        if backend == 'metaai' and self.metaai_tasks is None and not self._retry_task_store('metaai'):
            return {'status': 'return_channel_unavailable', 'backend': backend,
                    'reason': 'Canal de retour Meta AI indisponible (TaskStore non initialisé) ; aucun aller-retour garanti.',
                    'automatic_fallback': False}
        if preflight:
            return
        if backend not in ('claude-desktop', 'foundation-models', 'chatgpt', 'metaai') and not (self.ironclaw_program if backend == 'ironclaw'
                                                else (self.program if backend == 'codex' else self.claude_program)):
            return {'status': 'engine_unavailable', 'backend': backend, 'reason': 'Requested executable not configured', 'automatic_fallback': False}
        if backend == 'claude-code' and not self.claude_subscription_only:
            return {'status': 'billing_unverified', 'backend': backend, 'reason': 'Operator must confirm paid usage credits disabled before enabling Claude.', 'automatic_fallback': False}
        import routing_events
        # Admissibilité (lot ②) : la route demandée est explicite (consentement
        # porté par l'appel). admissible(automatic=False) est appelée ici ;
        # la substitution automatique éventuelle est résolue APRÈS le tour
        # réel, dans call(), via expert_router (jamais en preflight).
        allowed, billing = routing_events.admissible(self.db, backend, automatic=False)
        if not allowed:
            return {'status': 'admissibility_refused', 'backend': backend, 'billing_class': billing,
                    'reason': f'Route {backend} inadmissible (billing_class={billing}) ; aucune substitution automatique.', 'automatic_fallback': False}
        if preflight:
            return
        # Journal additif de la décision de routage (contrat §7.5 révisé §7.6) ;
        # le type routing_substituted est écrit par expert_router via call()
        # quand une substitution automatique devient effective.
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
                                         level=args.get('level'),
                                         request_key=(args.get('request_key')
                                                      if args.get('request_key') is not None
                                                      else self.requests.active_key),
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
        if backend == 'chatgpt':
            result = chatgpt_quick.review(self.root, question, kind=args.get('kind', 'expertise'),
                                          level=args.get('level', 'standard'), paths=args.get('paths'),
                                          state=self.chatgpt_state, request_key=self.requests.active_key)
            if self.chatgpt_dispatch:
                result = chatgpt_quick.finish_with_holder_dispatch(self.root, self.chatgpt_state, result)
            routing_events.finalize(self.db, event_id, str(result.get('status') or 'unknown'))
            return result | {'backend': backend, 'automatic_fallback': False}
        if backend == 'metaai':
            # #258 lot 3 + #277 : admission asynchrone — enqueue + matérialisation
            # inbox (pont fichiers : dossier partagé, plus de serveur WebDAV depuis
            # le 24/11), réponse consommée par le watcher metaaid puis lue via
            # reprise_wait_result. Pas de repli. L'injection du prompt
            # d'instruction dans la session ReMetaAI reste la responsabilité du
            # pont metaaid (dispatcher UI + markers .ui-state idempotents) : il
            # est aussi le seul consommateur de l'outbox, donc le détenteur du
            # roundtrip complet — décision opérateur du 27/11.
            payload = {'question': question, 'kind': args.get('kind', 'expertise'),
                       'level': args.get('level', 'standard'),
                       'request_key': self.requests.active_key}
            task_id = self.metaai_tasks.enqueue('expert_delegate', payload)
            task = self.metaai_tasks.claim_next(task_id)
            if task is None:
                routing_events.finalize(self.db, event_id, 'dispatch_refused')
                raise ValueError('Meta AI task ' + task_id + ' already claimed by another bridge process')
            if self.metaai_inbox is None:
                # Garde défensive : ne jamais arriver à dispatch_write_inbox
                # avec une inbox non résolue (Path(None) levait un TypeError
                # générique, issue #267).
                routing_events.finalize(self.db, event_id, 'dispatch_refused')
                raise ValueError("Répertoire inbox Meta AI non résolu (bridge_root) ; corrigez la connexion Reprise metaai.json avant de redéléguer.")
            inbox_file = self.metaai_tasks.dispatch_write_inbox(task, self.metaai_inbox)
            result = {'status': 'finished', 'response': None, 'task_id': task_id,
                      'task_type': 'expert_delegate', 'inbox_file': inbox_file,
                      'dispatch': 'files_inbox',
                      'circuit': metaai_taskstore.CIRCUIT_DEMANDEUR,
                      'wait': 'reprise_wait_result jusqu a answered/failed (watcher metaaid)'}
            routing_events.finalize(self.db, event_id, 'dispatched')
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
        # Issue #117 T2a : aucune décision d'approbation par la voie MCP. Le refus
        # est levé avant tout reattach et ne consomme ni ne résout l'approbation
        # (elle reste pending). La décision opérateur passe par la voie
        # desktop_task_store.resolve_approval (CLI/app — tranches T2b/T2c de #117),
        # pas par cet outil.
        raise Unavailable('approval_disabled',
            'approval_respond est désactivé jusqu’à la tranche délégation (#117 T2b) : réponse opérateur requise.')

    # Issue #265 : refus journalisés par routing_events.finalize avant tout
    # envoi natif ; seuls ces états prouvent l'absence d'envoi d'une ligne
    # héritée (sans propriétaire d'admission).
    PRE_DISPATCH_REFUSALS = ('quota_blocked', 'billing_unverified', 'blocked_auth', 'engine_unavailable',
                             'dispatch_refused', 'busy', 'blocked_owner', 'blocked_session')

    def _release_undispatched(self, key):
        """Prove the absence of a native send for one Codex admission (#265).

        Codex persists its turn (on_turn « starting ») BEFORE turn/start, so a
        request without a delegation_turns row never sent a prompt. Other
        backends keep no such record: they are refused, never guessed.
        """
        event = self.db.execute(
            'SELECT channel, terminal_state FROM routing_events WHERE request_key=? ORDER BY id DESC LIMIT 1',
            (key,)).fetchone()
        if event is None or event[0] != 'codex':
            raise ValueError('Absence of dispatch is only provable for a routed Codex admission')
        evidence = {'backend': 'codex', 'routing_terminal_state': event[1], 'native_turn': None,
                    'pre_dispatch_refusal': event[1] in self.PRE_DISPATCH_REFUSALS}
        result = self.requests.release_undispatched(key, evidence)
        return self._bounded_full(key, dict(result, automatic_fallback=False))

    def _reconcile(self, args):
        if not isinstance(args, dict) or args.keys() - {'request_key', 'action', 'reason'}:
            raise ValueError('Invalid reconciliation arguments')
        key = args.get('request_key')
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise ValueError('Persist a nonempty request_key (at most 128 characters)')
        action = args.get('action', 'observe')
        if action not in ('observe', 'stop', 'release_undispatched'):
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
                        'reason_code': 'bridge_busy', 'automatic_retry': False,
                        'reason': 'Bridge busy: one active client owns the state; no concurrent reconciliation.'}
            receipt = self.requests.lookup({'request_key': key})
            if receipt.get('status') == 'not_found':
                raise RequestNotFound('Unknown request_key')
            if action == 'release_undispatched':
                return self._release_undispatched(key)
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
                        'reason_code': 'bridge_busy', 'automatic_retry': False,
                        'reason': 'Another live owner holds this request; reconcile later without a new turn.'}
            try:
                result = reconcile_turn(self.requests, self.root, key, action=action, reason=args.get('reason'),
                                        program=self.program, values=self.values)
                # Libération par observation : un tour terminal rend son slot disponible.
                self._release_terminal_slots()
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


class ExpertStdioHandler(StdioHandler):
    """Request semantics of the codex expert stdio server (wait_result workers)."""

    ERRORS = (ValueError, TypeError, KeyError, OSError, sqlite3.Error, RuntimeError)

    def __init__(self, bridge):
        self.bridge = bridge
        self.active = {}
        self.workers = set()
        self.active_lock = threading.Lock()

    def _cancel_all(self):
        with self.active_lock:
            for cancel, _ in self.active.values():
                cancel.set()

    def notification(self, message, connection):
        params = message.get('params')
        if message.get('method') != 'notifications/cancelled' or not isinstance(params, dict):
            return
        target = params.get('requestId')
        if isinstance(target, bool) or not isinstance(target, (str, int, float)):
            return
        try:
            key = json.dumps(target, ensure_ascii=False, allow_nan=False)
        except ValueError:
            return
        with self.active_lock:
            current = self.active.get(key)
            if current is not None:
                current[0].set()

    def request(self, message, connection):
        ident = message['id']
        if isinstance(ident, bool) or not isinstance(ident, (str, int, float)):
            ident = None
            raise ValueError('String or numeric JSON-RPC id required')
        try:
            key = json.dumps(ident, ensure_ascii=False, allow_nan=False)
        except ValueError:
            ident = None
            raise
        with self.active_lock:
            duplicate = key in self.active
        if duplicate:
            connection.send({'jsonrpc': '2.0', 'id': None,
                             'error': {'code': -32600, 'message': 'Duplicate in-flight JSON-RPC id'}})
            return
        params = message.get('params') or {}
        if not isinstance(params, dict):
            raise ValueError('Object params required')
        if message.get('method') == 'tools/call' and params.get('name') == 'wait_result':
            if 'wait_result' not in self.bridge.allowed_tools:
                raise ValueError('Tool not enabled for this integration')
            arguments = params.get('arguments', {})
            request_key, _ = wait_arguments(arguments)
            if len(json.dumps(ident, ensure_ascii=False)) > 256:
                raise ValueError('Compact calls require a bounded JSON-RPC id')
            cancel = threading.Event()
            thread = threading.Thread(target=self._worker,
                args=(ident, key, arguments, cancel, connection), daemon=True)
            with self.active_lock:
                workers = {item for item in self.workers if item.is_alive()}
                full = len(self.active) >= MAX_RESULT_WAITS
                if not full:
                    self.active[key] = (cancel, thread)
                    workers.add(thread)
                    self.workers = workers
            if full:
                connection.send({'jsonrpc': '2.0', 'id': ident, 'result': wait_content(
                    wait_status(request_key, 'busy'), ident)})
            else:
                try:
                    thread.start()
                except RuntimeError:
                    with self.active_lock:
                        self.active.pop(key, None)
                        self.workers.discard(thread)
                    raise
            return
        connection.send({'jsonrpc': '2.0', 'id': ident, 'result': self.bridge.dispatch(message)})

    def on_error(self, message, ident, exc, connection):
        # Never echo an invalid JSON-RPC id back: null it like request() does.
        if isinstance(ident, bool) or not isinstance(ident, (str, int, float)):
            ident = None
        else:
            try:
                if compact_call(message) and len(json.dumps(ident, ensure_ascii=False)) > 256:
                    ident = None
            except ValueError:
                ident = None
        connection.send({'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)})

    def _worker(self, ident, key, arguments, cancel, connection):
        try:
            result = self.bridge.wait_result(arguments, cancel)
            if result is not None and not cancel.is_set():
                connection.send({'jsonrpc': '2.0', 'id': ident,
                                 'result': wait_content(result, ident)})
        except Exception as exc:
            if not cancel.is_set():
                try:
                    connection.send({'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)})
                except OSError:
                    pass
        finally:
            with self.active_lock:
                if self.active.get(key, (None,))[0] is cancel:
                    self.active.pop(key, None)

    def shutdown(self, connection):
        self._cancel_all()
        deadline = time.monotonic() + 1.0
        for thread in self.workers:
            if thread.ident is not None:
                thread.join(max(0.0, deadline - time.monotonic()))


def serve(bridge, source, output):
    """Serve one connection; framing lives in mcp_transport, semantics above."""

    def write(text):
        output.write(text)
        output.flush()

    handler = ExpertStdioHandler(bridge)
    connection = mcp_transport.Connection(write, on_failure=handler._cancel_all)
    mcp_transport.run_stdio_loop(source, connection, handler,
                                 max_line_bytes=mcp_transport.CODEX_MAX_LINE_BYTES)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--socket')
    parser.add_argument('--state')
    parser.add_argument('--tools', help='Explicit comma-separated exposed tool allowlist; legacy default exposes all')
    parser.add_argument('--claude-program', help='Explicit absolute Claude CLI path; disabled by default')
    parser.add_argument('--claude-included-quota-confirmed', action='store_true', help='Operator confirms paid usage credits disabled; CLI cannot verify this')
    parser.add_argument('--desktop-socket', help='Explicit absolute Quick Entry socket path; disabled by default')
    parser.add_argument('--chatgpt-state',
                        help='ChatGPT state directory, ledger and TaskStore (not a network socket)')
    # #282 : dispatch holder actif par défaut ; --no-chatgpt-dispatch est la seule bascule.
    parser.add_argument('--no-chatgpt-dispatch', dest='chatgpt_dispatch', action='store_false', default=True,
                        help='Admission only: never push admitted ChatGPT tasks to the holder')
    parser.add_argument('--metaai-config',
                        help='JSON de connexion Meta AI (taskstore, bridge_root) ; défaut : connexion Reprise standard si présente')
    parser.add_argument('--ironclaw-config', help='JSON 0600: {host,port,user,key} du point d’acceptation IronClaw')
    args = parser.parse_args()
    ironclaw_values = {}
    if args.ironclaw_config:
        try:
            ironclaw_values = json.loads(Path(args.ironclaw_config).read_text())
        except (OSError, ValueError):
            raise SystemExit('--ironclaw-config doit être un JSON 0600 lisible')
    metaai_values = {}
    metaai_source = args.metaai_config
    if metaai_source is None:
        default_metaai = Path.home() / 'Library' / 'Application Support' / 'Reprise' / 'connections' / 'metaai.json'
        if default_metaai.is_file():
            metaai_source = str(default_metaai)
    if metaai_source is not None:
        try:
            metaai_values = json.loads(Path(metaai_source).read_text())
            if not isinstance(metaai_values, dict):
                raise ValueError('objet JSON attendu')
            if metaai_retired_keys(metaai_values):
                raise ValueError('clé dav_root supprimée (#276) ; la renommer bridge_root')
            if metaai_values and not (isinstance(metaai_values.get('taskstore'), str)
                                      and metaai_values.get('taskstore', '').strip()
                                      and metaai_bridge_root(metaai_values)):
                print('Warning: connexion Meta AI sans taskstore ou bridge_root valides, backend metaai non configuré', file=sys.stderr)
        except (OSError, ValueError) as detail:
            if args.metaai_config is not None:
                raise SystemExit('--metaai-config doit être un JSON lisible : ' + str(detail))
            print('Warning: connexion Meta AI illisible, backend metaai non configuré : ' + str(detail), file=sys.stderr)
            metaai_values = {}
    chatgpt_state = args.chatgpt_state or str(state_location(Path(__file__).parent, 'chatgpt-state').absolute())
    bridge = Bridge(args.project_root, {'socket': args.socket} if args.socket else {}, args.state or str(state_directory(Path(__file__).parent)),
                    claude_program=args.claude_program, claude_subscription_only=args.claude_included_quota_confirmed,
                    desktop_socket=args.desktop_socket, allowed_tools=args.tools.split(',') if args.tools is not None else None,
                    ironclaw_values=ironclaw_values,
                    chatgpt_state=chatgpt_state,
                    chatgpt_dispatch=args.chatgpt_dispatch, metaai_values=metaai_values)
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
        if bridge.chatgpt_tasks is not None:
            bridge.chatgpt_tasks.close()
        if bridge.metaai_tasks is not None:
            try:
                bridge.metaai_tasks._conn.close()
            except sqlite3.Error:
                pass
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == '__main__':
    main()
