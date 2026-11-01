#!/usr/bin/env python3
"""Child daemon applying Quick Apply dispatches over CDP, one page, one click.

Spawned lazily as a subprocess by claude_desktop_quick; owns the CDP side so
the Quick Apply daemon never drives Chrome in process. Protocol (newline
delimited JSON, one request per line, connection kept open):

  greeting on connect: {"hello": true}
  request: {"prompt": ..., "chatId": ...}
  reply:   {"ok": true, stage, ...} | {"ok": false, stage, error}

Invariants inherited from _cdp_apply: one prepare, one fingerprint check, one
click; never a retry after a lost response, never a response read; errors are
per request and never crash the daemon; payloads above 8 MiB destroy the
connection. Logs go to stderr only; the socket file is removed on SIGTERM.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import stat
import sys

import claude_desktop_apply as mod
from claude_desktop import validate_chat_id

DEFAULT_SOCKET = Path.home() / '.claude-desktop' / 'quick-apply-chrome.sock'
MAX_LINE = 8 * 1024 * 1024


def apply_prompt(prompt, chat_id):
    """Stage and single-click the prompt over CDP, using apply primitives.

    Intermediate steps (state/stage) stay local to the DOM; no response is
    read and no polling happens after the click.
    """
    chat_id = validate_chat_id(chat_id)
    prompt = prompt.strip()
    path = '/chat/' + chat_id
    fingerprint = hashlib.sha256(prompt.encode()).hexdigest()
    cdp = None
    try:
        cdp = mod.CDP(mod.DEFAULT_PORT, chat_id=chat_id)
        info = mod.state(cdp)
        if mod._page_path(info.get('url')) == '/login':
            return {'stage': 'session', 'detail': 'Connexion claude.ai requise'}
        if info.get('changed') or mod._page_path(info.get('url')) is None:
            return {'stage': 'cible', 'detail': 'Page claude.ai unique requise'}
        if info.get('composerPresent') is not True or info.get('text_length') is None:
            return {'stage': 'composeur', 'detail': 'Composeur indisponible'}
        if info['text_length'] != 0:
            return {'stage': 'brouillon', 'detail': 'Contenu préexistant dans le composeur'}
        if mod._page_path(info['url']) != path:
            cdp.navigate('https://claude.ai' + path)
        mod.wait_composer(cdp)
        info = mod.state(cdp)
        problem = mod._check(info, path)
        if problem:
            return {'stage': problem, 'detail': 'État du composeur invalide'}
        if info.get('text_length') != 0:
            return {'stage': 'brouillon', 'detail': 'Contenu préexistant dans le composeur'}
        if not mod._prepare(cdp, path):
            return {'stage': 'cible', 'detail': 'Préparation du composeur refusée'}
        cdp.call('Input.insertText', {'text': prompt})
        info = mod.state(cdp)
        problem = mod._check(info, path)
        if problem:
            return {'stage': problem, 'detail': 'État du composeur invalide après insertion'}
        if info.get('text_sha256') != fingerprint:
            return {'stage': 'empreinte', 'detail': 'Empreinte du composeur inattendue'}
        outcome = mod._dispatch(cdp, path, fingerprint)
        return {'stage': outcome.get('stage'), 'ok': outcome.get('ok') is True,
                'click_observed': outcome.get('click_observed') is True}
    except (OSError, ValueError, mod.WSError, TimeoutError) as error:
        # A lost response may conceal a click: never retry, never claim success.
        return {'stage': 'incertain', 'detail': str(error) or type(error).__name__}
    finally:
        mod._close(cdp)


def _serve_conn(conn):
    conn.sendall(b'{"hello": true}\n')
    stream = conn.makefile('rb')
    for raw in stream:
        if len(raw) > MAX_LINE:
            break  # oversized payload: destroy the connection
        try:
            message = json.loads(raw)
            if not isinstance(message, dict):
                raise ValueError('Objet requis')
            prompt = message.get('prompt')
            chat_id = message.get('chatId')
            if not isinstance(prompt, str) or not isinstance(chat_id, str):
                raise ValueError('prompt et chatId requis')
            outcome = apply_prompt(prompt, chat_id)
            ok = outcome.get('stage') == 'envoyé' and outcome.get('click_observed') is True
            reply = dict(outcome, ok=ok)
        except (ValueError, TypeError, KeyError, OSError, RuntimeError) as error:
            reply = {'ok': False, 'stage': 'incertain',
                     'error': str(error) or type(error).__name__}
        conn.sendall((json.dumps(reply, ensure_ascii=False) + '\n').encode())
    stream.close()


def bind_socket(path):
    """Bind the UNIX socket, removing an orphan socket file first."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise SystemExit('Le chemin de socket existe déjà sans être un socket.')
        try:
            path.unlink()
        except OSError as error:
            raise SystemExit(f'Impossible de supprimer le socket orphelin {path} : {error}')
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    os.umask(0o077)
    sock.bind(str(path))
    os.chmod(path, 0o600)
    sock.listen(8)
    return sock


def serve(path):
    sock = bind_socket(path)
    try:
        while True:
            conn, _ = sock.accept()
            try:
                _serve_conn(conn)
            except (OSError, TimeoutError):
                pass
            finally:
                conn.close()
    finally:
        sock.close()
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default=str(DEFAULT_SOCKET))
    args = parser.parse_args()

    def terminate(signum, frame):
        try:
            Path(args.socket).unlink(missing_ok=True)
        except OSError:
            pass
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    serve(args.socket)


if __name__ == '__main__':
    main()
