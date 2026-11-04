"""Synthetic byte streams; never connects to a socket or starts a proxy."""
import base64
import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from websocket_transport import WebSocketTransport
import claude_desktop_apply
import codex_rpc


def frame(data, opcode=1, final=True):
    payload = data if isinstance(data, bytes) else json.dumps(data).encode()
    size = len(payload)
    prefix = bytes([(128 if final else 0) | opcode])
    if size < 126:
        return prefix + bytes([size]) + payload
    if size < 65536:
        return prefix + b'\x7e' + struct.pack('!H', size) + payload
    return prefix + b'\x7f' + struct.pack('!Q', size) + payload


def accept(key):
    return base64.b64encode(hashlib.sha1(
        (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()


class Fixture(WebSocketTransport):
    def __init__(self, chunks=()):
        self.buffer, self.timeout = bytearray(), 1
        self.chunks, self.sent = list(chunks), []

    def _write(self, data):
        self.sent.append(data)

    def _read(self, deadline):
        if not self.chunks:
            raise TimeoutError('Fixture exhausted')
        self.buffer.extend(self.chunks.pop(0))


class WebSocketFrames(unittest.TestCase):
    def test_fragmented_json_ping_and_pong_payload(self):
        stream = frame(b'{"answer":', final=False) + frame(b'check', 9) + frame(b'42}', 0)
        fixture = Fixture([stream[:3], stream[3:]])
        self.assertEqual(fixture.receive(), {'answer': 42})
        raw = fixture.sent[0]
        self.assertEqual(raw[0], 0x8a)
        self.assertTrue(raw[1] & 0x80)
        mask = raw[2:6]
        self.assertEqual(bytes(value ^ mask[i % 4] for i, value in enumerate(raw[6:])), b'check')

    def test_all_payload_length_encodings_roundtrip(self):
        for length in (1, 126, 65536):
            with self.subTest(length=length):
                expected = {'result': 'x' * length}
                self.assertEqual(Fixture([frame(expected)]).receive(), expected)

    def test_bad_continuation_control_mask_and_size_rejected(self):
        streams = [frame(b'{}', 0), frame(b'{}', 2), frame(b'x', 9, final=False),
                   frame(b'x' * 126, 9), b'\x81\x80', b'\xc1\x02{}',
                   b'\x81\x7f' + struct.pack('!Q', 2 ** 40), frame(b'[]'),
                   frame(b'{', final=False) + frame(b'{}')]
        for stream in streams:
            with self.subTest(stream=stream[:10]), self.assertRaises(ValueError):
                Fixture([stream]).receive()

    def test_aggregate_fragment_limit_is_enforced(self):
        fixture = Fixture([frame(b'123456', final=False) + frame(b'789012', 0)])
        fixture.LIMIT = 10
        with self.assertRaises(ValueError):
            fixture.receive()

    def test_request_limit_is_enforced_before_writing(self):
        fixture = Fixture()
        fixture.LIMIT = 3
        with self.assertRaises(ValueError):
            fixture.send({'too': 'large'})
        self.assertEqual(fixture.sent, [])

    def test_handshake_keeps_bytes_after_header(self):
        nonce = b'0' * 16
        key = base64.b64encode(nonce).decode()
        header = ('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                  'Connection: Upgrade\r\nSec-WebSocket-Accept: ' + accept(key) + '\r\n\r\n').encode()
        fixture = Fixture([header[:15], header[15:] + frame({'id': 1})])
        with patch('websocket_transport.os.urandom', return_value=nonce):
            fixture._handshake('localhost', '/rpc', 1)
        self.assertIn(b'GET /rpc HTTP/1.1', fixture.sent[0])
        self.assertEqual(fixture.receive(), {'id': 1})

    def test_wrong_accept_and_oversized_header_rejected(self):
        headers = [b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n'
                   b'Connection: Upgrade\r\nSec-WebSocket-Accept: wrong\r\n\r\n', b'x' * 16385, b'HTTP/1.1\r\n\r\n']
        for header in headers:
            with self.subTest(size=len(header)), self.assertRaises(ValueError):
                Fixture([header])._handshake('localhost', '/rpc', 1)

    def test_adapter_specific_close_contract_is_preserved(self):
        for adapter, expected in ((codex_rpc.Client, codex_rpc.Closed),
                                  (claude_desktop_apply.WebSocket, claude_desktop_apply.WSError)):
            client = adapter.__new__(adapter)
            client.buffer, client.timeout = bytearray(frame(b'', 8)), 1
            with self.subTest(adapter=adapter.__name__), self.assertRaises(expected):
                client.receive()

    def test_socket_handshake_failure_closes_only_its_connection(self):
        class Socket:
            closed = False
            def sendall(self, data):
                pass
            def settimeout(self, value):
                pass
            def recv(self, count):
                return b'HTTP/1.1 403 Forbidden\r\n\r\n'
            def close(self):
                self.closed = True
        sock = Socket()
        with patch.object(claude_desktop_apply.socket, 'create_connection', return_value=sock):
            with self.assertRaises(ValueError):
                claude_desktop_apply.WebSocket('127.0.0.1', 9333, '/devtools/page/id')
        self.assertTrue(sock.closed)

    def test_socket_deadline_prevents_another_read(self):
        client = claude_desktop_apply.WebSocket.__new__(claude_desktop_apply.WebSocket)
        with patch.object(claude_desktop_apply.time, 'monotonic', return_value=5):
            with self.assertRaises(TimeoutError):
                client._read(4)


if __name__ == '__main__':
    unittest.main()
