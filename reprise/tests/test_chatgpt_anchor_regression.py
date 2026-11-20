import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import chatgpt_desktop_pipe as adapter
import chatgpt_quick
from tests import test_chatgpt_desktop_pipe as fixtures


class AnchorLossTests(unittest.TestCase):
    def test_probe_budget_reserves_prompt_and_reply(self):
        self.assertGreaterEqual(adapter.MAX_PROBE_CODE_UNITS,
                                2 * (adapter.MAX_PROMPT_CHARS + adapter.MAX_CAPTURE_CHARS))
        self.assertIn('slice(-%d)' % adapter.MAX_PROBE_CODE_UNITS, adapter.SEND_HELPERS)

    def test_missing_anchor_never_confirms_an_older_fragment(self):
        prompt = 'P' * 5000
        first = prompt + ' ChatGPT a dit : ' + 'A' * 54000
        final = ('A' * 56000 + ' ' + adapter.REPLY_SENTINEL + ' Réponse terminée')[-60000:]
        cases = (final, '', None)
        for latest in cases:
            with self.subTest(latest=type(latest).__name__):
                transport = fixtures.TestGuardedSend().fake(
                    body=[first[-60000:], latest], quiet_ms=lambda _: None)
                with mock.patch.object(adapter, 'time', fixtures.AcceleratedTime()), \
                     mock.patch.object(adapter, '_persist_receipt'), \
                     mock.patch.object(adapter, 'drain_turn_completion',
                                       side_effect=[False, True, False]):
                    receipt = adapter._dispatch(transport, prompt, Path('/unused'),
                                                'anchor-loss', adapter._send_receipt())
                self.assertEqual(receipt['status'], 'captured_incomplete')
                self.assertEqual(receipt['reason'], 'capture_anchor_lost')
                self.assertFalse(receipt['response_complete'])
                self.assertFalse(receipt['output_complete'])
                self.assertTrue(receipt['output_truncated'])
                self.assertNotIn('response_full_length', receipt)
                with mock.patch.object(chatgpt_quick, 'dispatch_to_holder',
                                       return_value=receipt):
                    result = chatgpt_quick.finish_with_holder_dispatch(
                        '/unused', '/unused',
                        {'status': 'finished', 'request_key': 'anchor-loss'})
                self.assertEqual(result['status'], 'needs_review')
                self.assertTrue(result['capture_anchor_lost'])
                self.assertFalse(result['work_completed'])
