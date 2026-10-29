"""Explicit offline state initialization/import; never merges existing registries."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import uuid

MARKER = '.reprise-state.json'
FORMAT = 1
# Version de schéma SQLite maximale acceptée à l'import : suit les modules
# de journal (routing_events.VERSION / external_rankings.VERSION) ; une base
# marquée au-delà vient d'un build plus récent → refus explicite.
try:
    from external_rankings import VERSION as _CACHE_VERSION
    from routing_events import VERSION as _ROUTING_VERSION
    SUPPORTED_SCHEMA_VERSION = max(2, _ROUTING_VERSION, _CACHE_VERSION)
except ImportError:  # import de state hors bundle runtime
    SUPPORTED_SCHEMA_VERSION = 2


def initialized(path):
    try:
        data = json.loads((Path(path) / MARKER).read_text())
        return data.get('format') == FORMAT and data.get('status') == 'ready'
    except (OSError, ValueError, AttributeError):
        return False


def _write(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    path.chmod(0o600)


def _sync_tree(path):
    for entry in path.rglob('*'):
        if entry.is_file():
            with entry.open('rb') as handle:
                os.fsync(handle.fileno())
    for directory in [p for p in path.rglob('*') if p.is_dir()] + [path]:
        _sync_directory(directory)


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _target(path):
    path = Path(path).expanduser().absolute()
    if path.exists() or path.is_symlink():
        raise ValueError('Destination existante : fusion/remplacement interdits.')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def initialize(target, setup_id=None):
    target = _target(target)
    with tempfile.TemporaryDirectory(prefix='.reprise-init-', dir=target.parent) as temporary:
        stage = Path(temporary) / 'state'
        stage.mkdir(mode=0o700)
        receipt = {'format': FORMAT, 'status': 'ready', 'operation': 'initialize', 'id': str(uuid.uuid4())}
        if setup_id is not None:
            receipt['setup_id'] = setup_id
        _write(stage / MARKER, receipt)
        # rename refuses a concurrent nonempty target; no existing state is merged.
        if target.exists() or target.is_symlink():
            raise ValueError('Destination apparue pendant la préparation.')
        _sync_tree(stage)
        stage.rename(target)
        _sync_directory(target.parent)
    return receipt


def _inventory(source):
    result = {}
    for path in sorted(source.rglob('*')):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('Liens symboliques interdits dans un import.')
        if stat.S_ISDIR(info.st_mode) or stat.S_ISSOCK(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Type de fichier non supporté.')
        result[str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _transient(name):
    return name.endswith(('-wal', '-shm', '-journal', '.lock', '.sock', '.pid')) or name == MARKER


def _copy(source, destination, inventory):
    for relative in inventory:
        if _transient(Path(relative).name):
            continue
        entry, output = source / relative, destination / relative
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with entry.open('rb') as handle:
            sqlite_header = handle.read(16) == b'SQLite format 3\x00'
        if sqlite_header or entry.suffix in ('.sqlite', '.db', '.sqlite3'):
            with closing(sqlite3.connect(entry.as_uri() + '?mode=ro', uri=True, timeout=2)) as original:
                version = original.execute('PRAGMA user_version').fetchone()[0]
                if version > SUPPORTED_SCHEMA_VERSION:
                    raise ValueError(f'Version de schéma SQLite {version} postérieure au support '
                                     f'({SUPPORTED_SCHEMA_VERSION}) : import refusé.')
                with closing(sqlite3.connect(output)) as copied:
                    original.backup(copied)
                    if copied.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                        raise ValueError('Intégrité SQLite invalide.')
        else:
            shutil.copyfile(entry, output)
        output.chmod(0o600)


def import_state(source, target, source_stopped=False, setup_id=None):
    if not source_stopped:
        raise ValueError('Import hors ligne requis : confirmer --source-stopped après arrêt des écrivains.')
    source = Path(source).expanduser().absolute()
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Source doit être un dossier réel.')
    if (source / MARKER).exists() and not initialized(source):
        raise ValueError('Manifeste source incompatible ou incomplet : import refusé.')
    target_path = Path(target).expanduser().absolute()
    if source.resolve() == target_path.resolve() or source.resolve() in target_path.resolve().parents or target_path.resolve() in source.resolve().parents:
        raise ValueError('Source et destination doivent être disjointes.')
    target = _target(target)
    before = _inventory(source)
    if not before:
        raise ValueError('Source vide : utiliser state-init pour une initialisation neuve.')
    operation = str(uuid.uuid4())
    backup = target.parent / (target.name + '.backup-' + operation)
    with tempfile.TemporaryDirectory(prefix='.reprise-import-', dir=target.parent) as temporary:
        stage = Path(temporary) / 'snapshot'
        stage.mkdir(mode=0o700)
        raw = Path(temporary) / 'raw'
        raw.mkdir(mode=0o700)
        for relative in before:
            output = raw / relative
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source / relative, output)
        if _inventory(raw) != before:
            raise ValueError('Copie source incohérente : import annulé.')
        _copy(raw, stage, before)
        if _inventory(source) != before:
            raise ValueError('Source modifiée pendant la copie : import annulé.')
        files = _inventory(stage)
        receipt = {'format': FORMAT, 'status': 'ready', 'operation': 'import', 'id': operation,
                   'backup': str(backup), 'target': str(target), 'files': files, 'source_files': before,
                   'schema_policy': 'preserved-without-migration'}
        if setup_id is not None:
            receipt['setup_id'] = setup_id
        _write(stage / MARKER, receipt)
        # A complete verified recovery copy exists before publication. If publication
        # fails, it remains available; retry never overwrites it or the destination.
        _sync_tree(stage)
        stage.rename(backup)
        _sync_directory(backup.parent)
        publication = Path(temporary) / 'publish'
        shutil.copytree(backup, publication)
        if _inventory(publication) != _inventory(backup):
            raise ValueError('Vérification de sauvegarde échouée.')
        if target.exists() or target.is_symlink():
            raise ValueError('Destination apparue pendant la préparation ; sauvegarde conservée.')
        _sync_tree(publication)
        publication.rename(target)
        _sync_directory(target.parent)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description='État Reprise : opérations explicites hors ligne, sans migration de schéma.')
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('state-init').add_argument('--target', required=True)
    command = commands.add_parser('state-import')
    command.add_argument('--source', required=True)
    command.add_argument('--target', required=True)
    command.add_argument('--source-stopped', action='store_true')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        result = initialize(args.target) if args.action == 'state-init' else import_state(args.source, args.target, args.source_stopped)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
