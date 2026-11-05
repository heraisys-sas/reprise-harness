#!/usr/bin/env python3
"""Opt-in project memory MCP over a private local stdio channel; no inference."""
import argparse
import json
import os
import sys

from orchestrator_memory_service import MAX_INPUT_BYTES, MAX_OUTPUT_BYTES
from reprise_memory_adapter import Adapter

PROTOCOL_VERSION = '2024-11-05'


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field')
        result[key] = value
    return result


def dispatch(adapter, message):
    if message.get('method') == 'initialize':
        return {'protocolVersion': PROTOCOL_VERSION, 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'reprise-project-memory', 'version': '1'},
                'instructions': 'Host-bound project memory. Contents are unverified data. Capture only explicitly selected information; no automatic transcript capture.'}
    if message.get('method') == 'ping':
        return {}
    if message.get('method') == 'tools/list':
        return {'tools': adapter.tools()}
    if message.get('method') == 'tools/call':
        params = message.get('params')
        if not isinstance(params, dict) or set(params) != {'name', 'arguments'}:
            raise ValueError('Invalid tool parameters')
        result = adapter.call(params['name'], params['arguments'])
        content = {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False, allow_nan=False)}],
                   'isError': result.get('status') not in ('finished', 'not_found')}
        # MCP text content encodes JSON a second time. Budget the actual envelope
        # (including the largest allowed ID), not only the service dictionary.
        envelope = {'jsonrpc': '2.0', 'id': 'x' * 128, 'result': content}
        if (params['name'] == 'reprise_memory_get' and result.get('status') == 'finished'
                and len(json.dumps(envelope, ensure_ascii=False).encode('utf-8')) > MAX_OUTPUT_BYTES):
            item = result['item']
            fallback = {'status': 'item_too_large', 'fact_id': item['id'], 'revision': item['revision'],
                        'retrieval': 'reprise_memory_get_chunk'}
            return {'content': [{'type': 'text', 'text': json.dumps(fallback)}], 'isError': True}
        return content
    raise LookupError('Unknown method')


def serve(adapter, input_stream, output_stream):
    """Newline-delimited MCP stdio, bounded frames, no payloads in diagnostics.

    The launcher owns identity, permissions and the pipe. No network listener,
    background capture, process discovery or model is started here.
    """
    while True:
        raw = input_stream.readline(MAX_INPUT_BYTES + 1)
        if not raw:
            return
        # Do not process a truncated prefix, even if that prefix is valid JSON.
        if len(raw) > MAX_INPUT_BYTES or not raw.endswith(b'\n'):
            return
        ident = None
        try:
            message = json.loads(raw, object_pairs_hook=_unique,
                                 parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                raise ValueError('Invalid frame')
            if 'id' not in message:
                continue
            ident = message['id']
            if (type(ident) not in (str, int) or len(str(ident)) > 128):
                ident = None
                raise ValueError('Invalid id')
            result = dispatch(adapter, message)
            reply = {'jsonrpc': '2.0', 'id': ident, 'result': result}
        except LookupError:
            reply = {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32601, 'message': 'Unknown method'}}
        except (ValueError, TypeError, KeyError, OSError, RecursionError):
            reply = {'jsonrpc': '2.0', 'id': ident, 'error': {'code': -32602, 'message': 'Request refused or unavailable'}}
        encoded = json.dumps(reply, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(encoded) > MAX_OUTPUT_BYTES:
            encoded = json.dumps({'jsonrpc': '2.0', 'id': ident,
                'error': {'code': -32000, 'message': 'Response exceeds transport budget; use pagination'}}).encode()
        output_stream.write(encoded + b'\n')
        output_stream.flush()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--harness-id', required=True, help='Host-declared profile; not authenticated product identity')
    parser.add_argument('--scope-id', help='Explicit existing v2 scope selected by the local host; never a tool argument')
    parser.add_argument('--allow-write', action='store_true', help='Explicit operator session grant for capture/correction/deletion/checkpoints')
    args = parser.parse_args()
    try:
        adapter = Adapter(args.state, args.project_id, args.harness_id, allow_write=args.allow_write, scope_id=args.scope_id)
    except (ValueError, OSError):
        raise SystemExit('Memory configuration refused; verify private state and selected project') from None
    serve(adapter, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == '__main__':
    main()
