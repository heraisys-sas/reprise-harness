"""Common STDIO newline-JSON transport shared by the MCP facades (issue #156).

One loop, one connection abstraction, one protocol version: the codex expert
bridge, the Claude facade and the ChatGPT facade all run the same framing
(readline with a per-facade byte cap, JSON parse, notification routing,
canonical request-id keys, thread-safe output with close-on-failure) while
keeping their request semantics in their own handlers.
"""
import json
import threading

PROTOCOL_VERSION = '2024-11-05'
# Codex framing cap (per message). The Claude facade overrides it: a 2M-character
# submit_result answer JSON-escaped exceeds 1 MiB, hence its 8 MB cap.
CODEX_MAX_LINE_BYTES = 1024 * 1024
CLAUDE_MAX_LINE_BYTES = 8_000_000


def request_id_key(ident):
    """Canonical key for the JSON-RPC IDs accepted by these stdio servers."""
    if ident is None or isinstance(ident, bool) or not isinstance(ident, (str, int, float)):
        raise ValueError('JSON-RPC id must be a string or number')
    return json.dumps(ident, ensure_ascii=False, separators=(',', ':'))


def read_message(source, max_line_bytes):
    """Read one newline-JSON message; None on EOF or oversized line."""
    raw = source.readline(max_line_bytes + 1)
    if not raw or len(raw) > max_line_bytes:
        return None
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise ValueError('Object required')
    return message


class Connection:
    """Thread-safe newline-JSON output; closed after the first write failure."""

    def __init__(self, write, on_failure=None):
        self._write = write
        self._on_failure = on_failure
        self._lock = threading.Lock()
        self.closed = threading.Event()

    def send(self, payload):
        with self._lock:
            if self.closed.is_set():
                return
            try:
                self._write(json.dumps(payload, ensure_ascii=False) + '\n')
            except OSError:
                self.closed.set()
                if self._on_failure is not None:
                    self._on_failure()
                raise


class StdioHandler:
    """Per-facade semantics over the shared framing; subclass and override."""

    ERRORS = (ValueError, TypeError, KeyError, OSError)

    def notification(self, message, connection):
        pass

    def request(self, message, connection):
        """Handle one request message; must send exactly one response."""
        raise NotImplementedError

    def on_error(self, message, ident, exc, connection):
        """Send the JSON-RPC error answer for a failed message."""
        raise NotImplementedError

    def shutdown(self, connection):
        pass


def run_stdio_loop(source, connection, handler, max_line_bytes=CODEX_MAX_LINE_BYTES):
    """Shared framing: read -> notification/request -> bounded error handling."""
    try:
        while True:
            # A failed write means the client is gone (broken stdout): keep the
            # historical observable behavior — exit instead of lingering as a
            # zombie server nobody can read from.
            if connection.closed.is_set():
                break
            try:
                message = read_message(source, max_line_bytes)
            except handler.ERRORS as exc:
                handler.on_error(None, None, exc, connection)
                continue
            if message is None:
                break
            ident = None
            try:
                if 'id' not in message:
                    handler.notification(message, connection)
                    continue
                ident = message['id']
                handler.request(message, connection)
            except handler.ERRORS as exc:
                handler.on_error(message, ident, exc, connection)
    finally:
        connection.closed.set()
        handler.shutdown(connection)
