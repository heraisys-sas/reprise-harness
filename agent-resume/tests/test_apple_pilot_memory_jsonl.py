import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_orchestrator_memory as fixtures

SOURCE, uid = fixtures.SOURCE, fixtures.uid


class JsonlMemoryTests(unittest.TestCase):
    def setUp(self):
        fixtures.MemoryTests.setUp(self)

    def tearDown(self):
        fixtures.MemoryTests.tearDown(self)

    def run_process(self, requests):
        root = str(Path(__file__).resolve().parents[1])
        tests = str(Path(__file__).resolve().parent)
        code = '''import sys
sys.path[:0]=[sys.argv[1],sys.argv[2]]
import apple_pilot_service as service
import orchestrator_memory as memory
from test_orchestrator_memory import fake_crypt
sys.exit(service.serve(sys.stdin.buffer,sys.stdout.buffer,sys.argv[3],memory_factory=lambda state:memory.Memory(state,crypt=fake_crypt)))'''
        result = subprocess.run([sys.executable, '-c', code, root, tests, str(self.state)],
                                input=''.join(json.dumps(x) + '\n' for x in requests),
                                text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        return [json.loads(line) for line in result.stdout.splitlines()]

    def execute(self, request):
        bind_id, call_id = uid(), uid()
        replies = self.run_process([{'id':bind_id, 'op':'memory_bind', 'enabled':True, 'project_id':self.p1},
                                    {'id':call_id, 'op':'memory_call', 'request':request}])
        by_id = {item['id']:item for item in replies}
        self.assertEqual(by_id[bind_id]['status'], 'bound')
        return by_id[call_id]

    def test_mutation_survives_host_restart_without_inference_or_plaintext(self):
        operation = uid()
        request = {'op':'create_fact', 'operation_id':operation, 'text':'PRIVATE INPUT', 'source':SOURCE}
        first = self.execute(request)
        self.assertEqual(first['status'], 'finished')
        status = self.execute({'op':'operation_status', 'operation_id':operation})
        self.assertEqual(status['state'], 'recorded')
        self.assertEqual(status['result'], first['result'])
        replay = self.execute(request)
        self.assertEqual(replay['result'], first['result'])
        listing = self.execute({'op':'list_intents', 'offset':0})
        self.assertEqual(len(listing['items']), 1)
        self.assertNotIn('PRIVATE INPUT', json.dumps(listing))
        facts = self.execute({'op':'list_facts', 'offset':0})
        self.assertEqual(len(facts['items']), 1)
        self.assertNotIn('PRIVATE INPUT', json.dumps(facts))


if __name__ == '__main__': unittest.main()
