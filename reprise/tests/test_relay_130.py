"""Lot #130 — cycle relais Quick Apply simulé (daemon + relais factice).

Le daemon `claude_desktop_quick` est l'unique détenteur du socket ; le relais
(injection ReClaude) s'annonce et reçoit les payloads admis sur la connexion
tenue ouverte. Pas de socket réel : `_serve` est piloté directement avec des
connexions factices.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import claude_desktop_quick
from claude_desktop_quick import RelayHub, Endpoint, _serve


class _FakeStream:
    def __init__(self, lines):
        self.lines = iter(lines)
        self.written = []

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.lines)

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def close(self):
        pass


class _FakeConn:
    def __init__(self, lines):
        self._stream = _FakeStream(lines)
        self.shutdown_calls = []

    def makefile(self, mode):
        return self._stream

    def shutdown(self, how):
        self.shutdown_calls.append(how)

    def settimeout(self, value):
        pass


def _lines(*messages):
    return [(json.dumps(message, ensure_ascii=False) + '\n').encode()
            for message in messages]


class RelayHubTests(unittest.TestCase):
    def test_last_connected_relay_replaces_previous(self):
        hub = RelayHub()
        old = _FakeConn([])
        new = _FakeConn([])
        old_stream = _FakeStream([])
        new_stream = _FakeStream([])
        hub.register(old, old_stream)
        hub.register(new, new_stream)
        self.assertTrue(hub.connected())
        self.assertIn(__import__('socket').SHUT_RDWR, old.shutdown_calls,
                      'ancienne connexion relais non fermée par le daemon')

    def test_push_without_relay_returns_none(self):
        self.assertIsNone(RelayHub().push({'request_key': 'k'}))


class RelayCycleTests(unittest.TestCase):
    """Cycle complet simulé : greeting, enregistrement, submit, push, résultat."""

    def _endpoint(self, state):
        with mock.patch.object(Endpoint, '__init__', lambda self, state: None):
            endpoint = Endpoint.__new__(Endpoint)
        endpoint.state = Path(state)
        endpoint.relay = RelayHub()
        endpoint.requests = mock.MagicMock()
        endpoint.db = mock.MagicMock()
        endpoint.scope = 'test'
        endpoint.tasks = None
        endpoint.requests.begin.return_value = None
        endpoint.requests.finish.return_value = {'status': 'finished', 'admission_only': True}
        return endpoint

    def test_relay_registration_then_submit_pushes_payload(self):
        endpoint = self._endpoint(tempfile.mkdtemp())
        relay_stream = _FakeStream([])
        endpoint.relay.register(_FakeConn([]), relay_stream)
        payload = endpoint.submit({'request_key': 'rk-130', 'prompt': 'salut'})
        self.assertTrue(payload.get('relay_pushed'))
        pushed = b''.join(relay_stream.written).decode()
        self.assertIn('relay/dispatch', pushed)
        message = json.loads(pushed.splitlines()[0])
        self.assertEqual(message['method'], 'relay/dispatch')
        self.assertEqual(message['params']['request_key'], 'rk-130')

    def test_submit_without_relay_stays_admission_only(self):
        endpoint = self._endpoint(tempfile.mkdtemp())
        payload = endpoint.submit({'request_key': 'rk-130b', 'prompt': 'salut'})
        self.assertIsNone(payload.get('relay_pushed'))
        self.assertEqual(payload.get('admission_only'), True)

    def test_capabilities_reflect_relay_presence(self):
        endpoint = self._endpoint(tempfile.mkdtemp())
        endpoint.tasks = mock.MagicMock()
        with mock.patch('claude_desktop_quick._cdp_pipe') as cdp:
            cdp.return_value._chrome_bin.return_value = '/bin/echo'
            caps = endpoint.call('quick_entry/capabilities', {})
            self.assertFalse(caps['resume_validation_supported'])
            self.assertIn('indisponible', caps['reason'])
            endpoint.relay.register(_FakeConn([]), _FakeStream([]))
            caps = endpoint.call('quick_entry/capabilities', {})
            self.assertTrue(caps['resume_validation_supported'])

    def test_incompatible_protocol_is_not_a_relay(self):
        conn = _FakeConn(_lines({'hello': True, 'role': 'relay', 'protocol': 'autre-1'}))
        endpoint = self._endpoint(tempfile.mkdtemp())
        _serve(conn, endpoint)
        self.assertFalse(endpoint.relay.connected())
        # La ligne a été traitée comme requête RPC standard (pas d'enregistrement).
        self.assertFalse(conn.makefile('rwb').written[1:])

    def test_ping_answered_for_relay_connection(self):
        conn = _FakeConn(_lines(
            {'hello': True, 'role': 'relay', 'protocol': claude_desktop_quick.RELAY_PROTOCOL,
             'version': 1},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'ping', 'params': {}}))
        endpoint = self._endpoint(tempfile.mkdtemp())
        _serve(conn, endpoint)
        replies = b''.join(conn.makefile('rwb').written).decode().splitlines()
        self.assertEqual(json.loads(replies[0]), {'hello': True})
        self.assertEqual(json.loads(replies[1])['result'], {})
        self.assertFalse(endpoint.relay.connected(), 'relais retiré après déconnexion')

    def test_unregistered_relay_after_disconnect(self):
        conn = _FakeConn(_lines({'hello': True, 'role': 'relay',
                                 'protocol': claude_desktop_quick.RELAY_PROTOCOL, 'version': 1}))
        endpoint = self._endpoint(tempfile.mkdtemp())
        _serve(conn, endpoint)
        self.assertFalse(endpoint.relay.connected())


if __name__ == '__main__':
    unittest.main()
