"""Build-time evidence for the prototype's explicit macOS / Apple framework contract."""
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys

MINIMUM = '26.0'
BINARIES = ('Reprise', 'Embedding', 'FoundationExplain', 'Vault')


def check_sdk(version):
    if not re.fullmatch(r'\d+(?:\.\d+){1,2}', version) or int(version.split('.')[0]) < 26:
        raise ValueError('Le build Reprise nécessite le SDK macOS 26 ou ultérieur (Foundation Models).')


def build_version(output):
    section = re.search(r'cmd LC_BUILD_VERSION\n(.*?)(?=Load command|\Z)', output, re.S)
    if section is None:
        raise ValueError('Version minimale Mach-O absente.')
    values = dict(re.findall(r'^\s*(minos|sdk)\s+(\S+)', section[1], re.M))
    if values.get('minos') != MINIMUM or 'sdk' not in values:
        raise ValueError('Minimum macOS du binaire incompatible avec le contrat.')
    check_sdk(values['sdk'])
    return values


def record(app):
    app = Path(app)
    with (app / 'Contents/Info.plist').open('rb') as handle:
        if plistlib.load(handle).get('LSMinimumSystemVersion') != MINIMUM:
            raise ValueError('Minimum macOS Info.plist incohérent.')
    binaries = {}
    for name in BINARIES:
        binary = app / 'Contents/MacOS' / name
        output = subprocess.check_output(['/usr/bin/otool', '-l', str(binary)], text=True)
        binaries[name] = build_version(output) | {'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}
    manifest = {'format': 1, 'minimum_macos': MINIMUM, 'binaries': binaries,
                'foundation_models': {'required_for': 'optional_local_synthesis',
                    'model_bundled': False, 'availability_probe': 'Contents/MacOS/FoundationExplain --status'},
                'embedding_framework': 'NaturalLanguage'}
    (app / 'Contents/Resources/native-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


if __name__ == '__main__':
    if sys.argv[1] == '--check-sdk':
        check_sdk(sys.argv[2])
    else:
        record(sys.argv[1])
