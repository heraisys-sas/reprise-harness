"""Siri vault contract with a fake helper; never touches the real Keychain."""
import base64
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import vault


class SiriVaultContract(unittest.TestCase):
    @mock.patch('vault.subprocess.run')
    def test_siri_seal_requires_existing_key_in_same_helper_request(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(base64.b64encode(b'ciphertext').decode())
        result = vault.crypt(b'private request', 'siri-inbox-v1', 'seal')
        self.assertEqual(result, b'ciphertext')
        request = json.loads(run.call_args.kwargs['input'])
        self.assertEqual(request['purpose'], 'siri-inbox-v1')
        self.assertIs(request['require_existing_key'], True)
        self.assertFalse(request['interactive'])

    @mock.patch('vault.subprocess.run')
    def test_missing_key_is_an_error_and_does_not_return_plaintext(self, run):
        run.return_value.returncode = 1
        run.return_value.stdout = json.dumps({'error': {'code': 'key_missing'}})
        with self.assertRaisesRegex(ValueError, 'key_missing'):
            vault.crypt(b'private request', 'siri-inbox-v1', 'seal')

class SiriVaultSwiftFixture(unittest.TestCase):
    """Exercise the production protocol and crypto with a synthetic key provider."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        source = Path(__file__).resolve().parents[1].joinpath('swift', 'Vault.swift').read_text()
        start = source.index('func key(create: Bool, interactive: Bool) throws -> SymmetricKey {')
        end = source.index('func writeJSON(', start)
        source = source[:start] + '''func key(create: Bool, interactive: Bool) throws -> SymmetricKey {
    if create { throw VaultError(code: "unexpected_create", message: "Test key provider refused creation") }
    return SymmetricKey(data: Data(repeating: 7, count: 32))
}
''' + source[end:]
        start = source.index('    let disabled = SecKeychainSetUserInteractionAllowed(false)')
        end = source.index('    let input = FileHandle.standardInput', start)
        source = source[:start] + source[end:]
        source_path = Path(cls.tmp.name) / 'fixture.swift'
        source_path.write_text(source)
        cls.binary = Path(cls.tmp.name) / 'vault-fixture'
        subprocess.run(['swiftc', str(source_path), '-o', str(cls.binary)], check=True,
                       capture_output=True, timeout=60)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def call(self, purpose, operation, payload=b'', **extra):
        import subprocess
        request = dict(purpose=purpose, operation=operation, payload=base64.b64encode(payload).decode(),
                       interactive=False, **extra)
        result = subprocess.run([str(self.binary)], input=json.dumps(request), text=True,
                                capture_output=True, timeout=5)
        return result.returncode, json.loads(result.stdout)

    def test_siri_requires_explicit_existing_key_and_authenticates_purpose(self):
        code, failure = self.call('siri-inbox-v1', 'seal', b'private text')
        self.assertNotEqual(code, 0)
        self.assertEqual(failure['error']['code'], 'invalid_request')
        code, sealed = self.call('siri-inbox-v1', 'seal', b'private text', require_existing_key=True)
        self.assertEqual(code, 0)
        cipher = base64.b64decode(sealed)
        self.assertNotIn(b'private text', cipher)
        code, opened = self.call('siri-inbox-v1', 'open', cipher, require_existing_key=True)
        self.assertEqual((code, base64.b64decode(opened)), (0, b'private text'))
        code, _ = self.call('test-v1', 'open', cipher, require_existing_key=True)
        self.assertNotEqual(code, 0)
        code, _ = self.call('siri-inbox-v1', 'authorize', require_existing_key=True)
        self.assertNotEqual(code, 0)

    def test_encrypted_store_round_trip_with_synthetic_swift_key(self):
        import os
        import tempfile
        import uuid
        import siri_inbox_store
        def fixture_crypt(data, purpose, operation):
            code, response = self.call(purpose, operation, data, require_existing_key=True)
            if code:
                raise ValueError('Synthetic helper failed')
            return base64.b64decode(response)
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            inbox = siri_inbox_store.Inbox(folder, crypt=fixture_crypt)
            ident = str(uuid.uuid4())
            inbox.put(ident, 'texte strictement synthétique', now=1)
            encrypted = (Path(folder) / 'siri-inbox.noindex' / 'inbox.enc').read_bytes()
            self.assertNotIn('texte strictement synthétique'.encode(), encrypted)
            self.assertEqual(inbox.read(ident)['text'], 'texte strictement synthétique')
            inbox.dismiss(ident, now=2)
            self.assertNotIn('texte strictement synthétique'.encode(),
                             (Path(folder) / 'siri-inbox.noindex' / 'inbox.enc').read_bytes())
