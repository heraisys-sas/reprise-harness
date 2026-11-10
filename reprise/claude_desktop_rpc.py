"""Minimal JSON-RPC client for the local Claude Desktop Quick Entry endpoint.

Joins an already-running supervision socket; never starts a Desktop process or
a daemon. Mirrors the bounded, non-fallback behaviour of codex_rpc.Client but
over a private UNIX socket speaking newline-delimited JSON-RPC.
"""
import json
import socket
from pathlib import Path

from engines import Unavailable, socket_path

# Socket Quick Apply tenu par l'app ReClaude patchée (fusion 10/11/2026 — un seul
# socket, ancien nom quick-entry-reprise.sock supprimé) : greeting {"hello":true},
# puis une requête JSON par ligne, une réponse par ligne.
REPRISE_DISPATCH_SOCKET = Path.home() / '.claude-desktop/quick-entry.sock'
DISPATCH_LIMIT = 8 * 1024 * 1024


def _readline(sock, limit=DISPATCH_LIMIT):
    buffer = bytearray()
    while b'\n' not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            raise Unavailable('engine_unavailable',
                              'Connexion dispatch fermée avant réponse ; aucune relance automatique.')
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise ValueError('Réponse dispatch trop volumineuse')
    line, _ = buffer.split(b'\n', 1)
    return line.decode()


def probe_dispatch_socket(path, timeout=2.0):
    """True only when connect() succeeds AND the greeting is answered.

    An orphan socket file (present but refusing connections) is `stopped`,
    never `running`: verified by connect(), never by file existence.
    """
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        greeting = json.loads(_readline(sock, limit=1024))
        return isinstance(greeting, dict) and greeting.get('hello') is True
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def dispatch_turn(path, prompt, *, images=None, file_paths=None, chat_id=None, timeout=10.0):
    """One-shot native TYr dispatch; the connection is KEPT OPEN until the reply.

    Returns the parsed reply dict ({ok:true} or {ok:false,error:...}). Transport
    failures raise (Unavailable/ValueError/OSError) — never retried, never
    fall-backed.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('Prompt requis pour le dispatch')
    payload = {'prompt': prompt, 'images': [str(i) for i in (images or [])],
               'filePaths': [str(p) for p in (file_paths or [])]}
    if chat_id:
        payload['chatId'] = str(chat_id)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(path))
        greeting = json.loads(_readline(sock, limit=1024))
        if not (isinstance(greeting, dict) and greeting.get('hello') is True):
            raise ValueError('Greeting dispatch inattendu')
        line = json.dumps(payload, ensure_ascii=False).encode()
        if len(line) > DISPATCH_LIMIT:
            raise ValueError('Payload dispatch trop volumineux')
        sock.sendall(line + b'\n')
        reply = json.loads(_readline(sock))
        if not isinstance(reply, dict):
            raise ValueError('Réponse dispatch invalide')
        return reply
    finally:
        try:
            sock.close()
        except OSError:
            pass


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
        # Le socket de dispatch salue d'abord ({"hello":true}) — miroir de dispatch_turn.
        greeting = self.receive()
        if not (isinstance(greeting, dict) and greeting.get('hello') is True):
            self.sock.close()
            raise Unavailable('engine_unavailable',
                              'Greeting dispatch inattendu sur le endpoint Quick Entry.')

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
