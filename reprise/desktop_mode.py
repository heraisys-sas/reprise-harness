"""Quick Apply daemon lifecycle and linked-app launch; never force-quit.

Mirrors shared_mode.py's grammar for Codex. The Quick Entry server
(claude_desktop_quick) validates and routes the payload on its own, but
submitting into a conversation needs Claude Desktop running. This module
therefore exposes: launch (open the linked app if needed), start/stop (the
Quick Apply daemon) and status. Its startup action only starts an absent daemon
after refusing unknown or orphaned sockets; it never force-quits a running app.
"""
from app_paths import state_directory, state_location
import errno
import fcntl
import os
import plistlib
from contextlib import ExitStack, contextmanager
from pathlib import Path
import secrets
import subprocess
import sys

import engines
from claude_desktop import DEFAULT_SOCKET

# Application liée : copie ReClaude produite par desktop_patch (asar re-signé,
# injection relais #130) ; repli sur l'app Claude officielle si la copie manque.
APP = Path.home() / 'Applications' / 'ReClaude.app'
if not APP.exists():
    APP = Path('/Applications/Claude.app')
SERVER = Path(__file__).parent / 'claude_desktop_quick.py'


def app_version():
    try:
        return str(plistlib.loads((APP / 'Contents/Info.plist').read_bytes())['CFBundleShortVersionString'])
    except (OSError, KeyError, ValueError):
        return None


def app_state():
    """La copie ReClaude est un binaire re-signé dont le nom de process varie
    selon le bundle ; on cherche sur le chemin du bundle plutôt qu'un nom fixe.
    Une erreur de sonde n'est pas une preuve que l'application est absente."""
    try:
        probe = str(APP)
        result = subprocess.run(['/usr/bin/pgrep', '-f', probe], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=2)
        if result.returncode == 0:
            return 'running'
        if result.returncode == 1:
            return 'absent'
        return 'unknown'
    except (OSError, subprocess.TimeoutExpired):
        return 'unknown'


def app_running():
    """Compatibility boolean for display; launch decisions use app_state()."""
    return app_state() == 'running'


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


@contextmanager
def _startup_locks(state_path, socket_path):
    """Lock both this state and the endpoint so separate states cannot race."""
    state_path = Path(state_path).expanduser().absolute()
    socket_path = Path(socket_path).expanduser().absolute()
    socket_parent = socket_path.parent
    if state_path.is_symlink() or socket_parent.is_symlink():
        raise ValueError('Chemin d’état ou de socket symbolique.')
    state_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    socket_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_paths = sorted({state_path / 'daemon-startup.lock',
                         socket_path.with_name(socket_path.name + '.reprise-startup.lock')},
                        key=str)
    with ExitStack() as stack:
        for lock_path in lock_paths:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            stream = os.fdopen(fd, 'a+')
            stack.callback(stream.close)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise
        yield


def status():
    return {'app': APP.name, 'app_version': app_version(), 'app_running': app_running(),
            'server_running': server_running(), 'socket': str(DEFAULT_SOCKET)}


def launch():
    """Open the linked app if not already running. Never force-quits."""
    observed = app_state()
    if observed == 'running':
        return {'ok': True, 'launched': False, 'reason': 'Application liée déjà en cours.'}
    if observed != 'absent':
        return {'ok': False, 'launched': False, 'status': 'unknown',
                'reason': 'État de l’application inconnu ; aucune ouverture tentée.'}
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


def daemon_start(socket=None, state=None, daily_limit=8, project_root=None, _lock_held=False):
    """Serialize manual and startup launches using one Reprise-owned lock."""
    state_path = Path(state or DEFAULT_STATE).expanduser().absolute()
    socket_path = Path(socket or DEFAULT_SOCKET).expanduser().absolute()
    if _lock_held:
        return _daemon_start_impl(str(socket_path), str(state_path), daily_limit, project_root)
    try:
        with _startup_locks(state_path, socket_path):
            return _daemon_start_impl(str(socket_path), str(state_path), daily_limit, project_root)
    except BlockingIOError:
        return {'ok': False, 'started': False, 'status': 'startup_busy',
                'reason': 'Un autre démarrage cible ce socket ; aucun second processus lancé.'}
    except (OSError, ValueError) as error:
        return {'ok': False, 'started': False, 'status': 'blocked_state_path',
                'reason': f'Chemin d’état non utilisable ({type(error).__name__}).'}


def _daemon_start_impl(socket=None, state=None, daily_limit=8, project_root=None):
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
        try:
            proc = subprocess.Popen([sys.executable, '-E', '-s', '-B', str(SERVER), '--socket', socket,
                                 '--state', str(state)] + (['--project-root', str(project_root)] if project_root else []),
                                stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                start_new_session=True)
        except BaseException:
            log.close()
            raise
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


def daemon_startup(state=None):
    """Start only an absent Quick Apply daemon; report readiness separately from app/relais."""
    state_path = Path(state or DEFAULT_STATE).expanduser().absolute()
    try:
        with _startup_locks(state_path, DEFAULT_SOCKET):
            return _daemon_startup_locked(str(state_path))
    except BlockingIOError:
        return {'status': 'startup_busy', 'daemon_ready': False,
                'app_running': False, 'app_state': 'unknown', 'relay_ready': None,
                'socket': str(DEFAULT_SOCKET),
                'message': 'Un autre démarrage cible ce socket ; aucun second processus lancé.'}
    except (OSError, ValueError) as error:
        return {'status': 'blocked_state_path', 'daemon_ready': False,
                'app_running': False, 'app_state': 'unknown', 'relay_ready': None,
                'socket': str(DEFAULT_SOCKET),
                'message': f'Chemin d’état non utilisable ({type(error).__name__}) ; aucun démarrage tenté.'}


def _daemon_startup_locked(state):
    """Caller holds the startup lock; all socket checks happen inside it."""
    observed = socket_state()
    linked_app_state = app_state()
    socket_path = DEFAULT_SOCKET
    if observed == 'unknown':
        return {'status': 'unknown', 'daemon_ready': False, 'app_running': linked_app_state == 'running',
                'app_state': linked_app_state,
                'relay_ready': None, 'socket': str(socket_path),
                'message': 'État du socket inconnu ; aucun démarrage tenté.'}
    if observed == 'running':
        from claude_desktop_rpc import probe_dispatch_socket
        verified = probe_dispatch_socket(socket_path)
        return {'status': 'already_ready' if verified else 'unverified_socket',
                'daemon_ready': verified, 'app_running': linked_app_state == 'running',
                'app_state': linked_app_state,
                'relay_ready': None, 'socket': str(socket_path),
                'message': ('Daemon Quick Apply répond au greeting ; relais non vérifié.' if verified else
                            'Socket actif sans greeting Quick Apply vérifié ; aucune action.')}
    if socket_path.exists():
        return {'status': 'orphan_socket', 'daemon_ready': False, 'app_running': linked_app_state == 'running',
                'app_state': linked_app_state,
                'relay_ready': None, 'socket': str(socket_path),
                'message': 'Socket présent mais injoignable ; aucun remplacement automatique.'}
    result = daemon_start(state=state, _lock_held=True)
    from claude_desktop_rpc import probe_dispatch_socket
    verified = bool(result.get('ok')) and probe_dispatch_socket(socket_path)
    return {'status': 'started' if verified else 'start_unverified',
            'daemon_ready': verified, 'app_running': linked_app_state == 'running',
            'app_state': linked_app_state,
            'relay_ready': None, 'socket': str(socket_path), 'operation': result,
            'message': ('Daemon Quick Apply démarré et greeting vérifié ; relais non vérifié.' if verified else
                        'Démarrage non confirmé par greeting ; aucun second essai automatique.')}
