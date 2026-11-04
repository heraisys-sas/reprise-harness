import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
import connection
import engines
import scheduler

class FakeClient:
    def __init__(self, *args, **kw): self.calls=[]; self.closed=False; FakeClient.last=self
    def call(self, method, params): self.calls.append(method); return {'userAgent':'test'}
    def send(self, message): self.calls.append(message['method'])
    def close(self): self.closed=True

class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        engines.setup(self.db)
        scheduler.setup(self.db)
    def test_probe_initializes_without_model_or_session_calls(self):
        with patch('codex_rpc.Client', FakeClient):
            result=connection.probe('codex','/private/tmp/private/server.sock')
        self.assertTrue(result['connected'])
        self.assertFalse(result['model_called'])
        self.assertEqual(FakeClient.last.calls,['initialize','initialized'])
        self.assertTrue(FakeClient.last.closed)
    def test_failed_probe_is_structured_and_does_not_change_configuration(self):
        self.db.execute('INSERT INTO engine_settings VALUES (?,?)',('codex',json.dumps({'socket':'/old.sock'})))
        self.db.commit()
        with patch('codex_rpc.Client', side_effect=engines.Unavailable('engine_unavailable','Unavailable')):
            result=connection.configure(self.db,'codex','/new.sock')
        self.assertFalse(result['saved'])
        self.assertEqual(json.loads(self.db.execute('SELECT options FROM engine_settings').fetchone()[0])['socket'],'/old.sock')
    def test_verified_endpoint_saved_then_auto_restored(self):
        with patch('codex_rpc.Client', FakeClient):
            self.assertTrue(connection.configure(self.db,'codex','/chosen.sock')['saved'])
        self.assertEqual(connection.status(self.db,'/private/tmp')['configured_socket'],'/chosen.sock')
        with patch('codex_rpc.Client') as client:
            self.assertTrue(connection.configure(self.db,'codex')['saved'])
            client.assert_not_called()
        self.assertEqual(connection.status(self.db,'/private/tmp')['mode'],'auto')
    def test_missing_binary_or_bad_path_cannot_connect(self):
        self.assertFalse(connection.probe(None,'/chosen.sock')['connected'])
        self.assertFalse(connection.probe('codex','relative.sock')['connected'])
    def test_timeout_closes_client(self):
        with patch('codex_rpc.Client', FakeClient),patch.object(FakeClient,'call',side_effect=TimeoutError):
            self.assertFalse(connection.probe('codex','/chosen.sock')['connected'])
        self.assertTrue(FakeClient.last.closed)
    def test_detection_is_not_a_connection_test(self):
        with patch('engines.socket_path', return_value=Path('/chosen.sock')),patch('codex_rpc.Client') as client:
            info=connection.status(self.db,'/private/tmp')
        self.assertTrue(all(c['detected'] for c in info['candidates']))
        client.assert_not_called()

    def test_thread_check_does_not_resume_or_start_it(self):
        session = '12345678-1234-1234-1234-123456789abc'
        for state, loaded in [('idle', True), ('active', True), ('notLoaded', False)]:
            with patch('codex_rpc.Client', FakeClient), patch.object(FakeClient, 'call', side_effect=[{}, {'thread': {'id': session, 'status': {'type': state}}}]) as call:
                result = connection.probe('codex', '/chosen.sock', session)
            self.assertEqual(result['thread_loaded'], loaded)
            self.assertEqual([c.args[0] for c in call.call_args_list], ['initialize', 'thread/read'])

    def test_muted_protocol_socket_is_classified_protocol_muted(self):
        # Socket vivant mais muet : la connexion réussit, initialize ne répond pas (#52).
        with patch('codex_rpc.Client', FakeClient), patch.object(FakeClient, 'call', side_effect=TimeoutError):
            result = connection.probe('codex', '/chosen.sock')
        self.assertFalse(result['connected'])
        self.assertEqual(result['socket_state'], 'protocol_muted')
        self.assertTrue(FakeClient.last.closed)

    def test_unreachable_socket_is_classified_unreachable(self):
        # Le transport lui-même échoue (proxy/handshake) : jamais de requête partie (#52).
        with patch('codex_rpc.Client', side_effect=OSError('connection refused')):
            result = connection.probe('codex', '/chosen.sock')
        self.assertFalse(result['connected'])
        self.assertEqual(result['socket_state'], 'unreachable')

    def test_responsive_probe_reports_socket_state(self):
        with patch('codex_rpc.Client', FakeClient):
            result = connection.probe('codex', '/chosen.sock')
        self.assertTrue(result['connected'])
        self.assertEqual(result['socket_state'], 'responsive')
