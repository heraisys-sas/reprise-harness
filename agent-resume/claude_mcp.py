#!/usr/bin/env python3
"""One project-scoped Claude MCP client: receive tasks, return results, ask experts."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

from codex_expert_mcp import Bridge, TOOLS, error_payload, tool_content, bounded_content
from desktop_task_store import TaskStore
from delegation_store import PAGE_PROPERTIES

TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 128}
MAX_WAIT_CEILING = 600
DEFAULT_MAX_WAIT = 45


def schema(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required, 'additionalProperties': False}


INBOX_TOOLS = [
    {'name': 'get_task', 'description': 'Claim an explicitly identified project task. Persist claim_key before calling; reuse after restart.', 'inputSchema': schema({'request_key': TEXT, 'claim_key': TEXT})},
    {'name': 'await_task', 'description': 'Wait (bounded, no inference) for the next task the operator admitted through Quick Apply, claim it atomically and return it. Call it only in a chat the operator armed to receive Reprise tasks. When idle, call it again with the same claim_key.', 'inputSchema': schema({'claim_key': TEXT, 'wait_seconds': {'type': 'integer', 'minimum': 1, 'maximum': MAX_WAIT_CEILING}}, ['claim_key'])},
    {'name': 'submit_result', 'description': 'Complete a claimed task. Only this operation marks completion; identical retries are idempotent.', 'inputSchema': schema({'request_key': TEXT, 'attempt_id': TEXT, 'answer': {'type': 'string', 'minLength': 1, 'maxLength': 2_000_000}})},
    {'name': 'get_result', 'description': 'Retrieve a durable result. Acknowledge explicitly only after safely retaining it.', 'inputSchema': schema({'request_key': TEXT, 'acknowledge': {'type': 'boolean', 'default': False}, 'view': {'type': 'string', 'enum': ['receipt','page','full'], 'default': 'receipt'}, **PAGE_PROPERTIES}, ['request_key'])},
]
OUTBOUND = {'reprise_backends', 'reprise_requests', 'reprise_quotas', 'reprise_delegate', 'reprise_followup', 'codex_review', 'codex_followup'}


def outbound_tools():
    tools = copy.deepcopy([item for item in TOOLS if item['name'] in OUTBOUND])
    for item in tools:
        backend = item['inputSchema'].get('properties', {}).get('backend')
        if backend:
            backend['enum'] = [value for value in backend['enum'] if value != 'claude-desktop']
    return tools


class ClaudeMCP:
    def __init__(self, tasks, bridge, max_wait=DEFAULT_MAX_WAIT, poll=0.25, progress_interval=10.0):
        if type(max_wait) is not int or not 1 <= max_wait <= MAX_WAIT_CEILING:
            raise ValueError('max_wait must be an integer from 1 to %d' % MAX_WAIT_CEILING)
        self.tasks, self.bridge = tasks, bridge
        self.max_wait, self.poll, self.progress_interval = max_wait, poll, progress_interval

    def await_task(self, store, args, token=None, cancel=None, emit=None):
        """Bounded long-poll. Returns a tool result, or None when the request was cancelled."""
        wait = args.get('wait_seconds', self.max_wait)
        if type(wait) is not int or not 1 <= wait <= self.max_wait:
            raise ValueError('wait_seconds must be an integer from 1 to %d' % self.max_wait)
        deadline = time.monotonic() + wait
        started = time.monotonic()
        last_progress, count = started, 0
        while True:
            claimed = store.claim_next(args['claim_key'])
            if claimed is not None:
                return tool_content(claimed)
            if cancel is not None and cancel.is_set():
                return None
            now = time.monotonic()
            if now >= deadline:
                return tool_content({'status': 'finished', 'idle': True, 'task': False, 'waited_seconds': round(now - started, 1),
                                     'instruction': 'No task waiting. Call await_task again with the same claim_key.'})
            if token is not None and emit is not None and now - last_progress >= self.progress_interval:
                count += 1
                last_progress = now
                emit({'jsonrpc': '2.0', 'method': 'notifications/progress',
                      'params': {'progressToken': token, 'progress': count, 'message': 'Waiting for a Reprise task'}})
            pause = min(self.poll, max(0.0, deadline - now))
            if cancel is not None:
                cancel.wait(pause)
            else:
                time.sleep(pause)

    def dispatch(self, message):
        method = message.get('method')
        params = message.get('params') or {}
        if method == 'initialize':
            return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {
                'name': 'reprise-harness', 'version': Path(__file__).with_name('VERSION').read_text().strip(),
                'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                'instructions': 'Project-scoped bidirectional service. Quick Apply admission is not dispatch proof. Persist request_key and claim_key. Claim an incoming task only when its request_key is explicitly supplied, or by calling await_task in a chat the operator armed for that purpose. Only submit_result completes incoming tasks. Claude Desktop loopback is disabled.'}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': INBOX_TOOLS + outbound_tools()}
        if method != 'tools/call' or not isinstance(params, dict):
            raise ValueError('Unsupported MCP method')
        name, args = params.get('name'), params.get('arguments', {})
        if not isinstance(args, dict):
            raise ValueError('Object arguments required')
        definitions = {item['name']: item for item in INBOX_TOOLS}
        if name in definitions:
            expected = set(definitions[name]['inputSchema']['properties'])
            required = set(definitions[name]['inputSchema']['required'])
            if args.keys() - expected or required - args.keys():
                raise ValueError('Exact task arguments required')
            if name == 'get_result' and args.get('view', 'receipt') != 'full' and len(json.dumps(message.get('id'), ensure_ascii=False)) > 256:
                raise ValueError('Compact calls require a bounded JSON-RPC id')
            if name == 'await_task':
                token = (params.get('_meta') or {}).get('progressToken')
                if token is not None and not (isinstance(token, (str, int)) and not isinstance(token, bool)):
                    raise ValueError('Invalid progressToken')
                answer = self.await_task(self.tasks, args, token)
                return answer
            if name == 'get_task':
                result = self.tasks.claim(**args)
            elif name == 'submit_result':
                result = self.tasks.submit(**args)
            else:
                result = self.tasks.result(**args)
            if name == 'get_result' and args.get('view', 'receipt') != 'full':
                return bounded_content(result, message.get('id'))
            return tool_content(result)
        if name not in OUTBOUND:
            raise ValueError('Tool not enabled')
        if args.get('backend') == 'claude-desktop':
            raise ValueError('Claude Desktop loopback is disabled')
        # Reuse ownership, budgets, bounded pagination and native dispatch unchanged.
        result = self.bridge.dispatch(message)
        if name == 'reprise_backends':
            payload = json.loads(result['content'][0]['text'])
            # Preserve shape while removing this client's reentrant backend route.
            backends = payload.get('backends')
            if isinstance(backends, list):
                payload['backends'] = [item for item in backends if item.get('id', item.get('backend', item.get('name'))) != 'claude-desktop']
            elif isinstance(backends, dict):
                backends.pop('claude-desktop', None)
            return tool_content(payload)
        return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--expert-state', required=True)
    parser.add_argument('--socket')
    parser.add_argument('--max-wait', type=int, default=DEFAULT_MAX_WAIT,
                        help='longest await_task wait in seconds; raise only after proving the client tolerates it')
    args = parser.parse_args()
    tasks = TaskStore(args.state, args.project_root)
    bridge = Bridge(args.project_root, {'socket': args.socket} if args.socket else {}, args.expert_state, allowed_tools=OUTBOUND)
    facade = ClaudeMCP(tasks, bridge, max_wait=args.max_wait)
    send_lock = threading.Lock()
    active, workers = {}, []

    def send(payload):
        with send_lock:
            print(json.dumps(payload, ensure_ascii=False), flush=True)

    def wait_worker(ident, message, cancel):
        # Own connection: sqlite connections are not shared across threads.
        store = None
        try:
            store = TaskStore(args.state, args.project_root)
            params = message.get('params') or {}
            token = (params.get('_meta') or {}).get('progressToken')
            if token is not None and not (isinstance(token, (str, int)) and not isinstance(token, bool)):
                raise ValueError('Invalid progressToken')
            result = facade.await_task(store, params.get('arguments') or {}, token, cancel, send)
            if result is None or cancel.is_set():
                return  # cancelled: the protocol expects no response
            send({'jsonrpc': '2.0', 'id': ident, 'result': result})
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
            if not cancel.is_set():
                send({'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)})
        finally:
            active.pop(json.dumps(ident), None)
            if store is not None:
                store.close()

    try:
        while True:
            raw = sys.stdin.buffer.readline(8_000_001)
            if not raw or len(raw) > 8_000_000:
                break
            ident = None
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError('Object required')
                if 'id' not in message:
                    if message.get('method') == 'notifications/cancelled':
                        event = active.get(json.dumps((message.get('params') or {}).get('requestId')))
                        if event is not None:
                            event.set()
                    continue
                ident = message['id']
                params = message.get('params') or {}
                if message.get('method') == 'tools/call' and isinstance(params, dict) and params.get('name') == 'await_task':
                    arguments = params.get('arguments', {})
                    definition = next(item for item in INBOX_TOOLS if item['name'] == 'await_task')
                    if not isinstance(arguments, dict) or arguments.keys() - set(definition['inputSchema']['properties']) or 'claim_key' not in arguments:
                        raise ValueError('Exact task arguments required')
                    cancel = threading.Event()
                    active[json.dumps(ident)] = cancel
                    worker = threading.Thread(target=wait_worker, args=(ident, message, cancel), daemon=True)
                    workers.append(worker)
                    worker.start()
                    continue
                send({'jsonrpc': '2.0', 'id': ident, 'result': facade.dispatch(message)})
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
                send({'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)})
    finally:
        for event in list(active.values()):
            event.set()
        for worker in workers:
            worker.join(timeout=2)
        tasks.close()
        bridge.db.close()


if __name__ == '__main__':
    main()
