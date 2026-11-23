#!/usr/bin/env python3
"""Build and deploy the patched « Meta AI Reprise.app » copy (UI bridge).

Construit et déploie la copie patchée de Meta AI Desktop qui porte le pont UI
(invocation rapide) : dylib injectée par remplacement du binaire Sparkle
(ré-export du vrai Sparkle) + shim de Security.framework (émulation trousseau
pour les tokens de session) + socket de contrôle ~/.reprise/.

Sequence (chaque étape est idempotente) :
  1. copie sans xattrs de /Applications/Meta AI.app vers ~/Applications/Meta AI Reprise.app
  2. extraction du vrai Sparkle vers ~/.reprise/Sparkle-real (LC_ID absolu + re-signature)
  3. compilation des deux dylibs depuis ce dépôt (metaai_reprise.m, metaai_sec_shim.m)
  4. remplacement de Sparkle dans la copie + réécriture du chemin Security.framework
  5. signature Apple Development (runtime durci désactivé) + chmod 755

Le script n'exécute JAMAIS l'app ni launchctl : la commande de lancement est
proposée (doctrine install-quick-entry.py). L'original /Applications/Meta AI.app
n'est jamais modifié. Les tokens de session restent dans le trousseau login de
l'utilisateur ; le shim n'écrit aucune valeur secrète dans les logs.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPRISE = Path(__file__).resolve().parent
HOME = Path.home()
SRC_APP = Path('/Applications/Meta AI.app')
DST_APP = HOME / 'Applications' / 'Meta AI Reprise.app'
RUNTIME = HOME / '.reprise'
SPARKLE_REAL = RUNTIME / 'Sparkle-real'
SEC_SHIM = RUNTIME / 'sec-shim.dylib'
FAKE_INSTALL_NAME = str(RUNTIME / 'fake-sparkle')
SHIM_LOAD_PATH = str(SEC_SHIM)
SPARKLE_REL = 'Contents/Frameworks/Sparkle.framework/Versions/B/Sparkle'
SECURITY_PREFIX = '/System/Library/Frameworks/Security.framework'
BUILD_DIR = REPRISE / 'build'
LOG = HOME / 'Library/Logs/reprise/metaai-reprise-dylib.log'


def sh(*args: str) -> str:
    out = subprocess.run(args, check=True, capture_output=True, text=True)
    return (out.stdout + out.stderr).strip()


def find_identity(explicit):
    if explicit:
        return explicit
    out = subprocess.run(['security', 'find-identity', '-p', 'codesigning', '-v'],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        s = line.strip()
        if s and s[0].isdigit() and 'Apple Development' in s:
            return s.split()[1]
    raise SystemExit('aucune identité Apple Development ; passer --identity')


def binary_bytes(app: Path) -> bytes:
    return (app / 'Contents/MacOS/Meta AI').read_bytes()


def copy_app() -> dict:
    if DST_APP.exists():
        return {'step': 'copy', 'changed': False, 'app': str(DST_APP)}
    DST_APP.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['rsync', '-a', str(SRC_APP) + '/', str(DST_APP) + '/'], check=True)
    return {'step': 'copy', 'changed': True, 'app': str(DST_APP),
            'note': 'copie rsync -a : xattrs (quarantaine/provenance) non copiés'}


def prepare_sparkle_real() -> dict:
    """Extraire le vrai Sparkle (de l'originale) et lui donner un LC_ID absolu."""
    if SPARKLE_REAL.exists():
        return {'step': 'sparkle-real', 'changed': False, 'path': str(SPARKLE_REAL)}
    RUNTIME.mkdir(parents=True, exist_ok=True)
    real = SRC_APP / SPARKLE_REL
    if not real.exists():
        raise SystemExit(f'Sparkle introuvable dans l\'originale : {real}')
    subprocess.run(['cp', str(real), str(SPARKLE_REAL)], check=True)
    SPARKLE_REAL.chmod(0o755)
    subprocess.run(['install_name_tool', '-id', str(SPARKLE_REAL), str(SPARKLE_REAL)], check=True,
                   capture_output=True)
    sh('codesign', '--force', '-s', '-', str(SPARKLE_REAL))
    return {'step': 'sparkle-real', 'changed': True, 'path': str(SPARKLE_REAL)}


def compile_dylibs() -> dict:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    fake = BUILD_DIR / 'fake_sparkle.dylib'
    shim = BUILD_DIR / 'sec-shim.dylib'
    sh('clang', '-dynamiclib', '-framework', 'AppKit', '-framework', 'Foundation', '-fobjc-arc',
       '-Wno-arc-performSelector-leaks', f'-Wl,-reexport_library,{SPARKLE_REAL}',
       '-install_name', FAKE_INSTALL_NAME, '-o', str(fake), str(REPRISE / 'metaai_reprise.m'))
    sh('clang', '-dynamiclib', '-framework', 'Foundation', '-framework', 'Security', '-fobjc-arc',
       '-Wl,-reexport_framework,Security', '-install_name', SHIM_LOAD_PATH,
       '-o', str(shim), str(REPRISE / 'metaai_sec_shim.m'))
    sh('codesign', '-f', '-s', '-', str(fake))
    sh('codesign', '-f', '-s', '-', str(shim))
    SEC_SHIM.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['cp', str(shim), str(SEC_SHIM)], check=True)
    SEC_SHIM.chmod(0o755)
    return {'step': 'compile', 'fake_sparkle': str(fake), 'sec_shim_deployed': str(SEC_SHIM)}


def swap_sparkle() -> dict:
    target = DST_APP / SPARKLE_REL
    data = target.read_bytes()
    if b'.reprise/Sparkle-real' in data:
        return {'step': 'swap-sparkle', 'changed': False}
    backup = target.parent / 'Sparkle.reprise-real'
    if not backup.exists():
        target.rename(backup)
    subprocess.run(['cp', str(BUILD_DIR / 'fake_sparkle.dylib'), str(target)], check=True)
    target.chmod(0o755)
    return {'step': 'swap-sparkle', 'changed': True,
            'note': f'original conservé : {backup}'}


def patch_security_path() -> dict:
    binary = DST_APP / 'Contents/MacOS/Meta AI'
    if b'.reprise/sec-shim.dylib' in binary.read_bytes():
        return {'step': 'patch-security', 'changed': False}
    out = subprocess.run([sys.executable, str(REPRISE / 'metaai_patch_dylib_path.py'),
                          str(binary), SECURITY_PREFIX, SHIM_LOAD_PATH],
                         check=True, capture_output=True, text=True)
    binary.chmod(0o755)  # le patcheur réécrit via write_bytes : bit exec à restaurer
    return {'step': 'patch-security', 'changed': True, 'detail': out.stdout.strip()}


def sign(identity: str) -> dict:
    sh('codesign', '--force', '--deep', '--sign', identity, str(DST_APP))
    binary = DST_APP / 'Contents/MacOS/Meta AI'
    sh('codesign', '--force', '--sign', identity, str(binary))
    binary.chmod(0o755)
    sh('codesign', '--verify', '--deep', str(DST_APP))
    return {'step': 'sign', 'identity': identity,
            'note': 'runtime durci absent (signature par défaut), ad-hoc pour les dylibs'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity', default=None,
                        help='empreinte de l\'identité Apple Development (autodétection sinon)')
    parser.add_argument('--json', action='store_true', help='sortie JSON machine')
    args = parser.parse_args()
    if not SRC_APP.exists():
        raise SystemExit(f'{SRC_APP} introuvable')
    identity = find_identity(args.identity)
    steps = [
        copy_app(),
        prepare_sparkle_real(),
        compile_dylibs(),
        swap_sparkle(),
        patch_security_path(),
        sign(identity),
    ]
    result = {
        'app': str(DST_APP),
        'socket': str(RUNTIME / 'metaai-reprise-trigger.sock'),
        'log': str(LOG),
        'steps': steps,
        'next_step': (
            f'Lancement proposé (jamais exécuté par ce script) : '
            f'nohup "{DST_APP}/Contents/MacOS/Meta AI" ; contrôle via '
            'metaai_reprise_client.py (ping|dump|insert|send|len)'
        ),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
