import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import vault


class VaultTests(unittest.TestCase):
    def run_helper(self, response, returncode=0):
        return patch.object(vault.subprocess, 'run', return_value=subprocess.CompletedProcess(
            ['Vault'], returncode, json.dumps(response), ''))

    def test_default_calls_are_noninteractive_and_keep_format(self):
        for operation in ('seal', 'open'):
            with self.subTest(operation=operation), self.run_helper(base64.b64encode(b'result').decode()) as run:
                self.assertEqual(vault.crypt(b'input', 'test-v1', operation), b'result')
                request = json.loads(run.call_args.kwargs['input'])
                self.assertFalse(request['interactive'])
                self.assertEqual(request['purpose'], 'test-v1')
                self.assertEqual(run.call_args.kwargs['timeout'], 30)

    def test_authorize_is_explicit_and_returns_no_key(self):
        with self.run_helper({'authorized': True}) as run:
            self.assertIsNone(vault.authorize())
            request = json.loads(run.call_args.kwargs['input'])
            self.assertEqual(request['operation'], 'authorize')
            self.assertTrue(request['interactive'])
            self.assertEqual(run.call_args.kwargs['timeout'], 90)

    def test_check_never_authorizes(self):
        with self.run_helper({'ready': True}) as run:
            self.assertIsNone(vault.check())
            self.assertEqual(json.loads(run.call_args.kwargs['input'])['interactive'], False)

    def test_invalid_interactive_operations_never_start_helper(self):
        with patch.object(vault.subprocess, 'run') as run:
            for operation in ('seal', 'open', 'check'):
                with self.assertRaises(ValueError):
                    vault.crypt(b'', 'test-v1', operation, interactive=True)
            with self.assertRaises(ValueError):
                vault.crypt(b'', 'test-v1', 'authorize')
            run.assert_not_called()

    def test_denial_preserves_existing_file_and_writes_no_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cache'
            path.write_bytes(b'existing ciphertext')
            with self.run_helper({'error': {'code': 'keychain_unavailable', 'status': -25308}}, 1):
                with self.assertRaisesRegex(ValueError, 'autorisez explicitement'):
                    vault.save(path, b'secret plaintext', 'test-v1')
            self.assertEqual(path.read_bytes(), b'existing ciphertext')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_malformed_output_and_timeout_fail_closed(self):
        for response in ({}, {'authorized': True}, '!invalid base64'):
            with self.run_helper(response), self.assertRaises(ValueError):
                vault.crypt(b'', 'test-v1', 'open')
        with patch.object(vault.subprocess, 'run', side_effect=subprocess.TimeoutExpired('Vault', 30)):
            with self.assertRaisesRegex(ValueError, 'aucune copie en clair'):
                vault.check()
