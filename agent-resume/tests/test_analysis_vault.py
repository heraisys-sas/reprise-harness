from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_analysis


class AnalysisVaultTests(unittest.TestCase):
    def test_denied_vault_skips_inference_and_keeps_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            cache = state / 'analysis.enc'
            cache.write_bytes(b'preserved-ciphertext')
            with patch.object(local_analysis, 'check', side_effect=ValueError('locked')), patch.object(local_analysis.subprocess, 'run') as inference:
                local_analysis.analyze(state)
            inference.assert_not_called()
            self.assertEqual(cache.read_bytes(), b'preserved-ciphertext')

    def test_missing_key_skips_inference_without_plaintext_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with patch.object(local_analysis, 'check', side_effect=ValueError('missing')), patch.object(local_analysis.subprocess, 'run') as inference:
                local_analysis.analyze(state)
            inference.assert_not_called()
            self.assertFalse((state / 'analysis.enc').exists())
            self.assertFalse((state / 'analysis.json').exists())
