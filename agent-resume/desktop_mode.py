"""Explicit Quick Entry server lifecycle and linked-app launch; never force-quit.

Mirrors shared_mode.py's grammar for Codex. The Quick Entry server
(claude_desktop_quick) validates and routes the payload on its own, but
submitting into a conversation needs Claude Desktop running. This module
therefore exposes: launch (open the linked app if needed), start/stop (the
Quick Entry server) and status. It never force-quits a running app and never
starts anything implicitly; each action is explicit.
"""
from app_paths import state_directory
import errno
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


def socket_state():
    """A successful connection proves liveness; only absence/refusal proves stopped."""
    import socket
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect(str(DEFAULT_SOCKET))
        return 'running'
    except OSError as error:
        return 'stopped' if error.errno in (errno.ENOENT, errno.ECONNREFUSED) else 'unknown'
    finally:
        sock.close()


def server_running():
    return socket_state() == 'running'


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
        return {'ok': True, 'started': False, 'reason': 'Service claude-desktopd déjà actif.'}
    socket = socket or str(DEFAULT_SOCKET)
    state = state or str(state_directory(Path(__file__).parent, 'quick-state'))
    import tempfile
    import time
    err = Path(tempfile.gettempdir()) / 'reprise-quick-entry-error.log'
    try:
        with err.open('w') as sink:
            subprocess.Popen([sys.executable, str(SERVER), '--socket', socket, '--state', str(state)],
                             start_new_session=True, stdout=subprocess.DEVNULL, stderr=sink)
        time.sleep(0.5)
        if server_running():
            return {'ok': True, 'started': True, 'reason': 'Service claude-desktopd démarré.',
                    'needs_app': not app_running()}
        detail = ''
        if err.is_file():
            lines = err.read_text().strip().splitlines()
            detail = lines[-1] if lines else ''
        return {'ok': False, 'started': False,
                'reason': f'Service claude-desktopd non démarré : {detail or "cause inconnue"}'}
    except OSError as error:
        return {'ok': False, 'started': False, 'reason': f'Démarrage impossible : {error}'}


def stop():
    return daemon_stop()


# --- Quick Apply daemon (mirrors the Codex app-server daemon) --------------
DAEMON_NAME = 'reprise-quick-apply'
DEFAULT_STATE = state_directory(Path(__file__).parent, 'quick-state')


def _pid_file(state):
    return Path(state) / 'quick-apply.pid'


def _log_file(state):
    return Path(state) / 'quick-apply.log'


def daemon_pid(state=None):
    """A PID file cannot authenticate ownership; no verified PID is available."""
    return None


def daemon_start(socket=None, state=None, daily_limit=8):
    """Start the Quick Apply daemon (detached), independent of Desktop being ready.

    The server binds its socket without needing Claude Desktop, so it may start
    first; submissions are delivered whenever Desktop is available.
    """
    state = state or str(DEFAULT_STATE)
    if server_running():
        return {'ok': True, 'started': False, 'reason': 'Daemon Quick Apply déjà actif.'}
    socket = socket or str(DEFAULT_SOCKET)
    Path(state).mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        log = open(_log_file(state), 'ab')
        proc = subprocess.Popen([sys.executable, str(SERVER), '--socket', socket,
                                 '--state', str(state)],
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        _pid_file(state).write_text(str(proc.pid))
        import time
        time.sleep(1.0)  # let a fast failure (e.g. orphan socket) surface
        if server_running():
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
    """Never signal an unauthenticated PID or claim an unconfirmed stop."""
    observed = socket_state()
    if observed == 'stopped':
        return {'ok': True, 'stopped': False, 'status': 'stopped',
                'reason': 'Socket claude-desktopd absent ou sans écoute ; aucun arrêt effectué.'}
    return {'ok': False, 'stopped': False,
            'status': 'unsupported' if observed == 'running' else 'indeterminate',
            'reason': 'Arrêt non confirmé : aucun contrôle d’arrêt authentifié disponible. '
                      'Arrêter claude-desktopd depuis le terminal ou le gestionnaire qui l’a lancé, '
                      'puis vérifier que la connexion au socket est refusée. '
                      'L’application liée reste ouverte.'}


def daemon_status(state=None):
    observed = socket_state()
    return {'daemon': DAEMON_NAME, 'running': observed == 'running', 'pid': None,
            'state': observed, 'app_running': app_running(), 'app_version': app_version(),
            'socket': str(DEFAULT_SOCKET), 'listening': observed == 'running'}
