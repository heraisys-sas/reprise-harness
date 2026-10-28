"""HTTP fixtures use socket pairs, never an existing CDP endpoint."""
from contextlib import contextmanager
from pathlib import Path
import socket
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_desktop_apply as cdp


class MetadataDeadline(unittest.TestCase):
    @contextmanager
    def peer(self, response, *, drip=False):
        client, server = socket.socketpair()
        stopped = threading.Event()

        def respond():
            try:
                with server:
                    server.settimeout(2)
                    request = b''
                    while b'\r\n\r\n' not in request:
                        request += server.recv(4096)
                    server.sendall(response)
                    while drip and not stopped.wait(.02):
                        server.sendall(b'x')
            except OSError:
                pass  # Closing a timed-out client is expected.

        worker = threading.Thread(target=respond)
        worker.start()
        try:
            with patch.object(cdp.socket, 'create_connection', return_value=client) as connect:
                yield connect
        finally:
            stopped.set()
            client.close()
            worker.join(3)
            self.assertFalse(worker.is_alive(), 'Fixture must not leave a worker')

    def test_json_and_loopback_destination(self):
        with self.peer(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n[]') as connect:
            self.assertEqual(cdp.local_json(9333, '/json/list', timeout=.5), [])
            connect.assert_called_once_with(('127.0.0.1', 9333), timeout=.5)

    def test_redirect_is_rejected_without_second_connection(self):
        with self.peer(b'HTTP/1.1 302 Found\r\nLocation: http://evil.test/\r\nContent-Length: 0\r\n\r\n') as connect:
            with self.assertRaises(cdp.WSError):
                cdp.local_json(9333, '/json/list', timeout=.5)
            self.assertEqual(connect.call_count, 1)

    def test_total_deadline_covers_dripping_headers_and_body(self):
        for initial in (b'HTTP/1.1 200 OK\r\nX-Slow: ',
                        b'HTTP/1.1 200 OK\r\nContent-Length: 10000\r\n\r\n'):
            with self.subTest(initial=initial), self.peer(initial, drip=True):
                started = time.monotonic()
                with self.assertRaises(TimeoutError):
                    cdp.local_json(9333, '/json/version', timeout=.15)
                self.assertLess(time.monotonic() - started, .8)

    def test_invalid_path_never_connects(self):
        with patch.object(cdp.socket, 'create_connection') as connect:
            with self.assertRaises(cdp.WSError):
                cdp.local_json(9333, '/json/new')
            connect.assert_not_called()

    def test_malformed_and_oversized_json_are_rejected(self):
        for payload in (b'[' * 1200, b'x' * 65):
            data = b'HTTP/1.1 200 OK\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\n\r\n' + payload
            with self.subTest(length=len(payload)), self.peer(data):
                with patch.object(cdp.WebSocketTransport, 'LIMIT', 2048 if len(payload) == 1200 else 64):
                    with self.assertRaises(cdp.WSError):
                        cdp.local_json(9333, '/json/list', timeout=.5)
