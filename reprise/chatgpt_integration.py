"""Managed ChatGPT MCP configuration, without touching ChatGPT itself.

ChatGPT desktop shares the Codex MCP configuration: the `[mcp_servers.<name>]`
tables of `~/.codex/config.toml` (the same file read by Settings → MCP servers).
The integration tool edits only the managed `mcp_servers.reprise` table,
preserving every other byte of the file, and never reloads or restarts ChatGPT.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import uuid

from app_paths import state_location
from harness_integration import (atomic, bundle_info, digest, encoded, file_hash,
                                 release_check, stage_release)

REQUIRED_BRIDGE_TOOLS = {'get_task', 'submit_result', 'get_result',
                         'reprise_backends', 'reprise_delegate', 'reprise_followup'}
ENTRY_NAME = 'reprise'
SECTION_HEADER = f'[mcp_servers.{ENTRY_NAME}]'
RESTART_NOTICE = ('Redémarrez ChatGPT desktop (Réglages → MCP servers → Restart) pour qu’il relise '
                  'config.toml ; Reprise ne redémarre pas ChatGPT à votre place.')


def paths(home=None):
    # The bundle runs under sandboxed/redirected HOME values (ex. ~/.dsh); the
    # ChatGPT desktop app always reads CODEX_HOME's default under the real user
    # home, so default to the actual user home, never the process HOME.
    if home is None:
        home = Path.home()
        if home.name == '.dsh' or str(home).endswith('/.dsh'):
            home = home.parent
    else:
        home = Path(home).expanduser().absolute()
    if home.is_symlink():
        raise ValueError('Dossier utilisateur symbolique refusé.')
    home = home.resolve(strict=True)
    config = home / '.codex/config.toml'
    managed = home / 'Library/Application Support/Reprise/chatgpt-integration'
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


def metadata(managed):
    target = managed / 'managed.json'
    file_hash(target)
    if not target.exists():
        return None
    value = json.loads(target.read_text())
    if not isinstance(value, dict) or value.get('format') != 1:
        raise ValueError('Manifeste ChatGPT incompatible.')
    return value


def entry_hash(entry):
    return digest(encoded(entry).encode())


def read_config(config):
    file_hash(config)
    if not config.exists():
        return {}
    value = tomllib.loads(config.read_text())
    if not isinstance(value, dict) or not isinstance(value.get('mcp_servers', {}), dict):
        raise ValueError('Configuration Codex incompatible ; aucune réécriture.')
    return value


def toml_str(value):
    if not isinstance(value, str):
        raise ValueError('Valeur TOML non textuelle.')
    escaped = value.replace('\\', '\\\\').replace('"', '\\"')
    escaped = escaped.replace('\t', '\\t').replace('\n', '\\n').replace('\r', '\\r')
    for codepoint in escaped:
        if ord(codepoint) < 0x20:
            raise ValueError('Caractère de contrôle non sérialisable en TOML.')
    return '"' + escaped + '"'


def render(raw, entry):
    """Rewrite only the managed table, byte-preserving every other line."""
    text = '' if raw is None else raw.decode('utf-8')
    doc = tomllib.loads(text)
    lines, in_section = text.split('\n'), False
    for index, line in enumerate(lines):
        if line.startswith('['):
            in_section = line == SECTION_HEADER
        if in_section:
            lines[index] = None
    kept = [line for line in lines if line is not None]
    if entry is not None:
        section = [SECTION_HEADER,
                   'command = ' + toml_str(entry['command']),
                   'args = [' + ', '.join(toml_str(arg) for arg in entry['args']) + ']',
                   'enabled = true']
        while kept and kept[-1] == '':
            kept.pop()
        if kept:
            kept.append('')
        kept.extend(section)
    result = '\n'.join(kept)
    if result and not result.endswith('\n'):
        result += '\n'
    after = tomllib.loads(result)
    if ({key: value for key, value in doc.items() if key != 'mcp_servers'}
            != {key: value for key, value in after.items() if key != 'mcp_servers'}):
        raise ValueError('Réécriture refusée : le reste de config.toml serait modifié.')
    if entry is None:
        if ENTRY_NAME in after.get('mcp_servers', {}):
            raise ValueError('Table gérée non retirée ; structure non reconnue.')
    elif after.get('mcp_servers', {}).get(ENTRY_NAME) != entry:
        raise ValueError('Table gérée non reconnue dans config.toml ; aucune réécriture.')
    return result


def inspect(root, home=None):
    home, config, managed = paths(home)
    doc = read_config(config)
    old = metadata(managed)
    entry = doc.get('mcp_servers', {}).get(ENTRY_NAME)
    return {'status': 'inspected', 'home': str(home), 'config_exists': config.exists(),
            'configuration_written': entry is not None,
            'managed': bool(old and old.get('status') == 'installed' and entry is not None
                            and old.get('entry_sha256') == entry_hash(entry)),
            'project': old.get('project') if old else None,
            'client_loaded': 'unknown', 'message': 'Inspection locale ; ChatGPT n’est ni modifié ni redémarré.'}


def desired(root, home, project, action='install'):
    home, config, managed = paths(home)
    project = Path(project).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError('Projet existant requis.')
    if action not in ('install', 'remove'):
        raise ValueError('Action invalide.')
    old = metadata(managed)
    doc = read_config(config)
    existing = doc.get('mcp_servers', {}).get(ENTRY_NAME)
    if existing is not None and (not old or old.get('entry_sha256') != entry_hash(existing)):
        raise ValueError('Entrée mcp_servers.reprise personnalisée ou non gérée ; aucun écrasement.')
    if action == 'remove':
        if not old or old.get('status') not in ('installed', 'removed'):
            raise ValueError('Aucune intégration ChatGPT gérée à retirer.')
        if old.get('project') != str(project):
            raise ValueError('Projet sélectionné différent du connecteur géré.')
        manifest = dict(old, status='removed', entry_sha256=None)
        return home, config, managed, None, manifest, None, None
    helpers, runtime, identity = bundle_info(root)
    if not (helpers / 'chatgpt_mcp.py').is_file():
        raise ValueError('Facade ChatGPT absente du bundle ; reconstruire Reprise.')
    main_state = state_location(root).absolute()
    chatgpt_state = state_location(root, 'chatgpt-state').absolute()
    release = main_state.parent / 'integration-releases' / identity
    entry = {'command': str(release / 'Contents/Resources/runtime/bin/python3'),
             'args': ['-E', '-s', '-B', str(release / 'Contents/Resources/helpers/chatgpt_mcp.py'),
                      '--project-root', str(project), '--state', str(chatgpt_state),
                      '--expert-state', str(main_state)],
             'enabled': True}
    manifest = {'format': 1, 'status': 'installed', 'project': str(project), 'state': str(main_state),
                'chatgpt_state': str(chatgpt_state), 'entry_sha256': entry_hash(entry),
                'release': str(release), 'release_identity': identity}
    return home, config, managed, entry, manifest, helpers, runtime


def plan(root, home, project, action='install'):
    home, config, managed, entry, manifest, _, _ = desired(root, home, project, action)
    raw = config.read_bytes() if config.exists() else None
    rendered = render(raw, entry)
    changes = []
    before, after = file_hash(config), digest(rendered.encode())
    if before != after:
        changes.append({'path': str(config), 'before_sha256': before, 'after_sha256': after})
    manifest_target = managed / 'managed.json'
    before, after = file_hash(manifest_target), digest(encoded(manifest).encode())
    if before != after:
        changes.append({'path': str(manifest_target), 'before_sha256': before, 'after_sha256': after})
    public = {'format': 1, 'action': action, 'home': str(home), 'project': manifest['project'],
              'server_config': entry, 'config_path': str(config),
              'config_sha256': file_hash(config),
              'manifest_sha256': file_hash(manifest_target), 'release': manifest['release'],
              'release_identity': manifest['release_identity'], 'changes': changes}
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
            raise ValueError('Une installation ChatGPT est déjà active.')
        yield


def apply(root, public):
    if not isinstance(public, dict) or public.get('format') != 1:
        raise ValueError('Plan incompatible.')
    unsigned = {key: value for key, value in public.items() if key != 'fingerprint'}
    if digest(encoded(unsigned).encode()) != public.get('fingerprint'):
        raise ValueError('Plan modifié ; refaire la prévisualisation.')
    home, config, managed = paths(public['home'])
    with locked(managed, home):
        if public.get('action') not in ('install', 'remove'):
            raise ValueError('Action invalide.')
        if plan(root, home, public['project'], public['action']) != public:
            raise ValueError('Configuration ou bundle modifié ; refaire le plan.')
        if not public['changes']:
            return {'status': 'completed', 'changed': False, 'client_loaded': 'unknown'}
        _, _, _, entry, manifest, helpers, runtime = desired(root, home, public['project'], public['action'])
        if public['action'] == 'install':
            stage_release(helpers, runtime, Path(public['release']))
            release_check(Path(public['release']))
        backup = managed / 'backups' / str(uuid.uuid4())
        safe_path(backup, home)
        backup.mkdir(parents=True, mode=0o700)
        before = {}
        for name, target in (('config.toml', config), ('managed.json', managed / 'managed.json')):
            safe_path(target, home)
            before[target] = target.read_bytes() if target.exists() else None
            if before[target] is not None:
                saved = backup / name
                saved.write_bytes(before[target])
                saved.chmod(0o600)
        rendered = render(before[config], entry)
        after_config = digest(rendered.encode())
        after_manifest = digest(encoded(manifest).encode())
        wrote_config = False
        try:
            # Recheck both files after release staging and backup I/O.
            if file_hash(config) != public['config_sha256'] or file_hash(managed / 'managed.json') != public['manifest_sha256']:
                raise ValueError('Modification concurrente avant publication ; aucune réécriture.')
            wrote_config = True
            atomic(config, rendered)
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
            'next_step': RESTART_NOTICE, 'message': 'Table ' + SECTION_HEADER + ' écrite dans config.toml. ' + RESTART_NOTICE}


def verify(root, home=None, project=None):
    home, config, managed = paths(home)
    old = metadata(managed)
    entry = read_config(config).get('mcp_servers', {}).get(ENTRY_NAME)
    if not old or old.get('status') != 'installed' or entry is None:
        raise ValueError('Table ' + SECTION_HEADER + ' absente du config.toml ; appliquer le plan d’abord.')
    if project is not None and str(Path(project).expanduser().resolve(strict=True)) != old.get('project'):
        raise ValueError('Le projet sélectionné ne correspond pas au connecteur installé.')
    if entry_hash(entry) != old.get('entry_sha256'):
        raise ValueError('Table ' + SECTION_HEADER + ' divergente du manifeste ; refaire le plan.')
    selected_state = state_location(root).resolve()
    release = Path(old['release'])
    if (old.get('state') != str(selected_state) or release.parent.resolve() != (selected_state.parent / 'integration-releases').resolve()
            or release.name != old.get('release_identity') or len(release.name) != 64
            or any(c not in '0123456789abcdef' for c in release.name) or release.is_symlink() or release.parent.is_symlink()):
        raise ValueError('Release hors de l’installation sélectionnée.')
    release_check(release)
    command = [str(release / 'Contents/Resources/runtime/bin/python3'), '-E', '-s', '-B',
               str(release / 'Contents/Resources/helpers/chatgpt_mcp.py'), '--project-root', old['project']]
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}]
    with tempfile.TemporaryDirectory(prefix='reprise-chatgpt-check-') as temporary:
        command += ['--state', str(Path(temporary) / 'chatgpt-state'),
                    '--expert-state', str(Path(temporary) / 'expert')]
        env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'DYLD_')) and k != 'AGENT_RESUME_STATE'}
        response = subprocess.run(command, input='\n'.join(json.dumps(r) for r in requests) + '\n',
                                  capture_output=True, text=True, timeout=20, env=env, check=True)
    messages = [json.loads(line) for line in response.stdout.splitlines()]
    info = next(m['result']['serverInfo'] for m in messages if m.get('id') == 1)
    tools = next(m['result']['tools'] for m in messages if m.get('id') == 2)
    names = sorted(tool['name'] for tool in tools)
    if not REQUIRED_BRIDGE_TOOLS.issubset(names):
        raise ValueError('Outils de la facade ChatGPT incomplets.')
    return {'status': 'verified', 'config_written': True, 'bridge_tools': names,
            'bridge_version': info['version'], 'client_loaded': 'unknown', 'inference_executed': False,
            'next_step': RESTART_NOTICE,
            'message': 'Table ' + SECTION_HEADER + ' conforme et facade vérifiée par initialize/tools/list ; '
                       + RESTART_NOTICE}


def main(root, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('chatgpt-integration-inspect', 'chatgpt-integration-plan', 'chatgpt-integration-verify'):
        command = sub.add_parser(name)
        command.add_argument('--home')
        if name == 'chatgpt-integration-verify':
            command.add_argument('--project')
        if name == 'chatgpt-integration-plan':
            command.add_argument('--project', required=True)
            command.add_argument('--action', choices=('install', 'remove'), default='install')
    sub.add_parser('chatgpt-integration-apply')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command.endswith('-inspect'):
            result = inspect(root, args.home)
        elif args.command.endswith('-plan'):
            result = {'status': 'planned', 'plan': plan(root, args.home, args.project, args.action)}
        elif args.command.endswith('-apply'):
            result = apply(root, json.loads(sys.stdin.read(1024 * 1024))['plan'])
        else:
            result = verify(root, args.home, project=args.project)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, StopIteration):
        # Configurations can contain credentials; never echo parser errors or subprocess stderr.
        print(json.dumps({'status': 'refused', 'message': 'Installation refusée ; inspecter la configuration, les préconditions et les sauvegardes.'}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
