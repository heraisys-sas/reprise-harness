"""Minimal JSON-RPC client for the local Claude Desktop Quick Entry endpoint.

Joins an already-running supervision socket; never starts a Desktop process or
a daemon. Mirrors the bounded, non-fallback behaviour of codex_rpc.Client but
over a private UNIX socket speaking newline-delimited JSON-RPC.
"""
import json
import socket

from engines import Unavailable, socket_path


class Client:
    LIMIT = 1024 * 1024

    def __init__(self, program, values, timeout=15):
        # `program` is the supervision entry point; the transport is the socket.
        self.path = socket_path(values)
        self.ident = 0
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(str(self.path))
        except OSError as error:
            self.sock.close()
            raise Unavailable('engine_unavailable',
                              'Endpoint Quick Apply injoignable ; aucun serveur de remplacement lancé.') from error

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def send(self, message):
        data = json.dumps(message, ensure_ascii=False).encode()
        if len(data) > self.LIMIT:
            raise ValueError('Requête Quick Apply trop volumineuse')
        try:
            self.sock.sendall(data + b'\n')
        except OSError as error:
            raise Unavailable('needs_review',
                              'Connexion au endpoint interrompue ; résultat inconnu, aucune relance automatique.') from error

    def receive(self):
        buffer = bytearray()
        while b'\n' not in buffer:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise Unavailable('needs_review',
                                  'Connexion au endpoint interrompue ; résultat inconnu, aucune relance automatique.')
            buffer.extend(chunk)
            if len(buffer) > self.LIMIT:
                raise ValueError('Réponse Quick Apply trop volumineuse')
        line, _ = buffer.split(b'\n', 1)
        try:
            message = json.loads(line.decode())
        except (ValueError, UnicodeDecodeError):
            raise ValueError('Réponse JSON-RPC invalide')
        if not isinstance(message, dict):
            raise ValueError('Message JSON-RPC invalide')
        return message

    def call(self, method, params=None):
        self.ident += 1
        self.send({'jsonrpc': '2.0', 'id': self.ident, 'method': method, 'params': params or {}})
        message = self.receive()
        if 'error' in message:
            error = message['error'] or {}
            raise Unavailable('blocked_permission', error.get('message', 'Requête refusée par le endpoint.'))
        return message.get('result') or {}
