"""Explicit shared-server preparation and Desktop launch; never quit a running app."""
import fcntl
from pathlib import Path
import plistlib
import subprocess
from connection import probe
import engines
import scheduler
from runtime import codex_environment

DESKTOP = Path('/Applications/ChatGPT.app')


def desktop_version():
    try:
        return str(plistlib.loads((DESKTOP/'Contents/Info.plist').read_bytes())['CFBundleVersion'])
    except (OSError, ValueError, KeyError):
        return None


def desktop_running():
    return subprocess.run(['/usr/bin/pgrep', '-x', 'ChatGPT'], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, timeout=2).returncode != 1


def state(db):
    result = scheduler.get(db, 'shared_mode', {})
    version = desktop_version()
    return result | {'desktop_version': version, 'startup_eligible': bool(version and result.get('verified_version') == version and result.get('verified_session'))}


def prepare(db, program, folder, startup=False):
    if not program:
        return {'ok': False, 'reason': 'Exécutable Codex absent.'}
    previous = state(db)
    if startup and not previous['startup_eligible']:
        return {'ok': False, 'reason': 'Version Desktop non validée pour le lancement automatique. Refaites le test.'}
    with (Path(folder)/'worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'ok': False, 'reason': 'Une reprise est en cours. Réessayez après sa fin.'}
        restore_auto = startup and scheduler.enabled(db)
        with db:
            scheduler.put(db, 'auto_enabled', False)
            scheduler.put(db, 'shared_mode', previous | {'restore_auto_after_check': restore_auto})
        path = str(engines.SOCKET)
        connected = probe(program, path)['connected']
        if not connected:
            if engines.SOCKET.exists() or engines.SOCKET.is_symlink():
                return {'ok': False, 'reason': 'Socket déjà présent mais inaccessible. Aucun serveur remplacé.'}
            try:
                process = subprocess.run([program, 'app-server', 'daemon', 'start', '-c', 'forced_login_method="chatgpt"'],
                    env=codex_environment(), capture_output=True, timeout=20)
                if process.returncode:
                    return {'ok': False, 'reason': 'Le daemon Codex n’a pas pu démarrer. Automatisation à vérifier.'}
            except (OSError, subprocess.TimeoutExpired):
                return {'ok': False, 'reason': 'Démarrage non confirmé. Aucun nouvel essai automatique.'}
            connected = probe(program, path)['connected']
        if not connected:
            return {'ok': False, 'reason': 'Le serveur ne confirme pas la connexion Codex.'}
        with db:
            scheduler.put(db, 'shared_mode', previous | {'prepared': True, 'restore_auto_after_check': restore_auto})
        return {'ok': True, 'reason': 'Serveur prêt. Fermez Desktop avant de le lancer en mode partagé.'}


def launch(db, program, folder, startup=False):
    if desktop_running():
        return {'ok': False, 'reason': 'Desktop est déjà ouvert. Fermez-le normalement pour appliquer le nouveau mode.'}
    result = prepare(db, program, folder, startup)
    if not result['ok']:
        return result
    try:
        socket = engines.socket_path({})
        if any(c in str(socket) for c in (':', '\n', '\r', ' ')):
            return {'ok': False, 'reason': 'Chemin de socket incompatible avec le lancement Desktop.'}
        url = 'ws+unix://' + str(socket) + ':/rpc'
        process = subprocess.run(['/usr/bin/open', '-a', str(DESKTOP), '--env', 'CODEX_APP_SERVER_WS_URL='+url,
                                  '--env', 'CODEX_APP_SERVER_USE_LOCAL_DAEMON=1', '--env', 'CODEX_APP_SERVER_FORCE_CLI=0'],
                                 capture_output=True, timeout=5)
        if process.returncode:
            return {'ok': False, 'reason': 'Lancement de Desktop refusé.'}
        previous = scheduler.get(db, 'shared_mode', {})
        with db:
            scheduler.put(db, 'shared_mode', previous | {'launched': True, 'launch_version': desktop_version()})
        return {'ok': True, 'reason': 'Lancement demandé. Ouvrez un chat dans Desktop puis vérifiez son identifiant.'}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'ok': False, 'reason': 'Lancement non confirmé. Le test du chat reste nécessaire.'}


def check(db, program, session):
    previous = scheduler.get(db, 'shared_mode', {})
    if not previous.get('launched') or not desktop_running():
        return {'ok': False, 'reason': 'Lancez d’abord Desktop avec le bouton du mode partagé.'}
    result = probe(program, str(engines.SOCKET), session)
    ok = result.get('connected') is True and result.get('thread_loaded') is True
    if ok:
        version = desktop_version()
        if not version or previous.get('launch_version') != version:
            return {'ok': False, 'reason': 'La version Desktop a changé. Recommencez le lancement et le test.'}
        with db:
            previous = scheduler.get(db, 'shared_mode', {})
            if previous.get('restore_auto_after_check'):
                scheduler.put(db, 'auto_enabled', True)
            scheduler.put(db, 'shared_mode', previous | {'verified_version': version, 'verified_session': session,
                                                        'restore_auto_after_check': False})
    return result | {'ok': ok, 'reason': ('Chat chargé dans le serveur partagé. Vous pouvez choisir ce serveur dans Reprise.' if ok else result['reason'])}
