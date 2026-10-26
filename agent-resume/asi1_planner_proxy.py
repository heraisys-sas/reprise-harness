#!/usr/bin/env python3
"""Bounded ASI planner relay. No inference, retry or credential discovery."""
from __future__ import annotations

import argparse
import hashlib
import http.client
import http.server
import ipaddress
import json
import os
from pathlib import Path
import select
import signal
import socket
import ssl
import stat
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit
import uuid

PATH = '/v1/chat/completions'
HOP = {'connection', 'keep-alive', 'proxy-connection', 'proxy-authenticate',
       'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}
DEFAULT_UPSTREAM = 'https://inference.asicloud.cudos.org:443'


def clean_headers(headers):
    excluded = HOP | {item.strip().lower() for item in headers.get('connection', '').split(',')}
    return {key.lower(): value for key, value in headers.items() if key.lower() not in excluded}


class HeaderBudget:
    """Limit aggregate header bytes without buffering an unbounded block."""
    def __init__(self, stream, limit):
        self.stream, self.remaining = stream, limit

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def readline(self, limit=-1):
        line = self.stream.readline(min(limit if limit >= 0 else 65537, max(1, self.remaining + 1)))
        self.remaining -= len(line)
        if self.remaining < 0:
            raise http.client.LineTooLong('headers')
        return line


class BoundedResponse(http.client.HTTPResponse):
    def begin(self):
        original = self.fp
        self.fp = HeaderBudget(original, self.header_limit)
        try:
            super().begin()
        finally:
            # begin() can close fp for an invalid or bodyless response.
            if self.fp is not None:
                self.fp = original


class ProxyError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


class PlannerProxy(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address=('127.0.0.1', 8481), *, upstream=None, log_path=None,
                 connect_timeout=2, headers_timeout=10, idle_timeout=120,
                 total_timeout=600, max_body=4 * 1024 * 1024,
                 max_headers=32768, max_concurrent=32,
                 log_max_bytes=5 * 1024 * 1024, log_backups=3):
        target = urlsplit(upstream or os.environ.get('ASI1_PLANNER_UPSTREAM', DEFAULT_UPSTREAM))
        if (target.scheme not in ('http', 'https') or not target.hostname or
                target.username or target.password or target.path not in ('', '/') or
                target.query or target.fragment):
            raise ValueError('Upstream must be an HTTP(S) origin')
        if target.scheme == 'http':
            try:
                loopback = ipaddress.ip_address(target.hostname).is_loopback
            except ValueError:
                loopback = target.hostname == 'localhost'
            if not loopback:
                raise ValueError('Plain HTTP is limited to loopback fixtures')
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise ValueError('Proxy must bind loopback')
        self.target = target
        self.ssl_context = ssl.create_default_context()  # Shared read-only after construction.
        self.log_path = Path(log_path or os.environ.get('ASI1_PLANNER_LOG_PATH') or
                             Path.home() / 'Library/Logs/asi1-planner-proxy/traffic.jsonl')
        self.connect_timeout, self.headers_timeout = connect_timeout, headers_timeout
        self.idle_timeout, self.total_timeout = idle_timeout, total_timeout
        self.max_body, self.max_headers = max_body, max_headers
        self.slots = threading.BoundedSemaphore(max_concurrent)
        self.active = set()
        self.active_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self._log_failed = False
        self.log_max_bytes, self.log_backups = log_max_bytes, log_backups
        super().__init__(address, PlannerHandler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        # getaddrinfo is not bounded by socket timeouts. Its owner retains this
        # permit until resolution returns, so at most max_concurrent workers can
        # stall; later clients are refused rather than spawning replacements.
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def _open_log(self, path, *, create=False):
        flags = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK
        if create:
            flags |= os.O_CREAT
        fd = os.open(path, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise OSError('Unsafe log file')
            os.fchmod(fd, 0o600)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def record(self, entry):
        # Four files maximum by default: 5 MiB active + three 5 MiB archives.
        # These checks protect against accidental links, not a hostile same UID.
        with self.log_lock:
            try:
                payload = (json.dumps(entry, separators=(',', ':')) + '\n').encode()
                if len(payload) > self.log_max_bytes:
                    raise OSError('Log record too large')
                self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = self._open_log(self.log_path, create=True)
                try:
                    size = os.fstat(fd).st_size
                    if size > self.log_max_bytes:
                        # A pre-existing unbounded log must not become an oversized archive.
                        os.ftruncate(fd, 0)
                        size = 0
                    if size + len(payload) > self.log_max_bytes:
                        archives = [self.log_path.with_name(self.log_path.name + '.' + str(i))
                                    for i in range(1, self.log_backups + 1)]
                        for archive in archives:
                            try:
                                archive_fd = self._open_log(archive)
                            except FileNotFoundError:
                                continue
                            else:
                                os.close(archive_fd)
                        os.close(fd)
                        fd = None
                        if archives:
                            archives[-1].unlink(missing_ok=True)
                            for source, destination in reversed(list(zip(archives[:-1], archives[1:]))):
                                if source.exists():
                                    os.replace(source, destination)
                            os.replace(self.log_path, archives[0])
                        else:
                            self.log_path.unlink()
                        fd = self._open_log(self.log_path, create=True)
                    # Records are bounded and serialized; handle rare partial writes.
                    while payload:
                        written = os.write(fd, payload)
                        if not written:
                            raise OSError('Log write failed')
                        payload = payload[written:]
                finally:
                    if fd is not None:
                        os.close(fd)
            except OSError:
                self._log_status(True)
            else:
                self._log_status(False)

    def _log_status(self, failed):
        if self._log_failed == failed:
            return
        self._log_failed = failed
        # One static message per transition, never repeated for a persistent fault.
        import sys
        try:
            print('asi1-planner-proxy: LOG_WRITE_FAILED' if failed else
                  'asi1-planner-proxy: LOG_WRITE_RECOVERED', file=sys.stderr)
        except (OSError, ValueError):
            pass

    def stop_active(self):
        with self.active_lock:
            sessions = list(self.active)
        for abort in sessions:
            abort('SERVICE_STOPPING')

    def server_close(self):
        self.stop_active()
        super().server_close()

    def handle_error(self, request, client_address):
        # Base implementation prints exception context, potentially private data.
        pass


class PlannerHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'ReprisePlannerProxy'
    sys_version = ''

    def log_message(self, *args):
        pass

    def setup(self):
        self.header_deadline = time.monotonic() + min(self.server.headers_timeout, self.server.total_timeout)
        self.request.settimeout(min(self.server.headers_timeout, self.server.total_timeout))
        super().setup()
        def expire_headers(*_):
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.expire_headers = expire_headers
        self.header_timer = threading.Timer(min(self.server.headers_timeout, self.server.total_timeout), expire_headers)
        self.header_timer.daemon = True
        with self.server.active_lock:
            self.server.active.add(expire_headers)
        self.header_timer.start()

    def finish(self):
        self.header_timer.cancel()
        with self.server.active_lock:
            self.server.active.discard(self.expire_headers)
        super().finish()

    def parse_request(self):
        original = self.rfile
        handler = self
        class HeaderReader(HeaderBudget):
            def readline(reader, limit=-1):
                remaining = handler.header_deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('header deadline')
                handler.connection.settimeout(remaining)
                return super().readline(limit)
        self.rfile = HeaderReader(original, self.server.max_headers - len(self.raw_requestline))
        try:
            accepted = super().parse_request()
        finally:
            self.rfile = original
            self.header_timer.cancel()
            with self.server.active_lock:
                self.server.active.discard(self.expire_headers)
        if not accepted:
            return False
        size = len(self.raw_requestline) + sum(len(k) + len(v) + 4 for k, v in self.headers.items())
        if size > self.server.max_headers:
            self.send_error(431)
            return False
        return True

    def handle_expect_100(self):
        self.send_error(417)
        return False

    def send_error(self, code, message=None, explain=None):
        # Base HTML error includes incoming request method/path.
        self.close_connection = True
        data = json.dumps({'error': 'INVALID_HTTP_REQUEST'}).encode()
        try:
            self.send_response(code)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def __getattr__(self, name):
        if name.startswith('do_'):
            return self.do_POST
        raise AttributeError(name)

    def do_POST(self):
        self.close_connection = True
        started = time.monotonic()
        entry = dict(timestamp=datetime.now(timezone.utc).isoformat(), correlationId=uuid.uuid4().hex,
                     sessionIdHash=None, status=None, headersMs=None, totalMs=None,
                     requestBytes=0, bytesReceived=0, bytesSent=0, errorCode=None)
        done = threading.Event()
        stopped = threading.Event()
        state = {'upstream': None, 'response_started': False, 'header_deadline': None}
        lock = threading.Lock()

        def abort(code):
            with lock:
                if done.is_set() or stopped.is_set():
                    return
                stopped.set()
                entry['errorCode'] = code
                upstream = state['upstream']
                # shutdown interrupts reads held inside HTTPResponse/socket.makefile.
                if upstream and upstream.sock:
                    try:
                        upstream.sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                # Keep the socket reference stable until the owner thread exits.
                # shutdown interrupts I/O; close here could trigger http.client's
                # implicit auto-open or race a send into a None socket.
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        def monitor():
            while not done.wait(0.025):
                if time.monotonic() - started >= self.server.total_timeout:
                    abort('TOTAL_TIMEOUT')
                    continue
                if state['header_deadline'] and time.monotonic() >= state['header_deadline']:
                    # Expiry is irreversible even during getresponse's socket
                    # detachment window. Keep watching the total budget until the
                    # owner has actually finished and released its permit.
                    abort('HEADERS_TIMEOUT')
                    continue
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    if readable and not self.connection.recv(1, socket.MSG_PEEK):
                        abort('CLIENT_DISCONNECTED')
                        return
                except OSError:
                    abort('CLIENT_DISCONNECTED')
                    return

        with self.server.active_lock:
            self.server.active.add(abort)
        watchdog = threading.Thread(target=monitor, daemon=True)
        watchdog.start()
        upstream = None
        response = None
        phase = 'REQUEST'
        try:
            if self.command != 'POST' or self.path != PATH or self.headers.get('x-asi-planner') != '1':
                raise ProxyError(404, 'NOT_FOUND')
            lengths = self.headers.get_all('Content-Length', [])
            if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
                raise ProxyError(400, 'INVALID_CONTENT_LENGTH')
            try:
                length = int(lengths[0])
            except ValueError:
                raise ProxyError(400, 'INVALID_CONTENT_LENGTH')
            if length < 0:
                raise ProxyError(400, 'INVALID_CONTENT_LENGTH')
            if length > self.server.max_body:
                raise ProxyError(413, 'REQUEST_TOO_LARGE')
            self.connection.settimeout(self.server.idle_timeout)
            raw = self.rfile.read(length)
            entry['requestBytes'] = len(raw)
            if len(raw) != length:
                raise ProxyError(400, 'INCOMPLETE_REQUEST')
            try:
                body = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
            except (ValueError, UnicodeError, RecursionError):
                raise ProxyError(400, 'INVALID_JSON')
            if not isinstance(body, dict):
                raise ProxyError(400, 'INVALID_BODY')
            key = body.get('prompt_cache_key')
            if not isinstance(key, str) or not key.strip() or len(key) > 64:
                raise ProxyError(400, 'INVALID_PROMPT_CACHE_KEY')
            try:
                encoded_key = key.encode()
            except UnicodeError:
                raise ProxyError(400, 'INVALID_PROMPT_CACHE_KEY')
            entry['sessionIdHash'] = hashlib.sha256(encoded_key).hexdigest()[:24]
            body.update(planner_mode=True, **{'x-session-id': key}, reasoning_effort='low', temperature=0.25)
            body.pop('prompt_cache_key', None)
            body.pop('stream_options', None)
            payload = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            headers = clean_headers(self.headers)
            for name in ('x-asi-planner', 'accept-encoding', 'x-session-id', 'expect', 'host', 'content-length'):
                headers.pop(name, None)
            headers.update({'content-type': 'application/json', 'content-length': str(len(payload))})
            target = self.server.target
            klass = http.client.HTTPSConnection if target.scheme == 'https' else http.client.HTTPConnection
            options = {'timeout': self.server.connect_timeout}
            if target.scheme == 'https':
                options['context'] = self.server.ssl_context
            upstream = klass(target.hostname, target.port, **options)
            upstream.auto_open = 0  # Only the explicit connect below is permitted.
            upstream.response_class = type('Response', (BoundedResponse,), {'header_limit': self.server.max_headers})
            with lock:
                state['upstream'] = upstream
                if stopped.is_set():
                    raise ProxyError(502, entry['errorCode'])
            phase = 'CONNECT'
            upstream.connect()
            with lock:
                if stopped.is_set():
                    raise ProxyError(502, entry['errorCode'])
                upstream.sock.settimeout(self.server.headers_timeout)
                phase = 'HEADERS'
                state['header_deadline'] = time.monotonic() + self.server.headers_timeout
                # These only prepare in-memory buffers: no lock over network I/O.
                upstream.putrequest('POST', PATH, skip_accept_encoding=True)
                for name, value in headers.items():
                    upstream.putheader(name, value)
            with lock:
                if stopped.is_set():
                    raise ProxyError(502, entry['errorCode'])
            # Cancellation after the check shuts down this socket; auto_open=0
            # prevents any library send from creating a replacement connection.
            upstream.endheaders(payload)
            response = upstream.getresponse()
            with lock:
                state['header_deadline'] = None
                if stopped.is_set():
                    raise ProxyError(504, entry['errorCode'])
            lengths = response.headers.get_all('Content-Length', [])
            encodings = response.headers.get_all('Transfer-Encoding', [])
            if (len(lengths) > 1 or len(encodings) > 1 or
                    (lengths and encodings) or
                    (encodings and encodings[0].lower() != 'chunked')):
                raise ProxyError(502, 'UPSTREAM_INVALID_FRAMING')
            if lengths:
                try:
                    if int(lengths[0]) < 0:
                        raise ValueError
                except ValueError:
                    raise ProxyError(502, 'UPSTREAM_INVALID_FRAMING')
            entry['headersMs'] = round((time.monotonic() - started) * 1000)
            if response.getheader('content-encoding', 'identity').lower() != 'identity':
                raise ProxyError(502, 'UPSTREAM_COMPRESSION_REJECTED')
            response_headers = clean_headers(response.headers)
            for name in ('authorization', 'content-encoding', 'content-length', 'server', 'date'):
                response_headers.pop(name, None)
            # Emit our own chunk framing: no terminal chunk on aborted streams.
            self.send_response(response.status)
            for name, value in response_headers.items():
                self.send_header(name, value)
            self.send_header('Connection', 'close')
            body_allowed = response.status not in (204, 304) and response.status >= 200
            if body_allowed:
                self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            state['response_started'] = True
            entry['status'] = response.status
            phase = 'IDLE'
            # HTTPResponse may detach its socket from connection on Connection:close.
            if body_allowed and response.fp is not None:
                response_socket = response.fp.raw._sock
                upstream.sock = response_socket
                response_socket.settimeout(self.server.idle_timeout)
            self.connection.settimeout(self.server.idle_timeout)
            while body_allowed and not stopped.is_set():
                chunk = response.read1(65536)
                if not chunk:
                    if response.length not in (None, 0):
                        raise ProxyError(502, 'UPSTREAM_PREMATURE_CLOSE')
                    break
                entry['bytesReceived'] += len(chunk)
                self.wfile.write(('%x\r\n' % len(chunk)).encode() + chunk + b'\r\n')
                entry['bytesSent'] += len(chunk)
            if body_allowed and not stopped.is_set():
                self.wfile.write(b'0\r\n\r\n')
        except ProxyError as exc:
            entry['errorCode'] = entry['errorCode'] or exc.code
            if not state['response_started'] and not stopped.is_set():
                entry['status'] = exc.status
                try:
                    payload = json.dumps({'error': exc.code}).encode()
                    self.send_response(exc.status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    pass
        except (OSError, http.client.HTTPException, ValueError) as exc:
            code = ('%s_TIMEOUT' % phase if isinstance(exc, TimeoutError)
                    else 'UPSTREAM_RESPONSE_ERROR' if state['response_started'] else 'UPSTREAM_REQUEST_ERROR')
            entry['errorCode'] = entry['errorCode'] or code
            if not state['response_started'] and not stopped.is_set():
                try:
                    entry['status'] = 504 if entry['errorCode'].endswith('_TIMEOUT') else 502
                    self.send_error(entry['status'])
                except OSError:
                    pass
        finally:
            done.set()
            if response:
                response.close()
            if upstream:
                upstream.close()
            with self.server.active_lock:
                self.server.active.discard(abort)
            entry['totalMs'] = round((time.monotonic() - started) * 1000)
            self.server.record(entry)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8481)
    parser.add_argument('--check', action='store_true', help='Validate CLI without sockets or writes')
    args = parser.parse_args(argv)
    if args.check:
        return 0
    proxy = PlannerProxy((args.host, args.port))
    def stop(*_):
        proxy.stop_active()
        threading.Thread(target=proxy.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        proxy.serve_forever(poll_interval=0.1)
    finally:
        proxy.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
