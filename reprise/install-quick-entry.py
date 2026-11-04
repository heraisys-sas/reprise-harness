#!/usr/bin/env python3
"""Write a LaunchAgent plist for the local Claude Desktop Quick Entry server.

The plist only ever points at this checkout (claude_desktop_quick.py) and at
paths under the current user's home. Writing the plist never starts the
server: `launchctl load` is proposed, never executed silently, and an existing
plist is refused unless --force. This keeps every relaunch explicitly
programmed by the operator.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import sys

LABEL = 'com.example.reprise.quick-entry'
PLIST_NAME = LABEL + '.plist'
SOURCE = Path(__file__).resolve().parent
SERVER = SOURCE / 'claude_desktop_quick.py'
DEFAULT_SOCKET = Path.home() / '.claude-desktop' / 'quick-entry.sock'
DEFAULT_STATE = SOURCE / 'quick-state'


def plist_settings(*, server, socket_path, state, daily_limit, home):
    """Build the launchd settings for the Quick Entry server, fully explicit."""
    home = Path(home)
    if Path(server).resolve() != SERVER.resolve():
        raise ValueError('Le serveur doit rester dans ce dépôt')
    for path in (Path(socket_path), Path(state)):
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_relative_to(home.resolve()):
            raise ValueError('Les chemins du LaunchAgent doivent rester sous le domicile courant')
    return {
        'Label': LABEL,
        'ProgramArguments': [sys.executable, str(SERVER), '--socket', str(Path(socket_path).expanduser()),
                             '--state', str(Path(state).expanduser())],
        # No RunAtLoad / KeepAlive: the server runs only once explicitly loaded.
        'ProcessType': 'Interactive',
        'StandardOutPath': str(Path(state).expanduser() / 'launchd-out.log'),
        'StandardErrorPath': str(Path(state).expanduser() / 'launchd-error.log'),
    }


def install(*, home=None, socket_path=None, state=None, daily_limit=8, force=False):
    """Write the plist; refuse to overwrite an existing one without force."""
    home = Path.home() if home is None else Path(home)
    socket_path = DEFAULT_SOCKET if socket_path is None else Path(socket_path)
    state = DEFAULT_STATE if state is None else Path(state)
    target = home / 'Library' / 'LaunchAgents' / PLIST_NAME
    if target.exists() and not force:
        raise FileExistsError(f'{target} existe déjà ; utiliser --force pour le remplacer')
    settings = plist_settings(server=SERVER, socket_path=socket_path, state=state,
                              daily_limit=daily_limit, home=home)
    payload = plistlib.dumps(settings, sort_keys=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.reprise-tmp')
    temporary.write_bytes(payload)
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return {'changed': True, 'plist': str(target), 'label': LABEL,
            'socket': str(Path(socket_path).expanduser()), 'state': str(Path(state).expanduser()),
            'next_step': f'launchctl load {target}  # explicite, jamais exécuté par ce script'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', default=str(DEFAULT_SOCKET))
    parser.add_argument('--state', default=str(DEFAULT_STATE))
    parser.add_argument('--daily-limit', type=int, default=8, help='Deprecated compatibility option; ignored')
    parser.add_argument('--force', action='store_true',
                        help='remplacer un plist existant')
    parser.add_argument('--home', default=str(Path.home()),
                        help='racine des Library/LaunchAgents (tests uniquement)')
    args = parser.parse_args()
    try:
        result = install(home=args.home, socket_path=args.socket, state=args.state,
                         daily_limit=args.daily_limit, force=args.force)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
