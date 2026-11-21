"""Explicit local Siri inbox boundary, exercised with an in-memory fake."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid
sys.path.insert(0, str(Path(__file__).parents[1]))
import siri_inbox_cli as cli

class FakeInbox:
    calls = []
    def __init__(self, state): self.state = state
    def put(self, ident, text, *, now):
        self.calls.append(('put', self.state, ident, text))
        return {'id': ident, 'status': 'pending'}
    def list(self): return []
    def read(self, ident): return {'id': ident, 'text': 'explicit'}
    def dismiss(self, ident, *, now): return {'id': ident, 'status': 'dismissed'}

class InboxCliTests(unittest.TestCase):
    def run_request(self, raw):
        out = io.StringIO()
        with patch.object(cli, 'Inbox', FakeInbox), patch('sys.stdin', io.StringIO(raw)), patch('sys.stdout', out):
            code = cli.main(['/private/tmp/explicit-state'])
        return code, json.loads(out.getvalue())
    def test_put_requires_explicit_identity_and_returns_no_request_text(self):
        ident = str(uuid.uuid4())
        code, result = self.run_request(json.dumps({'action': 'put', 'id': ident, 'text': 'secret'}))
        self.assertEqual(code, 0)
        self.assertEqual(result, {'status': 'ok', 'result': {'id': ident, 'status': 'pending'}})
        self.assertNotIn('secret', repr(result))
    def test_invalid_and_duplicate_fields_are_refused_without_dispatch(self):
        for raw in ('{"action":"list","action":"put"}', '{"action":"list","extra":true}', 'x' * 20000):
            FakeInbox.calls.clear()
            code, result = self.run_request(raw)
            self.assertNotEqual(code, 0)
            self.assertEqual(result['status'], 'invalid_request')
            self.assertFalse(FakeInbox.calls)
    def test_store_failure_does_not_disclose_details(self):
        with patch.object(FakeInbox, 'list', side_effect=ValueError('private path and text')):
            code, result = self.run_request('{"action":"list"}')
        self.assertNotEqual(code, 0)
        self.assertNotIn('private', repr(result))

    def test_explicit_store_round_trip_without_starting_services(self):
        import os
        import tempfile
        import siri_inbox_store
        def synthetic(data, purpose, operation):
            self.assertEqual(purpose, 'siri-inbox-v1')
            if operation == 'seal': return b'S' + bytes(byte ^ 91 for byte in data)
            if not data.startswith(b'S'): raise ValueError('corrupt')
            return bytes(byte ^ 91 for byte in data[1:])
        ident = str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as folder:
            os.chmod(folder, 0o700)
            def invoke(request):
                out = io.StringIO()
                with patch.object(siri_inbox_store.vault, 'crypt', side_effect=synthetic), \
                     patch('sys.stdin', io.StringIO(json.dumps(request))), patch('sys.stdout', out):
                    code = cli.main([folder])
                return code, json.loads(out.getvalue())
            code, created = invoke({'action': 'put', 'id': ident, 'text': 'bonjour'})
            self.assertEqual((code, created['result']['status']), (0, 'pending'))
            code, page = invoke({'action': 'list'})
            self.assertEqual(code, 0)
            self.assertNotIn('bonjour', repr(page))
            code, loaded = invoke({'action': 'read', 'id': ident})
            self.assertEqual((code, loaded['result']['text']), (0, 'bonjour'))
            code, dismissed = invoke({'action': 'dismiss', 'id': ident})
            self.assertEqual((code, dismissed['result']['status']), (0, 'dismissed'))

    def test_failure_codes_are_stable_bounded_and_do_not_disclose_details(self):
        from siri_inbox_acl import ACLPresent, ACLUnavailable
        from siri_inbox_model import Conflict, CapacityExceeded, NotFound
        from siri_inbox_store import MissingRegistry, WriteUncertain
        cases = ((ACLPresent, 'unsafe_permissions'), (ACLUnavailable, 'permissions_unavailable'),
                 (Conflict, 'identity_conflict'), (CapacityExceeded, 'capacity_exceeded'),
                 (NotFound, 'not_found'), (MissingRegistry, 'registry_missing'),
                 (WriteUncertain, 'write_uncertain'), (ValueError, 'unavailable'),
                 (OSError, 'unavailable'))
        for error, expected in cases:
            with self.subTest(error=error.__name__), patch.object(FakeInbox, 'list', side_effect=error('private detail'*1000)):
                code, result = self.run_request('{"action":"list"}')
            self.assertEqual(code, 1)
            self.assertEqual(result, {'status': expected})
            self.assertLess(len(json.dumps(result)), 80)
