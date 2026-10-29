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
    {'name': 'await_task', 'description': 'Wait (bounded, no inference) for one explicit project task admitted through Quick Apply, then hand it over once. Call it only in a chat the operator armed for that exact request_key. When idle, stop unless the operator asked you to keep waiting. A task already handed over is never handed over again by this tool.', 'inputSchema': schema({'request_key': TEXT, 'claim_key': TEXT, 'wait_seconds': {'type': 'integer', 'minimum': 1, 'maximum': MAX_WAIT_CEILING}}, ['request_key', 'claim_key'])},
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
    def __init__(self, tasks, bridge, max_wait=DEFAULT_MAX_WAIT, poll=0.25, progress_interval=0, clock=time.monotonic, wait=None):
        if type(max_wait) is not int or not 1 <= max_wait <= MAX_WAIT_CEILING:
            raise ValueError('max_wait must be an integer from 1 to %d' % MAX_WAIT_CEILING)
        self.tasks, self.bridge = tasks, bridge
        # progress_interval = 0 (default): no progress notification, i.e. no artificial heartbeat.
        self.max_wait, self.poll, self.progress_interval = max_wait, poll, progress_interval
        self.clock = clock
        # wait(cancel, seconds): injectable so tests control time without sleeping.
        self.wait = wait or (lambda cancel, seconds: cancel.wait(seconds) if cancel is not None else time.sleep(seconds))

    def await_task(self, store, args, token=None, cancel=None, emit=None):
        """Bounded wait for one exact task. Returns a tool result, or None when the request was cancelled.

        Delivery protocol: reserve -> (cancelled before the intention is recorded: release a fresh
        reservation) -> record the delivery intention -> hand the prompt over. After the intention is
        recorded the task is never released nor handed over again by this tool.
        """
        wait = args.get('wait_seconds', self.max_wait)
        if type(wait) is not int or not 1 <= wait <= self.max_wait:
            raise ValueError('wait_seconds must be an integer from 1 to %d' % self.max_wait)
        request_key, claim_key = args['request_key'], args['claim_key']
        started = self.clock()
        deadline = started + wait
        last_progress, count, interval = started, 0, self.poll
        while True:
            reserved = store.reserve(request_key, claim_key)
            state = reserved['state']
            if state == 'completed':
                return tool_content({'status': 'finished', 'task': False, 'delivery': 'completed', 'request_key': request_key,
                                     'attempt_id': reserved['attempt_id'],
                                     'instruction': 'Task already completed; retrieve the result with get_result.'})
            if state == 'attempted':
                return tool_content({'status': 'finished', 'task': False, 'delivery': 'attempted', 'request_key': request_key,
                                     'attempt_id': reserved['attempt_id'],
                                     'instruction': 'The prompt was already handed over by an earlier call; do not execute it from here. If you hold it, finish and call submit_result; otherwise ask the operator.'})
            if state == 'reserved':
                if cancel is not None and cancel.is_set():
                    if reserved['fresh']:
                        store.release(request_key, reserved['attempt_id'])
                    return None
                if not store.mark_attempted(request_key, reserved['attempt_id']):
                    continue  # another wait recorded the intention first: next loop returns the receipt
                return tool_content({'status': 'finished', 'task': True, 'delivery': 'handed_over', 'request_key': request_key,
                                     'attempt_id': reserved['attempt_id'], 'prompt': reserved['prompt'], 'chat_id': reserved['chat_id'],
                                     'instruction': 'Perform the scoped task, then call submit_result with this attempt_id. Do not call await_task again unless the operator asked you to.'})
            if cancel is not None and cancel.is_set():
                return None
            now = self.clock()
            if now >= deadline:
                return tool_content({'status': 'finished', 'idle': True, 'task': False, 'request_key': request_key,
                                     'waited_seconds': round(now - started, 1),
                                     'instruction': 'No task yet for this request_key. Do not call await_task again unless the operator asked you to keep waiting.'})
            if token is not None and emit is not None and self.progress_interval and now - last_progress >= self.progress_interval:
                count += 1
                last_progress = now
                emit({'jsonrpc': '2.0', 'method': 'notifications/progress',
                      'params': {'progressToken': token, 'progress': count, 'message': 'Waiting for a Reprise task'}})
            # Arrival comes from another process (the Quick Apply daemon): a bounded local poll with
            # backoff is the minimal mechanism. It costs no model turn and sends nothing to the client.
            self.wait(cancel, min(interval, max(0.0, deadline - now)))
            interval = min(interval * 2, 1.0)

    def deliver_wait(self, ident, store, params, cancel, write, emit=None):
        """Run one await_task request and write its single response; False when nothing was written."""
        token = (params.get('_meta') or {}).get('progressToken')
        if token is not None and not (isinstance(token, (str, int)) and not isinstance(token, bool)):
            raise ValueError('Invalid progressToken')
        result = self.await_task(store, params.get('arguments') or {}, token, cancel, emit)
        if result is None or cancel.is_set():
            return False  # cancelled: the protocol expects no response; an attempted delivery stays uncertain
        write({'jsonrpc': '2.0', 'id': ident, 'result': result})
        return True

    def dispatch(self, message):
        method = message.get('method')
        params = message.get('params') or {}
        if method == 'initialize':
            return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {
                'name': 'reprise-harness', 'version': Path(__file__).with_name('VERSION').read_text().strip(),
                'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                'instructions': 'Project-scoped bidirectional service. Quick Apply admission is not dispatch proof. Persist request_key and claim_key. Claim an incoming task only when its request_key is explicitly supplied, or by calling await_task for an explicit request_key in a chat the operator armed for that purpose. Only submit_result completes incoming tasks. Claude Desktop loopback is disabled.'}
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
    parser.add_argument('--progress-interval', type=float, default=0,
                        help='seconds between notifications/progress during await_task; 0 (default) sends none')
    args = parser.parse_args()
    tasks = TaskStore(args.state, args.project_root)
    bridge = Bridge(args.project_root, {'socket': args.socket} if args.socket else {}, args.expert_state, allowed_tools=OUTBOUND)
    facade = ClaudeMCP(tasks, bridge, max_wait=args.max_wait, progress_interval=args.progress_interval)
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
            facade.deliver_wait(ident, store, message.get('params') or {}, cancel, send, send)
        except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
            if not cancel.is_set():
                try:
                    send({'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)})
                except OSError:
                    pass
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
                    if not isinstance(arguments, dict) or arguments.keys() - set(definition['inputSchema']['properties']) or not {'request_key', 'claim_key'} <= arguments.keys():
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
