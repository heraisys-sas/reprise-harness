"""Explicit scheduler installation; activation can run previously authorized work."""
import argparse
from contextlib import closing, contextmanager
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import shutil
import tempfile
import subprocess
import sys
import uuid
from app_paths import state_location
from state_install import initialized
from harness_integration import atomic, encoded, digest, file_hash, bundle_info, stage_release, release_check, tree_files

LABEL = 'local.agent-resume'
STARTUP_ACTIVATION_VERSION = 1


def paths(home):
    home = Path(home).expanduser().absolute()
    support = home / 'Library/Application Support/Reprise'
    plist = home / 'Library/LaunchAgents' / (LABEL + '.plist')
    managed = support / 'scheduler-service'
    for path in (plist, managed):
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ValueError('Chemin symbolique refusé.')
    return support, plist, managed


def launch(args):
    return subprocess.run(['/bin/launchctl', *args], capture_output=True, text=True, timeout=15)


def target():
    return f'gui/{os.getuid()}'


def read(path):
    return json.loads(path.read_text()) if file_hash(path) else None


def scheduler_arguments(settings):
    args = settings.get('ProgramArguments', [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return None
    if len(args) == 3 and args[2] == 'tick':
        return args[0], args[1]
    if len(args) == 6 and args[1:4] == ['-E', '-s', '-B'] and args[5] == 'tick':
        return args[0], args[4]
    return None


def loaded_arguments(observed):
    if observed.returncode != 0:
        return None
    lines = observed.stdout.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == 'arguments = {']
    if len(starts) != 1:
        return None
    args = []
    for line in lines[starts[0] + 1:]:
        if line.strip() == '}':
            return args
        if not line.strip():
            return None
        args.append(line.strip())
    return None


def loaded_registry(observed):
    """Only the job's explicit environment proves its worker-lock namespace."""
    if observed.returncode != 0:
        return None
    lines = observed.stdout.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == 'environment = {']
    if len(starts) != 1:
        return None
    values = []
    for line in lines[starts[0] + 1:]:
        if line.strip() == '}':
            if len(values) == 1 and Path(values[0]).is_absolute():
                return values[0]
            return None
        match = re.fullmatch(r'\s*AGENT_RESUME_STATE => (.+)', line)
        if match:
            values.append(match.group(1))
        elif '{' in line or '}' in line:
            return None
    return None


def loaded_matches(observed, settings):
    # launchd retains loaded arguments independently of the plist on disk.
    args = settings.get('ProgramArguments', [])
    expected_state = settings.get('EnvironmentVariables', {}).get('AGENT_RESUME_STATE')
    return bool(args) and bool(expected_state) and loaded_arguments(observed) == args and loaded_registry(observed) == expected_state


@contextmanager
def readonly_registry(path):
    # SQLite RO can create WAL/SHM sidecars. Query a stable private snapshot instead.
    sources = [path, Path(str(path) + '-wal')]
    def signature():
        result = []
        for source in sources:
            file_hash(source)  # reject symlinks and non-regular files
            stat = source.stat() if source.exists() else None
            result.append((stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns) if stat else None)
        return result
    before = signature()
    with tempfile.TemporaryDirectory(prefix='reprise-scheduler-inspect-') as folder:
        copied = Path(folder) / path.name
        for source in sources:
            if source.exists():
                shutil.copyfile(source, Path(folder) / source.name)
        if signature() != before:
            raise ValueError('Registre modifié pendant inspection ; réessayer sans modifier le service.')
        with closing(sqlite3.connect(copied.as_uri() + '?mode=ro', uri=True)) as db:
            yield db


def inspect(root, home=None, runner=launch):
    support, plist, managed = paths(home or Path.home())
    state = state_location(root).absolute()
    settings = plistlib.loads(plist.read_bytes()) if file_hash(plist) else {}
    loaded = runner(['print', target() + '/' + LABEL])
    result = {'status': 'inspected', 'state': str(state), 'state_prepared': initialized(state),
              'plist': str(plist), 'file_present': bool(settings), 'loaded': loaded.returncode == 0,
              'last_exit': next((line.strip() for line in loaded.stdout.splitlines() if 'last exit code' in line), None),
              'loaded_evidence': loaded.stdout if loaded.returncode == 0 else loaded.stderr,
              'interval': settings.get('StartInterval'), 'arguments': settings.get('ProgramArguments'),
              'configured_state': settings.get('EnvironmentVariables', {}).get('AGENT_RESUME_STATE'),
              'effective_state': loaded_registry(loaded),
              'auto_enabled': None, 'last_tick': None, 'last_error': None, 'pending': None,
              'runtime_integrity': False, 'configured_release': None, 'effective_release': None,
              'message': 'Inspection sans écriture. Chargement, tick et quotas sont des preuves distinctes.'}
    try:
        _, _, identity = bundle_info(root)
        result['expected_release'] = str(state.parent / 'integration-releases' / identity)
    except (OSError, ValueError, KeyError):
        result['expected_release'] = None
    observed_release = re.search(r'(/[^\n]*?/integration-releases/[0-9a-f]{64})/Contents/Resources/helpers/(?:resume|reprise)', loaded.stdout) if loaded.returncode == 0 else None
    if observed_release:
        result['effective_release'] = observed_release.group(1)
    args = scheduler_arguments(settings)
    if args:
        release = Path(args[1]).parents[3] if Path(args[1]).is_absolute() else Path('/')
        if release.parent == state.parent / 'integration-releases':
            result['configured_release'] = str(release)
            if loaded_matches(loaded, settings):
                result['effective_release'] = str(release)
            try:
                release_check(release)
                result['runtime_integrity'] = Path(args[0]) == release / 'Contents/Resources/runtime/bin/python3'
            except (OSError, ValueError, KeyError):
                pass
    result['configured_state_matches'] = result['configured_state'] == str(state)
    result['loaded_state_matches'] = result['effective_state'] == str(state)
    result['configured_current'] = (bool(result['expected_release']) and result['configured_release'] == result['expected_release']
                                    and settings.get('EnvironmentVariables', {}).get('AGENT_RESUME_STATE') == str(state))
    result['loaded_current'] = result['configured_current'] and loaded_matches(loaded, settings)
    dbpath = state / 'tasks.sqlite'
    if dbpath.is_file():
        with readonly_registry(dbpath) as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'scheduler_meta' in tables:
                meta = dict(db.execute('SELECT key,value FROM scheduler_meta'))
                for key in ('auto_enabled', 'last_tick', 'last_error'):
                    if key in meta:
                        result[key] = json.loads(meta[key])
            if 'tasks' in tables:
                result['pending'] = db.execute("SELECT count(*) FROM tasks WHERE status IN ('ready','waiting_quota','paused')").fetchone()[0]
    journal = read(managed / 'operation.json') or {}
    result['pending_plan'] = journal.get('plan') if journal.get('status') == 'pending' else None
    return result


def _activate_monitoring_once(state):
    """Apply the explicitly requested startup defaults once, without creating state."""
    state = Path(state)
    if not initialized(state):
        return {'status': 'needs_setup', 'changed': False,
                'message': 'Registre Reprise non préparé ; aucune configuration créée.'}
    dbpath = state / 'tasks.sqlite'
    worker_path = state / 'worker.lock'
    if (not dbpath.is_file() or dbpath.is_symlink()
            or any(path.is_symlink() for path in (state, worker_path, *state.parents))):
        return {'status': 'needs_setup', 'changed': False,
                'message': 'Registre du planificateur absent ou non régulier ; aucune configuration créée.'}
    with worker_path.open('a') as worker:
        try:
            fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'changed': False,
                    'message': 'Un worker est actif ; activation différée sans modifier le registre.'}
        with closing(sqlite3.connect(dbpath, timeout=2)) as db:
            db.execute('BEGIN IMMEDIATE')
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'scheduler_meta' not in tables:
                db.rollback()
                return {'status': 'needs_setup', 'changed': False,
                        'message': 'Métadonnées du planificateur absentes ; aucune configuration créée.'}
            try:
                row = db.execute('SELECT value FROM scheduler_meta WHERE key=?',
                                 ('startup_activation_version',)).fetchone()
                version = json.loads(row[0]) if row else 0
                if type(version) is not int or version < 0:
                    raise ValueError('Marqueur de démarrage invalide.')
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                db.rollback()
                return {'status': 'blocked', 'changed': False,
                        'message': str(error) or 'Marqueur de démarrage invalide.'}
            if version >= STARTUP_ACTIVATION_VERSION:
                values = dict(db.execute(
                    "SELECT key, value FROM scheduler_meta WHERE key IN (?, ?)",
                    ('auto_enabled', 'claude_desktop_auto_enabled')))
                db.commit()
                try:
                    enabled = {key: json.loads(value) for key, value in values.items()}
                except (TypeError, ValueError, json.JSONDecodeError):
                    return {'status': 'blocked', 'changed': False,
                            'message': 'Réglage d’activation illisible ; conservé sans modification.'}
                return {'status': 'already_applied', 'changed': False,
                        'enabled': {'auto_enabled': enabled.get('auto_enabled', True),
                                    'claude_desktop_auto_enabled': enabled.get('claude_desktop_auto_enabled', True)}}
            db.executemany('INSERT OR REPLACE INTO scheduler_meta(key,value) VALUES (?,?)', [
                ('auto_enabled', json.dumps(True)),
                ('claude_desktop_auto_enabled', json.dumps(True)),
                ('startup_activation_version', json.dumps(STARTUP_ACTIVATION_VERSION)),
            ])
            db.commit()
            return {'status': 'activated_once', 'changed': True,
                    'enabled': {'auto_enabled': True, 'claude_desktop_auto_enabled': True}}


def _bootstrap_current(root, home, runner, state, plist, managed, activation, operation):
    worker_path = Path(state) / 'worker.lock'
    if not worker_path.is_file() or worker_path.is_symlink():
        return {'status': 'blocked_changed_before_bootstrap', 'activation': activation,
                'service': None, 'operation': operation,
                'message': 'Verrou du worker absent ou non régulier ; aucun bootstrap tenté.'}
    with worker_path.open('a') as worker:
        try:
            fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'activation': activation, 'service': None,
                    'operation': operation,
                    'message': 'Un worker est actif ; bootstrap différé sans modifier le service.'}
        check = inspect(root, home, runner)
        manifest = read(managed / 'managed.json')
        expected_manifest = {'format': 1, 'plist_sha256': file_hash(plist),
                             'release': check.get('expected_release'), 'state': str(state)}
        if check['loaded']:
            return {'status': 'already_loaded' if check['loaded_current'] and manifest == expected_manifest
                    else 'blocked_changed_before_bootstrap', 'activation': activation,
                    'service': check, 'operation': operation,
                    'message': 'Service déjà chargé et vérifié.' if check['loaded_current'] and manifest == expected_manifest
                    else 'Service chargé ou ownership différent au contrôle final ; aucun remplacement.'}
        if (not check['configured_current'] or not check['runtime_integrity']
                or manifest != expected_manifest
                or not file_hash(plist) or not file_hash(managed / 'managed.json')):
            return {'status': 'blocked_changed_before_bootstrap', 'activation': activation,
                    'service': check, 'operation': operation,
                    'message': 'Plist, manifeste ou release non conforme ; aucun bootstrap tenté.'}
        plist_hash = file_hash(plist)
        manifest_hash = file_hash(managed / 'managed.json')
        if file_hash(plist) != plist_hash or file_hash(managed / 'managed.json') != manifest_hash:
            return {'status': 'blocked_changed_before_bootstrap', 'activation': activation,
                    'service': check, 'operation': operation,
                    'message': 'Plist ou manifeste changé au contrôle final ; aucun bootstrap tenté.'}
        try:
            result = runner(['bootstrap', target(), str(plist)])
            final = inspect(root, home, runner)
            stable = (file_hash(plist) == plist_hash
                      and file_hash(managed / 'managed.json') == manifest_hash)
            current = final['loaded_current'] and stable
        except Exception as error:
            return {'status': 'activation_indeterminate', 'activation': activation,
                    'service': None, 'operation': operation,
                    'message': f'Bootstrap envoyé mais état ou empreinte impossible à vérifier ({type(error).__name__}) ; aucun nouvel essai.'}
        if current:
            return {'status': 'started' if operation is None else 'installed_and_started',
                    'activation': activation, 'service': final, 'operation': operation,
                    'message': 'Service Reprise courant chargé et vérifié.'}
        return {'status': 'activation_unverified', 'activation': activation,
                'service': final, 'operation': operation,
                'message': 'Bootstrap lancé sans preuve stable de configuration chargée ; aucun second essai automatique.'}


def startup(root, home=None, runner=launch):
    """Idempotently activate only the current Reprise-owned scheduler service."""
    state = state_location(root).absolute()
    activation = None
    operation = None
    if not initialized(state) or not (state / 'tasks.sqlite').is_file():
        return {'status': 'needs_setup', 'activation': {'status': 'needs_setup', 'changed': False,
                'message': 'Registre Reprise non préparé ; aucune configuration créée.'},
                'service': None,
                'message': 'Registre Reprise non préparé ; aucune configuration créée.'}
    try:
        observed = inspect(root, home, runner)
        _, plist, managed = paths(home or Path.home())
        manifest = read(managed / 'managed.json')
        expected_manifest = {
            'format': 1,
            'plist_sha256': file_hash(plist),
            'release': observed.get('expected_release'),
            'state': str(state),
        }
        owned_current = (manifest == expected_manifest and observed['configured_current']
                         and observed['runtime_integrity'])
        if observed.get('pending_plan') is not None:
            return {'status': 'blocked_pending_operation', 'activation': None,
                    'service': observed,
                    'message': 'Une opération interrompue existe ; reprendre son plan explicitement.'}
        if observed['loaded'] and not owned_current:
            return {'status': 'blocked_unverified_service', 'activation': None,
                    'service': observed,
                    'message': 'Le service chargé ou sa propriété ne sont pas conformes ; aucune réparation automatique.'}
        if observed['file_present'] or manifest is not None:
            if not owned_current:
                return {'status': 'blocked_unverified_service', 'activation': None,
                        'service': observed,
                        'message': 'Service absent, modifié, non géré ou non à jour ; réconciliation explicite requise.'}
            if observed['loaded'] and not observed['loaded_current']:
                return {'status': 'blocked_unverified_service', 'activation': None,
                        'service': observed,
                        'message': 'Configuration chargée différente ; aucune réparation automatique.'}
            value = None
        else:
            if observed['loaded']:
                return {'status': 'blocked_unverified_service', 'activation': None,
                        'service': observed,
                        'message': 'Service chargé sans installation possédée vérifiable ; aucune action.'}
            # Planning is read-only; perform it before changing the one-time gates.
            value = plan(root, home, activate=False, startup_safe=True)

        activation = _activate_monitoring_once(state)
        if activation['status'] not in ('activated_once', 'already_applied'):
            return {'status': activation['status'], 'activation': activation,
                    'service': observed, 'message': activation['message']}
        if activation.get('enabled', {}).get('auto_enabled') is not True:
            return {'status': 'paused_by_user', 'activation': activation,
                    'service': observed,
                    'message': 'Le mode automatique global a été désactivé après son activation initiale ; service laissé dans son état courant.'}

        # A fresh install is published while stopped. Existing managed configuration
        # is never re-planned, backed up, or rewritten during startup.
        if value is not None:
            operation = apply(root, value, runner)
            observed = inspect(root, home, runner)
            manifest = read(managed / 'managed.json')
            expected_manifest = {'format': 1, 'plist_sha256': file_hash(plist),
                                 'release': observed.get('expected_release'), 'state': str(state)}
            if (manifest != expected_manifest or not observed['configured_current']
                    or not observed['runtime_integrity'] or observed['loaded']):
                return {'status': 'blocked_after_install', 'activation': activation,
                        'service': observed, 'operation': operation,
                        'message': 'Configuration installée mais non conforme ou déjà chargée ; aucun démarrage tenté.'}
        if observed['loaded']:
            return {'status': 'already_loaded', 'activation': activation,
                    'service': observed, 'message': 'Service Reprise chargé et conforme ; aucune modification.'}

        return _bootstrap_current(root, home, runner, state, plist, managed, activation, operation)
    except Exception as error:
        return {'status': 'blocked', 'activation': activation, 'service': None,
                'message': f'Démarrage bloqué ({type(error).__name__}) ; aucune réparation automatique.'}

def plan(root, home=None, action='install', activate=False, interval=60, startup_safe=False):
    if (action not in ('install', 'remove') or type(activate) is not bool
            or type(startup_safe) is not bool or type(interval) is not int
            or not 30 <= interval <= 86400):
        raise ValueError('Paramètres du plan invalides.')
    home = str(Path(home or Path.home()).absolute())
    support, plist, managed = paths(home)
    state = state_location(root).absolute()
    if any(p.is_symlink() for p in [state, *state.parents]):
        raise ValueError('État symbolique refusé.')
    previous = read(managed / 'managed.json')
    if previous and previous.get('state') != str(state):
        raise ValueError('Le service géré utilise un autre registre ; choisir son état avant réconciliation.')
    before = file_hash(plist)
    adoption = False
    if before and (not previous or previous.get('plist_sha256') != before):
        # Only the known legacy installer shape may be explicitly adopted.
        if previous:
            raise ValueError('Service personnalisé depuis installation : réconciliation requise.')
        try:
            existing = plistlib.loads(plist.read_bytes())
        except Exception as error:
            raise ValueError('Service personnalisé ou non géré : plist invalide.') from error
        args = scheduler_arguments(existing)
        adoption = (previous is None and existing.get('Label') == LABEL and args is not None and Path(args[1]).name in ('resume', 'reprise')
                    and existing.get('EnvironmentVariables', {}).get('AGENT_RESUME_STATE') == str(state)
                    and Path(args[1]).parent.name == 'helpers'
                    and Path(args[1]).parents[3].parent == state.parent / 'integration-releases')
        if not adoption:
            raise ValueError('Service personnalisé ou non géré : réconciliation requise.')
        legacy_release = Path(args[1]).parents[3]
        if Path(args[0]) != legacy_release / 'Contents/Resources/runtime/bin/python3':
            raise ValueError('Interpréteur du service non géré ; réconciliation requise.')
        release_check(legacy_release)
    if action == 'remove' and not previous:
        raise ValueError('Retrait réservé au service géré par Reprise ; installer/adopter explicitement d’abord.')
    release = None
    content = None
    repair_tree = None
    if action == 'install':
        if not initialized(state):
            raise ValueError('Préparer explicitement le registre avant installation.')
        helpers, runtime, identity = bundle_info(root)
        release = state.parent / 'integration-releases' / identity
        if release.exists():
            try:
                release_check(release)
            except (OSError, ValueError, KeyError):
                if not previous:
                    raise ValueError('Release existant altéré sans ownership du service ; réconciliation requise.')
                repair_tree = digest(encoded(tree_files(release)).encode())
        resources = release / 'Contents/Resources'
        settings = {'Label': LABEL, 'ProgramArguments': [str(resources / 'runtime/bin/python3'), '-E', '-s', '-B', str(resources / 'helpers/reprise'), 'tick'],
                    'StartInterval': interval, 'EnvironmentVariables': {'AGENT_RESUME_STATE': str(state), 'PYTHONDONTWRITEBYTECODE': '1', 'PATH': os.environ.get('PATH', '/usr/bin:/bin')},
                    'StandardOutPath': str(state / 'service.log'), 'StandardErrorPath': str(state / 'service-error.log')}
        content = plistlib.dumps(settings).decode()
    value = {'format': 1, 'home': home, 'action': action, 'activate': activate,
             'interval': interval,
             'state': str(state), 'state_marker_sha256': file_hash(state / '.reprise-state.json'), 'plist': str(plist), 'before_sha256': before, 'manifest_sha256': file_hash(managed / 'managed.json'),
             'release': str(release) if release else None, 'repair_tree_sha256': repair_tree,
             'release_backup': str(release) + '.damaged-' + repair_tree if repair_tree else None, 'content': content, 'adopt_legacy': adoption,
             'effects': 'Si chargé, le service est déchargé pour remplacement ou retrait. Sans activation cochée, il reste arrêté après remplacement. Activation : le prochain tick peut reprendre les travaux autorisés si le mode automatique est actif. La pause enregistrée est conservée.',
             'recovery': 'Sauvegarde du plist et journal conservés ; registres, résultats et releases conservés au retrait.'}
    if startup_safe:
        value['startup_safe'] = True
    value['fingerprint'] = digest(encoded(value).encode())
    return value


def apply(root, value, runner=launch):
    state = Path(value['state'])
    if any(p.is_symlink() for p in [state, state / 'worker.lock', *state.parents]) or not initialized(state):
        raise ValueError('État préparé non symbolique requis.')
    with (state / 'worker.lock').open('a') as worker:
        try:
            fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Un worker est actif ; attendre sa fin avant modification du service.') from error
        return _apply_locked(root, value, runner)


def _apply_locked(root, value, runner):
    support, plist, managed = paths(value['home'])
    managed.mkdir(parents=True, exist_ok=True, mode=0o700)
    file_hash(managed / 'lock')
    with (managed / 'lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_path = managed / 'operation.json'
        journal = read(journal_path)
        pending = journal and journal.get('status') == 'pending'
        if pending:
            if journal['plan'] != value:
                raise ValueError('Une opération interrompue doit être reprise avant un nouveau plan.')
        else:
            expected = plan(root, value['home'], value['action'], value['activate'], value['interval'],
                            value.get('startup_safe', False))
            if expected != value:
                raise ValueError('Plan périmé ; prévisualiser de nouveau.')
            wanted_hash = digest(value['content'].encode()) if value['content'] else None
            wanted_manifest = {'format': 1, 'plist_sha256': wanted_hash, 'release': value['release'], 'state': value['state']}
            if value['before_sha256'] == wanted_hash and read(managed / 'managed.json') == wanted_manifest and not value.get('repair_tree_sha256'):
                observed = runner(['print', target() + '/' + LABEL])
                settings = plistlib.loads(value['content'].encode()) if value['content'] else {}
                if loaded_matches(observed, settings) or (not value['activate'] and observed.returncode != 0):
                    return {'status': 'completed', 'message': 'Configuration identique : aucune modification. Pause enregistrée conservée.'}
            backup = str(managed / ('backup-' + uuid.uuid4().hex + '.plist')) if plist.exists() else None
            journal = {'format': 1, 'status': 'pending', 'plan': value, 'backup': backup, 'steps': []}
            if backup:
                atomic(Path(backup), plist.read_text())
            atomic(journal_path, encoded(journal))
        def done(step):
            journal['steps'].append(step)
            atomic(journal_path, encoded(journal))
        if file_hash(Path(value['state']) / '.reprise-state.json') != value['state_marker_sha256']:
            raise ValueError('Préparation du registre modifiée ; opération suspendue.')
        after = digest(value['content'].encode()) if value['content'] else None
        desired_manifest = {'format': 1, 'plist_sha256': after, 'release': value['release'], 'state': value['state']}
        if file_hash(managed / 'managed.json') not in (value['manifest_sha256'], digest(encoded(desired_manifest).encode())):
            raise ValueError('Manifeste modifié pendant l’opération ; réconciliation requise.')
        allowed = (after,) if 'published' in journal['steps'] else (value['before_sha256'], after)
        if file_hash(plist) not in allowed:
            raise ValueError('Plist modifié pendant l’opération ; sauvegarde et journal conservés.')
        settings = plistlib.loads(value['content'].encode()) if value['content'] else {}
        observed = runner(['print', target() + '/' + LABEL])
        stale_loaded = observed.returncode == 0 and not loaded_matches(observed, settings)
        changed = value['before_sha256'] != after
        should_unload = changed or value['action'] == 'remove' or bool(value.get('repair_tree_sha256'))
        if value.get('startup_safe') and observed.returncode == 0:
            raise ValueError('Service chargé apparu pendant installation startup-safe ; aucun déchargement effectué.')
        if stale_loaded or (should_unload and 'unloaded' not in journal['steps']):
            if observed.returncode == 0:
                if loaded_registry(observed) != value['state']:
                    raise ValueError('Registre du service chargé différent ou inconnu ; réconciliation requise avant déchargement.')
                if re.search(r'(?m)^\s*state = running\s*$', observed.stdout):
                    raise ValueError('Un tick est en cours ; attendre sa fin avant réparation ou retrait.')
                result = runner(['bootout', target() + '/' + LABEL])
                if result.returncode:
                    raise ValueError('Déchargement non confirmé : ' + result.stderr)
            done('unloaded')
        if value['action'] == 'install':
            helpers, runtime, identity = bundle_info(root)
            if Path(value['release']).name != identity:
                raise ValueError('Payload changé ; opération suspendue.')
            release = Path(value['release'])
            if value.get('repair_tree_sha256') and release.exists():
                try:
                    release_check(release)
                except (OSError, ValueError, KeyError):
                    if digest(encoded(tree_files(release)).encode()) != value['repair_tree_sha256']:
                        raise ValueError('Release altéré depuis le plan ; réconciliation requise.')
                    backup = Path(value['release_backup'])
                    if backup.exists():
                        raise ValueError('Sauvegarde du release déjà présente ; réconciliation requise.')
                    release.rename(backup)
            stage_release(helpers, runtime, release)
        if 'published' not in journal['steps']:
            if value['content'] is None:
                plist.unlink(missing_ok=True)
            else:
                atomic(plist, value['content'])
            done('published')
        if value['activate'] and value['action'] == 'install':
            if runner(['print', target() + '/' + LABEL]).returncode != 0:
                result = runner(['bootstrap', target(), str(plist)])
                if result.returncode:
                    raise ValueError('Activation non confirmée : ' + result.stderr)
        if file_hash(plist) != after:
            raise ValueError('Plist modifié avant clôture ; opération suspendue.')
        if value['activate'] and value['action'] == 'install':
            if not loaded_matches(runner(['print', target() + '/' + LABEL]), settings):
                raise ValueError('Activation non confirmée : configuration chargée différente ou inconnue.')
        atomic(managed / 'managed.json', encoded(desired_manifest))
        journal['status'] = 'completed'
        atomic(journal_path, encoded(journal))
        return {'status': 'completed', 'backup': journal['backup'], 'message': 'Service configuré ; pause automatique conservée. Vérifiez le chargement et le prochain tick.'}


def main(root, argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('scheduler-service-inspect', 'scheduler-service-plan', 'scheduler-service-apply', 'scheduler-service-verify', 'scheduler-service-startup'))
    parser.add_argument('--action', choices=('install', 'remove'), default='install')
    parser.add_argument('--activate', action='store_true')
    parser.add_argument('--interval', type=int, default=60)
    args = parser.parse_args(argv)
    try:
        if args.command.endswith('-startup'):
            result = startup(root)
        elif args.command.endswith('-plan'):
            result = {'plan': plan(root, action=args.action, activate=args.activate, interval=args.interval), 'message': 'Plan sans écriture ; confirmez les effets avant application.'}
        elif args.command.endswith('-apply'):
            result = apply(root, json.load(sys.stdin)['plan'])
        else:
            result = inspect(root)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'error', 'message': str(error), 'diagnostic': 'Aucun registre ni résultat supprimé. Si une opération est interrompue, inspectez et reprenez son plan conservé.'}, ensure_ascii=False))
        return 1
