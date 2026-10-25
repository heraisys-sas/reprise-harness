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
import subprocess
import time
import tomllib
import uuid

import claude_integration as claude
import harness_integration as harness
import scheduler_service as scheduler

COMPONENTS = ('app', 'scheduler', 'harness', 'claude')


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


def logs(state):
    result = []
    for p in (Path(state) / 'service-error.log', Path(state).parent / 'quick-state/quick-apply.log'):
        p = safe(p)
        result.append({'path': str(p), 'size': p.stat().st_size if p.is_file() else 0})
    return result


def plan(source, destination, home, state, components=('app',), profiles=(), activate=False,
         relaunch=False, ident=None):
    source, destination, home, state = map(safe, (source, destination, home, state))
    selected = list(components)
    if not selected or len(selected) != len(set(selected)) or set(selected) - set(COMPONENTS):
        raise ValueError('Composants explicites et uniques requis.')
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Bundle source et destination doivent être disjoints.')
    identity = bundle(source)
    helpers = source / 'Contents/Resources/helpers'
    nested = {}
    with settings(state):
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
                    codex_workspace_write=old.get('codex_workspace_write', False)))
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
             'components': selected, 'profiles': list(profiles), 'activate': activate, 'relaunch': relaunch,
             'bundle': identity, 'before_app': fingerprint(inventory(destination)), 'nested': nested,
             'log_offsets': logs(state),
             'manual_actions': ['Codex : mise à jour native séparée (#30). Aucun client ouvert n’est rechargé.',
                                'claude-desktopd : arrêt/remplacement authentifié séparé (#25/#27).',
                                'Fermer Reprise avant un remplacement du bundle.']}
    value['fingerprint'] = fingerprint(value)
    return value


def app_running():
    result = subprocess.run(['/usr/bin/pgrep', '-x', 'Reprise'], capture_output=True, timeout=5)
    if result.returncode not in (0, 1):
        raise ValueError('État du processus Reprise inconnu ; aucun remplacement.')
    return result.returncode == 0


def save(folder, receipt):
    harness.atomic(folder / 'receipt.json', harness.encoded(receipt))


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
    folder = operation(value)
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    with harness.locked(folder.parent):
        file = folder / 'receipt.json'
        if file.exists():
            receipt = json.loads(file.read_text())
            if receipt['plan'] != value:
                raise ValueError('Autre opération enregistrée ; ne pas remplacer le reçu.')
            if receipt['status'] == 'cleaned':
                raise ValueError('Opération nettoyée ; préparer un nouveau plan.')
        else:
            current = plan(value['source'], value['destination'], value['home'], value['state'],
                           value['components'], value['profiles'], value['activate'], value['relaunch'], value['id'])
            if current != value:
                raise ValueError('Plan périmé ; refaire la prévisualisation.')
            receipt = {'format': 1, 'plan': value, 'status': 'applying', 'steps': [], 'backups': [], 'started_at': time.time()}
            save(folder, receipt)
        if bundle(value['source']) != value['bundle']:
            raise ValueError('Bundle source changé ; aucune substitution.')
        helpers = Path(value['source']) / 'Contents/Resources/helpers'
        with settings(value['state']):
            for name in value['components']:
                if name in receipt['steps']:
                    if name == 'app' and bundle_if_valid(Path(value['destination'])) != value['bundle']:
                        raise ValueError('Application déjà publiée modifiée ; reprise suspendue.')
                    if name != 'app':
                        previous = value['nested'][name] if name == 'harness' else [value['nested'][name]]
                        for p in previous:
                            if p.get('changes') and not all(harness.file_hash(Path(c['path'])) == c['after_sha256'] for c in p['changes']):
                                raise ValueError('Intégration déjà publiée modifiée ; reprise suspendue.')
                            if name == 'scheduler' and harness.file_hash(Path(p['plist'])) != harness.digest(p['content'].encode()):
                                raise ValueError('Scheduler déjà publié modifié ; reprise suspendue.')
                    continue
                if name == 'app':
                    install_app(value, folder, receipt)
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
            if name == 'harness':
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
        result['codex'] = {'configured': 'reprise_codex' in servers,
                           'update_supported': False, 'followup': '#30', 'client_loaded': 'unknown'}
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
            p.add_argument('--activate-scheduler', action='store_true')
            p.add_argument('--relaunch-app', action='store_true')
    sub.add_parser('apply').add_argument('--plan', required=True)
    for name in ('verify', 'cleanup'):
        sub.add_parser(name).add_argument('--receipt', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command in ('inspect','plan'):
            state = args.state or str(Path(args.home) / 'Library/Application Support/Reprise/state')
            result = inspect(args.app,args.home,state) if args.command == 'inspect' else {'plan': plan(
                args.bundle,args.app,args.home,state,args.components.split(','),args.profile,args.activate_scheduler,args.relaunch_app)}
        elif args.command == 'apply':
            result = apply(json.loads(Path(args.plan).read_text())['plan'])
        else:
            result = (verify if args.command == 'verify' else cleanup)(args.receipt)
        print(json.dumps(result,ensure_ascii=False))
        return 1 if result.get('ok') is False else 0
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        print(json.dumps({'status':'refused','message':str(error),'recovery':'Conserver le reçu et les sauvegardes ; aucun arrêt forcé ni restauration aveugle.'},ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
