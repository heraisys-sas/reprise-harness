"""Reprise owns installation and control; launchd executes the ASI1 proxy.

No inference, credentials, downloads or Harness configuration changes here.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys
import time

from harness_integration import atomic, encoded, digest, file_hash, bundle_info, stage_release, release_check
from scheduler_service import launch, loaded_arguments, target

LABEL = 'sas.heraisys.asi1-planner-proxy'


def safe(path):
    path = Path(path).expanduser().absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Chemin symbolique ou traversée refusé.')
    return path


def paths(home):
    home = safe(home)
    return (safe(home / 'Library/Application Support/Reprise/proxy-service'),
            safe(home / 'Library/LaunchAgents' / (LABEL + '.plist')),
            safe(home / 'Library/Logs/asi1-planner-proxy'))


def read(path):
    return json.loads(path.read_text()) if file_hash(path) else None


def signature(value):
    return digest(encoded(value).encode())


def loaded_matches(observed, current):
    if loaded_arguments(observed) != current.get('ProgramArguments'):
        return False
    blocks = re.findall(r'^\s*environment = \{\n(.*?)^\s*\}', observed.stdout, re.M | re.S)
    if len(blocks) != 1:
        return False
    environment = dict(re.findall(r'^\s*([^\s]+) => (.*)$', blocks[0], re.M))
    return all(environment.get(k) == v for k, v in current.get('EnvironmentVariables', {}).items())


LEGACY_SCRIPT_SHA256 = '7489ec3eb2c263c97df299d6007596147950a2bed7c17da6a975396c99496649'


def settings(home, release):
    _, _, logs = paths(home)
    return {'Label': LABEL, 'ProgramArguments': [str(release / 'Contents/Resources/runtime/bin/python3'), '-E', '-s', '-B', str(release / 'Contents/Resources/helpers/asi1_planner_proxy.py')],
            'EnvironmentVariables': {'HOME': str(safe(home)),
                'ASI1_PLANNER_UPSTREAM': 'https://inference.asicloud.cudos.org:443',
                'ASI1_PLANNER_LOG_PATH': str(logs / 'traffic.jsonl')},
            'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30, 'Umask': 63,
            'ProcessType': 'Background', 'StandardOutPath': str(logs / 'stdout.log'),
            'StandardErrorPath': str(logs / 'stderr.log')}


def integrity(release):
    try:
        release = safe(release)
        release_check(release)
        helpers, _, identity = bundle_info(release / 'Contents/Resources/helpers')
        return release.name == identity and (helpers / 'asi1_planner_proxy.py').is_file()
    except (OSError, ValueError, KeyError):
        return False


def inspect(home=None, runner=launch):
    home = safe(home or Path.home())
    support, plist, _ = paths(home)
    current = plistlib.loads(plist.read_bytes()) if file_hash(plist) else {}
    managed = read(support / 'managed.json')
    observed = runner(['print', target() + '/' + LABEL])
    owned = bool(managed and managed.get('status') == 'installed' and
                 managed.get('plist_sha256') == file_hash(plist))
    return {'status': 'inspected', 'loaded': observed.returncode == 0,
            'running': bool(re.search(r'^\s*state = running\s*$', observed.stdout, re.M)),
            'pid': next((int(x) for x in re.findall(r'^\s*pid = (\d+)\s*$', observed.stdout, re.M)), None),
            'managed': owned, 'arguments': current.get('ProgramArguments'),
            'loaded_arguments': loaded_arguments(observed),
            'loaded_matches': bool(current and loaded_matches(observed, current)),
            'integrity': bool(owned and integrity(Path(managed['release']))),
            'pending': bool(managed and managed.get('status') == 'pending'),
            'message': 'launchd exécute le démon ; Reprise gère son installation. Aucun appel d’inférence.'}


def plan(root, home, action='install', activate=False, adopt=False, runner=launch):
    if action not in ('install', 'start', 'stop') or type(activate) is not bool or type(adopt) is not bool:
        raise ValueError('Action invalide.')
    home = safe(home)
    support, plist, _ = paths(home)
    old = read(support / 'managed.json')
    if old and old.get('status') != 'installed':
        raise ValueError('Opération inachevée : réconciliation requise avant toute autre action.')
    before = file_hash(plist)
    observed = runner(['print', target() + '/' + LABEL])
    current = plistlib.loads(plist.read_bytes()) if before else {}
    loaded = observed.returncode == 0
    if observed.returncode not in (0, 113):
        raise ValueError('État launchd inconnu ; aucune modification.')
    if loaded and (not current or not loaded_matches(observed, current)):
        raise ValueError('Arguments chargés différents du plist ; réconciliation requise.')
    owned = bool(old and old.get('plist_sha256') == before)
    if old and not owned:
        raise ValueError('Plist géré modifié ; aucun écrasement.')
    value = {'format': 1, 'root': str(safe(root)), 'home': str(home), 'action': action,
             'activate': activate, 'adopt': adopt, 'before': before,
             'manifest_before': file_hash(support / 'managed.json'), 'loaded': loaded,
             'loaded_arguments': loaded_arguments(observed), 'release': None, 'legacy_script_sha256': None}
    if action == 'install':
        helpers, _, identity = bundle_info(root)
        if not (helpers / 'asi1_planner_proxy.py').is_file():
            raise ValueError('Reconstruire Reprise avec son proxy Python embarqué.')
        if before and not owned:
            args = current.get('ProgramArguments', [])
            legacy = (adopt and current.get('Label') == LABEL and len(args) == 2
                      and current.get('EnvironmentVariables') == {'HOME': str(home)}
                      and Path(args[0]).name == 'node' and safe(args[0]).is_file()
                      and file_hash(safe(args[1])) == LEGACY_SCRIPT_SHA256)
            if not legacy:
                raise ValueError('Service non géré : adoption explicite du proxy connu requise.')
            value['legacy_script_sha256'] = LEGACY_SCRIPT_SHA256
        value['release'] = str(support.parent / 'integration-releases' / identity)
    elif not owned or not integrity(Path(old['release'])):
        raise ValueError('Action réservée au service Reprise intègre.')
    value['fingerprint'] = signature(value)
    return value


def idle(observed):
    """Refuse an interruption while this process has established TCP connections."""
    if observed.returncode:
        return
    matches = re.findall(r'^\s*pid = (\d+)\s*$', observed.stdout, re.M)
    if len(matches) != 1:
        raise ValueError('PID natif inconnu ; aucun arrêt.')
    result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', matches[0], '-iTCP', '-F', 'fT'],
                            capture_output=True, text=True, timeout=10)
    if result.returncode != 0 or 'TST=LISTEN' not in result.stdout or 'TST=ESTABLISHED' in result.stdout:
        raise ValueError('Connexions actives ou état TCP inconnu ; attendre avant tout arrêt.')


def port_available():
    with socket.socket() as probe:
        probe.settimeout(2)
        try:
            probe.connect(('127.0.0.1', 8481))
        except ConnectionRefusedError:
            return
        except OSError as error:
            raise ValueError('État du port inconnu ; aucun chargement.') from error
    raise ValueError('Port 8481 occupé par un autre lanceur ; aucun chargement.')


def ready(runner, desired, timeout=5):
    deadline = time.monotonic() + timeout
    while True:
        observed = runner(['print', target() + '/' + LABEL])
        pids = re.findall(r'^\s*pid = (\d+)\s*$', observed.stdout, re.M)
        if loaded_matches(observed, desired) and len(pids) == 1 and re.search(r'^\s*state = running\s*$', observed.stdout, re.M):
            result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', pids[0], '-iTCP:8481', '-sTCP:LISTEN', '-F', 'p'],
                                    capture_output=True, text=True, timeout=2)
            if result.returncode == 0 and 'p' + pids[0] in result.stdout.splitlines():
                try:
                    with socket.create_connection(('127.0.0.1', 8481), timeout=0.5):
                        return
                except OSError:
                    pass
        if time.monotonic() >= deadline:
            raise ValueError('PID/listener non confirmés ; conserver pending et réconcilier.')
        time.sleep(0.1)


def apply(value, runner=launch, idle_check=idle, port_check=port_available, ready_check=ready):
    if value.get('fingerprint') != signature({k:v for k,v in value.items() if k != 'fingerprint'}):
        raise ValueError('Plan altéré.')
    support, plist, logs = paths(value['home'])
    support.mkdir(parents=True, exist_ok=True)
    lock = safe(support / 'lock')
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Une opération Reprise est déjà active.') from error
        fresh = plan(value['root'], value['home'], value['action'], value['activate'], value['adopt'], runner)
        if fresh != value:
            raise ValueError('État changé depuis le plan ; préparer un nouveau plan.')
        if value['action'] == 'install' and value['loaded'] and value['activate']:
            old = read(support / 'managed.json')
            desired = settings(value['home'], Path(value['release']))
            current = plistlib.loads(plist.read_bytes())
            if old and old.get('release') == value['release'] and current == desired and integrity(Path(value['release'])):
                ready_check(runner, desired)
                return {**inspect(value['home'], runner), 'status': 'completed',
                        'message': 'Release déjà installée et chargée ; aucun redémarrage.'}
        if value['action'] == 'start' and value['loaded']:
            ready_check(runner, plistlib.loads(plist.read_bytes()))
            return {**inspect(value['home'], runner), 'status': 'completed',
                    'message': 'Service déjà chargé ; aucun redémarrage.'}
        observed = runner(['print', target() + '/' + LABEL])
        if value['loaded']:
            idle_check(observed)
        elif value['action'] == 'start' or (value['action'] == 'install' and value['activate']):
            port_check()
        if value['action'] == 'install':
            release = safe(value['release'])
            helpers, runtime, identity = bundle_info(value['root'])
            if release != support.parent / 'integration-releases' / identity:
                raise ValueError('Release hors du périmètre géré.')
            stage_release(helpers, runtime, release)
            if not integrity(release):
                raise ValueError('Release Python non intègre ; aucun chargement.')
            desired = settings(value['home'], release)
        else:
            desired = plistlib.loads(plist.read_bytes())
        previous = read(support / 'managed.json')
        # Recheck after release staging, which may have taken time. Never stop
        # a different loaded configuration or a connection arriving meanwhile.
        observed = runner(['print', target() + '/' + LABEL])
        if (observed.returncode == 0) != value['loaded'] or observed.returncode not in (0, 113):
            raise ValueError('Chargement modifié pendant préparation ; refaire le plan.')
        if file_hash(plist) != value['before'] or file_hash(support / 'managed.json') != value['manifest_before']:
            raise ValueError('Configuration modifiée pendant préparation ; aucun écrasement.')
        if value['loaded']:
            current = plistlib.loads(plist.read_bytes())
            if file_hash(plist) != value['before'] or not loaded_matches(observed, current):
                raise ValueError('Configuration modifiée pendant préparation ; aucun arrêt.')
            idle_check(observed)
        # Persist uncertainty before touching launchd. A timeout never authorizes replay.
        receipt = {'status': 'pending', 'plan': value, 'previous': previous,
                   'previous_plist': plist.read_text() if value['before'] else None}
        atomic(support / 'managed.json', encoded(receipt))
        service = target() + '/' + LABEL
        def command(args):
            result = runner(args)
            if result.returncode:
                raise ValueError('Action launchd non confirmée ; opération conservée en pending.')
        if value['loaded']:
            command(['bootout', service])
        if value['action'] == 'install':
            logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            logs.chmod(0o700)
            for name in ('stdout.log', 'stderr.log', 'traffic.jsonl'):
                path = safe(logs / name)
                path.touch(mode=0o600, exist_ok=True)
                path.chmod(0o600)
            plist.parent.mkdir(parents=True, exist_ok=True)
            atomic(plist, plistlib.dumps(desired).decode())
        if value['action'] == 'start' or (value['action'] == 'install' and value['activate']):
            command(['enable', service])
            command(['bootstrap', target(), str(plist)])
            observed = runner(['print', service])
            if not loaded_matches(observed, desired):
                raise ValueError('Chargement non confirmé ; conserver pending et réconcilier.')
            ready_check(runner, desired)
        else:
            command(['disable', service])
            if runner(['print', service]).returncode != 113:
                raise ValueError('Arrêt non confirmé ; conserver pending et réconcilier.')
        managed = {'status': 'installed', 'release': value['release'] if value['action'] == 'install' else previous['release'],
                   'plist_sha256': file_hash(plist)}
        atomic(support / 'last-operation.json', encoded({**receipt, 'status': 'completed'}))
        atomic(support / 'managed.json', encoded(managed))
        return {**inspect(value['home'], runner), 'status': 'completed'}


def main(root, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['proxy-service-inspect', 'proxy-service-plan', 'proxy-service-apply'])
    parser.add_argument('--home', default=str(Path.home()))
    parser.add_argument('--action', default='install', choices=['install', 'start', 'stop'])
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--adopt', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.operation.endswith('inspect'):
            result = inspect(args.home)
        elif args.operation.endswith('plan'):
            result = {'status': 'planned', 'plan': plan(root, args.home, args.action, args.activate, args.adopt),
                      'message': 'Reprise installe et gère ; launchd exécute. Le plan ne lance aucun service.'}
        else:
            result = apply(json.load(sys.stdin)['plan'])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'failed', 'message': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main(Path(__file__).resolve().parent))
