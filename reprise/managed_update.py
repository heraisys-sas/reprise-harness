"""Local, explicit bundle updates composing Reprise's existing managed adapters."""
# This module is also shipped inside immutable bundles: never create bytecode there.
import sys
sys.dont_write_bytecode = True

import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import time
import tomllib
import uuid

import claude_integration as claude
import codex_config_update as codex_config
import engines
import harness_integration as harness
import scheduler_service as scheduler

COMPONENTS = ('app', 'scheduler', 'harness', 'claude', 'codex')


def safe(value):
    path = Path(value).expanduser().absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Chemin symbolique ou traversée refusé : ' + str(path))
    return path


def fingerprint(value):
    return harness.digest(harness.encoded(value).encode())


def inventory(path):
    path = safe(path)
    if not path.exists():
        return None
    if path.is_file():
        return {'file': harness.file_hash(path)}
    return harness.tree_files(path)


def bundle(app):
    app = safe(app)
    evidence = json.loads((app / 'Contents/Resources/build-evidence.json').read_text())
    if evidence.get('format') != 1:
        raise ValueError('Preuve de build incompatible.')
    actual = {}
    for p in sorted(app.rglob('*')):
        if p.is_symlink():
            if not p.resolve().is_relative_to(app.resolve()):
                raise ValueError('Lien sortant du bundle.')
            actual[str(p.relative_to(app))] = {'link': os.readlink(p)}
        elif p.is_file():
            actual[str(p.relative_to(app))] = {'sha256': harness.digest(p.read_bytes()),
                                              'executable': bool(p.stat().st_mode & 0o111)}
    actual.pop('Contents/Resources/build-evidence.json', None)
    if actual != evidence.get('bundle'):
        raise ValueError('Bundle modifié ou incomplet ; reconstruire.')
    helpers = app / 'Contents/Resources/helpers'
    _, _, identity = harness.bundle_info(helpers)
    return {'sha256': fingerprint(actual), 'release_identity': identity,
            'version': (helpers / 'VERSION').read_text().strip(),
            'git_commit': evidence.get('git_commit')}


@contextmanager
def settings(state, path=None):
    old_state, old_path = os.environ.get('AGENT_RESUME_STATE'), os.environ.get('PATH')
    default_state = Path.home() / 'Library/Application Support/Reprise/state'
    if Path(state).absolute() == default_state.absolute():
        # An explicit default override changes quick-state from a sibling to a
        # child in app_paths. Keep standalone layout when selecting that state.
        os.environ.pop('AGENT_RESUME_STATE', None)
    else:
        os.environ['AGENT_RESUME_STATE'] = str(state)
    if path is not None:
        os.environ['PATH'] = path
    try:
        yield
    finally:
        for key, value in (('AGENT_RESUME_STATE', old_state), ('PATH', old_path)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def operation(plan):
    ident = str(uuid.UUID(plan['id']))
    if ident != plan['id']:
        raise ValueError('Identifiant d’opération non canonique.')
    return safe(Path(plan['state']).parent / 'maintenance/updates' / ident)


def validate(plan):
    if plan.get('format') != 1 or plan.get('fingerprint') != fingerprint({k:v for k,v in plan.items() if k != 'fingerprint'}):
        raise ValueError('Plan incompatible ou altéré.')
    operation(plan)
    if 'app_backup_policy' in plan:
        no_backup_scope(plan)


def logs(state):
    result = []
    for p in (Path(state) / 'service-error.log', Path(state).parent / 'quick-state/quick-apply.log'):
        p = safe(p)
        result.append({'path': str(p), 'size': p.stat().st_size if p.is_file() else 0})
    return result


@contextmanager
def codex_client(home):
    program = engines.executable('codex-cli')
    if not program:
        raise ValueError('Exécutable Codex indisponible.')
    client = codex_config.NativeClient(program, safe(home) / '.codex')
    try:
        yield client
    finally:
        client.close()


def codex_plan(root, home, state, names, ident=None):
    root, home, state = map(safe, (root, home, state))
    helpers, _, identity = harness.bundle_info(root)
    base = state.parent / 'integration-releases'
    with codex_client(home) as client:
        return codex_config.plan(client, home / '.codex/config.toml', base, base / identity,
                                 names, source=helpers, ident=ident)


def codex_scope(value, root, home, state, *, check_source=True):
    expected_base = safe(state).parent / 'integration-releases'
    if (safe(value['config']) != safe(home) / '.codex/config.toml'
            or safe(value['release_base']) != expected_base
            or (check_source and safe(value['source']) != safe(root))):
        raise ValueError('Plan Codex hors de l’installation sélectionnée.')


def codex_apply(value, root, home, state):
    codex_scope(value, root, home, state)
    operations = safe(state).parent / 'maintenance/codex-config'
    # A recorded operation can be reconciled, but an intent without a receipt
    # remains indeterminate and is never sent again.
    ident = str(uuid.UUID(value['id']))
    existing = operations / ident / 'receipt.json'
    with codex_client(home) as client:
        if existing.is_file():
            recorded_plan, status = codex_operation_read(state, ident)
            if status != 'recorded':
                raise ValueError('Durabilité du reçu Codex non confirmée ; réconciliation requise.')
            recorded = json.loads(existing.read_text())
            if (recorded_plan != value or not codex_config.verify(client, value)['ok']):
                raise ValueError('Reçu Codex divergent ; réconciliation requise.')
            return dict(recorded, operation=str(existing.parent))
        return codex_config.apply(client, value, operations)


def codex_verify(value, root, home, state, *, check_source=True):
    codex_scope(value, root, home, state, check_source=check_source)
    with codex_client(home) as client:
        return codex_config.verify(client, value)


def codex_operation(state, ident):
    ident = str(uuid.UUID(ident))
    return safe(state).parent / 'maintenance/codex-config' / ident


def codex_operation_read(state, ident):
    folder = codex_operation(state, ident)
    value = json.loads((folder / 'plan.json').read_text())
    if not isinstance(value, dict):
        raise ValueError('Plan durable Codex invalide.')
    fingerprint = value.get('fingerprint')
    if (value.get('id') != folder.name or fingerprint != codex_config._digest(
            codex_config._json({k: v for k, v in value.items() if k != 'fingerprint'}))):
        raise ValueError('Plan durable Codex altéré.')
    intent = json.loads((folder / 'dispatch-intent.json').read_text())
    if not isinstance(intent, dict) or intent.get('status') != 'indeterminate' or intent.get('expectedVersion') != value.get('version'):
        raise ValueError('Intention durable Codex altérée.')
    receipt = folder / 'receipt.json'
    status = 'indeterminate'
    if receipt.is_file():
        try:
            recorded = json.loads(receipt.read_text())
        except (OSError, ValueError):
            return value, 'indeterminate'
        if not isinstance(recorded, dict) or recorded.get('plan_fingerprint') != fingerprint or recorded.get('status') != 'written':
            return value, 'indeterminate'
        try:
            fd = os.open(receipt, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            codex_config._sync_dir(folder)
        except OSError:
            # A visible receipt is not a durable receipt until both syncs succeed.
            return value, 'indeterminate'
        status = 'recorded'
    return value, status


def codex_operations(state):
    base = safe(state).parent / 'maintenance/codex-config'
    if not base.is_dir():
        return []
    result = []
    for folder in sorted(base.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
        if folder.is_symlink() or not folder.is_dir():
            continue
        try:
            _, status = codex_operation_read(state, folder.name)
        except (ValueError, OSError, KeyError, TypeError):
            continue
        result.append({'id': folder.name, 'status': status})
    return result


def codex_verify_operation(root, home, state, ident):
    value, status = codex_operation_read(state, ident)
    try:
        result = codex_verify(value, root, home, state, check_source=False)
    except (OSError, ValueError, KeyError, TypeError, EOFError, TimeoutError, subprocess.SubprocessError):
        result = {'ok': False, 'configuration_written': None, 'client_loaded': 'unknown',
                  'diagnostic': 'Vérification de la configuration ou de la release indisponible.'}
    return dict(result, operation_status=status, id=value['id'],
                evidence='current_configuration_only')


def codex_cli(root, argv):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('codex-integration-inspect')
    p = sub.add_parser('codex-integration-plan')
    p.add_argument('--entry', action='append', required=True, choices=tuple(codex_config.ENTRIES))
    sub.add_parser('codex-integration-apply')
    sub.add_parser('codex-integration-verify')
    p = sub.add_parser('codex-integration-verify-operation')
    p.add_argument('--id', required=True)
    args = parser.parse_args(argv)
    home = Path.home()
    value = None
    state = None
    try:
        from app_paths import state_directory
        state = state_directory(root)
        if args.action == 'codex-integration-inspect':
            config = home / '.codex/config.toml'
            servers = tomllib.loads(config.read_text()).get('mcp_servers', {}) if config.is_file() else {}
            result = {'entries': [{'name': name, 'configured': name in servers} for name in codex_config.ENTRIES],
                      'operations': codex_operations(state), 'client_loaded': 'unknown'}
        elif args.action == 'codex-integration-plan':
            result = {'plan': codex_plan(root, home, state, args.entry), 'client_loaded': 'unknown'}
        elif args.action == 'codex-integration-verify-operation':
            result = codex_verify_operation(root, home, state, args.id)
        else:
            value = json.load(sys.stdin)['plan']
            if args.action == 'codex-integration-apply':
                result = codex_apply(value, root, home, state)
            else:
                result = codex_verify(value, root, home, state)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get('ok') is False else 0
    except (OSError, ValueError, KeyError, TypeError, EOFError, TimeoutError, subprocess.SubprocessError) as error:
        status = 'refused'
        if args.action == 'codex-integration-apply' and value and state:
            try:
                _, recorded = codex_operation_read(state, value['id'])
                # An authenticated intent means an attempted operation may have
                # reached the native server, even if cleanup subsequently failed.
                status = 'indeterminate'
            except (OSError, ValueError, KeyError, TypeError):
                pass
        print(json.dumps({'status': status, 'message': str(error), 'client_loaded': 'unknown'}, ensure_ascii=False))
        return 1


def plan(source, destination, home, state, components=('app',), profiles=(), activate=False,
         relaunch=False, ident=None, codex_entries=(), no_app_backup=False):
    ident = ident or str(uuid.uuid4())
    source, destination, home, state = map(safe, (source, destination, home, state))
    selected = list(components)
    if type(no_app_backup) is not bool or (no_app_backup and (selected != ['app'] or profiles or activate or codex_entries)):
        raise ValueError('Sans sauvegarde : seul le composant app est permis, sans activation ni intégration.')
    if not selected or len(selected) != len(set(selected)) or set(selected) - set(COMPONENTS):
        raise ValueError('Composants explicites et uniques requis.')
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Bundle source et destination doivent être disjoints.')
    if no_app_backup:
        regular_app_tree(source)
        if destination.exists(): regular_app_tree(destination)
    identity = bundle(source)
    helpers = source / 'Contents/Resources/helpers'
    nested = {}
    with settings(state):
        if 'codex' in selected:
            nested['codex'] = codex_plan(helpers, home, state, list(codex_entries), ident=ident)
        if 'scheduler' in selected:
            _, plist, _ = scheduler.paths(home)
            current = plistlib.loads(plist.read_bytes()) if plist.exists() else {}
            # Preserve the existing environment and interval rather than taking
            # the interactive shell's PATH as the service's new configuration.
            env_path = current.get('EnvironmentVariables', {}).get('PATH', os.environ.get('PATH', '/usr/bin:/bin'))
            with settings(state, env_path):
                nested['scheduler'] = scheduler.plan(helpers, home, activate=activate,
                                                     interval=current.get('StartInterval', 60))
        if 'harness' in selected:
            if not profiles:
                raise ValueError('Sélection explicite d’au moins un profil Harness existant requise.')
            nested['harness'] = []
            for profile in profiles:
                dsh = home / '.dsh'
                _, _, managed = harness.context(dsh, profile)
                old = harness.manifest(managed)
                if not old or old.get('status') != 'installed':
                    raise ValueError('Seules les intégrations Harness déjà gérées sont mises à jour.')
                nested['harness'].append(harness.plan(helpers, dsh, profile, old['project'],
                    delegate='reprise_delegate' in old.get('tools', []), agent_role=old.get('agent_role', 'standard'),
                    output_resume=old.get('output_resume', False), output_resume_project=old.get('output_resume_project'),
                    codex_workspace_write=old.get('codex_workspace_write', False),
                    asi1_effort=old.get('asi1_effort', False)))
        if 'claude' in selected:
            old = claude.metadata(claude.paths(home)[2])
            if not old or old.get('status') != 'installed':
                raise ValueError('Connecteur Claude géré existant requis.')
            from app_paths import state_location
            if (safe(old['state']) != state or safe(old['quick_state']) != safe(state_location(helpers, 'quick-state'))):
                raise ValueError('Le plan changerait un registre Claude existant ; réconciliation explicite requise.')
            nested['claude'] = claude.plan(helpers, home, old['project'])
    value = {'format': 1, 'id': ident or str(uuid.uuid4()), 'source': str(source),
             'destination': str(destination), 'home': str(home), 'state': str(state),
             'components': selected, 'profiles': list(profiles), 'activate': activate, 'relaunch': relaunch, 'codex_entries': list(codex_entries),
             'bundle': identity, 'before_app': fingerprint(inventory(destination)), 'nested': nested,
             'log_offsets': logs(state),
             'manual_actions': ['Codex : aucun client ouvert n’est rechargé.',
                                'claude-desktopd : arrêt/remplacement authentifié séparé (#25/#27).',
                                'Fermer Reprise avant un remplacement du bundle.']}
    if no_app_backup:
        value['app_backup_policy'] = 'none'
        value['before_app_directories'] = fingerprint(app_directories(destination))
        value['new_app_directories'] = fingerprint(app_directories(source))
        value['manual_actions'] += ['Sans sauvegarde : ancien bundle supprimé définitivement ; une interruption peut laisser la destination absente ou partielle. Aucun rollback. Tous les écrivains et Reprise doivent rester arrêtés.']
        no_backup_scope(value)
    value['fingerprint'] = fingerprint(value)
    return value


def app_running():
    result = subprocess.run(['/usr/bin/pgrep', '-x', 'Reprise'], capture_output=True, timeout=5)
    if result.returncode not in (0, 1):
        raise ValueError('État du processus Reprise inconnu ; aucun remplacement.')
    return result.returncode == 0


def save(folder, receipt):
    harness.atomic(folder / 'receipt.json', harness.encoded(receipt))
    if receipt.get('plan', {}).get('app_backup_policy') == 'none':
        sync_update_receipt(folder)


def remember_backup(receipt, path):
    if path and Path(path).exists() and str(path) not in {b['path'] for b in receipt['backups']}:
        receipt['backups'].append({'path': str(safe(path)), 'inventory': fingerprint(inventory(path))})


def install_app(value, folder, receipt):
    source, target = Path(value['source']), safe(value['destination'])
    desired = value['bundle']
    before = value['before_app']
    backup, stage = folder / 'app-before.app', target.parent / ('.Reprise-' + value['id'] + '.app')
    if target.exists() and bundle_if_valid(target) == desired:
        # Covers an interruption after publication, before recording completion.
        remember_backup(receipt, backup)
        return
    if app_running():
        raise ValueError('Reprise est ouvert : le fermer normalement avant application. Aucun arrêt automatique.')
    if target.exists() and fingerprint(inventory(target)) != before:
        raise ValueError('Application destination modifiée depuis le plan.')
    if not target.exists() and before != fingerprint(None) and not backup.exists():
        raise ValueError('Application disparue sans sauvegarde possédée ; réconciliation requise.')
    if backup.exists() and fingerprint(inventory(backup)) != before:
        raise ValueError('Sauvegarde app modifiée ; aucun écrasement.')
    if stage.exists():
        if bundle_if_valid(stage) != desired:
            raise ValueError('Copie de préparation inconnue ou modifiée.')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, stage, symlinks=True)
        if bundle(stage) != desired:
            raise ValueError('Bundle changé pendant la copie.')
    if target.exists():
        if fingerprint(inventory(target)) != before:
            raise ValueError('Application modifiée pendant la préparation ; aucun remplacement.')
        if backup.exists():
            raise ValueError('Ancienne app et sauvegarde simultanées ; réconciliation requise.')
        target.rename(backup)
    remember_backup(receipt, backup)
    save(folder, receipt)  # Backup ownership is durable before publication.
    stage.rename(target)
    if bundle(target) != desired:
        raise ValueError('Intégrité de l’application publiée non confirmée.')


def regular_app_tree(path):
    for entry in path.rglob('*'):
        mode = entry.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode)):
            raise ValueError('Type spécial dans un bundle ; aucun remplacement sans sauvegarde.')


def sync_app_tree(path):
    regular_app_tree(path)
    harness._sync_tree(path)


def app_directories(path):
    if not path.exists():
        return []
    if not path.is_dir():
        raise ValueError('Destination app : dossier requis.')
    return sorted(str(p.relative_to(path)) for p in path.rglob('*') if p.is_dir() and not p.is_symlink())


def no_backup_scope(value):
    if value.get('app_backup_policy') != 'none':
        raise ValueError('Politique de sauvegarde app inconnue.')
    if (value['components'] != ['app'] or value['profiles'] or value['activate']
            or value.get('codex_entries') or value['nested']):
        raise ValueError('Sans sauvegarde : seul le composant app est permis, sans activation ni intégration.')
    source, target, state = map(safe, (value['source'], value['destination'], value['state']))
    folder = operation(value)
    stage = target.parent / ('.Reprise-' + value['id'] + '.app')
    if target.suffix != '.app' or (target.exists() and not target.is_dir()):
        raise ValueError('Sans sauvegarde : destination .app dédiée requise.')
    if any(path == root or root in path.parents for path in (state, folder, source) for root in (target, stage)):
        raise ValueError('État, reçu et source doivent rester hors des bundles remplacés.')
    if source == stage or source in stage.parents or stage in source.parents:
        raise ValueError('Source et stage doivent être disjoints.')


def sync_update_receipt(folder):
    harness._sync_file(folder / 'receipt.json')
    # Persist the UUID directory and its maintenance/updates ancestors as well.
    for path in (folder, folder.parent, folder.parent.parent, folder.parent.parent.parent):
        harness._sync_directory(path)


def _remaining_owned_app(target, owned, directories):
    if target.exists(): regular_app_tree(target)
    current = inventory(target)
    if current is None:
        return
    if not isinstance(current, dict) or any(key not in owned or owned[key] != entry for key, entry in current.items()):
        raise ValueError('Ancien bundle partiellement supprimé mais divergent ; aucune suppression supplémentaire.')
    if not set(app_directories(target)).issubset(directories):
        raise ValueError('Répertoire nouveau dans l’ancien bundle ; suppression suspendue.')


@contextmanager
def owned_parent(root_fd, relative):
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in ('.', '..') for part in parts):
        raise ValueError('Chemin de l’inventaire hors bundle.')
    descriptor = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor); descriptor = next_descriptor
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def _remove_owned_app(target, owned, directories):
    _remaining_owned_app(target, owned, directories)
    if not target.exists():
        return
    root_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Never reinterpret a newly read value as ownership. Only receipt paths and
        # expected digests authorize deletion; new entries are not recursively removed.
        for relative, expected in sorted(owned.items(), reverse=True):
            try:
                with owned_parent(root_fd, relative) as (parent, name):
                    try:
                        entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    if isinstance(expected, dict) and set(expected) == {'symlink'}:
                        if not stat.S_ISLNK(entry.st_mode) or os.readlink(name, dir_fd=parent) != expected['symlink']:
                            raise ValueError('Lien de l’ancien bundle modifié ; suppression suspendue.')
                    else:
                        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                        try:
                            observed = os.fstat(descriptor)
                            if not stat.S_ISREG(observed.st_mode):
                                raise ValueError('Type de fichier de l’ancien bundle modifié.')
                            digest = hashlib.sha256()
                            while chunk := os.read(descriptor, 1024 * 1024): digest.update(chunk)
                            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                            if digest.hexdigest() != expected or (observed.st_dev, observed.st_ino) != (current.st_dev, current.st_ino):
                                raise ValueError('Fichier de l’ancien bundle modifié ; suppression suspendue.')
                        finally:
                            os.close(descriptor)
                    os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                # A previous interrupted deletion may already have removed this parent.
                continue
        for relative in sorted(directories, key=lambda p: (len(Path(p).parts), p), reverse=True):
            try:
                with owned_parent(root_fd, relative) as (parent, name): os.rmdir(name, dir_fd=parent)
            except FileNotFoundError:
                continue
        current = target.stat(follow_symlinks=False)
        opened = os.fstat(root_fd)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('Racine app remplacée ; suppression suspendue.')
        target.rmdir()  # Refuses every unowned child, including an added empty directory.
    finally:
        os.close(root_fd)
    harness._sync_directory(target.parent)


def install_app_without_backup(value, folder, receipt):
    no_backup_scope(value)
    if receipt.get('backups'):
        raise ValueError('Reçu sans sauvegarde incohérent ; aucune suppression.')
    source, target = Path(value['source']), safe(value['destination'])
    desired = value['bundle']
    stage = safe(target.parent / ('.Reprise-' + value['id'] + '.app'))
    progress = receipt.get('app_replacement')
    if progress is not None and (not isinstance(progress, dict) or progress.get('phase') not in ('prepared', 'removing', 'removed', 'published')
            or progress.get('stage') != str(stage)
            or fingerprint(progress.get('old_inventory')) != value['before_app']
            or fingerprint(progress.get('old_directories')) != value['before_app_directories']):
        raise ValueError('Reçu de remplacement altéré ; aucune suppression.')
    if stage.exists(): regular_app_tree(stage)
    if stage.exists() and (bundle_if_valid(stage) != desired or fingerprint(app_directories(stage)) != value['new_app_directories']):
        raise ValueError('Stage inconnu ou modifié ; aucune substitution.')
    if target.exists() and bundle_if_valid(target) == desired:
        if fingerprint(app_directories(target)) != value['new_app_directories']:
            raise ValueError('Répertoires de l’application publiée modifiés ; aucun nettoyage implicite.')
        sync_app_tree(target); harness._sync_directory(target.parent)
        # Retain a surviving stage; never recursively remove an unproven empty directory.
        if progress is not None:
            progress['phase'] = 'published'
            save(folder, receipt)
        return
    if app_running():
        raise ValueError('Reprise est ouvert : le fermer normalement avant application. Aucun arrêt automatique.')
    if progress is None:
        if fingerprint(inventory(target)) != value['before_app'] or fingerprint(app_directories(target)) != value['before_app_directories']:
            raise ValueError('Application destination modifiée depuis le plan.')
        if not stage.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, stage, symlinks=True)
        regular_app_tree(stage)
        if bundle(stage) != desired or fingerprint(app_directories(stage)) != value['new_app_directories']:
            raise ValueError('Bundle changé pendant la copie.')
        sync_app_tree(stage); harness._sync_directory(stage.parent)
        owned = inventory(target)
        directories = app_directories(target)
        if fingerprint(owned) != value['before_app'] or fingerprint(directories) != value['before_app_directories']:
            raise ValueError('Application modifiée pendant la préparation ; aucun remplacement.')
        progress = {'phase': 'prepared', 'stage': str(stage), 'old_inventory': owned,
                    'old_directories': directories}
        receipt['app_replacement'] = progress
        save(folder, receipt)  # Durable intent and verified stage precede the first unlink.
    if (not isinstance(progress, dict) or progress.get('phase') not in ('prepared', 'removing', 'removed', 'published')
            or progress.get('stage') != str(stage)
            or fingerprint(progress.get('old_inventory')) != value['before_app']
            or fingerprint(progress.get('old_directories')) != value['before_app_directories']):
        raise ValueError('Reçu de remplacement altéré ; aucune suppression.')
    if progress['old_inventory'] is None and target.exists():
        raise ValueError('Destination réapparue après plan sans ancienne app ; aucune suppression.')
    if progress['phase'] == 'removed' and target.exists():
        raise ValueError('Destination réapparue après suppression ; aucun rejeu.')
    if progress['phase'] == 'published':
        raise ValueError('Application déjà publiée absente ou divergente ; aucun rejeu.')
    if not stage.is_dir() or bundle(stage) != desired or fingerprint(app_directories(stage)) != value['new_app_directories']:
        raise ValueError('Stage vérifié indisponible ; cible possiblement absente, aucun rollback.')
    sync_app_tree(stage); harness._sync_directory(stage.parent)
    if progress['phase'] == 'prepared' and fingerprint(inventory(target)) != value['before_app']:
        raise ValueError('Destination modifiée avant suppression ; aucune adoption.')
    _remaining_owned_app(target, progress['old_inventory'] or {}, progress['old_directories'])
    if app_running():
        raise ValueError('Reprise est ouvert : suppression/publication suspendue, aucun arrêt forcé.')
    if progress['phase'] == 'prepared':
        progress['phase'] = 'removing'; save(folder, receipt)
    _remove_owned_app(target, progress['old_inventory'] or {}, progress['old_directories'])
    progress['phase'] = 'removed'; save(folder, receipt)
    if app_running():
        raise ValueError('Reprise est ouvert : publication suspendue ; ancien bundle absent, aucun rollback.')
    if target.exists():
        raise ValueError('Destination réapparue ; aucune publication par écrasement.')
    stage.rename(target)
    harness._sync_directory(target.parent)
    if bundle(target) != desired:
        raise ValueError('Intégrité de l’application publiée non confirmée.')
    sync_app_tree(target); harness._sync_directory(target.parent)
    progress['phase'] = 'published'; save(folder, receipt)


def bundle_if_valid(path):
    try:
        return bundle(path)
    except (OSError, ValueError, KeyError):
        return None


def component_complete(name, nested):
    if name == 'scheduler':
        plist = Path(nested['plist'])
        settings_value = plistlib.loads(nested['content'].encode())
        journal = scheduler.read(scheduler.paths(nested['home'])[2] / 'operation.json') or {}
        loaded = scheduler.launch(['print', scheduler.target() + '/' + scheduler.LABEL])
        return (journal.get('status') == 'completed' and journal.get('plan') == nested
                and harness.file_hash(plist) == harness.digest(nested['content'].encode())
                and (scheduler.loaded_matches(loaded, settings_value) if nested['activate'] else loaded.returncode != 0))
    if name == 'harness':
        _, _, managed = harness.context(nested['home'], nested['profile'])
        journal = harness.read_journal(managed / 'operation.json')
        if journal.get('status') != 'completed':
            return False
    changes = nested.get('changes', [])
    # An empty change list still needs the adapter to confirm its ownership.
    return bool(changes) and all(harness.file_hash(Path(c['path'])) == c['after_sha256'] for c in changes)


def apply(value):
    validate(value)
    if value.get('app_backup_policy') == 'none':
        target = Path(value['destination']).resolve()
        dependencies = [sys.executable, sys.prefix, sys.base_prefix, __file__, os.getcwd()]
        dependencies += [str(path) for path in sys.path if path]
        if any(Path(path).resolve().is_relative_to(target) for path in dependencies):
            raise ValueError('Exécuter l’updater et son Python depuis un bundle/runtime indépendant de la cible supprimée.')
    folder = operation(value)
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    with harness.locked(folder.parent):
        file = folder / 'receipt.json'
        if value.get('app_backup_policy') == 'none': safe(file)
        if file.exists():
            receipt = json.loads(file.read_text())
            if value.get('app_backup_policy') == 'none':
                sync_update_receipt(folder)
                if receipt.get('backups'):
                    raise ValueError('Reçu sans sauvegarde incohérent.')
            if receipt['plan'] != value:
                raise ValueError('Autre opération enregistrée ; ne pas remplacer le reçu.')
            if receipt['status'] == 'cleaned':
                raise ValueError('Opération nettoyée ; préparer un nouveau plan.')
        else:
            current = plan(value['source'], value['destination'], value['home'], value['state'],
                           value['components'], value['profiles'], value['activate'], value['relaunch'], value['id'], value.get('codex_entries', ()), no_app_backup=value.get('app_backup_policy') == 'none')
            if current != value:
                raise ValueError('Plan périmé ; refaire la prévisualisation.')
            receipt = {'format': 1, 'plan': value, 'status': 'applying', 'steps': [], 'backups': [], 'started_at': time.time()}
            save(folder, receipt)
        staged_recovery = value.get('app_backup_policy') == 'none' and (receipt.get('app_replacement') is not None or 'app' in receipt['steps'])
        if not staged_recovery and bundle(value['source']) != value['bundle']:
            raise ValueError('Bundle source changé ; aucune substitution.')
        helpers = Path(value['source']) / 'Contents/Resources/helpers'
        with settings(value['state']):
            for name in value['components']:
                if name in receipt['steps']:
                    if name == 'app' and bundle_if_valid(Path(value['destination'])) != value['bundle']:
                        raise ValueError('Application déjà publiée modifiée ; reprise suspendue.')
                    if name == 'app' and value.get('app_backup_policy') == 'none':
                        if fingerprint(app_directories(Path(value['destination']))) != value['new_app_directories']:
                            raise ValueError('Répertoires de l’application déjà publiée modifiés ; reprise suspendue.')
                        sync_app_tree(Path(value['destination'])); harness._sync_directory(Path(value['destination']).parent)
                    if name == 'codex' and not codex_verify(value['nested']['codex'], helpers, value['home'], value['state'])['ok']:
                        raise ValueError('Configuration Codex publiée modifiée ; reprise suspendue.')
                    if name != 'app':
                        previous = value['nested'][name] if name == 'harness' else [value['nested'][name]]
                        for p in previous:
                            if p.get('changes') and not all(harness.file_hash(Path(c['path'])) == c['after_sha256'] for c in p['changes']):
                                raise ValueError('Intégration déjà publiée modifiée ; reprise suspendue.')
                            if name == 'scheduler' and harness.file_hash(Path(p['plist'])) != harness.digest(p['content'].encode()):
                                raise ValueError('Scheduler déjà publié modifié ; reprise suspendue.')
                    continue
                if name == 'app':
                    if value.get('app_backup_policy') == 'none':
                        install_app_without_backup(value, folder, receipt)
                    else:
                        install_app(value, folder, receipt)
                elif name == 'codex':
                    result = codex_apply(value['nested']['codex'], helpers, value['home'], value['state'])
                    receipt['codex_operation'] = result.get('operation')
                    save(folder, receipt)
                else:
                    values = value['nested'][name] if name == 'harness' else [value['nested'][name]]
                    for nested in values:
                        if component_complete(name, nested):
                            # Reconcile adapter completion if the parent lost its reply.
                            if name == 'harness':
                                _, _, managed = harness.context(nested['home'], nested['profile'])
                                journal = harness.read_journal(managed / 'operation.json')
                                remember_backup(receipt, journal.get('backup'))
                            elif name == 'scheduler':
                                journal = scheduler.read(scheduler.paths(nested['home'])[2] / 'operation.json') or {}
                                remember_backup(receipt, journal.get('backup'))
                            continue
                        if name == 'scheduler':
                            path = plistlib.loads(nested['content'].encode())['EnvironmentVariables']['PATH']
                            with settings(value['state'], path):
                                result = scheduler.apply(helpers, nested)
                        else:
                            result = (harness if name == 'harness' else claude).apply(helpers, nested)
                        remember_backup(receipt, result.get('backup'))
                        save(folder, receipt)
                receipt['steps'].append(name)
                receipt.pop('verification', None)
                save(folder, receipt)
        receipt['status'] = 'applied'
        if value['relaunch'] and 'app' in value['components'] and not receipt.get('launch_requested'):
            # An uncertain open request is never automatically replayed.
            receipt['launch_requested'] = True
            save(folder, receipt)
            subprocess.run(['/usr/bin/open', '-a', value['destination']], check=True, timeout=15)
        save(folder, receipt)
    return {'status': receipt['status'], 'receipt': str(file), 'client_loaded': 'unknown'}


def observe_logs(value):
    result = []
    for entry in value['log_offsets']:
        p = safe(entry['path'])
        size = p.stat().st_size if p.exists() else 0
        start = entry['size']
        if size < start or size - start > 65536:
            result.append({'path': str(p), 'bounded': False, 'reason': 'rotation ou plus de 64 Kio ; examen manuel requis'})
            continue
        text = ''
        if p.exists():
            with p.open('rb') as f:
                f.seek(start)
                text = f.read(65536).decode(errors='replace')
        result.append({'path': str(p), 'bounded': True, 'new_bytes': size-start,
                       'errors': sum(text.count(s) for s in ('Traceback', 'PermissionError', 'ModuleNotFoundError', 'ERROR'))})
    return result


def verify(file, _already_locked=False):
    file = safe(file)
    receipt = json.loads(file.read_text());value = receipt['plan'];validate(value)
    if file != operation(value) / 'receipt.json' or receipt.get('status') not in ('applied', 'verified', 'cleaned'):
        raise ValueError('Reçu incomplet ou hors de son opération.')
    folder = operation(value)
    with (nullcontext() if _already_locked else harness.locked(folder.parent)), settings(value['state']):
        # Re-read after acquiring the operation lock.
        receipt = json.loads(file.read_text())
        if value.get('app_backup_policy') == 'none':
            sync_update_receipt(folder)
            if receipt.get('backups'):
                raise ValueError('Reçu sans sauvegarde incohérent.')
            if fingerprint(app_directories(Path(value['destination']))) != value['new_app_directories']:
                raise ValueError('Répertoires de l’application publiée modifiés.')
            sync_app_tree(Path(value['destination'])); harness._sync_directory(Path(value['destination']).parent)
        if receipt['plan'] != value:
            raise ValueError('Reçu modifié pendant vérification.')
        results = {};ok = True
        installed = Path(value['destination'])
        helpers = installed / 'Contents/Resources/helpers'
        if 'app' in value['components']:
            results['app'] = bundle(installed) == value['bundle'];ok &= results['app']
        else:
            release = Path(value['state']).parent / 'integration-releases' / value['bundle']['release_identity']
            helpers = release / 'Contents/Resources/helpers'
        for name in value['components']:
            if name == 'codex':
                results[name] = codex_verify(value['nested']['codex'], Path(value['source']) / 'Contents/Resources/helpers', value['home'], value['state'])
                ok &= results[name]['ok']
            elif name == 'harness':
                results[name] = [harness.verify(p['home'],p['profile'],root=helpers) for p in value['nested'][name]]
                ok &= all(not r['update_required'] for r in results[name])
            elif name == 'claude':
                results[name] = claude.verify(helpers, value['home'])
                configured = claude.metadata(claude.paths(value['home'])[2])
                ok &= (configured['release_identity'] == value['bundle']['release_identity']
                       and results[name].get('daemon', {}).get('binding') != 'mismatch')
            elif name == 'scheduler':
                results[name] = scheduler.inspect(helpers, value['home'])
                results[name].pop('loaded_evidence', None)
                observed = results[name];expected = value['nested'][name]
                ok &= (observed['runtime_integrity'] and observed['configured_current']
                       and (observed['loaded_current'] and observed['last_error'] is None
                            and (observed['last_tick'] or 0) >= receipt['started_at']
                            if expected['activate'] else not observed['loaded']))
        observed_logs = observe_logs(value)
        ok &= all(r['bounded'] and not r.get('errors', 0) for r in observed_logs)
        receipt['verification'] = {'ok': bool(ok), 'checked_at': time.time(), 'results': results,
                                   'logs': observed_logs, 'client_loaded': 'unknown'}
        if receipt['status'] != 'cleaned':
            receipt['status'] = 'verified' if ok else 'applied'
        save(folder, receipt)
    return {'status': receipt['status'], **receipt['verification']}


def cleanup(file):
    file = safe(file);receipt = json.loads(file.read_text());validate(receipt['plan'])
    folder = operation(receipt['plan'])
    if file != folder / 'receipt.json':
        raise ValueError('Reçu hors de son opération.')
    with harness.locked(folder.parent):
        # Verification and removal share the global updater lock.
        result = verify(file, _already_locked=True)
        if not result['ok']:
            raise ValueError('Vérification ou logs non concluants ; sauvegardes conservées.')
        receipt = json.loads(file.read_text())
        if receipt['status'] == 'cleaned':
            return {'status': 'cleaned', 'removed': []}
        removed = []
        allowed = [folder / 'app-before.app']
        value = receipt['plan'];home = Path(value['home'])
        roots = [home / 'Library/Application Support/Reprise/claude-integration/backups',
                 home / 'Library/Application Support/Reprise/scheduler-service']
        roots += [Path(p['home']) / 'profiles' / p['profile'] / '.reprise/backups' for p in value['nested'].get('harness', [])]
        for entry in receipt['backups']:
            path = safe(entry['path'])
            parent_allowed = any(path.parent == r for r in roots)
            if path.parent == roots[1]:
                parent_allowed = path.name.startswith('backup-') and path.suffix == '.plist'
            if not (path in allowed or parent_allowed):
                raise ValueError('Sauvegarde hors périmètre de l’opération.')
            if path.exists() and fingerprint(inventory(path)) != entry['inventory']:
                raise ValueError('Sauvegarde modifiée ; nettoyage refusé.')
        for entry in receipt['backups']:
            path = safe(entry['path'])
            if path.exists():
                shutil.rmtree(path) if path.is_dir() else path.unlink()
                removed.append(str(path))
        receipt['status'] = 'cleaned';receipt['removed_backups'] = removed;save(folder, receipt)
    return {'status': 'cleaned', 'removed': removed, 'receipt': str(file)}


def inspect(destination, home, state):
    home, destination, state = map(safe, (home, destination, state))
    result = {'app': bundle_if_valid(destination), 'app_present': destination.exists(),
              'state': str(state), 'harness': harness.inspect(home / '.dsh'), 'client_loaded': 'unknown'}
    config = home / '.codex/config.toml'
    if config.exists():
        servers = tomllib.loads(config.read_text()).get('mcp_servers', {})
        result['codex'] = {'entries': [{'name': name, 'configured': name in servers} for name in codex_config.ENTRIES],
                           'update_supported': True, 'client_loaded': 'unknown'}
    with settings(state):
        result['claude'] = claude.inspect(destination / 'Contents/Resources/helpers', home)
        result['scheduler'] = scheduler.inspect(destination / 'Contents/Resources/helpers', home)
        result['scheduler'].pop('loaded_evidence', None)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'plan'):
        p = sub.add_parser(name)
        p.add_argument('--app', default='/Applications/Reprise.app')
        p.add_argument('--home', default=str(Path.home()))
        p.add_argument('--state')
        if name == 'plan':
            p.add_argument('--bundle', required=True)
            p.add_argument('--components', default='app', help=','.join(COMPONENTS))
            p.add_argument('--profile', action='append', default=[])
            p.add_argument('--codex-entry', action='append', default=[], choices=tuple(codex_config.ENTRIES))
            p.add_argument('--activate-scheduler', action='store_true')
            p.add_argument('--relaunch-app', action='store_true')
            p.add_argument('--no-app-backup', action='store_true', help='App seule : suppression irréversible de l’ancien bundle, aucun rollback ; état absent/partiel possible après interruption')
    sub.add_parser('apply').add_argument('--plan', required=True)
    for name in ('verify', 'cleanup'):
        sub.add_parser(name).add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command in ('inspect','plan'):
            state = args.state or str(Path(args.home) / 'Library/Application Support/Reprise/state')
            result = inspect(args.app,args.home,state) if args.command == 'inspect' else {'plan': plan(
                args.bundle,args.app,args.home,state,args.components.split(','),args.profile,args.activate_scheduler,args.relaunch_app, codex_entries=args.codex_entry, no_app_backup=args.no_app_backup)}
        elif args.command == 'apply':
            result = apply(json.loads(Path(args.plan).read_text())['plan'])
        else:
            result = (verify if args.command == 'verify' else cleanup)(args.receipt)
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result.get('ok') is False else 0
    except (OSError,ValueError,KeyError,TypeError,EOFError,TimeoutError,subprocess.SubprocessError) as error:
        status = 'refused'
        if args.command == 'apply':
            try:
                pending = json.loads(Path(args.plan).read_text())['plan']
                _, observed = codex_operation_read(pending['state'], pending['id'])
                # A recorded native write can still precede failure of the
                # enclosing operation; never report that as non-admission.
                status = 'indeterminate'
            except (OSError, ValueError, KeyError, TypeError):
                pass
        detail = {'status':status,'message':str(error),'recovery':'Conserver le reçu et les sauvegardes ; aucun arrêt forcé ni restauration aveugle.','client_loaded':'unknown'}
        if args.command == 'apply':
            try:
                pending = json.loads(Path(args.plan).read_text())['plan']
                validate(pending)
                if pending.get('app_backup_policy') == 'none':
                    detail.update(app_backup_policy='none', rollback_available=False,
                        recovery='Conserver le reçu et le stage ; ancienne app possiblement absente ou partielle. Aucune sauvegarde ni rollback. Observer puis reprendre uniquement le même plan.')
                    file = safe(operation(pending) / 'receipt.json')
                    if file.is_file():
                        recorded = json.loads(file.read_text())
                        if recorded.get('plan') == pending:
                            detail['receipt'] = str(file)
                            detail['app_present'] = safe(pending['destination']).exists()
                            detail['app_replacement_phase'] = (recorded.get('app_replacement') or {}).get('phase')
                            if recorded.get('app_replacement'):
                                detail['status'] = 'indeterminate' if recorded.get('launch_requested') else 'interrupted'
            except (OSError, ValueError, KeyError, TypeError):
                pass
        print(json.dumps(detail,ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
