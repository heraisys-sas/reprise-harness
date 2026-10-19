"""Reprise post-update health check.

Two layers:

1. Deterministic (this module, no ML): after an update of Claude Desktop,
   Claude Code, Codex or another harness, decide (a) whether a supervised
   process (e.g. the Codex or Quick Entry server) should be relaunched and
   (b) whether the log signatures Reprise parses are still present. This is
   pure evidence: versions, process liveness and log lines.

2. Generative (Foundation Models, optional helper): explain recent activity
   and surface actions that need a human in the Reprise UI. That layer is an
   enrichment; the deterministic layer must stand alone.

Nothing here automates the UI or launches anything without an explicit
operator action.
"""
from pathlib import Path
import json
import os

import hashlib

import reprise_logs

DEFAULT_LOG_DIR = reprise_logs.DEFAULT_LOG_DIR
# R4: cached explanations are keyed to the source digest; stale ones are
# marked historical rather than shown as current.
_EXPLANATION_CACHE = {}

STATE_DIR = Path.home() / '.claude-desktop'

# Log signatures Reprise relies on. If one disappears after an update, the
# parser for it is stale and must be flagged (not silently ignored).
LOG_SIGNATURES = {
    'quick_entry_submitted': 'Quick entry submitted',
    'quick_entry_routing': '[QuickEntry] Submitting payload to',
    'quick_entry_guard': '[QuickEntry]',
    'mcp_interaction': 'Message from client',
}

# Supervised processes Reprise may need to relaunch after an update.
SUPERVISED = {
    'codex-server': {'label': 'Serveur Codex local', 'marker': 'app-server'},
    'quick-entry-server': {'label': 'Serveur Quick Entry', 'marker': 'claude_desktop_quick'},
}


def _read(path):
    try:
        return Path(path).read_text(errors='replace')
    except OSError:
        return ''


def log_signatures(log_dir=DEFAULT_LOG_DIR):
    """Report which watched log signatures are still present after an update."""
    text = ''
    main = Path(log_dir) / 'main.log'
    if main.is_file():
        text += _read(main)
    for extra in Path(log_dir).glob('mcp-server-*.log'):
        text += _read(extra)
    result = {}
    for name, marker in LOG_SIGNATURES.items():
        result[name] = {'present': marker in text, 'marker': marker}
    return result


def process_liveness():
    """Liveness of supervised servers via their socket, not process listing.

    The process listing is often denied to a sandboxed helper; the server
    socket is the authoritative signal Reprise already trusts (engines.
    socket_path). Falls back to 'unknown' rather than guessing.
    """
    import engines
    probes = {
        'quick-entry-server': Path.home() / '.claude-desktop/quick-entry.sock',
        'codex-server': engines.SOCKET,
    }
    result = {}
    for name, path in probes.items():
        if not Path(path).exists():
            result[name] = 'stopped'  # socket absent: the server is not listening
            continue
        try:
            engines.socket_path({'socket': str(path)})
            result[name] = 'running'
        except engines.Unavailable:
            # Socket exists but is rejected (not private, wrong owner, broken
            # symlink): the listener state is unknown, not proven stopped.
            result[name] = 'indeterminate'
        except Exception:
            result[name] = 'indeterminate'
    return result


def read_versions(bundle_root):
    """Read installed versions of known harness bundles (evidence, not guess)."""
    versions = {}
    candidates = {
        'claude-desktop': Path('/Applications/Claude.app/Contents/Info.plist'),
        'codex': Path('/Applications/Codex.app/Contents/Info.plist'),
    }
    for name, plist in candidates.items():
        if not plist.is_file():
            versions[name] = None
            continue
        try:
            import subprocess
            out = subprocess.run(['defaults', 'read', str(plist), 'CFBundleShortVersionString'],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            versions[name] = out or None
        except Exception:
            versions[name] = None
    return versions


def check(bundle_root, log_dir=DEFAULT_LOG_DIR):
    """Post-update health: what must be relaunched and what is still parsed."""
    versions = read_versions(bundle_root)
    signatures = log_signatures(log_dir)
    liveness = process_liveness()
    stale = [name for name, s in signatures.items() if not s['present']]
    relaunch = [name for name, state in liveness.items() if state == 'stopped']
    return {
        'versions': versions,
        'log_signatures': signatures,
        'process_liveness': liveness,
        'stale_log_signatures': stale,
        'needs_relaunch': relaunch,
        'actions_for_human': ([f'Relancer {SUPERVISED[n]["label"]}' for n in relaunch]
                              + [f'Adapter le parsing de la signature « {signatures[s]["marker"]} »' for s in stale]),
        'note': 'Deterministic evidence only; generative explanation is a separate, optional layer.',
    }


def explain(facts, question=None, binary=None):
    """Generative layer: explain activity + human actions via Foundation Models.

    Calls the on-device FoundationExplain helper (Swift, FoundationModels).
    Degrades to {'available': False} when the helper or model is unavailable;
    the deterministic layer must remain usable regardless.
    """
    import subprocess
    if binary:
        binary = Path(binary)
    else:
        candidates = [Path(__file__).parent / 'FoundationExplain',
                      Path(__file__).parent / 'Reprise.app/Contents/MacOS/FoundationExplain']
        binary = next((c for c in candidates if c.is_file()), candidates[0])
    if not binary.is_file():
        return {'available': False, 'answer': None, 'reason': 'FoundationExplain helper not built'}
    payload = {'facts': facts, 'question': question or 'Explique l activité récente et liste les actions qui requièrent un humain.'}
    try:
        out = subprocess.run([str(binary)], input=json.dumps(payload, ensure_ascii=False),
                             capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout or '{}')
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {'available': False, 'answer': None, 'reason': f'Foundation Models unavailable: {error}'}


def _digest(facts):
    """R4: lossless digest (strict UTF-8) so distinct facts never collide."""
    try:
        data = facts.encode('utf-8')  # strict: raises on lone surrogates
    except UnicodeEncodeError as error:
        raise ValueError('Invalid Unicode in explanation source') from error
    return hashlib.sha256(data).hexdigest()  # full digest, no truncation


def cached_explanation(facts, question=None, binary=None):
    """R4: explanation keyed to source digest AND question; stale entries marked historical.

    The historical flag is refreshed on both hits and misses so the cached
    entry always reflects the latest source state.
    """
    key = (_digest(facts), question or '')
    cached = _EXPLANATION_CACHE.get(key)
    if cached and cached.get('answer'):
        _mark_historical(key)  # Medium#7: refresh flags even on a hit
        return {**cached, 'cached': True, 'historical': False}
    result = explain(facts, question, binary=binary)
    if result.get('available'):
        _EXPLANATION_CACHE[key] = dict(result)
    _mark_historical(key)
    return {**result, 'cached': False, 'historical': False}


def _mark_historical(active_key):
    for key, entry in _EXPLANATION_CACHE.items():
        entry['historical'] = key != active_key


def summarize(bundle_root, log_dir=DEFAULT_LOG_DIR, binary=None):
    """Health + optional generative explanation, for the Reprise UI and MCP."""
    health = check(bundle_root, log_dir)
    health['quick_entry_features'] = quick_entry_features()
    # The generative explanation lives in local analysis; health stays deterministic.
    return health


# --- Update watch: detect a harness update and produce a health report -----
def watch(db, bundle_root, log_dir=DEFAULT_LOG_DIR):
    """Detect a post-update change and persist the resulting health report.

    Compares current versions against the last recorded state; when any
    version changes (or a first run), records an 'update_detected' health
    report. Returns the report with an 'update_detected' flag. Read-only with
    respect to processes: it never relaunches anything, it only reports.
    """
    import scheduler
    current = read_versions(bundle_root)
    previous = scheduler.get(db, 'health_versions', None)
    previous_components = scheduler.get(db, 'health_components', None)
    # Back-compat: an old flat component map has no 'codex' key yet.
    previous_claude = previous_components.get('claude') if isinstance(previous_components, dict) else previous_components
    previous_codex = previous_components.get('codex') if isinstance(previous_components, dict) else None
    anomaly_report = quick_entry_anomalies(log_dir, previous_claude)
    codex_anomaly_report = codex_anomalies(previous_codex)
    with db:
        scheduler.put(db, 'health_components', {
            'claude': anomaly_report['components'],
            'codex': codex_anomaly_report['components'],
        })
    report = check(bundle_root, log_dir)
    update_detected = previous is None or current != previous
    report['update_detected'] = update_detected
    report['quick_entry_anomalies'] = anomaly_report['anomalies']
    report['codex_features'] = codex_features()
    report['codex_anomalies'] = codex_anomaly_report['anomalies']
    report['previous_versions'] = previous
    with db:
        scheduler.put(db, 'health_versions', current)
        if update_detected:
            scheduler.put(db, 'health_report', report)
            scheduler.put(db, 'health_update_at', __import__('time').time())
    return report


# --- Deterministic Quick Entry feature check --------------------------------
CLAUDE_APP = Path('/Applications/Claude.app')
FEATURES = {
    'app_asar': ('Resources/app.asar', 'Bundle principal (routage Quick Entry)'),
    'native_binding': ('Resources/app.asar.unpacked/node_modules/@ant/claude-native/claude-native-binding.node', 'Additif natif claude-native'),
    'swift_addon': ('Resources/app.asar.unpacked/node_modules/@ant/claude-swift', 'Additif Swift (overlay Quick Entry)'),
}


def _component_meta(path):
    """Deterministic file metadata used to detect an update (size + mtime)."""
    try:
        if path.is_file():
            st = path.stat()
            return {'size': st.st_size, 'mtime': int(st.st_mtime)}
        if path.is_dir():
            files = [f for f in path.rglob('*') if f.is_file()]
            return {'size': sum(f.stat().st_size for f in files),
                    'mtime': int(max((f.stat().st_mtime for f in files), default=0))}
    except OSError:
        return None
    return None


def quick_entry_anomalies(log_dir=DEFAULT_LOG_DIR, previous=None):
    """Detect update-induced breakage in Quick Entry's Claude Desktop support.

    Cross-checks two deterministic signals: (a) a component whose metadata
    changed since the last recorded state (i.e. an update ran) and (b) the
    disappearance of the log signature that component backs. A component that
    moved but whose log signature is gone is a breakage anomaly. Never
    launches or automates anything; comparison only.
    """
    import reprise_logs
    current = {}
    for name, (rel, _label) in FEATURES.items():
        current[name] = _component_meta(CLAUDE_APP / 'Contents' / rel)
    signatures = log_signatures(log_dir)
    anomalies = []
    for name, meta in current.items():
        if meta is None:
            anomalies.append({'component': name, 'kind': 'missing', 'detail': 'Composant absent'})
            continue
        prev = (previous or {}).get(name)
        updated = prev is not None and prev != meta
        # The log signature this component backs: app_asar -> routing, swift_addon -> submitted.
        sig_key = {'app_asar': 'quick_entry_routing', 'swift_addon': 'quick_entry_submitted',
                   'native_binding': 'quick_entry_guard'}.get(name)
        sig_broken = sig_key is not None and not signatures.get(sig_key, {}).get('present')
        if updated and sig_broken:
            anomalies.append({'component': name, 'kind': 'update_breakage',
                              'detail': 'Composant modifié par une mise à jour mais signature de log disparue'})
        elif sig_broken:
            anomalies.append({'component': name, 'kind': 'signature_missing',
                              'detail': 'Signature de log absente alors que le composant est présent'})
    return {'components': current, 'signatures': signatures, 'anomalies': anomalies,
            'clean': not anomalies}


def quick_entry_features():
    """Deterministic check of the Claude Desktop features Quick Entry relies on.

    Presence and readability only; never launches or automates the app. A
    missing dependency is reported so an update that drops one is caught
    before a submission silently fails.
    """
    checks = {}
    for name, (rel, label) in FEATURES.items():
        path = CLAUDE_APP / 'Contents' / rel
        checks[name] = {'label': label, 'present': path.exists(),
                        'readable': path.is_file() and os.access(path, os.R_OK) if path.is_file() else path.is_dir(),
                        'path': str(path)}
    # Quick Entry preference shortcuts live in the Desktop config.
    prefs = Path.home() / 'Library/Application Support/Claude/claude_desktop_config.json'
    shortcuts = {}
    try:
        data = json.loads(prefs.read_text())
        p = data.get('preferences', {})
        shortcuts = {'quickEntryShortcut': p.get('quickEntryShortcut'),
                     'quickEntryDictationShortcut': p.get('quickEntryDictationShortcut')}
    except (OSError, ValueError):
        shortcuts = {'quickEntryShortcut': None, 'quickEntryDictationShortcut': None}
    checks['preferences'] = {'label': 'Raccourcis Quick Entry', 'shortcuts': shortcuts,
                             'present': all(v is not None for v in shortcuts.values())}
    missing = [n for n, c in checks.items() if not c.get('present')]
    result = {'checks': checks, 'missing': missing, 'ready': not missing,
              'note': 'Deterministic presence check; does not open or automate Claude Desktop.'}
    result['anomalies'] = quick_entry_anomalies()
    return result



CODEX_APPS = (Path('/Applications/ChatGPT.app'), Path('/Applications/Codex.app'))


def codex_features():
    """Report deterministic presence and metadata for Codex interaction dependencies."""
    import engines
    import plistlib

    bundles = {}
    for app in CODEX_APPS:
        bundles[app.name] = {
            'label': app.name.removesuffix('.app'),
            'present': app.is_dir(),
            'path': str(app),
            'meta': _component_meta(app),
        }
    program = engines.executable('codex-cli')
    executable_meta = _component_meta(Path(program)) if program else None
    try:
        engines.socket_path({})
        socket_usable = True
    except (engines.Unavailable, OSError, ValueError):
        socket_usable = False
    socket_meta = _component_meta(engines.SOCKET)

    desktop_version = None
    for app in CODEX_APPS:
        plist = app / 'Contents' / 'Info.plist'
        try:
            data = plistlib.loads(plist.read_bytes())
            desktop_version = data.get('CFBundleVersion') or data.get('CFBundleShortVersionString')
            if desktop_version:
                break
        except (OSError, ValueError):
            continue

    checks = {
        'bundles': {'label': 'Applications Codex Desktop', 'present': any(v['present'] for v in bundles.values()),
                    'apps': bundles},
        'codex_cli': {'label': 'Exécutable Codex CLI', 'present': program is not None,
                      'path': program, 'meta': executable_meta},
        'control_socket': {'label': 'Socket de contrôle local', 'present': socket_usable,
                           'path': str(engines.SOCKET), 'meta': socket_meta},
        'shared_mode': {'label': 'Prérequis du mode partagé', 'present': bool(desktop_version and program),
                        'desktop_version': desktop_version, 'codex_cli': program is not None},
    }
    missing = [name for name, item in checks.items() if not item['present']]
    return {'checks': checks, 'missing': missing, 'ready': not missing,
            'note': 'Présence déterministe seulement ; aucun processus ni connexion n’est lancé.'}


def codex_anomalies(previous=None, features=None):
    """Compare Codex dependencies and report update-linked interaction failures.

    Presence and metadata are distinct: a control socket has no regular-file
    metadata, so it must not be called 'missing' when merely metadata-less.
    """
    features = features if features is not None else codex_features()
    checks = features['checks']
    current = {
        'chatgpt_app': checks['bundles']['apps']['ChatGPT.app']['meta'],
        'codex_app': checks['bundles']['apps']['Codex.app']['meta'],
        'codex_cli': checks['codex_cli']['meta'],
        'control_socket': checks['control_socket']['meta'],
    }
    # Presence (not file metadata) is the existence signal for the socket.
    socket_present = checks['control_socket']['present']
    previous = previous or {}
    updated = any(previous.get(name) is not None and previous.get(name) != meta
                  for name, meta in current.items())
    anomalies = []

    for name, meta in current.items():
        if name in ('chatgpt_app', 'codex_app') and meta is None:
            continue
        if name == 'control_socket':
            continue  # handled by presence logic below, not file metadata
        if meta is None:
            anomalies.append({'component': name, 'kind': 'missing', 'detail': 'Composant absent'})

    if updated and not socket_present:
        anomalies.append({'component': 'control_socket', 'kind': 'update_breakage',
                          'detail': 'Composant Codex modifié après mise à jour ; socket de contrôle absent ou inutilisable'})
    elif not socket_present:
        anomalies.append({'component': 'control_socket', 'kind': 'missing',
                          'detail': 'Socket de contrôle absent ou inutilisable'})

    old_version = previous.get('desktop_version')
    version = checks['shared_mode']['desktop_version']
    if old_version and version and old_version != version:
        anomalies.append({'component': 'shared_mode', 'kind': 'update_breakage',
                          'detail': 'Version Codex Desktop modifiée ; mode partagé à revérifier'})
    if not checks['shared_mode']['present']:
        anomalies.append({'component': 'shared_mode', 'kind': 'missing',
                          'detail': 'Prérequis du mode partagé absents'})

    if not checks['codex_cli']['present'] and updated:
        anomalies.append({'component': 'codex_cli', 'kind': 'update_breakage',
                          'detail': 'Composants modifiés après mise à jour ; exécutable Codex absent'})

    return {'components': current | {'desktop_version': version, 'control_socket_present': socket_present},
            'anomalies': anomalies, 'clean': not anomalies}


def local_synthesis(log_dir=DEFAULT_LOG_DIR, binary=None):
    """Generative local analysis: updates, inter-harness relations, recent activity.

    Reads the deterministic facts (health + log history + Quick Entry features)
    and asks Foundation Models for a simplified synthesis. Optional: returns
    {'available': False} without the helper or model. Never a source of truth.
    """
    health = check('.', log_dir)
    features = quick_entry_features()
    import reprise_logs
    history = reprise_logs.history(log_dir, limit=10)
    facts = (
        f"Santé post-update — versions: {health['versions']}; "
        f"à relancer: {health['needs_relaunch'] or 'aucun'}; "
        f"signatures de logs obsolètes: {health['stale_log_signatures'] or 'aucune'}. "
        f"Fonctionnalités Quick Entry: {'prêtes' if features['ready'] else 'manquantes ' + str(features['missing'])}. "
        f"Activité récente — soumissions: {[s['state'] for s in history['submissions']]}; "
        f"interactions MCP récentes: {[i['method'] for i in history['interactions'][-5:]]}. "
    )
    question = ("Fais une synthèse simplifiée : (1) ce que les mises à jour ont changé, "
                "(2) les relations entre les Harness et leurs interactions, "
                "(3) les dernières activités distinguées, et (4) les actions qui requièrent un humain.")
    return cached_explanation(facts, question, binary=binary)
