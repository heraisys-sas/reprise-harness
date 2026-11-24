#!/usr/bin/env python3
"""Deploy the Meta AI bridge files and write the `app.reprise.metaaid` LaunchAgent.

Doctrine (cf. install-quick-entry.py) : écrire le plist ne démarre jamais le
service — `launchctl bootstrap` est proposé, jamais exécuté silencieusement,
et un plist existant est refusé sans --force. Le plist pointe ce checkout
(reprise/metaaid.py) et des chemins sous le domicile courant uniquement.

Différence assumée avec quick-entry : le pont Meta AI est un daemon
long-durée (watcher de fichiers), donc le plist porte RunAtLoad + KeepAlive.

Le fichier de connexion (JSON plat, convention ironclaw-config.example.json)
est écrit une seule fois s'il est absent ; il n'est jamais réécrit : c'est la
configuration opérateur (port, racine dav, point de montage, TaskStore).
"""
import argparse
import json
import os
import plistlib
import shutil
from pathlib import Path
import sys

LABEL = 'app.reprise.metaaid'
PLIST_NAME = LABEL + '.plist'
SOURCE = Path(__file__).resolve().parent
DAEMON = SOURCE / 'metaaid.py'
MODULES = ('metaaid.py', 'metaai_taskstore.py', 'metaai_mcp_server.py', 'metaai_bridge_watch.py')
DEFAULT_SERVICE_DIR = Path.home() / 'Library/Application Support/Reprise/metaaid'
DEFAULT_CONFIG = Path.home() / 'Library/Application Support/Reprise/connections/metaai.json'
LOG_DIR = Path.home() / 'Library/Logs/reprise'
CONNECTION_EXAMPLE_FILE = SOURCE / 'metaai-config.example.json'


def connection_example() -> dict:
    return json.loads(CONNECTION_EXAMPLE_FILE.read_text(encoding='utf-8'))


def deploy_modules(service_dir: Path, force: bool) -> dict:
    """Copier les quatre modules du pont vers le répertoire service."""
    service_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in MODULES:
        src = SOURCE / name
        dst = service_dir / name
        if dst.exists() and not force:
            raise FileExistsError(f'{dst} existe déjà ; utiliser --force pour le remplacer')
        shutil.copy2(src, dst)
        copied.append(name)
    return {'service_dir': str(service_dir), 'copied': copied}


def write_connection(config_path: Path, dav_root=None, mount_point=None, taskstore=None) -> dict:
    """Écrire la connexion metaai uniquement si absente (jamais réécrite)."""
    if config_path.exists():
        return {'config': str(config_path), 'changed': False,
                'note': 'connexion existante conservée'}
    example = connection_example()
    if dav_root:
        example['dav_root'] = dav_root
    if mount_point:
        example['mount_point'] = mount_point
    if taskstore:
        example['taskstore'] = taskstore
    config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(example, indent=2, ensure_ascii=False)
    temporary = config_path.with_name(config_path.name + '.reprise-tmp')
    # Revue (MINEUR S9-C8) : créé directement en 0600 — jamais lisible
    # (creds dav) entre création et chmod.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        handle.write(payload)
    temporary.chmod(0o600)
    os.replace(temporary, config_path)
    return {'config': str(config_path), 'changed': True}


def plist_settings(*, daemon, service_dir, log_dir) -> dict:
    home = Path.home().resolve()
    if Path(daemon).resolve() != DAEMON.resolve():
        raise ValueError('Le daemon doit rester dans ce dépôt (reprise/metaaid.py)')
    for path in (service_dir, log_dir):
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_relative_to(home):
            raise ValueError('Les chemins du LaunchAgent doivent rester sous le domicile courant')
    return {
        'Label': LABEL,
        'ProgramArguments': [sys.executable, str(daemon)],
        'RunAtLoad': True,
        'KeepAlive': True,
        'ProcessType': 'Background',
        'StandardOutPath': str(log_dir / 'metaaid.log'),
        'StandardErrorPath': str(log_dir / 'metaaid.log'),
    }


def install(*, service_dir=None, config_path=None, force=False) -> dict:
    """Déployer les modules, la connexion (si absente) et écrire le plist."""
    service_dir = DEFAULT_SERVICE_DIR if service_dir is None else Path(service_dir)
    config_path = DEFAULT_CONFIG if config_path is None else Path(config_path)
    log_dir = LOG_DIR
    deployed = deploy_modules(service_dir=service_dir, force=force)
    connection = write_connection(config_path)
    target = Path.home() / 'Library' / 'LaunchAgents' / PLIST_NAME
    if target.exists() and not force:
        raise FileExistsError(f'{target} existe déjà ; utiliser --force pour le remplacer')
    settings = plist_settings(daemon=DAEMON, service_dir=service_dir, log_dir=log_dir)
    payload = plistlib.dumps(settings, sort_keys=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.reprise-tmp')
    # Revue (MINEUR S9-C8) : même garde que pour la connexion.
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(payload)
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return {
        'changed': True,
        'plist': str(target),
        'label': LABEL,
        'deployed': deployed,
        'connection': connection,
        'next_step': f'launchctl bootstrap gui/$(id -u) {target}  # explicite, jamais exécuté par ce script',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service-dir', default=str(DEFAULT_SERVICE_DIR))
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('--force', action='store_true', help='remplacer un plist/modules existants')
    parser.add_argument('--print-connection-example', action='store_true')
    args = parser.parse_args()
    if args.print_connection_example:
        print(json.dumps(connection_example(), indent=2, ensure_ascii=False))
        return
    result = install(service_dir=Path(args.service_dir), config_path=Path(args.config), force=args.force)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
