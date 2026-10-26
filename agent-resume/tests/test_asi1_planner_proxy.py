"""Local fixture-only contract tests; never contact ASI or discover credentials."""
import http.client
import http.server
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
import sys
from unittest import mock
import ssl
import os
import stat
import io

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from asi1_planner_proxy import PlannerProxy, main


class Fixture(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.server.calls += 1
        raw = self.rfile.read(int(self.headers['content-length']))
        self.server.observed = (json.loads(raw), dict(self.headers))
        self.server.entered.set()
        mode = self.server.mode
        if mode == 'headers':
            self.server.disconnected = self.connection.recv(1) == b''
            return
        self.send_response(204 if mode == 'empty' else 200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        if mode == 'compressed':
            self.send_header('Content-Encoding', 'gzip')
        if mode == 'duplicate-length':
            self.send_header('Content-Length', '1')
        if mode == 'cl-and-te':
            self.send_header('Transfer-Encoding', 'chunked')
        if mode == 'encoding':
            self.send_header('Transfer-Encoding', 'gzip')
        if mode == 'continuous':
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            deadline = time.monotonic() + 2
            try:
                while time.monotonic() < deadline:
                    self.wfile.write(b'c\r\ndata: tick\n\n\r\n')
                    self.wfile.flush()
                    time.sleep(0.002)
            except OSError:
                self.server.disconnected = True
            self.close_connection = True
            return
        if mode in ('raw-chunked', 'padded-chunked'):
            self.send_header('Transfer-Encoding', 'chunked' if mode == 'raw-chunked' else 'chunked ')
            self.end_headers()
            for chunk in (self.server.payload[:7], self.server.payload[7:]):
                self.wfile.write(('%x\r\n' % len(chunk)).encode() + chunk + b'\r\n')
            self.wfile.write(b'0\r\n\r\n')
            self.close_connection = True
            return
        if mode == 'huge-headers':
            self.send_header('X-Large', 'x' * 4000)
        self.send_header('Content-Length', str(0 if mode == 'empty' else 1000 if mode == 'truncated' else len(self.server.payload)))
        self.end_headers()
        if mode == 'empty':
            return
        if mode == 'latched-stream':
            try:
                self.wfile.write(self.server.payload[:7])
                self.wfile.flush()
                self.server.first_sent.set()
                self.server.release_stream.wait(2)
                self.wfile.write(self.server.payload[7:])
                self.wfile.flush()
            except OSError:
                pass
            self.close_connection = True
            return
        if mode in ('idle', 'total'):
            self.server.disconnected = self.connection.recv(1) == b''
        else:
            try:
                for piece in (self.server.payload[:7], self.server.payload[7:20], self.server.payload[20:]):
                    self.wfile.write(piece)
                    self.wfile.flush()
                    time.sleep(0.003)
            except OSError:
                pass
        self.close_connection = True


class PlannerProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='reprise-proxy-test-')
        self.addCleanup(self.temp.cleanup)
        self.upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Fixture)
        self.upstream.daemon_threads = True
        self.upstream.calls = 0
        self.upstream.mode = 'normal'
        self.upstream.disconnected = False
        self.upstream.entered = threading.Event()
        self.upstream.payload = 'data: {"text":"été"}\n\ndata: [DONE]\n\n'.encode()
        self.start(self.upstream)
        self.proxy = PlannerProxy(upstream='http://127.0.0.1:%s' % self.upstream.server_port,
                                  address=('127.0.0.1', 0),
                                  log_path=Path(self.temp.name) / 'traffic.jsonl',
                                  headers_timeout=0.5, idle_timeout=0.5, total_timeout=2)
        self.start(self.proxy)

    def start(self, server):
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
        worker.start()
        def stop():
            if isinstance(server, PlannerProxy):
                server.stop_active()
            server.shutdown()
            server.server_close()
            worker.join(1)
        self.addCleanup(stop)

    def request(self, body=None, *, marker='1', path='/v1/chat/completions', headers=None, method='POST'):
        connection = http.client.HTTPConnection('127.0.0.1', self.proxy.server_port, timeout=3)
        self.addCleanup(connection.close)
        payload = json.dumps(body if body is not None else {'prompt_cache_key': 'session-secret'})
        outgoing = {'x-asi-planner': marker, 'Authorization': 'Bearer auth-secret',
                    'Content-Type': 'application/json', 'x-session-id': 'header-secret',
                    'Accept-Encoding': 'gzip', 'Connection': 'close, x-private', 'x-private': 'drop'}
        outgoing.update(headers or {})
        connection.request(method, path, payload, outgoing)
        response = connection.getresponse()
        return response, connection

    def logs(self):
        deadline = time.monotonic() + 1
        log = Path(self.temp.name) / 'traffic.jsonl'
        while time.monotonic() < deadline:
            try:
                text = log.read_text()
            except FileNotFoundError:
                text = ''
            # Creation precedes the append; wait for a complete first record.
            if text.endswith('\n'):
                return [json.loads(line) for line in text.splitlines()]
            time.sleep(0.01)
        self.fail('No completed traffic record within fixture deadline')

    def test_rewrite_and_exact_sse(self):
        response, _ = self.request({'prompt_cache_key': 'session-secret', 'messages': [{'content': 'body-secret'}],
                                    'stream_options': {'include_usage': True}, 'planner_mode': False,
                                    'reasoning_effort': 'high', 'temperature': 1, 'extra': {'a': 1}})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), self.upstream.payload)
        body, headers = self.upstream.observed
        self.assertEqual(body, {'messages': [{'content': 'body-secret'}], 'planner_mode': True,
                                'x-session-id': 'session-secret', 'reasoning_effort': 'low',
                                'temperature': 0.25, 'extra': {'a': 1}})
        normalized = {k.lower(): v for k, v in headers.items()}
        self.assertEqual(normalized['authorization'], 'Bearer auth-secret')
        self.assertEqual(normalized['host'], '127.0.0.1:%s' % self.upstream.server_port)
        for key in ('x-private', 'x-session-id', 'accept-encoding', 'x-asi-planner'):
            self.assertNotIn(key, normalized)
        logs = self.logs()
        text = json.dumps(logs)
        for secret in ('session-secret', 'auth-secret', 'body-secret', 'header-secret'):
            self.assertNotIn(secret, text)
        self.assertIsNone(logs[-1]['errorCode'])
        self.assertEqual(logs[-1]['bytesSent'], len(self.upstream.payload))

    def test_invalid_keys_never_reach_upstream(self):
        for key in (None, '', ' ', 'x' * 65, 3, []):
            response, _ = self.request({'prompt_cache_key': key})
            self.assertEqual(response.status, 400)
            response.read()
        self.assertEqual(self.upstream.calls, 0)

    def test_wrong_route_marker_method(self):
        for kwargs in ({'marker': '0'}, {'path': '/v1/models'}, {'method': 'GET'}):
            response, _ = self.request(**kwargs)
            self.assertEqual(response.status, 404)
            response.read()
        self.assertEqual(self.upstream.calls, 0)

    def test_request_body_limit(self):
        self.proxy.max_body = 8
        response, _ = self.request()
        self.assertEqual(response.status, 413)
        response.read()
        self.assertEqual(self.upstream.calls, 0)

    def test_header_limit(self):
        self.proxy.max_headers = 256
        response, _ = self.request(headers={'X-Large': 'x' * 512})
        self.assertEqual(response.status, 431)
        response.read()
        self.assertEqual(self.upstream.calls, 0)

    def test_compressed_response_rejected(self):
        self.upstream.mode = 'compressed'
        response, _ = self.request()
        self.assertEqual(response.status, 502)
        self.assertIn(b'UPSTREAM_COMPRESSION_REJECTED', response.read())
        self.assertEqual(self.upstream.calls, 1)

    def test_truncated_response_has_no_terminal_chunk_or_fake_done(self):
        self.upstream.mode = 'truncated'
        self.upstream.payload = b'data: partial\n\n'
        response, _ = self.request()
        with self.assertRaises(http.client.IncompleteRead):
            response.read()
        self.assertEqual(self.logs()[-1]['errorCode'], 'UPSTREAM_PREMATURE_CLOSE')
        self.assertEqual(self.upstream.calls, 1)

    def test_headers_timeout_no_retry(self):
        self.upstream.mode = 'headers'
        self.proxy.headers_timeout = 0.08
        response, _ = self.request()
        self.assertEqual(response.status, 504)
        response.read()
        self.assertEqual(self.logs()[-1]['errorCode'], 'HEADERS_TIMEOUT')
        self.assertEqual(self.upstream.calls, 1)

    def test_idle_timeout_terminates_chunk_framing(self):
        self.upstream.mode = 'idle'
        self.proxy.idle_timeout = 0.08
        response, _ = self.request()
        with self.assertRaises(http.client.IncompleteRead):
            response.read()
        self.assertEqual(self.logs()[-1]['errorCode'], 'IDLE_TIMEOUT')
        self.assertEqual(self.upstream.calls, 1)

    def test_total_timeout_interrupts_headers(self):
        self.upstream.mode = 'headers'
        self.proxy.total_timeout = 0.08
        with self.assertRaises((OSError, http.client.HTTPException)):
            response, _ = self.request()
            response.read()
        self.assertEqual(self.logs()[-1]['errorCode'], 'TOTAL_TIMEOUT')

    def test_client_disconnect_interrupts_headers(self):
        self.upstream.mode = 'headers'
        connection = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=1)
        payload = b'{"prompt_cache_key":"test"}'
        connection.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nx-asi-planner: 1\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\n\r\n' + payload)
        self.assertTrue(self.upstream.entered.wait(1))
        connection.close()
        deadline = time.monotonic() + 0.4
        while not self.upstream.disconnected and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.upstream.disconnected)
        self.assertEqual(self.logs()[-1]['errorCode'], 'CLIENT_DISCONNECTED')

    def test_server_stop_interrupts_active_request(self):
        self.upstream.mode = 'headers'
        connection = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=1)
        self.addCleanup(connection.close)
        payload = b'{"prompt_cache_key":"test"}'
        connection.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nx-asi-planner: 1\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\n\r\n' + payload)
        self.assertTrue(self.upstream.entered.wait(1))
        self.proxy.stop_active()
        self.assertEqual(connection.recv(1), b'')
        self.assertEqual(self.logs()[-1]['errorCode'], 'SERVICE_STOPPING')

    def test_config_refuses_plain_remote_and_nonloopback_bind(self):
        for upstream in ('http://example.com', 'https://user:pass@example.com', 'https://example.com/path'):
            with self.assertRaises(ValueError):
                PlannerProxy(upstream=upstream)
        with self.assertRaises(ValueError):
            PlannerProxy(('0.0.0.0', 0))

    def test_bodyless_response(self):
        self.upstream.mode = 'empty'
        response, _ = self.request()
        self.assertEqual(response.status, 204)
        self.assertEqual(response.read(), b'')
        self.assertIsNone(self.logs()[-1]['errorCode'])

    def test_ambiguous_upstream_framing_rejected(self):
        for mode in ('duplicate-length', 'cl-and-te', 'encoding'):
            self.upstream.mode = mode
            response, _ = self.request()
            self.assertEqual(response.status, 502)
            self.assertIn(b'UPSTREAM_INVALID_FRAMING', response.read())

    def test_upstream_aggregate_header_limit(self):
        self.proxy.max_headers = 1024
        self.upstream.mode = 'huge-headers'
        response, _ = self.request()
        self.assertEqual(response.status, 502)
        response.read()

    def test_slow_header_trickle_has_absolute_deadline(self):
        self.proxy.headers_timeout = 0.12
        connection = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=1)
        self.addCleanup(connection.close)
        started = time.monotonic()
        connection.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nX-Slow: ')
        for _ in range(12):
            try:
                connection.sendall(b'x')
            except OSError:
                break
            time.sleep(0.025)
        try:
            self.assertEqual(connection.recv(1), b'')
        except ConnectionResetError:
            pass
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.upstream.calls, 0)

    def test_expect_continue_rejected_before_body(self):
        connection = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=1)
        self.addCleanup(connection.close)
        connection.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nExpect: 100-continue\r\nContent-Length: 100\r\n\r\n')
        response = http.client.HTTPResponse(connection)
        response.begin()
        self.assertEqual(response.status, 417)
        response.read()
        self.assertEqual(self.upstream.calls, 0)

    def test_nonfinite_json_rejected(self):
        response, _ = self.request({'prompt_cache_key': 'session', 'temperature': float('nan')})
        self.assertEqual(response.status, 400)
        self.assertIn(b'INVALID_JSON', response.read())
        self.assertEqual(self.upstream.calls, 0)

    def test_connect_timeout_is_reported_without_retry(self):
        original_connect = http.client.HTTPConnection.connect
        def connect(connection):
            if connection.port == self.upstream.server_port:
                raise TimeoutError
            return original_connect(connection)
        with mock.patch('asi1_planner_proxy.http.client.HTTPConnection.connect', connect):
            response, _ = self.request()
            self.assertEqual(response.status, 504)
            response.read()
        self.assertEqual(self.logs()[-1]['errorCode'], 'CONNECT_TIMEOUT')
        self.assertEqual(self.upstream.calls, 0)

    def test_tls_uses_verifying_default_context(self):
        from urllib.parse import urlsplit
        self.proxy.target = urlsplit('https://example.invalid')
        contexts = []
        def connection(*args, **kwargs):
            contexts.append(kwargs['context'])
            raise OSError('fixture only')
        with mock.patch('asi1_planner_proxy.http.client.HTTPSConnection', side_effect=connection):
            response, _ = self.request()
            self.assertEqual(response.status, 502)
            response.read()
        self.assertEqual(contexts[0].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(contexts[0].check_hostname)

    def test_concurrency_bound_rejects_extra_request(self):
        self.proxy.slots = threading.BoundedSemaphore(1)
        self.upstream.mode = 'headers'
        first = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=1)
        self.addCleanup(first.close)
        payload = b'{"prompt_cache_key":"test"}'
        first.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nx-asi-planner: 1\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\n\r\n' + payload)
        self.assertTrue(self.upstream.entered.wait(1))
        self.assertFalse(self.proxy.slots.acquire(blocking=False))
        # Admission happens before the rejected request is consumed. The 503 is
        # best effort: TCP may close while http.client is still sending its body.
        try:
            response, _ = self.request()
            self.assertEqual(response.status, 503)
            response.read()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                http.client.RemoteDisconnected):
            pass
        self.assertEqual(self.upstream.calls, 1)
        self.assertFalse(self.proxy.slots.acquire(blocking=False))

    def test_total_timeout_interrupts_incomplete_client_body(self):
        self.proxy.total_timeout = 0.08
        connection = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=1)
        self.addCleanup(connection.close)
        connection.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nx-asi-planner: 1\r\nContent-Length: 100\r\n\r\n{')
        self.assertEqual(connection.recv(1), b'')
        self.assertEqual(self.logs()[-1]['errorCode'], 'TOTAL_TIMEOUT')
        self.assertEqual(self.upstream.calls, 0)

    def test_cancel_before_preparation_or_send_never_reconnects(self):
        original_connect = http.client.HTTPConnection.connect
        original_endheaders = http.client.HTTPConnection.endheaders
        for window in ('before-putrequest', 'before-endheaders'):
            ready, release = threading.Event(), threading.Event()
            connects = []
            def connect(connection):
                original_connect(connection)
                if connection.port == self.upstream.server_port:
                    connects.append(connection)
                    if window == 'before-putrequest':
                        ready.set()
                        release.wait(2)
            def endheaders(connection, *args, **kwargs):
                if connection.port == self.upstream.server_port and window == 'before-endheaders':
                    ready.set()
                    release.wait(2)
                return original_endheaders(connection, *args, **kwargs)
            failures = []
            def client():
                connection = http.client.HTTPConnection('127.0.0.1', self.proxy.server_port, timeout=2)
                try:
                    connection.request('POST', '/v1/chat/completions',
                                       b'{"prompt_cache_key":"cancel-secret"}',
                                       {'x-asi-planner': '1', 'Authorization': 'Bearer cancel-auth'})
                    response = connection.getresponse()
                    response.read()
                except (OSError, http.client.HTTPException):
                    pass
                except Exception as exc:
                    failures.append(exc)
                finally:
                    connection.close()
            with mock.patch('asi1_planner_proxy.http.client.HTTPConnection.connect', connect), \
                    mock.patch('asi1_planner_proxy.http.client.HTTPConnection.endheaders', endheaders):
                worker = threading.Thread(target=client)
                worker.start()
                try:
                    self.assertTrue(ready.wait(1))
                    self.proxy.stop_active()
                finally:
                    release.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
                deadline = time.monotonic() + 1
                while self.proxy.active and time.monotonic() < deadline:
                    time.sleep(0.01)
            self.assertFalse(failures)
            self.assertEqual(len(connects), 1)
            self.assertEqual(connects[0].auto_open, 0)
            self.assertEqual(self.upstream.calls, 0)

    def test_headers_expiry_after_socket_detach_is_irreversible(self):
        self.proxy.slots = threading.BoundedSemaphore(1)
        self.proxy.headers_timeout = 0.04
        self.proxy.total_timeout = 0.12
        self.upstream.mode = 'continuous'
        detached, release = threading.Event(), threading.Event()
        original = http.client.HTTPConnection.getresponse
        responses = []
        def delayed(connection):
            response = original(connection)
            if connection.port == self.upstream.server_port:
                responses.append(response)
                self.assertIsNone(connection.sock)
                detached.set()
                release.wait(1)
            return response
        client = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=1)
        self.addCleanup(client.close)
        payload = b'{"prompt_cache_key":"expiry"}'
        started = time.monotonic()
        with mock.patch('asi1_planner_proxy.http.client.HTTPConnection.getresponse', delayed):
            try:
                client.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nx-asi-planner: 1\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\n\r\n' + payload)
                self.assertTrue(detached.wait(1))
                raw = client.recv(4096)
                self.assertEqual(raw, b'')
                self.assertNotIn(b'0\r\n\r\n', raw)
                self.assertLess(time.monotonic() - started, 0.22)
            finally:
                release.set()
            deadline = time.monotonic() + 0.2
            while self.proxy.active and time.monotonic() < deadline:
                time.sleep(0.002)
        self.assertFalse(self.proxy.active)
        self.assertTrue(self.proxy.slots.acquire(blocking=False))
        self.proxy.slots.release()
        entry = self.logs()[-1]
        self.assertEqual(entry['errorCode'], 'HEADERS_TIMEOUT')
        self.assertIsNone(entry['status'])
        self.assertEqual(entry['bytesSent'], 0)
        self.assertTrue(responses[0].isclosed())

    def test_log_errors_alert_once_until_recovery(self):
        valid_log = self.proxy.log_path
        self.proxy.log_path = Path(self.temp.name)  # Existing directory, never writable as file.
        stderr = io.StringIO()
        with mock.patch('sys.stderr', stderr):
            for _ in range(1000):
                self.proxy.record({'metadata': 'only'})
            self.assertEqual(stderr.getvalue().splitlines(), ['asi1-planner-proxy: LOG_WRITE_FAILED'])
            self.proxy.log_path = valid_log
            self.proxy.record({'metadata': 'recovered'})
            self.proxy.record({'metadata': 'still-good'})
            self.proxy.log_path = Path(self.temp.name)
            for _ in range(1000):
                self.proxy.record({'metadata': 'only'})
        self.assertEqual(stderr.getvalue().splitlines(), [
            'asi1-planner-proxy: LOG_WRITE_FAILED',
            'asi1-planner-proxy: LOG_WRITE_RECOVERED',
            'asi1-planner-proxy: LOG_WRITE_FAILED'])

    def test_stalled_dns_retains_permit_and_never_posts_after_deadline(self):
        self.proxy.slots = threading.BoundedSemaphore(1)
        self.proxy.total_timeout = 0.08
        entered, release = threading.Event(), threading.Event()
        original_resolve = socket.getaddrinfo
        original_connect = http.client.HTTPConnection.connect
        resolver_calls, late_sockets = [], []
        def resolve(host, port, *args, **kwargs):
            if port == self.upstream.server_port:
                resolver_calls.append((host, port))
                entered.set()
                release.wait(2)
            return original_resolve(host, port, *args, **kwargs)
        def connect(connection):
            original_connect(connection)
            if connection.port == self.upstream.server_port:
                late_sockets.append(connection.sock)
        client = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=1)
        self.addCleanup(client.close)
        payload = b'{"prompt_cache_key":"dns"}'
        with mock.patch('asi1_planner_proxy.socket.getaddrinfo', resolve), \
                mock.patch('asi1_planner_proxy.http.client.HTTPConnection.connect', connect):
            try:
                client.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nx-asi-planner: 1\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\n\r\n' + payload)
                self.assertTrue(entered.wait(1))
                self.assertEqual(client.recv(1), b'')
                self.assertFalse(self.proxy.slots.acquire(blocking=False))
                for _ in range(5):
                    try:
                        response, _ = self.request()
                        self.assertEqual(response.status, 503)
                        response.read()
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                            http.client.RemoteDisconnected):
                        pass
                self.assertEqual(len(resolver_calls), 1)
                self.assertFalse(self.proxy.slots.acquire(blocking=False))
            finally:
                release.set()
            deadline = time.monotonic() + 1
            permit_returned = False
            while time.monotonic() < deadline:
                if self.proxy.slots.acquire(blocking=False):
                    self.proxy.slots.release()
                    permit_returned = True
                    break
                time.sleep(0.005)
        self.assertTrue(permit_returned)
        self.assertEqual(self.upstream.calls, 0)
        self.assertEqual(len(late_sockets), 1)
        self.assertEqual(late_sockets[0].fileno(), -1)
        self.assertEqual(self.logs()[-1]['errorCode'], 'TOTAL_TIMEOUT')

    def test_raw_chunked_sse_decoded_exactly(self):
        self.upstream.mode = 'raw-chunked'
        response, _ = self.request()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), self.upstream.payload)

    def test_padded_transfer_encoding_is_rejected(self):
        self.upstream.mode = 'padded-chunked'
        response, _ = self.request()
        self.assertEqual(response.status, 502)
        self.assertIn(b'UPSTREAM_INVALID_FRAMING', response.read())

    def test_large_stream_first_bytes_precede_upstream_completion(self):
        self.upstream.mode = 'latched-stream'
        self.upstream.payload = b'data: ' + b'x' * (2 * 1024 * 1024) + b'\n\n'
        self.upstream.first_sent = threading.Event()
        self.upstream.release_stream = threading.Event()
        self.addCleanup(self.upstream.release_stream.set)
        response, _ = self.request()
        self.assertTrue(self.upstream.first_sent.wait(1))
        self.assertEqual(response.read1(7), self.upstream.payload[:7])
        self.assertFalse(self.upstream.release_stream.is_set())
        self.upstream.release_stream.set()
        total = 7
        while True:
            chunk = response.read1(65536)
            if not chunk:
                break
            self.assertLessEqual(len(chunk), 65536)
            total += len(chunk)
        self.assertEqual(total, len(self.upstream.payload))

    def test_logs_rotate_with_bounded_files_and_private_modes(self):
        self.proxy.log_max_bytes = 80
        for index in range(30):
            self.proxy.record({'index': index, 'metadata': 'x' * 20})
        files = sorted(Path(self.temp.name).glob('traffic.jsonl*'))
        self.assertEqual(len(files), 4)
        for path in files:
            self.assertLessEqual(path.stat().st_size, 80)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(path.stat().st_uid, os.getuid())
            for line in path.read_text().splitlines():
                self.assertIsInstance(json.loads(line), dict)

    def test_log_symlinks_are_not_followed_or_rotated(self):
        victim = Path(self.temp.name) / 'unrelated'
        victim.write_text('untouched')
        log = self.proxy.log_path
        log.symlink_to(victim)
        with mock.patch('sys.stderr'):
            self.proxy.record({'event': 'refused'})
        self.assertTrue(log.is_symlink())
        self.assertEqual(victim.read_text(), 'untouched')
        log.unlink()
        log.write_text('{"metadata":"old"}\n')
        self.proxy.log_max_bytes = 30
        archive = log.with_name(log.name + '.1')
        archive.symlink_to(victim)
        with mock.patch('sys.stderr'):
            self.proxy.record({'event': 'rotation-needed'})
        self.assertTrue(archive.is_symlink())
        self.assertEqual(victim.read_text(), 'untouched')
        self.assertEqual(log.read_text(), '{"metadata":"old"}\n')

    def test_response_server_header_has_no_python_version(self):
        response, _ = self.request()
        self.assertEqual(response.getheader('Server').strip(), 'ReprisePlannerProxy')
        response.read()

    def test_ambiguous_request_framing_rejected(self):
        connection = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=1)
        self.addCleanup(connection.close)
        connection.sendall(b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\nx-asi-planner: 1\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}')
        response = http.client.HTTPResponse(connection)
        response.begin()
        self.assertEqual(response.status, 400)
        response.read()
        self.assertEqual(self.upstream.calls, 0)

    def test_check_has_no_side_effect(self):
        self.assertEqual(main(['--check']), 0)

    def test_log_failure_does_not_break_response(self):
        self.proxy.log_path = Path(self.temp.name)
        response, _ = self.request()
        self.assertEqual(response.read(), self.upstream.payload)


if __name__ == '__main__':
    unittest.main()
