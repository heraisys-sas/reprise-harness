import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import chatgpt_desktop_pipe as adapter
import chatgpt_quick
from tests import test_chatgpt_desktop_pipe as fixtures


class AnchorLossTests(unittest.TestCase):
    def test_extract_reply_multi_turn_keeps_last_sentinel_segment(self):
        # Issue #228: a multi-turn aggregate carries one sentinel per assistant
        # turn; the reply is the text between the previous sentinel and the
        # last one (live dispatchs c2/c3 returned stale earlier turns).
        sentinel = adapter.REPLY_SENTINEL
        body = (
            "Vous avez dit : Q1 ChatGPT a dit : Ancienne reponse ancienne reponse ancienne reponse ancienne reponse ancienne reponse ancienne reponse\n"
            + sentinel + "\nEffort de réflexion\nAuto\nRéponse terminée\n"
            "Vous avez dit : Q2 ChatGPT a dit : Nouvelle reponse fraiche et complete qui doit etre seule capture\n"
            + sentinel + "\nEffort de réflexion\nAuto\nRéponse terminée\n"
            "ChatGPT peut faire des erreurs")
        reply, anchor, total = adapter.extract_reply(body, "Vous avez dit : Q2")
        self.assertEqual(anchor, 'sentinel')
        self.assertIn('Nouvelle reponse fraiche', reply)
        self.assertNotIn('Ancienne', reply)
        self.assertEqual(total, len('Nouvelle reponse fraiche et complete qui doit etre seule capture'))

    def test_extract_reply_multi_turn_dom_anchored_skips_stale_chrome(self):
        # The DOM probe now returns the last assistant container only, but the
        # segment after the previous sentinel still starts with the previous
        # turn's chrome block; the extraction must skip it, not cut on it.
        sentinel = adapter.REPLY_SENTINEL
        dom = ("Vous avez dit : Q1 Ancienne reponse assez longue pour exister dans la capture.\n"
               + sentinel + "\nEffort de réflexion\nAuto\nRéponse terminée\n"
               "Vous avez dit : Q2 Nouvelle reponse dom echo " + "avec contenu " * 10 + sentinel)
        reply, anchor, _ = adapter.extract_reply(None, 'Vous avez dit : Q2', assistant_text=dom)
        self.assertEqual(anchor, 'sentinel')
        self.assertIn('Nouvelle reponse dom echo', reply)
        self.assertNotIn('Ancienne', reply)

    def test_extract_reply_single_sentinel_and_no_sentinel_unchanged(self):
        sentinel = adapter.REPLY_SENTINEL
        body = ("Vous avez dit : Q ChatGPT a dit : " + "Réponse simple avec suffisamment de contenu utile pour la capture. " * 2
                + sentinel + "\nEffort de réflexion\nAuto\nRéponse terminée")
        reply, anchor, _ = adapter.extract_reply(body, "Vous avez dit : Q")
        self.assertEqual(anchor, 'sentinel')
        self.assertIn('Réponse simple', reply)
        plain = ("Vous avez dit : Q ChatGPT a dit : Réponse sans sentinel avec assez de contenu utile pour dépasser le seuil de capture valide.\n"
                 "ChatGPT peut faire des erreurs")
        reply2, anchor2, _ = adapter.extract_reply(plain, "Vous avez dit : Q")
        self.assertEqual(anchor2, 'footer')
        self.assertIn('Réponse sans sentinel', reply2)

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
