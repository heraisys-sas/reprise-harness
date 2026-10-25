#!/usr/bin/env python3
"""Read-only static census of the external entry points that can reach Claude Desktop's Quick Entry dispatch.

Answers one question without launching, modifying or automating anything: which non-UI, already
existing mechanism (URL scheme, macOS Service, user activity, scripting dictionary, second-instance
or open-file event, ...) leads, through the app's own code, to `dispatchOnQuickEntrySubmit`?

Reads Contents/Info.plist and Contents/Resources/app.asar of an installed app (default
/Applications/Claude.app) and prints a Markdown report. It never writes next to the app, never starts
it, never opens a network connection. The call graph is a heuristic over minified code: every edge is
a lead to confirm by reading the cited snippet, not a proof.
"""
import argparse
import json
import plistlib
import re
import struct
import sys
from pathlib import Path

ANCHOR = 'dispatchOnQuickEntrySubmit'
MARKERS = ('open-url', 'second-instance', 'open-file', 'setAsDefaultProtocolClient', 'protocol.handle',
           'continue-activity', 'handleDeepLink', 'deeplink', 'deepLink', 'globalShortcut', 'claude://',
           'NSUserActivity', 'quickEntrySubmitted', 'requestQuickWindowDismissWithPayload')
PLIST_KEYS = ('CFBundleIdentifier', 'CFBundleShortVersionString', 'CFBundleVersion', 'LSMinimumSystemVersion',
              'LSUIElement', 'NSAppleScriptEnabled', 'OSAScriptingDefinition', 'NSUserActivityTypes')
MAX_SCRIPT = 80 * 1024 * 1024
DECL = re.compile(r'(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\('
                  r'|(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?'
                  r'(?:function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)')
LONG_TOKEN = re.compile(r'[A-Za-z0-9_\-+/=]{40,}')


class CensusError(Exception):
    pass


def read_asar(path):
    """Return (files, data_offset) where files maps 'a/b.js' to its header entry."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise CensusError(f'app.asar introuvable ou lien symbolique : {path}')
    with path.open('rb') as handle:
        prefix = handle.read(16)
        if len(prefix) < 16:
            raise CensusError('Archive asar tronquée')
        size_pickle, header_size, _inner, json_length = struct.unpack('<IIII', prefix)
        if size_pickle != 4 or json_length > header_size or header_size > 64 * 1024 * 1024:
            raise CensusError('En-tête asar inattendu')
        header = json.loads(handle.read(json_length).decode('utf-8'))
    files = {}

    def walk(node, prefix=''):
        for name, entry in (node.get('files') or {}).items():
            full = prefix + name
            if 'files' in entry:
                walk(entry, full + '/')
            else:
                files[full] = entry
    walk(header)
    return files, 8 + header_size


def read_entry(path, base, entry):
    if entry.get('unpacked') or 'offset' not in entry:
        return b''
    size = int(entry.get('size', 0))
    if size > MAX_SCRIPT:
        return b''
    with Path(path).open('rb') as handle:
        handle.seek(base + int(entry['offset']))
        return handle.read(size)


def main_scripts(asar, files, base):
    """Application scripts (not node_modules) large enough to be a bundle, as (name, text)."""
    found = []
    for name, entry in sorted(files.items()):
        if name.startswith('node_modules/') or '/node_modules/' in name:
            continue
        if not name.endswith(('.js', '.cjs', '.mjs')) or int(entry.get('size', 0)) < 20000:
            continue
        found.append((name, read_entry(asar, base, entry).decode('utf-8', 'replace')))
    return found


def redact(text):
    return LONG_TOKEN.sub('<redacted>', text.replace('\n', ' '))


def declarations(text):
    return [(m.start(), m.group(1) or m.group(2)) for m in DECL.finditer(text)]


def enclosing(decls, position):
    owner = None
    for start, name in decls:
        if start > position:
            break
        owner = name
    return owner


def references(text, name):
    pattern = re.compile(r'(?<![\w$.])' + re.escape(name) + r'(?![\w$])')
    return [m.start() for m in pattern.finditer(text)]


def call_chain(text, depth=6, limit=60):
    """Backward closure from the anchor: {function: [(caller, position)]}."""
    decls = declarations(text)
    seeds = {}
    for match in re.finditer(re.escape(ANCHOR), text):
        owner = enclosing(decls, match.start())
        if owner:
            seeds.setdefault(owner, match.start())
    graph, frontier, seen = {}, list(seeds), set(seeds)
    for _ in range(depth):
        following = []
        for name in frontier:
            callers = []
            for position in references(text, name)[:40]:
                caller = enclosing(decls, position)
                if caller and caller != name:
                    callers.append((caller, position))
            graph[name] = callers
            for caller, _ in callers:
                if caller not in seen and len(seen) < limit:
                    seen.add(caller)
                    following.append(caller)
        frontier = following
    return seeds, graph


def declaration_snippet(text, decls, name, width=260):
    for start, declared in decls:
        if declared == name:
            return redact(text[start:start + width])
    return ''


def plist_section(app):
    out = ['## 1. Identité et déclarations natives (Info.plist)', '']
    info = Path(app) / 'Contents' / 'Info.plist'
    if not info.is_file():
        return out + [f'Info.plist introuvable : `{info}`', '']
    with info.open('rb') as handle:
        data = plistlib.load(handle)
    for key in PLIST_KEYS:
        if key in data:
            out.append(f'- `{key}` : `{data[key]}`')
    schemes = [(item.get('CFBundleURLName'), item.get('CFBundleURLSchemes')) for item in data.get('CFBundleURLTypes', [])]
    out.append(f'- **Schémas d’URL** (`CFBundleURLTypes`) : {schemes if schemes else "aucun"}')
    services = [(item.get('NSMenuItem', {}).get('default'), item.get('NSMessage'), item.get('NSSendTypes'))
                for item in data.get('NSServices', [])]
    out.append(f'- **Services macOS** (`NSServices`, nom, message, types) : {services if services else "aucun"}')
    resources = Path(app) / 'Contents' / 'Resources'
    extras = sorted(path.name for path in resources.iterdir()
                    if path.suffix in {'.sdef', '.appintents', '.scpt'} or 'appintents' in path.name.lower()) if resources.is_dir() else []
    out.append(f'- Dictionnaire de script / App Intents (ressources) : {extras if extras else "aucun"}')
    for folder in ('PlugIns', 'XPCServices'):
        path = Path(app) / 'Contents' / folder
        out.append(f'- `{folder}` : {sorted(item.name for item in path.iterdir()) if path.is_dir() else "absent"}')
    return out + ['']


def code_sections(app):
    out = []
    asar = Path(app) / 'Contents' / 'Resources' / 'app.asar'
    files, base = read_asar(asar)
    scripts = main_scripts(asar, files, base)
    out += ['## 2. Scripts applicatifs analysés', '']
    out += [f'- `{name}` ({len(text)} caractères)' for name, text in scripts] or ['Aucun script de plus de 20 000 octets.']
    out.append('')
    for name, text in scripts:
        if ANCHOR not in text:
            continue
        decls = declarations(text)
        out += [f'## 3. Chaîne d’appel vers `{ANCHOR}` dans `{name}` (heuristique, à confirmer sur l’extrait)', '']
        seeds, graph = call_chain(text)
        out.append(f'Fonction(s) contenant l’ancre : {sorted(seeds)}')
        out.append('')
        for function, callers in graph.items():
            out.append(f'### `{function}` — `{declaration_snippet(text, decls, function)}`')
            for caller, position in callers[:12]:
                out.append(f'- appelée dans `{caller}` (offset {position}) : `{redact(text[max(0, position - 90):position + 140])}`')
            if not callers:
                out.append('- aucun appelant trouvé')
            out.append('')
        out += ['## 4. Entrées externes détectées dans le même script (premières occurrences)', '']
        for marker in MARKERS:
            hits = [m.start() for m in re.finditer(re.escape(marker), text)][:4]
            if not hits:
                out.append(f'- `{marker}` : absent')
                continue
            out.append(f'- `{marker}` : {len(re.findall(re.escape(marker), text))} occurrence(s)')
            for position in hits:
                owner = enclosing(decls, position)
                out.append(f'  - dans `{owner}` (offset {position}) : `{redact(text[max(0, position - 80):position + 200])}`')
        out.append('')
    if not any(ANCHOR in text for _, text in scripts):
        out += [f'`{ANCHOR}` absent de tous les scripts analysés : la chaîne documentée ne correspond pas à ce bundle.', '']
    return out


def report(app):
    app = Path(app)
    lines = ['# Recensement statique Quick Apply (lecture seule)', '', f'Application : `{app}`', '']
    lines += plist_section(app)
    lines += code_sections(app)
    lines += ['## Limites', '',
              '- Lecture seule : aucune exécution, aucun lancement de l’application, aucun accès réseau, aucune écriture hors de ce rapport.',
              '- Graphe d’appels heuristique sur code minifié (portée des fonctions approximée par la déclaration précédente) : confirmer chaque lien sur l’extrait cité.',
              '- Un lien trouvé ne prouve ni l’accessibilité par un processus externe ni l’absence de garde (compte, attestation) : à qualifier dans ASTRA-QUICK-APPLY.md.']
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('app', nargs='?', default='/Applications/Claude.app')
    parser.add_argument('--out', help='fichier de rapport à créer (refuse d’écraser) ; défaut : sortie standard')
    args = parser.parse_args(argv)
    try:
        text = report(args.app)
    except (CensusError, OSError, ValueError, KeyError) as error:
        print(f'Recensement impossible : {error}', file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, 'x', encoding='utf-8') as output:
            output.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
