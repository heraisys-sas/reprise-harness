"""Closed inventory and integrity checks for the vendored YAML package."""
import hashlib
from pathlib import Path

YAML_FILES = ('LICENSE', '__init__.py', 'composer.py', 'constructor.py', 'cyaml.py',
              'dumper.py', 'emitter.py', 'error.py', 'events.py', 'loader.py',
              'nodes.py', 'parser.py', 'reader.py', 'representer.py', 'resolver.py',
              'scanner.py', 'serializer.py', 'tokens.py')


def yaml_hashes(root, *, exact=False):
    root = Path(root)
    vendor = root / 'vendor'
    yaml = vendor / 'yaml'
    if vendor.is_symlink() or yaml.is_symlink():
        raise ValueError('Répertoire YAML lié : opération refusée.')
    if not yaml.exists():
        return {}
    if not yaml.is_dir():
        raise ValueError('Répertoire YAML invalide.')
    if exact:
        names = {item.name for item in yaml.iterdir()}
        if names != set(YAML_FILES):
            raise ValueError('Contenu YAML incomplet ou inattendu.')
    result = {}
    for name in YAML_FILES:
        item = yaml / name
        if item.is_symlink() or not item.is_file():
            raise ValueError(f'Fichier YAML requis absent ou lien symbolique : {name}')
        result[name] = hashlib.sha256(item.read_bytes()).hexdigest()
    return result


def validate_yaml(root, payload):
    actual = yaml_hashes(root, exact=True)
    if 'vendor_yaml' not in payload:
        if actual:
            raise ValueError('Ancien manifeste YAML non authentifié ; reconstruire le bundle.')
        return
    expected = payload['vendor_yaml']
    if not isinstance(expected, dict) or set(expected) != set(YAML_FILES) or actual != expected:
        raise ValueError('Payload YAML modifié ou incomplet ; reconstruire le bundle.')
