"""Managed Claude Desktop MCP configuration, without starting or reloading Desktop."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from app_paths import state_location
from harness_integration import (atomic, bundle_info, digest, encoded, file_hash,
                                 release_check, stage_release)


def paths(home):
    home = Path(home).expanduser().absolute()
    if home.is_symlink():
        raise ValueError('Dossier utilisateur symbolique refusé.')
    home = home.resolve(strict=True)
    config = home / 'Library/Application Support/Claude/claude_desktop_config.json'
    managed = home / 'Library/Application Support/Reprise/claude-integration'
    for target in (config, managed / 'managed.json', managed / 'lock'):
        safe_path(target, home)
    return home, config, managed


def safe_path(target, home):
    if '..' in target.parts or not target.is_relative_to(home):
        raise ValueError('Destination hors du dossier utilisateur.')
    for candidate in (target, *target.parents):
        if candidate == home.parent:
            break
        if candidate.is_symlink():
            raise ValueError('Destination symbolique refusée.')


def read_config(config):
    file_hash(config)
    value = json.loads(config.read_text()) if config.exists() else {}
    if not isinstance(value, dict) or not isinstance(value.get('mcpServers', {}), dict):
        raise ValueError('Configuration Claude incompatible ; aucune réécriture.')
    return value


def metadata(managed):
    target = managed / 'managed.json'
    file_hash(target)
    if not target.exists():
        return None
    value = json.loads(target.read_text())
    if not isinstance(value, dict) or value.get('format') != 1:
        raise ValueError('Manifeste Claude incompatible.')
    return value


def entry_hash(entry):
    return digest(encoded(entry).encode())


def inspect(root, home=None):
    home, config, managed = paths(home or Path.home())
    value, old = read_config(config), metadata(managed)
    entry = value.get('mcpServers', {}).get('reprise')
    return {'status': 'inspected', 'home': str(home), 'config_exists': config.exists(),
            'configuration_written': entry is not None,
            'managed': bool(old and entry is not None and old.get('entry_sha256') == entry_hash(entry)),
            'project': old.get('project') if old else None,
            'client_loaded': 'unknown', 'message': 'Inspection locale ; aucun client chargé ni redémarré.'}


def desired(root, home, project, replace_probe, action='install'):
    home, config, managed = paths(home)
    project = Path(project).expanduser().resolve(strict=True)
    if not project.is_dir() or type(replace_probe) is not bool:
        raise ValueError('Projet existant et option explicite requis.')
    original = read_config(config)
    old = metadata(managed)
    servers = dict(original.get('mcpServers', {}))
    existing = servers.get('reprise')
    if 'reprise' in servers and (not old or old.get('entry_sha256') != entry_hash(existing)):
        raise ValueError('Entrée reprise personnalisée ou non gérée ; aucun écrasement.')
    if action not in ('install', 'remove'):
        raise ValueError('Action invalide.')
    if action == 'remove':
        if not old or old.get('status') not in ('installed', 'removed'):
            raise ValueError('Aucune intégration Claude gérée à retirer.')
        if old.get('project') != str(project):
            raise ValueError('Projet sélectionné différent du connecteur géré.')
        servers.pop('reprise', None)
        manifest = dict(old, status='removed', entry_sha256=None)
        return home, config, managed, dict(original, mcpServers=servers), manifest, None, None
    replaced_probe = (old or {}).get('replaced_probe')
    probe = servers.get('reprise_quick_probe')
    if probe is not None and replace_probe:
        args = probe.get('args') if isinstance(probe, dict) else None
        script_args = list(args) if isinstance(args, list) else []
        while script_args and script_args[0] in ('-E', '-s', '-B'):
            script_args.pop(0)
        if (len(script_args) != 3 or not all(isinstance(arg, str) for arg in script_args)
                or Path(script_args[0]).name != 'desktop_job_probe.py' or not Path(script_args[0]).is_absolute()
                or script_args[1] != '--state' or not Path(script_args[2]).is_absolute()):
            raise ValueError('Ancien probe non reconnu ; aucun retrait.')
        replaced_probe = {'entry_sha256': entry_hash(probe), 'original_config_sha256': file_hash(config),
                          'recovery': 'Private config.json backup; never automatically restored.'}
        del servers['reprise_quick_probe']
    helpers, runtime, identity = bundle_info(root)
    if not (helpers / 'claude_mcp.py').is_file():
        raise ValueError('Connecteur unifié absent du bundle ; reconstruire Reprise.')
    main_state = state_location(root).absolute()
    quick_state = state_location(root, 'quick-state').absolute()
    release = main_state.parent / 'integration-releases' / identity
    program = release / 'Contents/Resources/runtime/bin/python3'
    entry = {'command': str(program), 'args': ['-E', '-s', '-B', str(release / 'Contents/Resources/helpers/claude_mcp.py'),
             '--project-root', str(project), '--state', str(quick_state), '--expert-state', str(main_state),
             '--socket', str(home / '.codex/app-server-control/app-server-control.sock')]}
    servers['reprise'] = entry
    configured = dict(original, mcpServers=servers)
    manifest = {'format': 1, 'status': 'installed', 'project': str(project),
                'state': str(main_state), 'quick_state': str(quick_state),
                'entry_sha256': entry_hash(entry), 'release': str(release), 'release_identity': identity}
    if replaced_probe is not None:
        manifest['replaced_probe'] = replaced_probe
    return home, config, managed, configured, manifest, helpers, runtime


def plan(root, home, project, replace_probe=False, action='install'):
    home, config, managed, configured, manifest, _, _ = desired(root, home, project, replace_probe, action)
    changes = []
    for target, value in ((config, configured), (managed / 'managed.json', manifest)):
        before, after = file_hash(target), digest(encoded(value).encode())
        if before != after:
            changes.append({'path': str(target), 'before_sha256': before, 'after_sha256': after})
    public = {'format': 1, 'action': action, 'home': str(home), 'project': manifest['project'], 'replace_probe': replace_probe,
              'config_sha256': file_hash(config), 'manifest_sha256': file_hash(managed / 'managed.json'),
              'release': manifest['release'], 'release_identity': manifest['release_identity'], 'changes': changes}
    public['fingerprint'] = digest(encoded(public).encode())
    return public


@contextmanager
def locked(managed, home):
    safe_path(managed / 'lock', home)
    managed.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(managed / 'lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Une installation Claude est déjà active.')
        yield


def apply(root, public):
    if not isinstance(public, dict) or public.get('format') != 1:
        raise ValueError('Plan incompatible.')
    unsigned = {key: value for key, value in public.items() if key != 'fingerprint'}
    if digest(encoded(unsigned).encode()) != public.get('fingerprint'):
        raise ValueError('Plan modifié ; refaire la prévisualisation.')
    home, config, managed = paths(public['home'])
    with locked(managed, home):
        if plan(root, home, public['project'], public['replace_probe'], public['action']) != public:
            raise ValueError('Configuration ou bundle modifié ; refaire le plan.')
        if not public['changes']:
            return {'status': 'completed', 'changed': False, 'client_loaded': 'unknown'}
        _, _, _, configured, manifest, helpers, runtime = desired(root, home, public['project'], public['replace_probe'], public['action'])
        if public['action'] == 'install':
            stage_release(helpers, runtime, Path(public['release']))
            release_check(Path(public['release']))
        backup = managed / 'backups' / str(uuid.uuid4())
        safe_path(backup, home)
        backup.mkdir(parents=True, mode=0o700)
        before = {}
        for name, target in (('config.json', config), ('managed.json', managed / 'managed.json')):
            safe_path(target, home)
            before[target] = target.read_bytes() if target.exists() else None
            if before[target] is not None:
                saved = backup / name
                saved.write_bytes(before[target])
                saved.chmod(0o600)
        after_config = digest(encoded(configured).encode())
        after_manifest = digest(encoded(manifest).encode())
        wrote_config = False
        try:
            # Recheck both files after release staging and backup I/O.
            if file_hash(config) != public['config_sha256'] or file_hash(managed / 'managed.json') != public['manifest_sha256']:
                raise ValueError('Modification concurrente avant publication ; aucune réécriture.')
            wrote_config = True
            atomic(config, encoded(configured))
            if file_hash(config) != after_config or file_hash(managed / 'managed.json') != public['manifest_sha256']:
                raise ValueError('Modification concurrente après publication ; réconcilier les sauvegardes.')
            atomic(managed / 'managed.json', encoded(manifest))
            if file_hash(config) != after_config or file_hash(managed / 'managed.json') != after_manifest:
                raise ValueError('Publication modifiée ; sauvegardes conservées.')
        except BaseException:
            # Restore only our exact publication, never a concurrent user edit.
            target = managed / 'managed.json'
            if file_hash(target) == after_manifest:
                if before[target] is None:
                    target.unlink()
                else:
                    atomic(target, before[target].decode('utf-8'))
            if wrote_config and file_hash(config) == after_config:
                old_config = before[config]
                if old_config is None:
                    config.unlink()
                else:
                    atomic(config, old_config.decode('utf-8'))
            raise
    return {'status': 'completed', 'changed': True, 'backup': str(backup), 'client_loaded': 'unknown',
            'message': 'Connecteur configuré ; aucun démarrage, arrêt ou rechargement de Claude Desktop.'}


def verify(root, home=None):
    home, config, managed = paths(home or Path.home())
    old = metadata(managed)
    entry = read_config(config).get('mcpServers', {}).get('reprise')
    if not old or old.get('status') != 'installed' or entry is None or entry_hash(entry) != old.get('entry_sha256'):
        raise ValueError('Connecteur géré absent ou modifié.')
    selected_state = state_location(root).resolve()
    release = Path(old['release'])
    if (old.get('state') != str(selected_state) or release.parent.resolve() != (selected_state.parent / 'integration-releases').resolve()
            or release.name != old.get('release_identity') or len(release.name) != 64
            or any(c not in '0123456789abcdef' for c in release.name) or release.is_symlink() or release.parent.is_symlink()):
        raise ValueError('Release hors de l’installation sélectionnée.')
    release_check(release)
    command = [str(release / 'Contents/Resources/runtime/bin/python3'), '-E', '-s', '-B',
               str(release / 'Contents/Resources/helpers/claude_mcp.py'), '--project-root', old['project']]
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}]
    with tempfile.TemporaryDirectory(prefix='reprise-claude-check-') as temporary:
        command += ['--state', str(Path(temporary) / 'quick'), '--expert-state', str(Path(temporary) / 'expert'),
                    '--socket', str(home / '.codex/app-server-control/app-server-control.sock')]
        env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'DYLD_')) and k != 'AGENT_RESUME_STATE'}
        response = subprocess.run(command, input='\n'.join(json.dumps(r) for r in requests) + '\n',
                                  capture_output=True, text=True, timeout=20, env=env, check=True)
    messages = [json.loads(line) for line in response.stdout.splitlines()]
    info = next(m['result']['serverInfo'] for m in messages if m.get('id') == 1)
    tools = next(m['result']['tools'] for m in messages if m.get('id') == 2)
    names = sorted(tool['name'] for tool in tools)
    if not {'get_task', 'submit_result', 'reprise_backends', 'reprise_requests', 'reprise_delegate', 'reprise_followup'}.issubset(names):
        raise ValueError('Outils du connecteur unifié incomplets.')
    daemon = {'binding': 'unknown', 'dispatch_supported': 'unknown'}
    client = None
    try:
        from claude_desktop_rpc import Client
        client = Client(None, {'socket': str(home / '.claude-desktop/quick-entry.sock')}, timeout=2)
        client.call('initialize', {})
        observed = client.call('quick_entry/capabilities', {})
        matches = (observed.get('project_root') == old['project'] and
                   observed.get('state_sha256') == hashlib.sha256(str(Path(old['quick_state']).resolve()).encode()).hexdigest())
        daemon = {'binding': 'matched' if matches else 'mismatch',
                  'dispatch_supported': observed.get('dispatch_supported', 'unknown'),
                  'version': observed.get('version')}
    except (OSError, ValueError, TimeoutError):
        pass
    finally:
        if client:
            client.close()
    return {'status': 'verified', 'daemon': daemon, 'bridge_tools': names, 'bridge_version': info['version'],
            'client_loaded': 'unknown', 'inference_executed': False,
            'next_step': 'Recharger Claude lorsque son travail le permet et démarrer le claude-desktopd de cette release ; vérifier le rattachement projet/état.',
            'message': 'Release vérifié par initialize/tools/list ; chargement par Claude Desktop non observé.'}


def main(root, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('claude-integration-inspect', 'claude-integration-plan', 'claude-integration-verify'):
        command = sub.add_parser(name)
        command.add_argument('--home', default=str(Path.home()))
        if name == 'claude-integration-plan':
            command.add_argument('--project', required=True)
            command.add_argument('--replace-probe', action='store_true')
            command.add_argument('--action', choices=('install', 'remove'), default='install')
    sub.add_parser('claude-integration-apply')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command.endswith('-inspect'):
            result = inspect(root, args.home)
        elif args.command.endswith('-plan'):
            result = {'status': 'planned', 'plan': plan(root, args.home, args.project, args.replace_probe, args.action)}
        elif args.command.endswith('-apply'):
            result = apply(root, json.loads(sys.stdin.read(1024 * 1024))['plan'])
        else:
            result = verify(root, args.home)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, StopIteration):
        # Configurations can contain credentials; never echo parser errors or subprocess stderr.
        print(json.dumps({'status': 'refused', 'message': 'Installation refusée ; inspecter la configuration, les préconditions et les sauvegardes.'}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
