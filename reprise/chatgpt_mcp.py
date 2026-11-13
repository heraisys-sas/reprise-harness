#!/usr/bin/env python3
"""One project-scoped ChatGPT MCP client: admit tasks, delegate outward, return results.

V1 consultative: the bridge admits every inbound request durably
(chatgpt_quick.Endpoint) and the roundtrip closes only on a validated
submit_result. Outbound delegation allows codex and claude-code backends;
loopback (backend chatgpt) is refused both at the schema layer (backend enum
filtered, aliases included) and at dispatch (reprise_backends payload filtered
in list AND dict forms).
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

from codex_expert_mcp import Bridge, error_payload, tool_content, bounded_content
from desktop_task_store import TaskStore
import mcp_transport
from mcp_transport import StdioHandler
from claude_mcp import CHATGPT_REMOVED_BACKENDS, INBOX_TOOLS as _CLAUDE_INBOX, outbound_tools, schema

OUTBOUND = {'reprise_backends', 'reprise_requests', 'reprise_quotas', 'reprise_delegate', 'reprise_followup', 'codex_review', 'codex_followup'}
# v1 policy: receive (get_task/submit_result/get_result) always; the queue and
# the bounded wait are Claude-façade opt-ins, not part of the ChatGPT contract.
INBOX_TOOLS = ('get_task', 'submit_result', 'get_result')
VERSION = Path(__file__).with_name('VERSION').read_text().strip()


def inbox_tools():
    return [copy.deepcopy(item) for item in _CLAUDE_INBOX if item['name'] in INBOX_TOOLS]


def claude_mcp_outbound():
    from claude_mcp import outbound_tools
    return outbound_tools(CHATGPT_REMOVED_BACKENDS)


def chatgpt_backends_payload(payload):
    """Filter reprise_backends payload: remove chatgpt in list and dict forms."""
    if isinstance(payload, dict):
        backends = payload.get('backends')
        if isinstance(backends, list):
            payload = dict(payload, backends=[item for item in backends
                                              if not (isinstance(item, dict) and item.get('id') == 'chatgpt')
                                              and item != 'chatgpt'])
        if isinstance(backends, dict):
            payload = dict(payload, backends={key: value for key, value in backends.items()
                                              if not (isinstance(value, dict) and value.get('id') == 'chatgpt')
                                              and key != 'chatgpt' and value != 'chatgpt'})
    return payload


class ChatGPTMCP:
    """Facade: inbox roundtrip on the bridge TaskStore, outbound via Bridge.

    Mirrors claude_mcp.ClaudeMCP.dispatch minus await_task/claim_next/
    register_session (v1 policy) and with the ChatGPT loopback removed.
    """

    def __init__(self, tasks, bridge):
        self.tasks, self.bridge = tasks, bridge
        self.tools_list = inbox_tools() + outbound_tools(CHATGPT_REMOVED_BACKENDS)
        self.definitions = {item['name']: item for item in inbox_tools()}

    def dispatch(self, message):
        method = message.get('method')
        params = message.get('params') or {}
        if method == 'initialize':
            return {'protocolVersion': mcp_transport.PROTOCOL_VERSION, 'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'reprise-harness', 'version': VERSION,
                                   'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
                    'instructions': ('Project-scoped bidirectional service for ChatGPT. Quick Apply admission '
                                     'is not dispatch proof. Persist request_key and claim_key. Only submit_result '
                                     'completes incoming tasks. ChatGPT loopback is disabled.')}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': self.tools_list}
        if method != 'tools/call' or not isinstance(params, dict):
            raise ValueError('Unsupported MCP method')
        name, args = params.get('name'), params.get('arguments', {})
        if not isinstance(args, dict):
            raise ValueError('Object arguments required')
        if name not in OUTBOUND and name not in INBOX_TOOLS:
            # v1 policy: no queue, no bounded wait, no session registration.
            raise ValueError('Unknown tool')
        if name in self.definitions:
            definition = self.definitions[name]
            expected = set(definition['inputSchema']['properties'])
            required = set(definition['inputSchema']['required'])
            if args.keys() - expected or required - args.keys():
                raise ValueError('Exact task arguments required')
            if name == 'get_task':
                result = self.tasks.claim(**args)
            elif name == 'submit_result':
                result = self.tasks.submit(**args)
            else:
                result = self.tasks.result(**args)
            if name == 'get_result' and args.get('view', 'receipt') != 'full':
                return bounded_content(result, message.get('id'))
            return tool_content(result)
        if args.get('backend') == 'chatgpt':
            raise ValueError('ChatGPT loopback is disabled')
        result = self.bridge.dispatch(message)
        if name == 'reprise_backends':
            payload = json.loads(result['content'][0]['text'])
            payload = chatgpt_backends_payload(payload)
            return tool_content(payload)
        return result


class ChatGPTStdioHandler(StdioHandler):
    ERRORS = (ValueError, TypeError, KeyError, RuntimeError, OSError, sqlite3.Error)

    def __init__(self, facade):
        self.facade = facade

    def notification(self, message, connection):
        pass

    def request(self, message, connection):
        params = message.get('params') or {}
        if not isinstance(params, dict):
            raise ValueError('Object params required')
        connection.send({'jsonrpc': '2.0', 'id': message['id'], 'result': self.facade.dispatch(message)})

    def on_error(self, message, ident, exc, connection):
        connection.send({'jsonrpc': '2.0', 'id': ident, 'error': error_payload(exc)})


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', required=True)
    parser.add_argument('--state', required=True,
                        help='ChatGPT endpoint state directory (admission ledger + roundtrip TaskStore)')
    parser.add_argument('--expert-state', required=True)
    args = parser.parse_args()
    # Endpoint + TaskStore + Bridge + transport : the Endpoint owns the durable
    # admission ledger and the roundtrip TaskStore; the Bridge gets the state
    # directory as chatgpt_socket (absolute path) so its chatgpt TasksStore
    # opens and the reprise_backends projection stays consistent.
    from chatgpt_quick import Endpoint
    state = Path(args.state).resolve()
    endpoint = Endpoint(state, args.project_root)
    tasks = endpoint.tasks
    bridge = Bridge(args.project_root, {}, args.expert_state,
                    desktop_state=state, allowed_tools=OUTBOUND, chatgpt_socket=str(state))
    facade = ChatGPTMCP(tasks, bridge)
    handler = ChatGPTStdioHandler(facade)

    def write(text):
        print(text, end='', flush=True)

    connection = mcp_transport.Connection(write)
    try:
        mcp_transport.run_stdio_loop(sys.stdin.buffer, connection, handler,
                                     max_line_bytes=mcp_transport.CLAUDE_MAX_LINE_BYTES)
    finally:
        connection.closed.set()
        bridge.db.close()
        endpoint.close()


if __name__ == '__main__':
    main()
