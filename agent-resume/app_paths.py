import os
from pathlib import Path


def bundled(root):
    root = Path(root).resolve()
    return root.name == 'helpers' and root.parent.name == 'Resources' and root.parent.parent.name == 'Contents'


def state_directory(root, name="state"):
    root = Path(root).resolve()
    if 'AGENT_RESUME_STATE' in os.environ:
        return Path(os.environ['AGENT_RESUME_STATE']).expanduser() if name == 'state' else Path(os.environ['AGENT_RESUME_STATE']).expanduser() / name
    if not bundled(root):
        return root / name
    target = Path.home() / 'Library/Application Support/Reprise' / name
    legacy = root.parents[3] / name
    if any(legacy.glob('*.sqlite')) and not target.exists():
        raise RuntimeError('État historique détecté près du bundle ; import sauvegardé requis, ou AGENT_RESUME_STATE explicite.')
    from state_install import initialized
    if not initialized(target) and not any(target.glob('*.sqlite')) and os.environ.get('REPRISE_ALLOW_NEW_STATE') != '1':
        raise RuntimeError('Initialisation du prototype bloquée : utiliser state-import ou state-init --target explicite (REPRISE_ALLOW_NEW_STATE=1 reste compatible).')
    return target


def native_binary(root, name):
    root = Path(root).resolve()
    if bundled(root):
        return root.parent.parent / 'MacOS' / name
    return root / 'Reprise.app/Contents/MacOS' / name
