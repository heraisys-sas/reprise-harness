import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from runtime import JsonLines, codex_environment, terminate

class RuntimeTests(unittest.TestCase):
    def test_fragmented_unicode_and_non_object_records(self):
        decoder = JsonLines()
        self.assertEqual(decoder.feed(b'{"text":"\xc3'), [])
        self.assertEqual(decoder.feed(b'\xa9"}\n[]\nnull\ninvalid\n{"ok":true}\n'),
                         [{'text': 'é'}, {'ok': True}])

    def test_oversized_record_is_discarded_until_its_end(self):
        decoder = JsonLines(limit=16)
        self.assertEqual(decoder.feed(b'x'*100), [])
        self.assertLessEqual(len(decoder.buffer), 16)
        self.assertEqual(decoder.feed(b'{"fake":1}\n{"real":1}\n'), [{'real': 1}])

    def test_environment_preserves_everything_except_api_overrides(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test', 'OPENAI_BASE_URL': 'test', 'KEEP': 'yes'}, clear=True):
            self.assertEqual(codex_environment(), {'KEEP': 'yes'})

    def test_cleanup_reaps_process_and_closes_streams(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True)
        terminate(process, grace=.1)
        self.assertIsNotNone(process.returncode)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stdin.closed)
