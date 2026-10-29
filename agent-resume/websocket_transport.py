"""Bounded JSON WebSocket framing shared by the local RPC adapters.

Adapters own I/O, deadlines and the meaning of a closed connection.
"""
import base64
import hashlib
import json
import os
import struct
import time


class WebSocketTransport:
    LIMIT = 1024 * 1024

    def _handshake(self, host, path, timeout):
        key = base64.b64encode(os.urandom(16)).decode()
        self._write((f'GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n'
                     'Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
                     f'Sec-WebSocket-Key: {key}\r\n\r\n').encode())
        deadline = time.monotonic() + timeout
        while b'\r\n\r\n' not in self.buffer:
            self._read(deadline)
            if len(self.buffer) > 16384:
                raise ValueError('Oversized WebSocket handshake')
        header, remaining = self.buffer.split(b'\r\n\r\n', 1)
        self.buffer = bytearray(remaining)
        lines = header.decode('ascii').split('\r\n')
        headers = {name.lower(): value.strip() for name, value in
                   (line.split(':', 1) for line in lines[1:])}
        expected = base64.b64encode(hashlib.sha1(
            (key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
        status = lines[0].split()
        if (len(status) < 2 or status[1] != '101' or headers.get('sec-websocket-accept') != expected
                or headers.get('upgrade', '').lower() != 'websocket'
                or 'upgrade' not in headers.get('connection', '').lower().split(', ')):
            raise ValueError('Invalid WebSocket handshake')

    def _take(self, count, deadline):
        while len(self.buffer) < count:
            self._read(deadline)
        value = bytes(self.buffer[:count])
        del self.buffer[:count]
        return value

    def _frame(self, payload, opcode=1):
        if len(payload) > self.LIMIT:
            raise ValueError('Oversized WebSocket request')
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header = bytes([128 | opcode, 128 | size])
        elif size < 65536:
            header = bytes([128 | opcode, 254]) + struct.pack('!H', size)
        else:
            header = bytes([128 | opcode, 255]) + struct.pack('!Q', size)
        self._write(header + mask + bytes(value ^ mask[index % 4]
                                         for index, value in enumerate(payload)))

    def send(self, message):
        self._frame(json.dumps(message).encode())

    def receive(self, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        payload, fragmented = bytearray(), False
        while True:
            first, second = self._take(2, deadline)
            opcode, final = first & 15, bool(first & 128)
            size = second & 127
            if first & 112 or second & 128:
                raise ValueError('Unsupported WebSocket frame')
            if size == 126:
                size = struct.unpack('!H', self._take(2, deadline))[0]
            elif size == 127:
                size = struct.unpack('!Q', self._take(8, deadline))[0]
            if opcode >= 8 and (not final or size > 125):
                raise ValueError('Invalid WebSocket control frame')
            if size > self.LIMIT or len(payload) + size > self.LIMIT:
                raise ValueError('Oversized WebSocket response')
            part = self._take(size, deadline)
            if opcode == 8:
                self._peer_closed()
            if opcode == 9:
                self._frame(part, 10)
                continue
            if opcode == 10:
                continue
            if opcode not in (0, 1) or (opcode == 0) != fragmented:
                raise ValueError('Invalid WebSocket message')
            payload.extend(part)
            fragmented = not final
            if final:
                result = json.loads(payload)
                if not isinstance(result, dict):
                    raise ValueError('Invalid JSON-RPC response')
                return result

    def _peer_closed(self):
        raise ConnectionError('WebSocket closed by peer')
