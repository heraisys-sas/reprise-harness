#!/usr/bin/env python3
"""Build the optional ChatGPT Reprise.app launcher from tracked sources."""
import argparse
from pathlib import Path
import plistlib
import shutil
import tempfile

ROOT = Path(__file__).resolve().parent
NAME = 'ChatGPT Reprise.app'
EXECUTABLE = 'ChatGPT Reprise'


def build(output_dir):
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / NAME
    if target.exists() or target.is_symlink():
        raise FileExistsError(f'Existing wrapper must be handled explicitly: {target}')
    with tempfile.TemporaryDirectory(prefix='.chatgpt-reprise-', dir=output_dir) as folder:
        app = Path(folder) / NAME
        macos = app / 'Contents/MacOS'
        macos.mkdir(parents=True)
        script = macos / EXECUTABLE
        shutil.copyfile(ROOT / 'launch.sh', script)
        script.chmod(0o755)
        info = {
            'CFBundleExecutable': EXECUTABLE,
            'CFBundleIdentifier': 'sas.heraisys.reprise.chatgpt-launcher',
            'CFBundleName': 'ChatGPT Reprise',
            'CFBundlePackageType': 'APPL',
            'CFBundleShortVersionString': '1.0',
            'CFBundleVersion': '1',
            'LSUIElement': True,
        }
        with (app / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump(info, stream, sort_keys=True)
        app.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='Directory to receive the generated app')
    args = parser.parse_args()
    print(build(args.output))


if __name__ == '__main__':
    main()
