#!/usr/bin/env python3
"""Managed app clones: frozen, re-identified copies of ChatGPT/Claude Desktop.

Contrat APP-CLONES-CONTRACT.md : chaque clone vit dans ~/Applications sous
une identité de bundle propre, version gelée (auto-update de SON identité
désactivé), originale /Applications jamais mutée. Les opérations suivent la
logique gérée : inspect / plan / apply / verify, avec manifeste local
(`~/Library/Application Support/Reprise/app-clones.json`). Le branchement
comme composant managed_update est prévu avec la release ; ce module porte
déjà la même sémantique et un CLI complet.
"""
import argparse
import json
import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path

CLONES_HOME = Path.home() / 'Applications'
MANIFEST_PATH = Path.home() / 'Library/Application Support/Reprise/app-clones.json'
MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024

CLONES = {
    'rechatgpt': {
        'source': Path('/Applications/ChatGPT.app'),
        'target': CLONES_HOME / 'ReChatGPT.app',
        'bundle_id': 'com.reprise.rechatgpt',
        'display_name': 'ReChatGPT',
        'kind': 'clone',
    },
    'reclaude': {
        'source': Path('/Applications/Claude.app'),
        'target': CLONES_HOME / 'ReClaude.app',
        'bundle_id': 'com.anthropic.claudefordesktop.reprise',
        'display_name': 'ReClaude',
        'kind': 'patched',
    },
}


def _plist(app):
    return Path(app) / 'Contents' / 'Info.plist'


def _read_plist(app):
    with open(_plist(app), 'rb') as handle:
        return plistlib.load(handle)


def _executable(app):
    plist = _read_plist(app)
    return Path(app) / 'Contents' / 'MacOS' / plist.get('CFBundleExecutable', 'App')


def _version(app):
    try:
        return _read_plist(app).get('CFBundleShortVersionString', '')
    except (OSError, plistlib.InvalidFileException):
        return None


def _signature_valid(app):
    try:
        done = subprocess.run(['codesign', '--verify', '--deep', str(app)],
                              capture_output=True, timeout=300)
        return done.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _running_pid(target):
    """Authentic PID by executable path, or None (leçon #198 : jamais un nom)."""
    try:
        exe = str(_executable(target))
        done = subprocess.run(['pgrep', '-f', exe], capture_output=True, text=True,
                              timeout=15)
        pids = [line for line in done.stdout.split() if line.strip().isdigit()]
        for pid in pids:
            try:
                running = subprocess.run(['ps', '-p', pid, '-o', 'command='],
                                         capture_output=True, text=True, timeout=15)
                if exe in running.stdout:
                    return int(pid)
            except (OSError, subprocess.TimeoutExpired, ValueError):
                continue
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


def _defaults_writer(bundle_id, key, value):
    subprocess.run(['defaults', 'write', bundle_id, key, '-bool', str(value).lower()],
                   capture_output=True, text=True, timeout=30, check=True)


def _su_state(bundle_id):
    try:
        done = subprocess.run(['defaults', 'read', bundle_id, 'SUEnableAutomaticChecks'],
                              capture_output=True, text=True, timeout=30)
        return done.stdout.strip() == '0'
    except (OSError, subprocess.TimeoutExpired):
        return False


def _sign(app):
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(app)],
                   capture_output=True, text=True, timeout=900, check=True)


def _set_identity(app, spec):
    path = _plist(app)
    with open(path, 'rb') as handle:
        plist = plistlib.load(handle)
    if spec['kind'] == 'patched':
        # Electron uses CFBundleName to locate the original nested helper apps.
        source_name = _read_plist(spec['source']).get('CFBundleName')
        if source_name is None:
            plist.pop('CFBundleName', None)
        else:
            plist['CFBundleName'] = source_name
    else:
        plist['CFBundleName'] = spec['display_name']
    plist['CFBundleDisplayName'] = spec['display_name']
    plist['CFBundleIdentifier'] = spec['bundle_id']
    with open(path, 'wb') as handle:
        plistlib.dump(plist, handle)


def _clone_source(source, target):
    """APFS clonefile via cp -c ; repli copie profonde si le volume refuse."""
    done = subprocess.run(['cp', '-c', '-R', str(source), str(target)],
                          capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        shutil.copytree(source, target, symlinks=True)


def _load_manifest(path=MANIFEST_PATH):
    if not Path(path).exists():
        return {'clones': {}}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) and 'clones' in data else {'clones': {}}
    except (OSError, ValueError):
        return {'clones': {}}


def _save_manifest(manifest, path=MANIFEST_PATH):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(manifest, handle, sort_keys=True, ensure_ascii=False, indent=1)


def inventory(home=CLONES_HOME, *, specs=None, manifest_path=MANIFEST_PATH,
              defaults_state=_su_state):
    """État par clone : présence, versions, identité, signature, auto-update."""
    specs = specs if specs is not None else CLONES
    manifest = _load_manifest(manifest_path)
    out = {}
    for name, spec in specs.items():
        source, target = Path(spec['source']), Path(spec['target'])
        frozen = manifest['clones'].get(name, {})
        row = {'present': target.is_dir(), 'kind': spec['kind'],
               'source_version': _version(source) if source.is_dir() else None,
               'target_version': _version(target) if target.is_dir() else None,
               'frozen_version': frozen.get('version'),
               'frozen_at': frozen.get('frozen_at'),
               'bundle_id_ok': None, 'display_name_ok': None, 'bundle_name_ok': None,
               'signature_valid': None, 'su_off': None, 'running_pid': None,
               'upstream_newer': None}
        if row['present']:
            try:
                plist = _read_plist(target)
                row['bundle_id_ok'] = plist.get('CFBundleIdentifier') == spec['bundle_id']
                row['display_name_ok'] = plist.get('CFBundleDisplayName') == spec['display_name']
                if spec['kind'] == 'patched':
                    if row['source_version'] is not None:
                        source_name = _read_plist(source).get('CFBundleName')
                        row['bundle_name_ok'] = plist.get('CFBundleName') == source_name
                else:
                    row['bundle_name_ok'] = plist.get('CFBundleName') == spec['display_name']
            except (OSError, plistlib.InvalidFileException):
                row['bundle_id_ok'] = row['display_name_ok'] = False
            row['signature_valid'] = _signature_valid(target)
            row['su_off'] = defaults_state(spec['bundle_id'])
            row['running_pid'] = _running_pid(target)
            if row['source_version'] and row['frozen_version'] and \
                    row['source_version'] != row['frozen_version']:
                row['upstream_newer'] = True
        issues = []
        if not row['present']:
            issues.append('clone')
        else:
            if any(row[key] is False for key in ('bundle_id_ok', 'display_name_ok', 'bundle_name_ok')):
                issues.append('identity')
            if row['signature_valid'] is False:
                issues.append('sign')
            if row['su_off'] is False:
                issues.append('update-off')
            if row['upstream_newer']:
                issues.append('refresh')
        row['issues'] = issues
        out[name] = row
    return out


def plan(home=CLONES_HOME, *, specs=None, manifest_path=MANIFEST_PATH):
    """Actions par clone, dans l'ordre d'exécution d'apply."""
    state = inventory(home, specs=specs, manifest_path=manifest_path)
    out = {}
    for name, row in state.items():
        actions = []
        for issue in row['issues']:
            if issue == 'clone':
                actions += ['clone', 'identity', 'sign', 'update-off']
            elif issue == 'identity':
                actions += ['identity', 'sign']
            elif issue == 'sign':
                actions.append('sign')
            elif issue == 'update-off':
                actions.append('update-off')
            elif issue == 'refresh':
                actions += ['refresh', 'clone', 'identity', 'sign', 'update-off']
            if issue in ('clone', 'refresh') or not row['frozen_version']:
                actions.append('freeze-record')
        out[name] = actions
    return out


def apply(home=CLONES_HOME, names=None, *, specs=None, manifest_path=MANIFEST_PATH,
          defaults_writer=_defaults_writer):
    """Exécute les actions planifiées ; ne touche jamais l'originale."""
    specs = specs if specs is not None else CLONES
    names = list(specs) if names is None else list(names)
    planned = plan(home, specs=specs, manifest_path=manifest_path)
    manifest = _load_manifest(manifest_path)
    receipt = {}
    for name in names:
        spec = specs[name]
        actions = planned.get(name, [])
        if not actions:
            receipt[name] = {'actions': [], 'status': 'nothing_to_do'}
            continue
        source, target = Path(spec['source']), Path(spec['target'])
        row = receipt[name] = {'actions': actions, 'status': 'applied', 'steps': []}
        if not source.is_dir():
            row['status'] = 'refused'
            row['reason'] = 'source_missing'
            continue
        if 'refresh' in actions and target.is_dir():
            pid = _running_pid(target)
            if pid is not None:
                row['status'] = 'refused'
                row['reason'] = f'target_running:{pid}'
                continue
            shutil.rmtree(target)
            row['steps'].append('old_clone_removed')
        if row['status'] != 'applied':
            continue
        free = (Path(home).mkdir(parents=True, exist_ok=True),
                shutil.disk_usage(home).free)[1]
        if free < MIN_FREE_BYTES:
            row['status'] = 'refused'
            row['reason'] = 'disk_low'
            continue
        for action in actions:
            if action == 'clone':
                if target.exists():
                    row['steps'].append('clone_skipped')
                    continue
                _clone_source(source, target)
                row['steps'].append('clone')
            elif action == 'identity':
                _set_identity(target, spec)
                row['steps'].append('identity')
            elif action == 'sign':
                _sign(target)
                row['steps'].append('sign')
            elif action == 'update-off':
                defaults_writer(spec['bundle_id'], 'SUEnableAutomaticChecks', False)
                defaults_writer(spec['bundle_id'], 'SUAutomaticallyUpdate', False)
                row['steps'].append('update-off')
            elif action == 'freeze-record':
                manifest['clones'][name] = {
                    'version': _version(target), 'upstream_version': _version(source),
                    'bundle_id': spec['bundle_id'], 'frozen_at': time.time()}
                row['steps'].append('freeze-record')
        _save_manifest(manifest, manifest_path)
        row['target_version'] = _version(target) if target.is_dir() else None
    return receipt


def verify(home=CLONES_HOME, *, specs=None, manifest_path=MANIFEST_PATH,
           defaults_state=_su_state):
    """Post-conditions : identité, signature, SU off, gel cohérent."""
    state = inventory(home, specs=specs, manifest_path=manifest_path,
                      defaults_state=defaults_state)
    manifest = _load_manifest(manifest_path)
    ok = True
    out = {}
    for name, row in state.items():
        problems = list(row['issues'])
        frozen = manifest['clones'].get(name)
        # Le gel (manifeste) ne concerne que kind='clone' (#256) : une app
        # patched saine est volontairement re-signée à chaque montée Desktop.
        if row['present'] and frozen is None and row['kind'] == 'clone':
            problems.append('not_frozen')
        if frozen is not None and row['present'] and \
                frozen.get('version') not in (row['target_version'], None):
            problems.append('manifest_version_mismatch')
        out[name] = {'ok': not problems, 'problems': problems}
        ok = ok and not problems
    return {'ok': ok, 'clones': out}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for cmd in ('inspect', 'plan', 'verify'):
        sub.add_parser(cmd)
    apply_parser = sub.add_parser('apply')
    apply_parser.add_argument('names', nargs='*', help='clones ciblés (défaut: tous)')
    args = parser.parse_args(argv)
    if args.action == 'inspect':
        out = inventory()
    elif args.action == 'plan':
        out = plan()
    elif args.action == 'apply':
        out = apply(names=args.names or None)
    else:
        out = verify()
    print(json.dumps(out, sort_keys=True, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
