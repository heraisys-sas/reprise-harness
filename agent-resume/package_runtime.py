"""Build-time-only packaging of a pinned CPython distribution; no network access."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import tempfile

LOCK = Path(__file__).with_name('python-runtime.lock.json')


def verify_archive(path, expected_sha256):
    with Path(path).open('rb') as handle:
        actual = hashlib.file_digest(handle, 'sha256').hexdigest()
    if actual != expected_sha256:
        raise ValueError('Empreinte de l’archive Python incorrecte.')


def safe_extract(archive, destination):
    if Path(destination).is_symlink():
        raise ValueError('Destination d’extraction symbolique interdite.')
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise ValueError('Destination d’extraction non vide.')
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        names = set()
        links = set()
        for member in members:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'python':
                raise ValueError('Chemin interdit dans l’archive Python.')
            if member.name in names or not (member.isfile() or member.isdir() or member.issym()):
                raise ValueError('Type ou doublon interdit dans l’archive Python.')
            names.add(member.name)
            if member.issym():
                links.add(path)
                link = PurePosixPath(member.linkname)
                resolved = (destination / str(path.parent) / str(link)).resolve()
                if link.is_absolute() or not resolved.is_relative_to(destination / 'python'):
                    raise ValueError('Lien sortant dans l’archive Python.')
        for member in members:
            if any(parent in links for parent in PurePosixPath(member.name).parents):
                raise ValueError('Écriture sous un lien interdite.')
        source.extractall(destination, members=members, filter='data')


def clean_environment():
    environment = {key: value for key, value in os.environ.items() if not key.startswith(('PYTHON', 'DYLD_'))}
    environment.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    return environment


def inventory(root):
    files = {}
    for entry in sorted(root.rglob('*')):
        if entry.is_symlink():
            if not entry.resolve().is_relative_to(root.resolve()):
                raise ValueError('Lien runtime sortant.')
            files[str(entry.relative_to(root))] = {'symlink': os.readlink(entry)}
        elif entry.is_file():
            with entry.open('rb') as handle:
                files[str(entry.relative_to(root))] = hashlib.file_digest(handle, 'sha256').hexdigest()
    return files


def metadata(app):
    runtime = Path(app).resolve() / 'Contents/Resources/runtime'
    if (runtime.parent / 'runtime-installing.json').exists():
        raise ValueError('Remplacement du runtime interrompu ; reconstruire avec --runtime-archive.')
    if not runtime.exists():
        return 'external-python-required'
    manifest = json.loads((runtime / 'runtime-manifest.json').read_text())
    expected = manifest['files']
    actual = inventory(runtime)
    actual.pop('runtime-manifest.json', None)
    lock = json.loads(LOCK.read_text())
    target = lock['targets'].get(platform.machine(), {})
    provenance = {'format': 1, 'provider': 'astral-sh/python-build-standalone', 'release': lock['release'],
                  'target': target.get('triple'), 'archive_sha256': target.get('sha256'), 'source_url': target.get('url')}
    if (actual != expected or any(manifest.get(key) != value for key, value in provenance.items())
            or manifest.get('observed', {}).get('version') != lock['python_version']
            or manifest.get('observed', {}).get('machine') != platform.machine()
            or not os.access(runtime / 'bin/python3', os.X_OK)):
        raise ValueError('Runtime existant modifié ou incompatible ; reconstruire avec --runtime-archive.')
    return {key: manifest[key] for key in ('provider', 'release', 'target', 'archive_sha256', 'observed')}


def install(archive, app, lock_path=None):
    lock = json.loads(Path(lock_path or LOCK).read_text())
    machine = platform.machine()
    if platform.system() != 'Darwin' or machine not in lock['targets']:
        raise ValueError('Runtime prototype disponible seulement pour les architectures macOS épinglées.')
    target = lock['targets'][machine]
    verify_archive(archive, target['sha256'])
    resources = Path(app).resolve() / 'Contents/Resources'
    resources.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.runtime-', dir=resources) as temporary:
        temporary = Path(temporary)
        safe_extract(archive, temporary)
        runtime = temporary / 'python'
        notices = Path(lock_path or LOCK).parent / lock['notices']
        verify_archive(notices, lock['notices_sha256'])
        shutil.copyfile(notices, runtime / 'THIRD-PARTY-NOTICES.txt')
        python = runtime / 'bin/python3'
        script = ('import sys,platform,json,sqlite3,ssl,hashlib,zlib,bz2,lzma,ctypes,fcntl,socket; '
                  'assert sqlite3.connect(":memory:").execute("select 1").fetchone()==(1,); '
                  'print(json.dumps({"version":platform.python_version(),"machine":platform.machine(),'
                  '"prefix":sys.prefix,"sqlite":sqlite3.sqlite_version,"openssl":ssl.OPENSSL_VERSION}))')
        checked = subprocess.run([str(python), '-I', '-B', '-c', script], env=clean_environment(),
                                 capture_output=True, text=True, timeout=30, check=True)
        observed = json.loads(checked.stdout)
        if observed['version'] != lock['python_version'] or observed['machine'] != machine or Path(observed['prefix']).resolve() != runtime.resolve():
            raise ValueError('Runtime observé incompatible avec le verrou.')
        # Remove the staging prefix from the published evidence.
        observed.pop('prefix')
        manifest = {'format': 1, 'provider': 'astral-sh/python-build-standalone',
                    'release': lock['release'], 'target': target['triple'],
                    'archive_sha256': target['sha256'], 'source_url': target['url'],
                    'observed': observed, 'files': inventory(runtime)}
        (runtime / 'runtime-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        destination = resources / 'runtime'
        previous = temporary / 'previous'
        journal = resources / 'runtime-installing.json'
        journal.write_text(json.dumps({'archive_sha256': target['sha256'], 'status': 'replacing'}) + '\n')
        if destination.exists():
            destination.rename(previous)
        try:
            runtime.rename(destination)
        except BaseException:
            if previous.exists():
                previous.rename(destination)
            raise
        journal.unlink()
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--app', required=True)
    args = parser.parse_args()
    install(args.archive, args.app)
