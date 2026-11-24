#!/usr/bin/env python3
"""Envoi d'un message dans le composer de la copie Meta AI Reprise via le pont socket.

Usage : envoyer.py <fichier-message-utf8>
Fait : insert (texte du fichier) → vérif len → send (insertNewline) → vérif len.
"""
import json
import pathlib
import socket
import sys

SOCK = pathlib.Path.home() / ".reprise/metaai-reprise-trigger.sock"


def demande(payload: dict) -> str:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(SOCK))
    sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf += chunk
    sock.close()
    return buf.decode(errors="replace").strip()


texte = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
print("insert :", demande({"cmd": "insert", "text": texte}))
print("len    :", demande({"cmd": "len"}))
print("send   :", demande({"cmd": "send"}))
print("len    :", demande({"cmd": "len"}))
