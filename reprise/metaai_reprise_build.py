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
import hashlib
import json
import plistlib
import shutil
import socket
import subprocess
import sys
import time
import uuid
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
STATE_FILE = RUNTIME / 'metaai-reprise-state.json'
SPARKLE_META = RUNTIME / 'Sparkle-real.meta.json'
REQUIRED_TOOLS = ('clang', 'codesign', 'rsync', 'install_name_tool', 'security')
PLAN_NAMES = ['metaai-reprise']


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
    # Meta AI distribue ses binaires en r-xr-xr-x : sans réinscription, codesign --deep
    # échoue en Permission denied en tentant de réécrire les _CodeSignature/CodeResources.
    subprocess.run(['chmod', '-R', 'u+w', str(DST_APP)], check=True)
    return {'step': 'copy', 'changed': True, 'app': str(DST_APP),
            'note': 'copie rsync -a : xattrs (quarantaine/provenance) non copiés'}


def prepare_sparkle_real() -> dict:
    """Extraire le vrai Sparkle (de l'originale) et lui donner un LC_ID absolu.

    Réextrait automatiquement quand le Sparkle de l'originale change (mise à
    jour de Meta AI) : l'empreinte courante est comparée à celle consignée dans
    Sparkle-real.meta.json — la comparaison directe des octets est impossible
    (le LC_ID et la signature ad-hoc diffèrent dès l'extraction).
    """
    real = SRC_APP / SPARKLE_REL
    if not real.exists():
        raise SystemExit(f'Sparkle introuvable dans l\'originale : {real}')
    source_digest = hashlib.sha256(real.read_bytes()).hexdigest()
    try:
        meta = json.loads(SPARKLE_META.read_text())
    except (OSError, ValueError):
        meta = {}
    if SPARKLE_REAL.exists() and meta.get('source_digest') == source_digest:
        return {'step': 'sparkle-real', 'changed': False, 'path': str(SPARKLE_REAL)}
    RUNTIME.mkdir(parents=True, exist_ok=True)
    subprocess.run(['cp', str(real), str(SPARKLE_REAL)], check=True)
    SPARKLE_REAL.chmod(0o755)
    subprocess.run(['install_name_tool', '-id', str(SPARKLE_REAL), str(SPARKLE_REAL)], check=True,
                   capture_output=True)
    sh('codesign', '--force', '-s', '-', str(SPARKLE_REAL))
    SPARKLE_META.write_text(json.dumps({'source_digest': source_digest}, indent=2) + '\n')
    return {'step': 'sparkle-real', 'changed': True, 'path': str(SPARKLE_REAL),
            'note': 'réextrait : le Sparkle de l originale a changé' if meta else None}


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


def refresh_app() -> dict:
    """Resynchroniser la copie depuis l'originale (redéploiement après mise à jour).

    La copie est entièrement dérivée de l'originale : rsync --delete y est sûr.
    Le Sparkle déjà patché est réinjecté ensuite par swap_sparkle ; la sauvegarde
    Sparkle.reprise-real obsolète est retirée pour repartir d'un Sparkle neuf.
    """
    if not DST_APP.exists():
        return copy_app()
    subprocess.run(['rsync', '-a', '--delete', str(SRC_APP) + '/', str(DST_APP) + '/'], check=True)
    # Meta AI distribue ses binaires en r-xr-xr-x : sans réinscription, codesign --deep
    # échoue en Permission denied en tentant de réécrire les _CodeSignature/CodeResources.
    subprocess.run(['chmod', '-R', 'u+w', str(DST_APP)], check=True)
    backup = (DST_APP / SPARKLE_REL).parent / 'Sparkle.reprise-real'
    if backup.exists():
        backup.unlink()
    return {'step': 'refresh-copy', 'changed': True,
            'note': 'copie resynchronisée depuis l originale (rsync -a --delete)'}


# --- Pont GUI (pattern codex-integration) : inspect / plan / apply / verify ---

def socket_ping(timeout: float = 2.0):
    """Ping de la socket de contrôle : True (répond), False (muette), None (absente)."""
    path = RUNTIME / 'metaai-reprise-trigger.sock'
    if not path.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(path))
        sock.sendall((json.dumps({'cmd': 'ping'}) + '\n').encode())
        buf = b''
        while b'\n' not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        sock.close()
        return bool(buf)
    except OSError:
        return False


def tree_digest(app: Path) -> str:
    """Empreinte SHA-256 de l'arbre du bundle (chemins relatifs + contenus, triés)."""
    hasher = hashlib.sha256()
    for path in sorted(p for p in app.rglob('*') if p.is_file() and not p.is_symlink()):
        hasher.update(str(path.relative_to(app)).encode())
        hasher.update(b'\0')
        file_hash = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                file_hash.update(chunk)
        hasher.update(file_hash.digest())
    return hasher.hexdigest()


def read_version(app: Path):
    try:
        with (app / 'Contents/Info.plist').open('rb') as handle:
            return plistlib.load(handle).get('CFBundleShortVersionString')
    except (OSError, plistlib.InvalidFileException):
        return None


def patched_state(app: Path) -> dict:
    """État de patch de la copie, en lecture seule (marqueurs, signature, version)."""
    if not app.exists():
        return {'present': False}
    checks = {}
    for key, rel in (('sparkle_patched', SPARKLE_REL), ('binary_patched', 'Contents/MacOS/Meta AI')):
        marker = b'.reprise/Sparkle-real' if key == 'sparkle_patched' else b'.reprise/sec-shim.dylib'
        try:
            checks[key] = marker in (app / rel).read_bytes()
        except OSError:
            checks[key] = False
    verify = subprocess.run(['codesign', '--verify', '--deep', str(app)],
                            capture_output=True, text=True)
    return dict(checks, present=True, signature_ok=verify.returncode == 0, version=read_version(app))


def original_running() -> bool:
    return subprocess.run(['pgrep', '-f', str(SRC_APP)], capture_output=True, text=True).returncode == 0


def copy_running() -> bool:
    return subprocess.run(['pgrep', '-f', str(DST_APP)], capture_output=True, text=True).returncode == 0


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + '\n')


def missing_tools() -> list:
    return [name for name in REQUIRED_TOOLS if shutil.which(name) is None]


def refused(message: str) -> dict:
    return {'ok': False, 'status': 'refused', 'status_label': 'Refusé', 'message': message}


def inspect_payload() -> dict:
    src_present = SRC_APP.exists()
    src_version = read_version(SRC_APP) if src_present else None
    src_digest = tree_digest(SRC_APP) if src_present else None
    copy = patched_state(DST_APP)
    state = load_state()
    up_to_date = bool(src_digest and copy.get('present') and state.get('original_tree_digest') == src_digest
                      and copy.get('sparkle_patched') and copy.get('binary_patched') and copy.get('signature_ok'))
    tools = missing_tools()
    if not src_present:
        ok, label, message = False, 'Originale introuvable', f'{SRC_APP} introuvable — rien à redéployer.'
    elif not copy.get('present'):
        ok, label, message = True, 'Copie absente', 'Aucune copie déployée ; préparez un redéploiement pour l installer.'
    elif up_to_date:
        ok, label, message = True, 'Copie à jour', \
            f"Copie conforme à l'originale (version {src_version}) ; lanceur et socket gérés par metaaid."
    else:
        ok, label, message = True, 'Redéploiement disponible', \
            (f"L'originale (version {src_version}) diffère du dernier déploiement "
             f"({state.get('original_version') or 'inconnu'}) ; préparez puis appliquez un redéploiement.")
    return {'ok': ok, 'status_label': label, 'message': message,
            'original': {'path': str(SRC_APP), 'present': src_present, 'version': src_version,
                         'tree_digest': src_digest, 'running': original_running()},
            'copy': dict(copy, path=str(DST_APP), up_to_date=up_to_date, running=copy_running()),
            'socket_alive': socket_ping(), 'tools_missing': tools, 'plan_available': ok}


def plan_payload() -> dict:
    info = inspect_payload()
    feasible = bool(info.get('ok')) and not info['original']['running'] and not info['tools_missing']
    will_refresh = info['copy'].get('present') and not info['copy'].get('up_to_date')
    steps = []
    if not info['copy'].get('present') or will_refresh:
        steps.append({'step': 'refresh-copy', 'detail': 'resynchronisation de la copie depuis l originale (rsync)'})
    steps.extend([
        {'step': 'sparkle-real', 'detail': 'extraction du vrai Sparkle si son empreinte a changé'},
        {'step': 'compile', 'detail': 'compilation des dylibs (fake Sparkle, shim Security)'},
        {'step': 'swap-sparkle', 'detail': 'remplacement du binaire Sparkle de la copie'},
        {'step': 'patch-security', 'detail': 'réécriture du chemin Security.framework vers le shim'},
        {'step': 'sign', 'detail': 're-signature Apple Development de la copie'},
    ])
    plan = {'names': list(PLAN_NAMES), 'action': 'redeploy', 'id': uuid.uuid4().hex,
            'target': str(DST_APP), 'original': info['original'], 'copy': info['copy'],
            'original_running': info['original']['running'], 'tools_missing': info['tools_missing'],
            'socket_alive': info['socket_alive'], 'steps': steps, 'feasible': feasible}
    if info['original']['running']:
        message = 'Meta AI (originale) est en cours d exécution — quittez-la puis replanifiez.'
    elif info['tools_missing']:
        message = 'Outils de build manquants : ' + ', '.join(info['tools_missing']) + '.'
    else:
        message = 'Plan de redéploiement prêt ; il attend votre accord.'
    return {'plan': plan, 'ok': True,
            'status_label': 'Plan prêt' if feasible else 'Plan bloqué', 'message': message}


def apply_payload(plan) -> dict:
    if not isinstance(plan, dict) or plan.get('names') != PLAN_NAMES or plan.get('action') != 'redeploy':
        return refused('Plan de redéploiement Meta AI reconnu requis.')
    if plan.get('target') != str(DST_APP):
        return refused('Cible du plan inattendue : ' + str(plan.get('target')))
    if original_running():
        return refused('Meta AI (originale) est en cours d exécution — quittez-la puis replanifiez.')
    missing = missing_tools()
    if missing:
        return refused('Outils de build manquants : ' + ', '.join(missing) + '.')
    if not SRC_APP.exists():
        return refused(f'{SRC_APP} introuvable.')
    src_digest = tree_digest(SRC_APP)
    if plan.get('original', {}).get('tree_digest') != src_digest:
        return refused("Plan périmé : l'originale a changé depuis la préparation — replanifiez.")
    try:
        identity = find_identity(None)
    except SystemExit as error:
        return refused(str(error))
    try:
        steps = [refresh_app(), prepare_sparkle_real(), compile_dylibs(),
                 swap_sparkle(), patch_security_path(), sign(identity)]
    except SystemExit as error:
        return refused('Redéploiement interrompu : ' + str(error))
    except subprocess.CalledProcessError as error:
        return refused('Étape de build en échec : ' + str(error))
    save_state({'original_tree_digest': src_digest, 'original_version': read_version(SRC_APP),
                'deployed_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'last_plan_id': plan.get('id')})
    return {'ok': True, 'status_label': 'Redéploiement appliqué', 'app': str(DST_APP),
            'steps': steps,
            'message': 'Copie reconstruite et re-signée ; le lancement reste géré par le lanceur metaaid.'}


def verify_payload(plan) -> dict:
    if not isinstance(plan, dict) or plan.get('names') != PLAN_NAMES:
        return refused('Plan de redéploiement Meta AI reconnu requis.')
    copy = patched_state(DST_APP)
    state = load_state()
    src_digest = tree_digest(SRC_APP) if SRC_APP.exists() else None
    checks = {'copy_present': copy.get('present') is True,
              'sparkle_patched': copy.get('sparkle_patched') is True,
              'binary_patched': copy.get('binary_patched') is True,
              'signature_ok': copy.get('signature_ok') is True,
              'state_digest_current': bool(src_digest) and state.get('original_tree_digest') == src_digest,
              'plan_id_matches': bool(plan.get('id')) and state.get('last_plan_id') == plan.get('id')}
    ok = all(checks.values())
    return {'ok': ok, 'checks': checks, 'copy': dict(copy, path=str(DST_APP)),
            'status_label': 'Redéploiement vérifié' if ok else 'Vérification non conforme',
            'message': 'Copie conforme au dernier déploiement enregistré.' if ok else
                       "Un ou plusieurs contrôles ont échoué — relisez l'état avant toute nouvelle opération."}


def metaai_cli(root, argv):
    """CLI JSON pour la GUI Reprise (mêmes conventions que codex-integration-*)."""
    parser = argparse.ArgumentParser(prog='metaai-integration')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('metaai-integration-inspect')
    sub.add_parser('metaai-integration-plan')
    sub.add_parser('metaai-integration-apply')
    sub.add_parser('metaai-integration-verify')
    args = parser.parse_args(argv)
    try:
        if args.action == 'metaai-integration-inspect':
            result = inspect_payload()
        elif args.action == 'metaai-integration-plan':
            result = plan_payload()
        else:
            value = json.load(sys.stdin).get('plan')
            result = apply_payload(value) if args.action == 'metaai-integration-apply' else verify_payload(value)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get('ok') is False else 0
    except (OSError, ValueError, KeyError, TypeError, EOFError, subprocess.SubprocessError) as error:
        print(json.dumps({'ok': False, 'status': 'refused', 'status_label': 'Refusé',
                          'message': str(error)}, ensure_ascii=False))
        return 1


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
