import io
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reprise_memory_mcp import serve
from orchestrator_memory_service import MAX_INPUT_BYTES


class Sentinel:
    def tools(self):
        return []

    def call(self, name, args):
        raise AssertionError('Invalid transport must never reach a memory operation')


class ProtocolTests(unittest.TestCase):
    def exchange(self, frames):
        output = io.BytesIO()
        serve(Sentinel(), io.BytesIO(frames), output)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_initialize_notifications_ping_and_unknown_method(self):
        frames = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': 'future'}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'ping'},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'missing'},
        ]
        replies = self.exchange(b''.join(json.dumps(frame).encode() + b'\n' for frame in frames))
        self.assertEqual(len(replies), 4)
        self.assertEqual(replies[0]['result']['protocolVersion'], '2024-11-05')
        self.assertEqual(replies[1]['result']['tools'], [])
        self.assertEqual(replies[2]['result'], {})
        self.assertEqual(replies[3]['error']['code'], -32601)

    def test_malformed_duplicate_and_invalid_tool_frames_hide_payload(self):
        for raw in (b'PRIVATE invalid\n',
                    b'{"jsonrpc":"2.0","id":1,"id":2,"PRIVATE":"secret"}\n',
                    b'{"jsonrpc":"2.0","id":true,"method":"ping"}\n',
                    b'{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"PRIVATE":"secret"}}\n'):
            with self.subTest(raw=raw):
                reply = self.exchange(raw)[0]
                self.assertEqual(reply['error']['code'], -32602)
                self.assertNotIn('PRIVATE', json.dumps(reply))
                self.assertNotIn('secret', json.dumps(reply))
        self.assertEqual(self.exchange(b'x' * (MAX_INPUT_BYTES + 1) + b'\n'), [])
        self.assertEqual(self.exchange(b'{"jsonrpc":"2.0","id":1,"method":"ping"}'), [])

    def test_production_entrypoint_readonly_catalog_without_vault_or_model(self):
        # initialize/list do not open state, run Vault, or create memory.
        script = Path(__file__).resolve().parents[1] / 'reprise_memory_mcp.py'
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        result = subprocess.run([sys.executable, '-B', str(script), '--state', '/not-opened',
            '--project-id', '67e17924-f8a5-4144-a239-71625f618c4d', '--harness-id', 'read-only'],
            input=b''.join(json.dumps(m).encode() + b'\n' for m in messages), capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b'')
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        names = [tool['name'] for tool in replies[1]['result']['tools']]
        self.assertIn('reprise_memory_search', names)
        self.assertNotIn('reprise_memory_capture', names)
