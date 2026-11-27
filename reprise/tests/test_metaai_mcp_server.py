import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPRISE = Path(__file__).parents[1]
sys.path.insert(0, str(REPRISE))
SERVER = REPRISE / 'metaai_mcp_server.py'


class TestMetaaiMcpServer(unittest.TestCase):
    """Serveur MCP stdio réel (subprocess) sur un répertoire temporaire isolé."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        dav = self.root / 'webdav'
        (dav / 'inbox').mkdir(parents=True)
        (dav / 'outbox').mkdir(parents=True)
        self.config = self.root / 'metaai.json'
        self.config.write_text(json.dumps({
            'bridge_root': str(dav),
            'taskstore': str(self.root / 'tasks.sqlite'),
        }), encoding='utf-8')
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER), '--config', str(self.config)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
        )
        self._id = 0

    def tearDown(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.proc.terminate()
        self.tmp.cleanup()

    def send(self, method, params=None, notify=False):
        msg = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            msg['params'] = params
        if not notify:
            self._id += 1
            msg['id'] = self._id
        self.proc.stdin.write(json.dumps(msg) + '\n')
        self.proc.stdin.flush()
        return self._id

    def recv(self):
        return json.loads(self.proc.stdout.readline())

    def call(self, name, args=None):
        mid = self.send('tools/call', {'name': name, 'arguments': args or {}})
        response = self.recv()
        self.assertNotIn('error', response, response)
        return json.loads(response['result']['content'][0]['text'])

    def test_initialize_and_tools_list(self):
        self.send('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                 'clientInfo': {'name': 'test', 'version': '0'}})
        init = self.recv()
        self.assertEqual(init['result']['serverInfo']['name'], 'reprise-metaai')
        self.send('notifications/initialized', notify=True)
        self.send('tools/list')
        tools = self.recv()['result']['tools']
        names = {t['name'] for t in tools}
        self.assertEqual(names, {'enqueue_task', 'dispatch_task', 'get_task',
                                 'submit_result', 'get_status', 'list_tasks'})

    def test_dispatch_writes_inbox_and_status(self):
        tid = self.call('enqueue_task', {'type': 'question',
                                         'payload': {'question': 'Combien font 17 x 23 ?'}})['task_id']
        dispatched = self.call('dispatch_task', {'task_id': tid})
        inbox_file = Path(dispatched['inbox_path'])
        self.assertTrue(inbox_file.exists())
        self.assertEqual(json.loads(inbox_file.read_text(encoding='utf-8'))['task_id'], tid)
        result = self.call('submit_result', {'task_id': tid, 'result': {'reponse': '391'}})
        self.assertEqual(result['status'], 'answered')
        status = self.call('get_status', {'task_id': tid})
        self.assertEqual(status['status'], 'answered')
        self.assertEqual(status['result']['reponse'], '391')

    def test_get_task_claims_without_inbox_write(self):
        self.call('enqueue_task', {'type': 'question', 'payload': {'q': 'x'}})
        claimed = self.call('get_task')
        self.assertIsNotNone(claimed['task_id'])
        self.assertEqual(list((self.root / 'webdav' / 'inbox').glob('*.json')), [])

    def test_dispatch_without_pending_returns_none(self):
        result = self.call('dispatch_task')
        self.assertIsNone(result['task_id'])


if __name__ == '__main__':
    unittest.main()
