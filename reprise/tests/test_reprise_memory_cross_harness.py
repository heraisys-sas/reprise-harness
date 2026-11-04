"""Real MCP stdio processes/storage; test-only crypto, never user Keychain."""
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_orchestrator_memory as fixtures

BOOTSTRAP = '''import sys
sys.path[:0]=sys.argv[1:3]
from test_orchestrator_memory import fake_crypt
from orchestrator_memory import Memory
from reprise_memory_adapter import Adapter
from reprise_memory_mcp import serve
adapter=Adapter(sys.argv[3],sys.argv[4],sys.argv[5],allow_write=sys.argv[6]=='write',
 memory_factory=lambda state:Memory(state,crypt=fake_crypt))
serve(adapter,sys.stdin.buffer,sys.stdout.buffer)
'''


class CrossHarnessTests(unittest.TestCase):
    def setUp(self):
        fixtures.MemoryTests.setUp(self)
        self.children = []
        self.sequence = 0
        self.a = self.start(self.p1, 'a', True)
        self.b = self.start(self.p1, 'b', True)
        self.q = self.start(self.p2, 'b', True)
        self.reader = self.start(self.p1, 'reader', False)

    def tearDown(self):
        for process in self.children:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
        fixtures.MemoryTests.tearDown(self)

    def start(self, project, name, write):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.Popen([sys.executable, '-B', '-c', BOOTSTRAP, str(root), str(root / 'tests'),
            str(self.state), project, name, 'write' if write else 'read'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, bufsize=0)
        self.children.append(process)
        return process

    def rpc(self, process, method, params=None):
        self.sequence += 1
        frame = {'jsonrpc': '2.0', 'id': self.sequence, 'method': method}
        if params is not None:
            frame['params'] = params
        process.stdin.write(json.dumps(frame).encode() + b'\n'); process.stdin.flush()
        self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'MCP response timed out')
        reply = json.loads(process.stdout.readline())
        self.assertEqual(reply['id'], self.sequence)
        return reply['result']

    def call(self, process, name, **fields):
        response = self.rpc(process, 'tools/call', {'name': 'reprise_memory_' + name, 'arguments': fields})
        return json.loads(response['content'][0]['text'])

    def test_two_harnesses_roundtrip_recovery_history_and_project_boundary(self):
        self.assertEqual(self.rpc(self.a, 'initialize')['serverInfo']['name'], 'reprise-project-memory')
        operation = fixtures.uid()
        fields = dict(operation_id=operation, text='Atlas : capture explicite inter-harness', source=fixtures.SOURCE)
        created = self.call(self.a, 'capture', **fields)
        self.assertEqual(created['effect'], 'recorded')
        ident = created['result']['id']
        self.assertEqual(self.call(self.a, 'capture', **fields), created)
        self.assertEqual(self.call(self.b, 'search', query='atlas', offset=0)['items'][0]['id'], ident)
        item = self.call(self.b, 'get', fact_id=ident)['item']
        self.assertEqual(item['recorded_by']['harness_id'], 'a')
        self.assertEqual(item['source'], fixtures.SOURCE)
        self.assertEqual(self.call(self.q, 'get', fact_id=ident)['status'], 'not_found')
        self.assertEqual(self.call(self.q, 'search', query='atlas', offset=0)['items'], [])
        self.assertEqual(self.call(self.q, 'get', fact_id=ident, project_id=self.p1)['status'], 'invalid_request')
        self.assertNotIn('reprise_memory_capture', [t['name'] for t in self.rpc(self.reader, 'tools/list')['tools']])
        self.assertEqual(self.call(self.reader, 'capture', **fields)['status'], 'permission_denied')
        work = fixtures.uid()
        checkpoint = self.call(self.a, 'checkpoint', operation_id=fixtures.uid(), work_id=work,
            expected_version=0, fact_ids=[ident], decisions=['relire'], constraints=[], next_action='vérifier',
            uncertain_effects=['appel non confirmé'], source=fixtures.SOURCE)['result']['id']
        correction = dict(operation_id=fixtures.uid(), fact_id=ident, expected_revision=1,
                          text='Atlas corrigé par B', source=fixtures.SOURCE)
        self.assertEqual(self.call(self.b, 'correct', **correction)['result']['revision'], 2)
        updated = self.call(self.a, 'get', fact_id=ident)['item']
        self.assertEqual(updated['recorded_by']['harness_id'], 'b')
        self.assertEqual(self.call(self.a, 'correct', **(correction | {'operation_id': fixtures.uid()}))['status'], 'version_conflict')
        deleted = self.call(self.b, 'delete', operation_id=fixtures.uid(), fact_id=ident, expected_revision=2)
        self.assertEqual(deleted['deletion_scope'], 'current_fact_only')
        self.assertEqual(self.call(self.a, 'get', fact_id=ident)['status'], 'not_found')
        historical = self.call(self.b, 'get_checkpoint', work_id=work, checkpoint_id=checkpoint)
        self.assertTrue(historical['historical_snapshot'])
        self.assertEqual(historical['item']['payload']['facts'][0]['revision'], 1)
        self.assertEqual(self.call(self.b, 'get_checkpoint', work_id=fixtures.uid(), checkpoint_id=checkpoint)['status'], 'not_found')
        # Fresh server sees durable original operation, not a duplicate capture.
        restarted = self.start(self.p1, 'a', True)
        self.assertEqual(self.call(restarted, 'operation_status', operation_id=operation)['state'], 'recorded')
        self.assertEqual(self.call(restarted, 'capture', **fields), created)
        self.assertEqual(self.call(restarted, 'capture', **(fields | {'text': 'different'}))['status'], 'operation_conflict')
        for child in self.children:
            child.stdin.close(); child.wait(timeout=5)
            self.assertEqual(child.returncode, 0)
            self.assertEqual(child.stderr.read(), b'')

    def test_maximal_unicode_and_escaped_text_is_recoverable_over_real_stdio(self):
        for text in ('é' * 8192, '"' * 16384, '\\' * 16384):
            with self.subTest(character=text[0]):
                ident = self.call(self.a, 'capture', operation_id=fixtures.uid(), text=text,
                                  source=fixtures.SOURCE)['result']['id']
                result = self.call(self.b, 'get', fact_id=ident)
                if result['status'] == 'finished':
                    self.assertEqual(result['item']['text'], text)
                else:
                    self.assertEqual(result['retrieval'], 'reprise_memory_get_chunk')
                    chunks = []
                    offset = 0
                    while offset is not None:
                        chunk = self.call(self.b, 'get_chunk', fact_id=ident,
                                          expected_revision=result['revision'], offset=offset)
                        self.assertEqual(chunk['status'], 'finished')
                        chunks.append(chunk['text_chunk'])
                        offset = chunk['next_offset']
                    self.assertEqual(''.join(chunks), text)
                self.call(self.a, 'correct', operation_id=fixtures.uid(), fact_id=ident,
                          expected_revision=1, text='nouvelle révision', source=fixtures.SOURCE)
                self.assertEqual(self.call(self.b, 'get_chunk', fact_id=ident,
                    expected_revision=1, offset=0)['status'], 'version_conflict')
