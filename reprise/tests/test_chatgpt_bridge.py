import sys
import tempfile
import unittest
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import Bridge


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


if __name__ == '__main__':
    unittest.main()
