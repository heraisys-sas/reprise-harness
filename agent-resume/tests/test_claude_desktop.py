import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_desktop
import engines


class FakeClient:
    def __init__(self, *args, **kw):
        self.calls = []
        self.closed = False
        FakeClient.last = self

    def call(self, method, params):
        self.calls.append(method)
        return {'thread': {'id': params.get('threadId'), 'status': {'type': 'idle'}}} if method == 'thread/read' else {}

    def send(self, message):
        self.calls.append(message['method'])

    def close(self):
        self.closed = True


class PayloadTests(unittest.TestCase):
    def test_text_guard_accepts_over_two_chars(self):
        self.assertEqual(claude_desktop.build_payload('abc')['text'], 'abc')
        with self.assertRaises(ValueError):
            claude_desktop.build_payload('ab')
        with self.assertRaises(ValueError):
            claude_desktop.build_payload('   ')

    def test_image_satisfies_guard_without_text(self):
        payload = claude_desktop.build_payload('ab', images=[{'base64': 'x', 'mimeType': 'image/jpeg'}])
        self.assertEqual(len(payload['images']), 1)

    def test_non_string_prompt_rejected(self):
        with self.assertRaises(ValueError):
            claude_desktop.build_payload(123)

    def test_image_shape_enforced(self):
        with self.assertRaises(ValueError):
            claude_desktop.build_payload('abc', images=[{'base64': 'x'}])
        with self.assertRaises(ValueError):
            claude_desktop.build_payload('abc', images=['not-a-dict'])

    def test_chat_id_must_be_uuid_or_none(self):
        self.assertIsNone(claude_desktop.build_payload('abc')['chatId'])
        self.assertEqual(claude_desktop.build_payload('abc', chat_id='178dcceb-fcd3-4f2d-ac4f-116a8a153bc3')['chatId'],
                         '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3')
        with self.assertRaises(ValueError):
            claude_desktop.build_payload('abc', chat_id='pas-uuid')

    def test_file_must_be_regular_and_under_cap(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b'x' * 10)
            path = f.name
        try:
            payload = claude_desktop.build_payload('abc', file_paths=[path])
            self.assertEqual(payload['filePaths'], [os.path.realpath(path)])
        finally:
            os.unlink(path)
        big = tempfile.NamedTemporaryFile(delete=False)
        big.write(b'x' * (claude_desktop.MAX_FILE_BYTES + 1))
        big.close()
        try:
            with self.assertRaises(ValueError):
                claude_desktop.build_payload('abc', file_paths=[big.name])
        finally:
            os.unlink(big.name)
        with self.assertRaises(ValueError):
            claude_desktop.build_payload('abc', file_paths=['/does/not/exist'])


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        engines.setup(self.db)

    def test_probe_initializes_without_model_calls(self):
        with patch('claude_desktop_rpc.Client', FakeClient):
            result = claude_desktop.probe('supervisor', '/private/tmp/private.sock')
        self.assertTrue(result['connected'])
        self.assertFalse(result['model_called'])
        self.assertEqual(FakeClient.last.calls, ['initialize', 'initialized'])
        self.assertTrue(FakeClient.last.closed)

    def test_failed_probe_is_structured_and_preserves_config(self):
        self.db.execute('INSERT INTO engine_settings VALUES (?,?)', ('claude-desktop', json.dumps({'socket': '/old.sock'})))
        self.db.commit()
        with patch('claude_desktop_rpc.Client', side_effect=engines.Unavailable('engine_unavailable', 'Unavailable')):
            result = claude_desktop.configure(self.db, 'supervisor', '/new.sock')
        self.assertFalse(result['saved'])
        self.assertEqual(json.loads(self.db.execute('SELECT options FROM engine_settings').fetchone()[0])['socket'], '/old.sock')

    def test_verified_socket_saved_then_auto_restored(self):
        with patch('claude_desktop_rpc.Client', FakeClient):
            self.assertTrue(claude_desktop.configure(self.db, 'supervisor', '/chosen.sock')['saved'])
        self.assertEqual(claude_desktop.status(self.db, '/private/tmp')['configured_socket'], '/chosen.sock')
        with patch('claude_desktop_rpc.Client') as client:
            self.assertTrue(claude_desktop.configure(self.db, 'supervisor')['saved'])
            client.assert_not_called()
        self.assertEqual(claude_desktop.status(self.db, '/private/tmp')['mode'], 'auto')

    def test_missing_program_or_bad_path_cannot_connect(self):
        self.assertFalse(claude_desktop.probe(None, '/chosen.sock')['connected'])
        self.assertFalse(claude_desktop.probe('supervisor', 'relative.sock')['connected'])


if __name__ == '__main__':
    unittest.main()
