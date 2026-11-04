"""Ephemeral local fm transport for the experimental pilot; no cloud fallback."""
import json
import math
import os
from pathlib import Path
import select
import signal
import socket
import stat
import struct
import subprocess
import tempfile
import time

import apple_pilot

FM = '/usr/bin/fm'
LOCAL_PEERPID = 2  # Darwin sys/un.h
SOL_LOCAL = 0
MAX_REQUEST = 131_072
MAX_HEADERS = 16_384
MAX_BODY = 131_072
MAX_LIFETIME_SECONDS = 65
POLL_SECONDS = 0.05


class TransportError(apple_pilot.BackendError):
    """Stable public error code; never contains request, response or stderr."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _peer_pid(sock):
    # Never fall back to UID-only verification or an unchecked socket.
    value = sock.getsockopt(SOL_LOCAL, LOCAL_PEERPID, struct.calcsize('i'))
    return struct.unpack('i', value)[0]


def _json_unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_key')
        result[key] = value
    return result


def _env(directory):
    allowed = ('HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL')
    return {k: os.environ[k] for k in allowed if k in os.environ} | {'PATH': '/usr/bin:/bin', 'TMPDIR': directory}


def _spawn(command, directory):
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=_env(directory), start_new_session=True,
                            close_fds=True)


def _stop(process):
    """Signal only our live child process group; do not kill a reused PID."""
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError:
            raise TransportError('cleanup_unconfirmed') from None
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    raise TransportError('cleanup_unconfirmed') from None
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired as exc:
                raise TransportError('cleanup_unconfirmed') from exc
    else:
        process.wait()


class LocalFM:
    """Trusted host owns a new fm subprocess for each call; not an external socket client.

    spawn and peer_pid are trusted-host injection seams for tests, never model input.
    A failed or cancelled call is never automatically retried.
    """
    def __init__(self, *, spawn=_spawn, peer_pid=_peer_pid, clock=time.monotonic):
        self._spawn = spawn
        self._peer_pid = peer_pid
        self._clock = clock

    def _check(self, deadline, cancelled, process):
        if cancelled():
            raise TransportError('cancelled')
        if self._clock() >= deadline:
            raise TransportError('deadline_exceeded')
        if process.poll() is not None:
            raise TransportError('process_exited')

    def _ready(self, sock, writable, deadline, cancelled, process):
        while True:
            self._check(deadline, cancelled, process)
            wait = min(POLL_SECONDS, max(0.0, deadline - self._clock()))
            readable, writable_fds, exceptional = select.select([] if writable else [sock],
                [sock] if writable else [], [sock], wait)
            if exceptional:
                raise TransportError('connection_failed')
            if writable_fds or readable:
                return

    def _connect(self, path, deadline, cancelled, process):
        while True:
            self._check(deadline, cancelled, process)
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.settimeout(POLL_SECONDS)
                sock.connect(path)
            except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
                sock.close()
                time.sleep(min(POLL_SECONDS, max(0.0, deadline - self._clock())))
                continue
            except OSError:
                sock.close()
                raise TransportError('connection_failed') from None
            try:
                if self._peer_pid(sock) != process.pid or process.poll() is not None:
                    raise TransportError('peer_unverified')
            except (OSError, ValueError, struct.error):
                sock.close()
                raise TransportError('peer_unverified') from None
            except BaseException:
                sock.close()
                raise
            sock.setblocking(False)
            return sock

    def _exchange(self, sock, request, deadline, cancelled, process):
        sent = 0
        while sent < len(request):
            self._ready(sock, True, deadline, cancelled, process)
            try:
                written = sock.send(request[sent:])
            except BlockingIOError:
                continue
            if written <= 0:
                raise TransportError('connection_failed')
            sent += written
        data = bytearray()
        header_end = None
        expected = None
        while True:
            self._ready(sock, False, deadline, cancelled, process)
            try:
                chunk = sock.recv(65536)
            except BlockingIOError:
                continue
            if not chunk:
                raise TransportError('incomplete_response')
            data.extend(chunk)
            if header_end is None:
                index = data.find(b'\r\n\r\n')
                if index < 0:
                    if len(data) > MAX_HEADERS:
                        raise TransportError('response_too_large')
                    continue
                header_end = index + 4
                if header_end > MAX_HEADERS:
                    raise TransportError('response_too_large')
                expected = self._headers(bytes(data[:index]))
            if len(data) - header_end > MAX_BODY:
                raise TransportError('response_too_large')
            if len(data) - header_end >= expected:
                if len(data) - header_end != expected:
                    raise TransportError('invalid_response')
                return bytes(data[header_end:])

    @staticmethod
    def _headers(data):
        try:
            lines = data.decode('iso-8859-1').split('\r\n')
            status = lines[0].split(' ', 2)
            if len(status) < 2 or status[0] not in ('HTTP/1.0', 'HTTP/1.1') or status[1] != '200':
                raise TransportError('http_error')
            headers = {}
            for line in lines[1:]:
                if ':' not in line:
                    raise ValueError('invalid_header')
                name, value = line.split(':', 1)
                name = name.strip().lower()
                if not name or name in headers:
                    raise ValueError('duplicate_header')
                headers[name] = value.strip()
            if 'transfer-encoding' in headers or not headers.get('content-length', '').isdigit():
                raise ValueError('unsupported_framing')
            length = int(headers['content-length'])
            if length <= 0 or length > MAX_BODY:
                raise ValueError('invalid_length')
            return length
        except (UnicodeError, ValueError):
            raise TransportError('invalid_response') from None

    def respond(self, messages, tools, deadline, cancelled):
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline - self._clock() > MAX_LIFETIME_SECONDS or not callable(cancelled)):
            raise ValueError('Deadline and cancellation callback required')
        if not isinstance(messages, list) or tools != apple_pilot.TOOLS:
            raise ValueError('Pilot messages and fixed tools required')
        try:
            body = json.dumps({'model': 'system', 'messages': messages, 'tools': tools,
                               'stream': False}, ensure_ascii=False, allow_nan=False,
                              separators=(',', ':')).encode('utf-8')
        except (TypeError, ValueError, UnicodeError):
            raise TransportError('invalid_request') from None
        if len(body) > MAX_REQUEST:
            raise TransportError('request_too_large')
        request = (b'POST /v1/chat/completions HTTP/1.1\r\nHost: localhost\r\n'
                   b'Content-Type: application/json\r\nConnection: close\r\nContent-Length: '
                   + str(len(body)).encode('ascii') + b'\r\n\r\n' + body)
        if cancelled():
            raise TransportError('cancelled')
        if self._clock() >= deadline:
            raise TransportError('deadline_exceeded')
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix='reprise-fm-', dir='/private/tmp') as directory:
                info = os.lstat(directory)
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                    raise TransportError('unsafe_socket_directory')
                path = str(Path(directory) / 'fm.sock')
                try:
                    process = self._spawn([FM, 'serve', '--socket', path], directory)
                except OSError:
                    raise TransportError('process_unavailable') from None
                try:
                    sock = self._connect(path, deadline, cancelled, process)
                    try:
                        response = self._exchange(sock, request, deadline, cancelled, process)
                    finally:
                        sock.close()
                    self._check(deadline, cancelled, process)
                finally:
                    _stop(process)
                    process = None
            data = json.loads(response.decode('utf-8'), object_pairs_hook=_json_unique)
            if not isinstance(data, dict) or data.get('model') != 'system' or not isinstance(data.get('choices'), list) or len(data['choices']) != 1:
                raise ValueError('invalid_payload')
            choice = data['choices'][0]
            if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
                raise ValueError('invalid_choice')
            reason, message = choice.get('finish_reason'), choice['message']
            has_calls = bool(message.get('tool_calls'))
            if reason not in ('stop', 'tool_calls') or (reason == 'tool_calls') != has_calls:
                raise ValueError('invalid_finish')
            return message
        except TransportError:
            raise
        except (OSError, ValueError, TypeError, UnicodeError, struct.error):
            raise TransportError('invalid_response') from None
