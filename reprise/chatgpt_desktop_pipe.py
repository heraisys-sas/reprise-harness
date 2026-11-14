#!/usr/bin/env python3
"""Read-only CDP pipe feasibility probe for an isolated ChatGPT Desktop instance.

The official ChatGPT.app binary is launched as an owned child with its own
persistent profile. No Codex CLI, operator app, TCP DevTools port, chat send,
session scraping, or implicit provider fallback. This is a capability probe,
NOT an autonomous ChatGPT delegation backend.
"""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys

from claude_cdp_pipe import launch_cdp_process, terminate_chrome

DEFAULT_BINARY = Path('/Applications/ChatGPT.app/Contents/MacOS/ChatGPT')
DEFAULT_PROFILE_ROOT = Path.home() / 'Library/Application Support/Reprise/chatgpt-cdp-profiles'
PROFILE_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
MAIN_URL = 'app://-/index.html'
# Fixed, read-only probe. Never capture conversation text or authorization tokens.
RENDERER_PROBE = """(() => ({
    href: location.href,
    ready: document.readyState,
    composer_count: [...document.querySelectorAll('textarea, [contenteditable="true"], [role="textbox"]')]
      .filter(e => { const r=e.getBoundingClientRect();
                    return r.width >= 80 && r.height >= 12 && !e.disabled && !e.readOnly; }).length
}))()"""


def profile_path(root, name):
    """Resolve a named isolated profile without following existing symlinks."""
    if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
        raise ValueError('Nom de profil CDP invalide.')
    root = Path(root).expanduser().absolute()
    if root.is_symlink() or (root / name).is_symlink():
        raise ValueError('Profil ou racine symbolique refusé.')
    return root / name


def _private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Dossier symbolique refusé.')
    if not path.exists():
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or (stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError('Dossier de profil non privé ou non détenu par cet utilisateur.')


def _claim_lock(root, name):
    """Never adopt another process or auto-remove a potentially stale lock."""
    lock = root / ('.' + name + '.cdp-pipe.lock')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(lock, flags, 0o600)
    return lock, fd


def _release_lock(lock, fd):
    try:
        owned_inode = os.fstat(fd).st_ino
    except OSError:
        owned_inode = None
    try:
        os.close(fd)
    except OSError:
        pass
    if owned_inode is not None:
        try:
            if lock.lstat().st_ino == owned_inode and not lock.is_symlink():
                lock.unlink()
        except FileNotFoundError:
            pass


def build_args(profile):
    return ['--remote-debugging-pipe', f'--user-data-dir={profile}',
            '--no-first-run']


def select_main(targets):
    """Require exactly one main renderer; do not fall back to arbitrary pages."""
    if not isinstance(targets, (list, tuple)):
        raise ValueError('Inventaire CDP invalide.')
    matches = [item for item in targets if isinstance(item, dict) and
               item.get('type') == 'page' and item.get('url') == MAIN_URL]
    if len(matches) != 1:
        return None, 'target_missing' if not matches else 'target_ambiguous'
    ident = matches[0].get('targetId')
    if not isinstance(ident, str) or not ident:
        return None, 'target_unidentified'
    return ident, 'ready'


def census(transport):
    """Attach to the main app renderer and inspect DOM *without* reading chat."""
    info = transport.call('Target.getTargets', {}).get('targetInfos')
    ident, stage = select_main(info)
    if stage != 'ready':
        return {'status': stage, 'transport': 'cdp-pipe', 'ready': False}
    transport.attach_to(ident)
    result = transport.evaluate(RENDERER_PROBE)
    if not isinstance(result, dict) or result.get('href') != MAIN_URL:
        return {'status': 'target_changed', 'transport': 'cdp-pipe', 'ready': False}
    count = result.get('composer_count')
    ready = type(count) is int and count == 1
    return {'status': 'ready' if ready else 'selectors_stale',
            'transport': 'cdp-pipe', 'ready': ready,
            'composer_count': count if type(count) is int else None,
            'document_ready': result.get('ready')}


def inspect(binary=DEFAULT_BINARY, profile_root=DEFAULT_PROFILE_ROOT, name='default'):
    profile = profile_path(profile_root, name)
    binary = Path(binary).expanduser().absolute()
    lock = profile.parent / ('.' + name + '.cdp-pipe.lock')
    return {'status': 'inspect_only', 'transport': 'cdp-pipe',
            'binary_present': binary.is_file() and os.access(binary, os.X_OK),
            'profile_present': profile.is_dir() and not profile.is_symlink(),
            'profile_lock_present': lock.exists() or lock.is_symlink(),
            'launch_attempted': False, 'operator_app_touched': False}


def probe(binary=DEFAULT_BINARY, profile_root=DEFAULT_PROFILE_ROOT, name='default'):
    """Launch and stop *our own child only*, no app singleton takeover.

    A second instance may refuse to spawn because of app-specific singleton
    behavior. This is reported as pipe_unavailable, never a request to close the
    operator's ChatGPT instance.
    """
    profile = profile_path(profile_root, name)
    binary = Path(binary).expanduser().absolute()
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        return {'status': 'binary_unavailable', 'ready': False, 'launch_attempted': False}
    _private_directory(profile.parent)
    _private_directory(profile)
    lock, fd = _claim_lock(profile.parent, name)
    transport = proc = None
    try:
        transport, proc = launch_cdp_process(binary, build_args(profile), label='ChatGPT Desktop')
        os.write(fd, str(proc.pid).encode('ascii'))
        os.fsync(fd)
        try:
            return census(transport) | {'launch_attempted': True,
                                        'operator_app_touched': False}
        except (TimeoutError, EOFError, RuntimeError, OSError, ValueError) as exc:
            return {'status': 'pipe_unavailable', 'ready': False,
                    'launch_attempted': True, 'operator_app_touched': False,
                    'reason': type(exc).__name__}
    finally:
        if proc is not None:
            # Never enumerate, quit, signal or adopt the operator's app.
            terminate_chrome(proc, timeout=3)
        if transport is not None:
            for handle in (transport._reader, transport._writer):
                try:
                    handle.close()
                except OSError:
                    pass
        _release_lock(lock, fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for cmd in ('inspect', 'probe'):
        obj = sub.add_parser(cmd)
        obj.add_argument('--profile', default='default')
        obj.add_argument('--profile-root', type=Path, default=DEFAULT_PROFILE_ROOT)
        obj.add_argument('--binary', type=Path, default=DEFAULT_BINARY)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        values = dict(binary=args.binary, profile_root=args.profile_root, name=args.profile)
        outcome = inspect(**values) if args.action == 'inspect' else probe(**values)
    except (ValueError, OSError) as exc:
        outcome = {'status': 'refused', 'ready': False, 'reason': type(exc).__name__}
    print(json.dumps(outcome, sort_keys=True, ensure_ascii=False))
    return 0 if args.action == 'inspect' or outcome.get('status') == 'ready' else 2


if __name__ == '__main__':
    sys.exit(main())
