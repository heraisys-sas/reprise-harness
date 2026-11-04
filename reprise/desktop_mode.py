"""Explicit Quick Entry server lifecycle and linked-app launch; never force-quit.

Mirrors shared_mode.py's grammar for Codex. The Quick Entry server
(claude_desktop_quick) validates and routes the payload on its own, but
submitting into a conversation needs Claude Desktop running. This module
therefore exposes: launch (open the linked app if needed), start/stop (the
Quick Entry server) and status. It never force-quits a running app and never
starts anything implicitly; each action is explicit.
"""
from app_paths import state_directory, state_location
import errno
import os
import plistlib
from pathlib import Path
import secrets
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
    """Use the managed daemon launcher for the app's Connections start action."""
    try:
        return daemon_start(socket=socket, state=state, daily_limit=daily_limit)
    except OSError as error:
        return {'ok': False, 'started': False, 'reason': f'Démarrage impossible : {error}'}


def stop():
    return daemon_stop()


# --- Quick Apply daemon (mirrors the Codex app-server daemon) --------------
DAEMON_NAME = 'reprise-quick-apply'
DEFAULT_STATE = state_location(Path(__file__).parent, 'quick-state')


def _pid_file(state):
    return Path(state) / 'quick-apply.pid'


def _log_file(state):
    return Path(state) / 'quick-apply.log'


def daemon_pid(state=None):
    """A PID file cannot authenticate ownership; no verified PID is available."""
    return None


def daemon_start(socket=None, state=None, daily_limit=8, project_root=None):
    """Start the Quick Apply daemon (detached), independent of Desktop being ready.

    The server binds its socket without needing Claude Desktop, so it may start
    first; submissions are delivered whenever Desktop is available.
    """
    state = state or str(DEFAULT_STATE)
    if server_running():
        return {'ok': True, 'started': False, 'reason': 'Daemon Quick Apply déjà actif.'}
    socket = socket or str(DEFAULT_SOCKET)
    Path(state).mkdir(parents=True, exist_ok=True, mode=0o700)
    if project_root is None:
        try:
            import claude_integration
            installed = claude_integration.inspect(Path(__file__).parent)
            if installed.get('managed'):
                project_root = installed.get('project')
        except (OSError, ValueError, KeyError):
            pass
    try:
        log = open(_log_file(state), 'ab')
        proc = subprocess.Popen([sys.executable, '-E', '-s', '-B', str(SERVER), '--socket', socket,
                                 '--state', str(state)] + (['--project-root', str(project_root)] if project_root else []),
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        _pid_file(state).write_text(str(proc.pid))
        # Write shutdown token (plain hex, verified by SHA-256 in _shutdown)
        shutdown_token = secrets.token_hex(32)
        token_path = Path(state) / 'quick-apply-shutdown.token'
        # Write token with 0600 permissions
        tmp = token_path.with_suffix('.tmp')
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, shutdown_token.encode())
        finally:
            os.close(fd)
        tmp.rename(token_path)
        import time
        time.sleep(1.0)  # let a fast failure (e.g. orphan socket) surface
        if server_running():
            return {'ok': True, 'started': True, 'daemon': DAEMON_NAME, 'pid': proc.pid,
                    'needs_app': not app_running(),
                    'reason': 'Daemon Quick Apply démarré. Admission disponible ; émission native non raccordée.'}
        detail = ''
        log = _log_file(state)
        if log.is_file():
            lines = log.read_text(errors='replace').strip().splitlines()
            detail = lines[-1] if lines else ''
        # Clean up token on failure
        try:
            token_path.unlink(missing_ok=True)
        except OSError:
            pass
        return {'ok': False, 'started': False, 'reason': f'Daemon Quick Apply non démarré : {detail or "voir le journal"}'}
    except OSError as error:
        return {'ok': False, 'started': False, 'reason': f'Démarrage impossible : {error}'}


def daemon_stop(state=None):
    """Authenticated shutdown: verify token via Client, then verify socket closes.

    Without a valid token and active socket → status 'unsupported'.
    Token rejected → 'unauthenticated'.
    After ok: verify socket refuses connect in bounded loop (~10 s) before stopped=True.
    Token written with 0600 permissions; cleaned up on failure.
    """
    import errno
    import time
    state = state or str(DEFAULT_STATE)
    # Read stored token
    token_path = Path(state) / 'quick-apply-shutdown.token'
    try:
        fd = os.open(str(token_path), os.O_RDONLY, 0o600)
        try:
            stored_token = os.read(fd, 4096).decode().strip()
        finally:
            os.close(fd)
    except OSError:
        # Try normal read as fallback
        try:
            stored_token = token_path.read_text().strip()
        except OSError:
            # No token file — check socket
            observed = socket_state()
            if observed == 'stopped':
                return {'ok': True, 'stopped': False, 'status': 'stopped',
                        'reason': 'Socket absent ou sans écoute ; aucun arrêt effectué.'}
            return {'ok': False, 'stopped': False, 'status': 'unsupported',
                    'reason': 'Aucun jeton d\'arrêt trouvé et socket encore actif.'}

    # Send shutdown command via Client (JSON-RPC over UNIX socket)
    from claude_desktop_rpc import Client
    client = Client(str(DEFAULT_SOCKET))
    try:
        resp = client.call('shutdown', {'token': stored_token})
        if not isinstance(resp, dict):
            return {'ok': False, 'stopped': False, 'status': 'unsupported',
                    'reason': 'Réponse du serveur invalide.'}
        if not resp.get('ok'):
            status_code = resp.get('status', 'unsupported')
            return {'ok': False, 'stopped': False, 'status': status_code,
                    'reason': resp.get('reason', 'Arrêt refusé par le serveur')}
        # Token accepted — now verify the socket actually stops accepting connections
        deadline = time.monotonic() + 10
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            while time.monotonic() < deadline:
                try:
                    sock.connect(str(DEFAULT_SOCKET))
                    time.sleep(0.2)
                except (ConnectionRefusedError, FileNotFoundError):
                    return {'ok': True, 'stopped': True, 'status': 'stopped',
                            'reason': 'Daemon Quick Apply arrêté par l\'opérateur.'}
        finally:
            sock.close()
        # Timeout: socket still accepting
        return {'ok': True, 'stopped': False, 'status': 'indeterminate',
                'reason': 'Le socket reste actif après l\'arrêt du daemon.'}
    except Exception as exc:
        return {'ok': False, 'stopped': False, 'status': 'error',
                'reason': f'Erreur lors de l\'arrêt : {exc}'}
    finally:
        client.close()


def daemon_status(state=None):
    observed = socket_state()
    running = observed == 'running'
    result = {
        'daemon': DAEMON_NAME,
        'running': running,
        'pid': None,
        'state': observed,
        'app_running': app_running(),
        'app_version': app_version(),
        'socket': str(DEFAULT_SOCKET),
        'listening': running,
    }
    # Add CDP status if server is running
    if running:
        try:
            from claude_desktop_rpc import Client
            client = Client(str(DEFAULT_SOCKET))
            try:
                cdp_resp = client.call('cdp/status', {})
                if isinstance(cdp_resp, dict):
                    result['cdp'] = cdp_resp
            except Exception:
                pass  # cdp/status not available on older daemons
            finally:
                client.close()
        except Exception:
            pass  # cannot connect
    return result
