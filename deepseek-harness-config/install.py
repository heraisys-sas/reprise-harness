import datetime
import json
import os
import pathlib
import shutil

source = pathlib.Path(__file__).resolve().parent
home = pathlib.Path(os.environ.get('DSH_HOME', pathlib.Path.home() / '.dsh'))
stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
backup = home / 'backups' / ('code-devops-' + stamp)
files = [(source / 'AGENTS.md', home / 'AGENTS.md'),
         (source / 'cordis.patch.yml', home / 'profiles/desktop/cordis.patch.yml')]
files += [(p, home / p.relative_to(source)) for p in (source / 'skills').rglob('*')
          if p.is_file() and '__pycache__' not in p.parts]
patch = home / 'profiles/desktop/cordis.patch.yml'
if patch.read_text().strip() not in ('[]', '# Your patch layer for this dsh profile, applied after every bundle layer:\n# a top-level YAML array of loader patch entries (id-targeted config\n# overrides, disables, and insert lists; `!!js` expressions allowed).\n[]'):
    raise SystemExit('Profile changed since inspection; inspect and merge before installation.')
conflicts = [str(dst.relative_to(home)) for src, dst in files
             if dst != patch and dst.exists() and dst.read_bytes() != src.read_bytes()]
if conflicts:
    raise SystemExit('Existing custom files require a manual merge: ' + ', '.join(conflicts))
backup.mkdir(parents=True)
manifest = []
for src, dst in files:
    relative = dst.relative_to(home)
    exists = dst.exists()
    if exists:
        saved = backup / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dst, saved)
    dst.parent.mkdir(parents=True, exist_ok=True)
    temporary = dst.with_name(dst.name + '.install-tmp')
    shutil.copy2(src, temporary)
    os.chmod(temporary, 0o600)
    os.replace(temporary, dst)
    manifest.append({'path': str(relative), 'previously_existed': exists})
(backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
print(json.dumps({'installed': len(files), 'backup': str(backup)}, ensure_ascii=False))
