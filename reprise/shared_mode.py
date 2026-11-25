"""Retired Desktop→Codex socket binding; preserve Desktop's native stdio route.

The old shared-mode check accepted thread/read as proof that ChatGPT Desktop
used codexd. That only proves a thread is readable from the selected server,
not that Desktop routes its Codex requests there. In the observed build,
Desktop retains its private stdio app-server instead of the requested socket.

Keep entrypoints for old CLI/GUI callers, but refuse all actions without
launching, attaching to, stopping, or reconfiguring either daemon. The normal
Codex engine and explicitly selected Codex RPC adapters are unaffected.
"""
from pathlib import Path
import plistlib
import subprocess

import scheduler

DESKTOP = Path('/Applications/ChatGPT.app')
REASON = (
    'Mode partagé Desktop/codexd indisponible : le rattachement socket de '
    'ChatGPT Desktop ne peut pas être prouvé durablement. Desktop utilise '
    'son app-server Codex stdio natif ; aucune relance, connexion ou '
    'réactivation automatique effectuée. Les délégations Codex explicites '
    'restent indépendantes.'
)


def desktop_version():
    try:
        return str(plistlib.loads((DESKTOP / 'Contents/Info.plist').read_bytes())['CFBundleVersion'])
    except (OSError, ValueError, KeyError):
        return None


def desktop_running():
    """Compatibility-only process observation; never called during refusal."""
    return subprocess.run(['/usr/bin/pgrep', '-x', 'ChatGPT'], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=2).returncode != 1


def state(db):
    """Historical metadata is inert, never evidence of current attachment."""
    saved = scheduler.get(db, 'shared_mode', {})
    return saved | {
        'status': 'unsupported',
        'shared_mode_supported': False,
        'desktop_socket_binding_verified': False,
        'startup_eligible': False,
        'prepared': False,
        'launched': False,
        'verified_version': None,
        'verified_session': None,
        'restore_auto_after_check': False,
        'legacy_verification_present': bool(saved.get('verified_version') and saved.get('verified_session')),
        'desktop_version': desktop_version(),
        'reason': REASON,
    }


def _refused(operation):
    return {'ok': False, 'status': 'unsupported', 'operation': operation,
            'shared_mode_supported': False, 'desktop_socket_binding_verified': False,
            'auto_restored': False, 'reason': REASON}


def prepare(db, program, folder, startup=False):
    """Never create or replace codexd for an unverified Desktop socket route."""
    return _refused('prepare')


def launch(db, program, folder, startup=False):
    """Do not push socket environment overrides into the native stdio app."""
    return _refused('launch')


def check(db, program, session):
    """A readable thread does not prove the Desktop client's RPC ownership."""
    return _refused('check')


def stop(db, program, folder, startup=False):
    """Never stop codexd as part of retiring the Desktop shared-mode feature."""
    return _refused('stop')
