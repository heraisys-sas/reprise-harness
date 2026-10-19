"""Explicit Quick Entry server lifecycle and linked-app launch; never force-quit.

Mirrors shared_mode.py's grammar for Codex. The Quick Entry server
(claude_desktop_quick) validates and routes the payload on its own, but
submitting into a conversation needs Claude Desktop running. This module
therefore exposes: launch (open the linked app if needed), start/stop (the
Quick Entry server) and status. It never force-quits a running app and never
starts anything implicitly; each action is explicit.
"""
import os
import plistlib
from pathlib import Path
import subprocess
import sys

import engines
from claude_desktop import DEFAULT_SOCKET

APP = Path('/Applications/Claude.app')
SERVER = Path(__file__).parent / 'claude_desktop_quick.py'


def app_version():
    try:
        return str(plistlib.loads((APP / 'Contents/Info.plist').read_bytes())['CFBundleShortVersionString'])
    except (OSError, KeyError, ValueError):
        return None


def app_running():
    try:
        return subprocess.run(['/usr/bin/pgrep', '-x', 'Claude'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=2).returncode != 1
    except (OSError, subprocess.TimeoutExpired):
        return False


def server_running():
    """True only when the Quick Entry server is actually listening.

    An existing socket file is not enough: a stale/orphan socket with no
    listener must not be reported as running. Probe a real connection; a
    missing socket or refused connection means stopped.
    """
    import engines
    import socket as socket_mod
    if not DEFAULT_SOCKET.exists():
        return False
    try:
        engines.socket_path({'socket': str(DEFAULT_SOCKET)})
    except engines.Unavailable:
        return False
    sock = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect(str(DEFAULT_SOCKET))
        return True
    except OSError:
        return False  # socket file exists but nothing is listening (orphan)
    finally:
        sock.close()


def status():
    return {'app': APP.name, 'app_version': app_version(), 'app_running': app_running(),
            'server_running': server_running(), 'socket': str(DEFAULT_SOCKET)}


def launch():
    """Open the linked app if not already running. Never force-quits."""
    if app_running():
        return {'ok': True, 'launched': False, 'reason': 'Application liée déjà en cours.'}
    try:
        subprocess.run(['/usr/bin/open', '-a', str(APP)], check=True, timeout=10)
        return {'ok': True, 'launched': True, 'reason': 'Application liée ouverte.'}
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        return {'ok': False, 'launched': False, 'reason': f'Ouverture impossible : {error}'}


def start(socket=None, state=None, daily_limit=8):
    """Start the Quick Entry server (opt-in). The linked app is not required
    for the server itself but is needed for a real submission."""
    if server_running():
        return {'ok': True, 'started': False, 'reason': 'Serveur Quick Entry déjà actif.'}
    socket = socket or str(DEFAULT_SOCKET)
    state = state or str(Path(__file__).parent / 'quick-state')
    import tempfile
    import time
    err = Path(tempfile.gettempdir()) / 'reprise-quick-entry-error.log'
    try:
        with err.open('w') as sink:
            subprocess.Popen([sys.executable, str(SERVER), '--socket', socket, '--state', str(state),
                              '--daily-limit', str(daily_limit)],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=sink)
        time.sleep(0.5)
        if server_running():
            return {'ok': True, 'started': True, 'reason': 'Serveur Quick Entry démarré.',
                    'needs_app': not app_running()}
        detail = ''
        if err.is_file():
            lines = err.read_text().strip().splitlines()
            detail = lines[-1] if lines else ''
        return {'ok': False, 'started': False,
                'reason': f'Serveur non démarré : {detail or "cause inconnue"}'}
    except OSError as error:
        return {'ok': False, 'started': False, 'reason': f'Démarrage impossible : {error}'}


def stop():
    """Stop the Quick Entry server by closing its socket listener.

    A graceful stop is the operator's job via launchctl; here we only report.
    Never kills the linked app.
    """
    return {'ok': True, 'stopped': False,
            'reason': 'Arrêt du serveur via launchctl (unload) ; l’application liée n’est pas fermée.'}


# --- Quick Apply daemon (mirrors the Codex app-server daemon) --------------
DAEMON_NAME = 'reprise-quick-apply'
DEFAULT_STATE = Path(__file__).parent / 'quick-state'


def _pid_file(state):
    return Path(state) / 'quick-apply.pid'


def _log_file(state):
    return Path(state) / 'quick-apply.log'


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def daemon_pid(state=None):
    state = state or DEFAULT_STATE
    try:
        pid = int(_pid_file(state).read_text().strip())
        return pid if _pid_alive(pid) else None
    except (OSError, ValueError):
        return None


def daemon_start(socket=None, state=None, daily_limit=8):
    """Start the Quick Apply daemon (detached), independent of Desktop being ready.

    The server binds its socket without needing Claude Desktop, so it may start
    first; submissions are delivered whenever Desktop is available.
    """
    state = state or str(DEFAULT_STATE)
    if daemon_pid(state) or server_running():
        return {'ok': True, 'started': False, 'reason': 'Daemon Quick Apply déjà actif.'}
    socket = socket or str(DEFAULT_SOCKET)
    Path(state).mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        log = open(_log_file(state), 'ab')
        proc = subprocess.Popen([sys.executable, str(SERVER), '--socket', socket,
                                 '--state', str(state), '--daily-limit', str(daily_limit)],
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        _pid_file(state).write_text(str(proc.pid))
        import time
        time.sleep(1.0)  # let a fast failure (e.g. orphan socket) surface
        if server_running() or (proc.poll() is None and daemon_pid(state)):
            return {'ok': True, 'started': True, 'daemon': DAEMON_NAME, 'pid': proc.pid,
                    'needs_app': not app_running(),
                    'reason': 'Daemon Quick Apply démarré. La soumission sera livrée dès que Claude Desktop sera prêt.'}
        detail = ''
        log = _log_file(state)
        if log.is_file():
            lines = log.read_text(errors='replace').strip().splitlines()
            detail = lines[-1] if lines else ''
        return {'ok': False, 'started': False, 'reason': f'Daemon Quick Apply non démarré : {detail or "voir le journal"}'}
    except OSError as error:
        return {'ok': False, 'started': False, 'reason': f'Démarrage impossible : {error}'}


def daemon_stop(state=None):
    """Stop the Quick Apply daemon by PID; never touches the linked app."""
    pid = daemon_pid(state)
    if pid is None:
        return {'ok': True, 'stopped': False, 'reason': 'Aucun daemon Quick Apply en cours.'}
    try:
        os.kill(pid, 15)  # SIGTERM
        import time
        time.sleep(0.5)
    except OSError:
        pass
    try:
        _pid_file(state or DEFAULT_STATE).unlink(missing_ok=True)
    except OSError:
        pass
    return {'ok': True, 'stopped': True, 'pid': pid,
            'reason': 'Daemon Quick Apply arrêté. L’application liée n’a pas été fermée.'}


def daemon_status(state=None):
    pid = daemon_pid(state)
    return {'daemon': DAEMON_NAME, 'running': pid is not None, 'pid': pid,
            'app_running': app_running(), 'app_version': app_version(),
            'socket': str(DEFAULT_SOCKET), 'listening': server_running()}
