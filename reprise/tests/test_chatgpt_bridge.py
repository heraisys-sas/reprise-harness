import sys
import tempfile
import unittest
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import Bridge
from desktop_task_store import TaskStore


class RequestCalls:
    def call(self, name, args):
        if name not in ('reprise_backends', 'reprise_health', 'reprise_resume', 'reprise_history', 'reprise_requests'):
            args = {'request_key': str(uuid.uuid4()), **args}
        return self.bridge.call(name, args)


class ChatgptBridgeTests(RequestCalls, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state', daily_limit=2)
        self.bridge.program = 'codex'
        self.addCleanup(self.bridge.close)

    def test_backends_lists_chatgpt_unconfigured_by_default(self):
        backends = self.call('reprise_backends', {})['backends']
        self.assertNotIn('chatgpt', [entry['id'] for entry in backends])

    def test_delegate_chatgpt_without_socket_returns_engine_unavailable(self):
        payload = self.call('reprise_delegate', {'question': 'Revue', 'kind': 'review', 'backend': 'chatgpt'})
        self.assertEqual(payload['status'], 'engine_unavailable')
        self.assertEqual(payload['reason'], '--chatgpt-socket non configuré')

    def test_implementation_refused_in_v1(self):
        with self.assertRaises(ValueError) as caught:
            self.call('reprise_delegate', {'question': 'Implement', 'backend': 'chatgpt',
                                           'kind': 'batch', 'mode': 'implementation'})
        self.assertIn('consultative expertise/review only', str(caught.exception))

    def test_chatgpt_model_slot_is_empty_and_rejected(self):
        self.bridge.chatgpt_socket = '/tmp/chatgpt.sock'
        entry = [b for b in self.call('reprise_backends', {})['backends'] if b['id'] == 'chatgpt'][0]
        self.assertEqual(entry['models'], [])
        with self.assertRaises(ValueError) as caught:
            self.call('reprise_delegate', {'question': 'Revue', 'backend': 'chatgpt', 'model': 'gpt-5'})
        self.assertIn('Unknown or unsupported model for backend chatgpt', str(caught.exception))


class ChatgptProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'chatgpt-state'
        self.store = TaskStore(self.state, self.root)
        self.addCleanup(self.store.close)

    def test_chatgpt_projection_routes_requests_and_wait_to_chatgpt_state(self):
        bridge = Bridge(self.root, {}, self.root / 'expert', daily_limit=2,
                        desktop_state=self.root / 'desktop', chatgpt_socket=str(self.state))
        self.addCleanup(bridge.db.close)
        bridge.requests.begin('k', {'backend': 'chatgpt'})
        bridge.requests.finish('k', {'status': 'finished', 'backend': 'chatgpt',
                                     'admission_only': True, 'response': None})
        self.store.admit('k', 'one prompt')
        attempt = self.store.claim('k', 'owner')['attempt_id']
        self.store.submit('k', attempt, 'réponse ChatGPT')
        receipt = bridge.call('reprise_requests', {'request_key': 'k', 'view': 'receipt'})
        self.assertEqual(receipt['source'], 'chatgpt_tasks')
        self.assertEqual(receipt['backend'], 'chatgpt')
        self.assertEqual(receipt['result_sha256'], self.store.result('k')['result_sha256'])
        full = bridge.call('reprise_requests', {'request_key': 'k'})
        self.assertEqual(full['response'], 'réponse ChatGPT')
        waited = bridge.call('wait_result', {'request_key': 'k', 'wait_seconds': 1})
        self.assertEqual(waited['backend'], 'chatgpt')
        self.assertEqual(waited['status'], 'finished')

    def test_request_key_preserved_in_routing_journal(self):
        # Local ChatGPT admission has no inference or remote service dependency.
        # An unconfigured Codex executable can refuse before routing is logged.
        bridge = Bridge(self.root, {}, self.root / 'expert', daily_limit=2,
                        desktop_state=self.root / 'desktop', chatgpt_socket=str(self.state))
        self.addCleanup(bridge.close)
        result = bridge.call('reprise_delegate', {'question': 'Revue', 'kind': 'expertise',
                                                  'backend': 'chatgpt', 'request_key': 'jk'})
        self.assertEqual(result['status'], 'finished')
        self.assertTrue(result['admission_only'])
        row = bridge.db.execute(
            "SELECT request_key FROM routing_events WHERE tool='reprise_delegate' "
            "AND channel='chatgpt' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], 'jk')

    def test_pre_admission_refusal_does_not_invent_routing_event(self):
        bridge = Bridge(self.root, {}, self.root / 'expert', daily_limit=2,
                        desktop_state=self.root / 'desktop')
        self.addCleanup(bridge.close)
        result = bridge.call('reprise_delegate', {'question': 'Revue', 'kind': 'expertise',
                                                  'backend': 'chatgpt', 'request_key': 'denied'})
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertIsNone(bridge.db.execute(
            'SELECT id FROM routing_events ORDER BY id DESC LIMIT 1').fetchone())


if __name__ == '__main__':
    unittest.main()
