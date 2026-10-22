"""Isolated ARM64 bundle builds and explicit, freshness-checked GUI launch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs(root):
    # Only build inputs: never state, logs, credentials, generated bundles or tests.
    suffixes = {'.swift', '.py', '.md', '.json', '.txt', '.html', '.cjs'}
    return {p.name: sha(p) for p in sorted(root.iterdir())
            if p.is_file() and not p.is_symlink() and (p.suffix in suffixes or p.name in ('VERSION', 'native-install', 'resume'))}


def identity(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def bundle_files(app):
    files = {}
    for p in sorted(app.rglob('*')):
        name = str(p.relative_to(app))
        if p.is_symlink():
            if not p.resolve().is_relative_to(app.resolve()):
                raise ValueError('Lien sortant du bundle.')
            files[name] = {'link': os.readlink(p)}
        elif p.is_file():
            files[name] = {'sha256': sha(p), 'executable': bool(p.stat().st_mode & 0o111)}
    return files


def check(app, root=ROOT):
    app = Path(app).resolve()
    evidence = json.loads((app / 'Contents/Resources/build-evidence.json').read_text())
    if evidence.get('format') != 1 or evidence['inputs'] != inputs(root):
        raise ValueError('Build périmé ou autre copie du dépôt : reconstruire avant lancement.')
    actual = bundle_files(app)
    actual.pop('Contents/Resources/build-evidence.json', None)
    if actual != evidence['bundle']:
        raise ValueError('Bundle modifié ou incomplet : reconstruire avant lancement.')
    return evidence


def smoke(app):
    resources = app / 'Contents/Resources'
    python = resources / 'runtime/bin/python3'
    helper = resources / 'helpers/resume'
    with tempfile.TemporaryDirectory(prefix='reprise-smoke-') as folder:
        home = Path(folder)
        env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'DYLD_')) and k != 'AGENT_RESUME_STATE'}
        env.update(HOME=str(home), AGENT_RESUME_STATE=str(home / 'state'), PYTHONDONTWRITEBYTECODE='1')
        for args in (['state-init', '--target', str(home / 'state')], ['scheduler-status']):
            result = subprocess.run([str(python), '-E', '-s', '-B', str(helper), *args],
                                    env=env, capture_output=True, text=True, check=True, timeout=30)
            json.loads(result.stdout)


def build(archive, root=ROOT):
    archive = Path(archive).expanduser().resolve(strict=True)
    source = inputs(root)
    builds = root / 'builds'
    builds.mkdir(exist_ok=True)
    destination = builds / identity(source)
    app = destination / 'Reprise.app'
    if destination.exists():
        check(app, root)
        return app
    with tempfile.TemporaryDirectory(prefix='reprise-build-') as folder:
        stage = Path(folder) / 'source'
        stage.mkdir()
        for name in source:
            shutil.copy2(root / name, stage / name)
        if inputs(stage) != source:
            raise ValueError('Sources modifiées pendant la copie ; recommencer.')
        subprocess.run(['/bin/sh', str(stage / 'native-install'), '--runtime-archive', str(archive)], check=True)
        produced = stage / 'Reprise.app'
        # Test actual packaged CLI after relocation, without GUI, socket or real state.
        moved = Path(folder) / 'bundle déplacé' / 'Reprise.app'
        moved.parent.mkdir()
        produced.rename(moved)
        smoke(moved)
        subprocess.run(['python3', str(stage / 'native_contract.py'), str(moved)], check=True)
        if inputs(root) != source:
            raise ValueError('Sources modifiées pendant la compilation ; build non publié.')
        evidence = {'format': 1, 'inputs': source, 'source_sha256': identity(source),
                    'git_commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
                    'bundle': bundle_files(moved), 'smoke': 'relocated CLI, temporary HOME/state, no inference'}
        (moved / 'Contents/Resources/build-evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
        # Publish a new immutable directory; never replace an app in use.
        publish = Path(tempfile.mkdtemp(prefix='.publish-', dir=builds))
        try:
            shutil.copytree(moved, publish / 'Reprise.app', symlinks=True)
            check(publish / 'Reprise.app', root)
            publish.rename(destination)
        finally:
            if publish.exists():
                shutil.rmtree(publish)
    return app


def launch(app, root=ROOT):
    check(app, root)
    env = dict(os.environ)
    env.pop('AGENT_RESUME_STATE', None)
    # No development arguments: bundled runtime and Application Support state.
    subprocess.run(['/usr/bin/open', '-n', '-a', str(Path(app).resolve())], env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    b = sub.add_parser('build')
    b.add_argument('--runtime-archive', required=True)
    for action in ('check', 'open'):
        sub.add_parser(action).add_argument('--app')
    args = parser.parse_args()
    try:
        if args.action == 'build':
            app = build(args.runtime_archive)
        else:
            app = Path(args.app) if args.app else ROOT / 'builds' / identity(inputs(ROOT)) / 'Reprise.app'
            if args.action == 'open':
                launch(app)
            else:
                check(app)
        print(json.dumps({'status': 'ready', 'app': str(app)}))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
