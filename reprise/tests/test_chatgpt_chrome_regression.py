import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import chatgpt_desktop_pipe as adapter


class ChromeExtractionTests(unittest.TestCase):
    def test_assistant_words_are_not_ui_chrome(self):
        for word in ('reasoning', 'thinking', 'Afficher plus'):
            with self.subTest(word=word):
                expected = ('A detailed explanation of ' + word + ' in the assistant answer. ') * 3
                body = 'unique prompt ChatGPT a dit : ' + expected + adapter.REPLY_SENTINEL
                reply, anchor, total = adapter.extract_reply(body, 'unique prompt')
                self.assertEqual(reply, expected.strip())
                self.assertEqual(total, len(expected.strip()))
                self.assertEqual(anchor, 'sentinel')
                self.assertEqual(adapter._usable_reply(reply, anchor), (True, None))

    def test_cut_before_sentinel_cannot_attest_completion(self):
        body = ('unique prompt ChatGPT a dit : ' + 'Useful content. ' * 10
                + 'ChatGPT can make mistakes. More content. ' + adapter.REPLY_SENTINEL)
        reply, anchor, _ = adapter.extract_reply(body, 'unique prompt')
        self.assertNotEqual(anchor, 'sentinel')
        self.assertEqual(adapter._usable_reply(reply, anchor),
                         (False, 'capture_cut_before_sentinel'))
