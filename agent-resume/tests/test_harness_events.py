import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

HELPER = Path(__file__).resolve().parents[1] / 'harness-events.cjs'
BUNDLED_NODE = Path('/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/node/bin/node')
NODE = str(BUNDLED_NODE) if BUNDLED_NODE.is_file() else shutil.which('node')


@unittest.skipUnless(NODE, 'Node runtime unavailable')
class HarnessEventsTests(unittest.TestCase):
    def probe(self, events, compressed=False, suffix=b''):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ('session.jsonl.zstd' if compressed else 'session.jsonl')
            lines = [json.dumps(event) + '\n' for event in events]
            if compressed:
                program = "const fs=require('fs'),z=require('zlib'); if(!z.zstdCompressSync)process.exit(77); const lines=JSON.parse(fs.readFileSync(0,'utf8')); process.stdout.write(Buffer.concat(lines.map(x=>z.zstdCompressSync(Buffer.from(x)))));"
                packed = subprocess.run([NODE, '-e', program], input=json.dumps(lines).encode(), capture_output=True, check=False)
                if packed.returncode == 77:
                    self.skipTest('Node zstd unavailable')
                self.assertEqual(packed.returncode, 0, packed.stderr)
                path.write_bytes(packed.stdout + suffix)
            else:
                path.write_bytes(''.join(lines).encode() + suffix)
            return subprocess.run([NODE, str(HELPER), str(path)], capture_output=True, text=True)

    def test_concatenated_frames_reach_terminal_event(self):
        events = [{'type': 'session'}, {'type': 'turn/start', 'seq': 2, 'time': 100, 'data': {'turn': 21}},
                  {'type': 'turn/end', 'seq': 3, 'time': 200, 'data': {'turn': 21, 'reason': {'kind': 'max-tokens'}}}]
        for compressed in (False, True):
            with self.subTest(compressed=compressed):
                process = self.probe(events, compressed)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertEqual(json.loads(process.stdout), {'kind': 'max-tokens', 'code': None, 'seq': 3, 'time': 200, 'turn': 21})

    def test_manual_continuation_invalidates_exhausted_turn(self):
        events = [{'type': 'turn/end', 'seq': 2, 'data': {'turn': 21, 'reason': {'kind': 'max-tokens'}}},
                  {'type': 'user/message', 'seq': 3}]
        self.assertEqual(json.loads(self.probe(events, True).stdout)['kind'], 'pending_input')
        events.append({'type': 'turn/start', 'seq': 4, 'data': {'turn': 22}})
        self.assertEqual(json.loads(self.probe(events, True).stdout)['kind'], 'open')
        events.append({'type': 'turn/end', 'seq': 5, 'data': {'turn': 22, 'reason': {'kind': 'completed'}}})
        self.assertEqual(json.loads(self.probe(events, True).stdout)['kind'], 'completed')

    def test_error_code_retained(self):
        events = [{'type': 'turn/end', 'seq': 2, 'data': {'turn': 1, 'reason': {'kind': 'error', 'error': {'code': 'QUOTA'}}}}]
        self.assertEqual(json.loads(self.probe(events, True).stdout)['code'], 'QUOTA')

    def test_truncated_or_corrupt_tail_never_returns_stale_terminal(self):
        events = [{'type': 'turn/end', 'seq': 2, 'data': {'turn': 1, 'reason': {'kind': 'max-tokens'}}}]
        for tail in (b'broken', bytes.fromhex('28b52ffd')):
            process = self.probe(events, True, tail)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(process.stdout, '')


if __name__ == '__main__':
    unittest.main()
