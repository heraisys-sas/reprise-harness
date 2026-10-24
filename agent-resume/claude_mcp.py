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

from codex_expert_mcp import Bridge, TOOLS, error_payload, tool_content, bounded_content
from desktop_task_store import TaskStore
from delegation_store import PAGE_PROPERTIES

TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 128}


def schema(properties, required=None):
    return {'type': 'object', 'properties': properties, 'required': list(properties) if required is None else required, 'additionalProperties': False}


INBOX_TOOLS = [
    {'name': 'get_task', 'description': 'Claim an explicitly identified project task. Persist claim_key before calling; reuse after restart.', 'inputSchema': schema({'request_key': TEXT, 'claim_key': TEXT})},
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
    def __init__(self, tasks, bridge):
        self.tasks, self.bridge = tasks, bridge

    def dispatch(self, message):
        method = message.get('method')
        params = message.get('params') or {}
        if method == 'initialize':
            return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {
                'name': 'reprise-claude', 'version': Path(__file__).with_name('VERSION').read_text().strip(),
                'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                'instructions': 'Project-scoped bidirectional service. Quick Apply admission is not dispatch proof. Persist request_key and claim_key. Claim an incoming task only when its request_key is explicitly supplied. Only submit_result completes incoming tasks. Claude Desktop loopback is disabled.'}
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
    args = parser.parse_args()
    tasks = TaskStore(args.state, args.project_root)
    bridge = Bridge(args.project_root, {'socket': args.socket} if args.socket else {}, args.expert_state, allowed_tools=OUTBOUND)
    facade = ClaudeMCP(tasks, bridge)
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
                    continue
                ident = message['id']
                reply = {'jsonrpc': '2.0', 'id': ident, 'result': facade.dispatch(message)}
            except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
                reply = {'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)}
            print(json.dumps(reply, ensure_ascii=False), flush=True)
    finally:
        tasks.close()
        bridge.db.close()


if __name__ == '__main__':
    main()
