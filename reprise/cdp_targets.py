"""Target registry for the CDP dispatch transport.

Targets are declared in the versioned cdp_targets.json shipped with the repo
and can be overridden per target by ~/.local/share/claude-cdp/targets.json
(user file wins). Every shipped target ships disabled: enabling a route is an
explicit operator action, and a disabled or unknown target yields
route_disabled before any transport is touched.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

_REPO_FILE = Path(__file__).with_name('cdp_targets.json')
_OVERRIDE_FILE = Path.home() / '.local/share/claude-cdp/targets.json'
_REQUIRED_SELECTORS = ('composer', 'send', 'user_message', 'assistant_message')


def _validate(name, entry):
    if not isinstance(name, str) or not name or not isinstance(entry, dict):
        raise ValueError('Cible CDP invalide')
    origin = urlsplit(entry.get('origin', ''))
    if origin.scheme != 'https' or not origin.netloc or origin.path not in ('', '/'):
        raise ValueError(f"Cible {name} : origin https requis")
    pattern = entry.get('chat_url_pattern')
    if (not isinstance(pattern, str) or '{chat_id}' not in pattern
            or pattern.startswith('/') is False or '?' in pattern or '#' in pattern):
        raise ValueError(f"Cible {name} : chat_url_pattern invalide")
    selectors = entry.get('selectors')
    if not isinstance(selectors, dict) or not all(
            isinstance(selectors.get(key), str) and selectors[key]
            for key in _REQUIRED_SELECTORS):
        raise ValueError(f"Cible {name} : sélecteurs requis ({', '.join(_REQUIRED_SELECTORS)})")
    max_chars = entry.get('max_prompt_chars', 12000)
    if type(max_chars) is not int or not 1 <= max_chars <= 12000:
        raise ValueError(f"Cible {name} : max_prompt_chars invalide")
    profile = entry.get('profile')
    if not isinstance(profile, str) or not profile or '/' in profile or profile.startswith('.'):
        raise ValueError(f"Cible {name} : profile invalide")
    return {'enabled': bool(entry.get('enabled')), 'origin': entry['origin'],
            'chat_url_pattern': pattern, 'profile': profile,
            'max_prompt_chars': max_chars, 'selectors': dict(selectors)}


def _registry():
    repo = json.loads(_REPO_FILE.read_text())
    registry = {name: _validate(name, entry)
                for name, entry in repo.get('targets', {}).items()}
    try:
        override = json.loads(_OVERRIDE_FILE.read_text())
    except (OSError, ValueError):
        return registry
    if not isinstance(override, dict):
        return registry
    for name, entry in override.get('targets', {}).items():
        merged = dict(registry.get(name, {}), **entry)
        if isinstance(merged.get('selectors'), dict) and registry.get(name):
            merged['selectors'] = dict(registry[name]['selectors'], **entry.get('selectors', {}))
        registry[name] = _validate(name, merged)
    return registry


def load(name):
    """Return the validated config for name, or None when absent."""
    if not isinstance(name, str) or not name:
        return None
    return _registry().get(name)


def enabled_names():
    return sorted(name for name, entry in _registry().items() if entry['enabled'])
