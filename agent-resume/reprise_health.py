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
from app_paths import native_binary
import json
import os
import time
from datetime import datetime

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
    'quick-entry-server': {'label': 'Service Quick Apply (claude-desktopd)', 'marker': 'claude_desktop_quick'},
}


def _read(path):
    try:
        return Path(path).read_text(errors='replace')
    except OSError:
        return ''


def _signature_epoch(db, versions):
    """Borne d'observation propre aux composants Claude, persistée par watch."""
    import scheduler
    identity = {
        'version': versions.get('claude-desktop'),
        'components': {name: _component_meta(CLAUDE_APP / 'Contents' / relative)
                       for name, (relative, _) in FEATURES.items()},
    }
    previous = scheduler.get(db, 'health_signature_epoch', None) if db is not None else None
    if previous is not None and previous.get('identity') == identity:
        return previous
    return {'identity': identity, 'since': time.time()}


def _log_timestamp(line):
    try:
        if len(line) > 10 and line[10] == 'T':
            return datetime.fromisoformat(line.split()[0].replace('Z', '+00:00')).timestamp()
        return datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S').timestamp()
    except (ValueError, IndexError, OverflowError):
        return None


def log_signatures(log_dir=DEFAULT_LOG_DIR, since=None):
    """Trois états : présent, absent après activité pertinente, non vérifié."""
    result = {name: {'present': False, 'marker': marker, 'status': 'unverified'}
              for name, marker in LOG_SIGNATURES.items()}
    if since is None:
        return result
    paths = [Path(log_dir) / 'main.log']
    paths.extend(Path(log_dir).glob('mcp-server-*.log'))
    observed = set()
    for path in paths:
        for line in _read(path).splitlines():
            timestamp = _log_timestamp(line)
            if timestamp is None or timestamp <= since:
                continue
            group = ('mcp_interaction',) if path.name != 'main.log' else ('quick_entry_submitted', 'quick_entry_routing', 'quick_entry_guard')
            relevant = 'Message from ' in line if path.name != 'main.log' else ('quick entry' in line.lower() or '[QuickEntry]' in line)
            if not relevant:
                continue
            observed.update(group)
            for name in group:
                if LOG_SIGNATURES[name] in line:
                    result[name]['present'] = True
    for name, item in result.items():
        item['status'] = 'present' if item['present'] else ('missing' if name in observed else 'unverified')
    return result


def socket_liveness(path):
    """Distingue écoute confirmée, arrêt et état indéterminé."""
    import errno
    import socket
    import engines
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return 'stopped'
    except OSError:
        return 'indeterminate'
    try:
        target = engines.socket_path({'socket': str(path)})
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(1)
            probe.connect(str(target))
        return 'running'
    except OSError as error:
        return 'stopped' if error.errno in (errno.ENOENT, errno.ECONNREFUSED) else 'indeterminate'
    except engines.Unavailable:
        return 'indeterminate'


def health_socket(db=None, state=None):
    """Suit le socket enregistré dans Connexions, sans repli silencieux."""
    import engines
    if db is not None:
        import sqlite3
        import connection
        try:
            from app_paths import state_directory
            configured = connection.status(db, state or state_directory(Path(__file__).parent))['configured_socket']
        except sqlite3.Error:
            configured = None  # schéma de configuration absent : sondes par défaut
        if configured:
            return Path(configured)
    return engines.SOCKET


def process_liveness(db=None, state=None):
    """Sonde les sockets effectivement sélectionnés, sans lancer de service."""
    return {'quick-entry-server': socket_liveness(Path.home() / '.claude-desktop/quick-entry.sock'),
            'codex-server': socket_liveness(health_socket(db, state))}


def read_versions(bundle_root):
    """Versions de Claude Desktop et des composants Codex détectés."""
    import engines
    return {'claude-desktop': engines.bundle_version(CLAUDE_APP),
            'codex': engines.codex_versions()}


def check(bundle_root, log_dir=DEFAULT_LOG_DIR, db=None, state=None, versions=None, signature_since=None):
    """Post-update health: what must be relaunched and what is still parsed."""
    versions = read_versions(bundle_root) if versions is None else versions
    if signature_since is None:
        signature_since = _signature_epoch(db, versions)['since']
    signatures = log_signatures(log_dir, since=signature_since)
    liveness = process_liveness(db, state)
    stale = [name for name, s in signatures.items() if not s['present'] and s.get('status', 'missing') == 'missing']
    relaunch = [name for name, state in liveness.items() if state == 'stopped']
    return {
        'versions': versions,
        'log_signatures': signatures,
        'process_liveness': liveness,
        'stale_log_signatures': stale,
        'unverified_log_signatures': [name for name, s in signatures.items() if s.get('status') == 'unverified'],
        'needs_relaunch': relaunch,
        'actions_for_human': ([f'Relancer {SUPERVISED[n]["label"]}' for n in relaunch]
                              + [f'Adapter le parsing de la signature « {signatures[s]["marker"]} »' for s in stale]
                              + [f'Signature « {s["marker"]} » : non vérifié depuis la mise à jour ; effectuer une activité pertinente'
                                 for s in signatures.values() if s.get('status') == 'unverified']),
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
                      native_binary(Path(__file__).parent, 'FoundationExplain')]
        binary = next((c for c in candidates if c.is_file()), candidates[0])
    if not binary.is_file():
        return {'available': False, 'answer': None, 'reason_code': 'helper_missing', 'reason': 'Le composant local Foundation Models n’est pas installé.'}
    payload = {'facts': facts, 'question': question or 'Explique l activité récente et liste les actions qui requièrent un humain.'}
    try:
        out = subprocess.run([str(binary)], input=json.dumps(payload, ensure_ascii=False),
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return {'available': False, 'answer': None, 'reason_code': 'helper_failed',
                    'reason': 'Le composant Foundation Models ne peut pas être exécuté sur ce système.'}
        result = json.loads(out.stdout or '{}')
        if not isinstance(result, dict) or type(result.get('available')) is not bool:
            raise ValueError('invalid Foundation Models response')
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        return {'available': False, 'answer': None, 'reason_code': 'helper_unavailable', 'reason': 'Foundation Models indisponible ou délai dépassé.'}


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


def retained_anomalies(db):
    """Lit les événements sans les acquitter."""
    import scheduler
    return scheduler.get(db, 'health_anomalies', []) if db is not None else []


def retain_changes(events, source, previous, report):
    """Conserve les anomalies liées à une transition de composants."""
    result = {a['event_id']: a for a in events}
    for anomaly in report['anomalies']:
        if anomaly['kind'] != 'update_breakage':
            continue
        evidence = [source, previous, report['components'], anomaly]
        identity = hashlib.sha256(json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
        result[identity] = dict(anomaly, event_id=identity, source=source,
                                previous_components=previous, components=report['components'],
                                acknowledged=False)
    return list(result.values())


def merge_retained(current, events, source):
    pending = [a for a in events if a['source'] == source and not a['acknowledged']]
    signatures = {(a['component'], a['kind'], a['detail']) for a in pending}
    return [a for a in current if (a['component'], a['kind'], a['detail']) not in signatures] + pending


def reconcile_anomalies(db, event_ids):
    """Acquitte explicitement les seuls événements réconciliés."""
    import scheduler
    if not isinstance(event_ids, list) or any(not isinstance(i, str) for i in event_ids):
        raise ValueError('Liste d’identifiants d’événements requise')
    with db:
        events = retained_anomalies(db)
        if set(event_ids) - {a['event_id'] for a in events}:
            raise ValueError('Événement de santé inconnu')
        scheduler.put(db, 'health_anomalies', [
            dict(a, acknowledged=True) if a['event_id'] in event_ids else a for a in events])


def summarize(bundle_root, log_dir=DEFAULT_LOG_DIR, binary=None, db=None, state=None):
    """Health + optional generative explanation, for the Reprise UI and MCP."""
    health = check(bundle_root, log_dir, db, state)
    health['quick_entry_features'] = quick_entry_features()
    health['quick_entry_features']['anomalies'] = quick_entry_anomalies(
        log_dir, signatures=health.get('log_signatures'))
    features = codex_features(db, state, versions=health['versions'].get('codex'))
    health['codex_features'] = features
    events = retained_anomalies(db)
    health['codex_anomalies'] = merge_retained(codex_anomalies(features=features)['anomalies'], events, 'codex')
    claude = health['quick_entry_features']['anomalies']
    claude['anomalies'] = merge_retained(claude['anomalies'], events, 'claude')
    claude['clean'] = not claude['anomalies']
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
    signature_epoch = _signature_epoch(db, current)
    anomaly_report = quick_entry_anomalies(log_dir, previous_claude, since=signature_epoch['since'])
    features = codex_features(db, versions=current.get('codex'))
    codex_anomaly_report = codex_anomalies(previous_codex, features=features)
    components = {'claude': anomaly_report['components'], 'codex': codex_anomaly_report['components']}
    report = check(bundle_root, log_dir, db, versions=current, signature_since=signature_epoch['since'])
    update_detected = previous is None or current != previous
    report['update_detected'] = update_detected
    report['quick_entry_anomalies'] = anomaly_report['anomalies']
    report['codex_features'] = features
    report['codex_anomalies'] = codex_anomaly_report['anomalies']
    report['previous_versions'] = previous
    with db:
        events = retained_anomalies(db)
        events = retain_changes(events, 'claude', previous_claude, anomaly_report)
        events = retain_changes(events, 'codex', previous_codex, codex_anomaly_report)
        report['quick_entry_anomalies'] = merge_retained(anomaly_report['anomalies'], events, 'claude')
        report['codex_anomalies'] = merge_retained(codex_anomaly_report['anomalies'], events, 'codex')
        report['components_changed'] = previous_components != components
        scheduler.put(db, 'health_anomalies', events)
        scheduler.put(db, 'health_report', report)
        scheduler.put(db, 'health_signature_epoch', signature_epoch)
        scheduler.put(db, 'health_versions', current)
        scheduler.put(db, 'health_components', components)
        if update_detected or report['components_changed']:
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


def quick_entry_anomalies(log_dir=DEFAULT_LOG_DIR, previous=None, since=None, signatures=None):
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
    signatures = log_signatures(log_dir, since=since) if signatures is None else signatures
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
        signature = signatures.get(sig_key, {})
        sig_broken = (sig_key is not None and not signature.get('present')
                      and signature.get('status', 'missing') != 'unverified')
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


def codex_features(db=None, state=None, versions=None):
    """Report deterministic presence and metadata for Codex interaction dependencies."""
    import engines

    versions = engines.codex_versions() if versions is None else versions
    bundles = {Path(a['path']).name: {
        'label': Path(a['path']).stem, 'present': a['status'] != 'absent',
        'path': a['path'], 'status': a['status'], 'meta': _component_meta(Path(a['path'])),
    } for a in versions['apps']}
    program = versions['cli_path']
    executable_meta = _component_meta(Path(program)) if program else None
    socket_path = health_socket(db, state)
    socket_state = socket_liveness(socket_path)
    socket_usable = socket_state == 'running'
    socket_meta = _component_meta(socket_path)

    # Compatibilité du curseur existant : ce champ contenait le build.
    desktop_version = versions['desktop_build'] or versions['desktop_version']

    checks = {
        'bundles': {'label': 'Applications Codex Desktop', 'present': any(v['present'] for v in bundles.values()),
                    'apps': bundles},
        'codex_cli': {'label': 'Exécutable Codex CLI', 'present': program is not None,
                      'path': program, 'meta': executable_meta},
        'control_socket': {'label': 'Socket de contrôle local', 'present': socket_usable,
                           'path': str(socket_path), 'state': socket_state, 'meta': socket_meta},
        'shared_mode': {'label': 'Prérequis du mode partagé', 'present': bool(desktop_version and program),
                        'desktop_version': desktop_version, 'codex_cli': program is not None},
    }
    missing = [name for name, item in checks.items() if not item['present']]
    return {'checks': checks, 'missing': missing, 'ready': not missing, 'versions': versions,
            'note': 'Inventaire local, version CLI et écoute du socket ; aucune inférence.'}


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

    if previous.get('codex_cli') is not None and current['codex_cli'] is not None and previous['codex_cli'] != current['codex_cli']:
        anomalies.append({'component': 'codex_cli', 'kind': 'update_breakage',
                          'detail': 'Exécutable Codex modifié ; interaction à revérifier'})
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
    conversations = reprise_logs.active_conversations(log_dir, limit=10)
    conv_summary = [
        {"chat": c['chatId'][:8], "du": c['first'][:16], "au": c['last'][:16], "messages": c['submissions']}
        for c in conversations
    ]
    facts = (
        f"Santé post-update — versions: {health['versions']}; "
        f"à relancer: {health['needs_relaunch'] or 'aucun'}; "
        f"signatures de logs obsolètes: {health['stale_log_signatures'] or 'aucune'}. "
        f"Fonctionnalités Quick Entry: {'prêtes' if features['ready'] else 'manquantes ' + str(features['missing'])}. "
        f"Conversations actives (pense-bête) : {conv_summary}. "
        f"Activité récente — soumissions: {[s['state'] for s in history['submissions']]}; "
        f"interactions MCP récentes: {[i['method'] for i in history['interactions'][-5:]]}. "
    )
    question = ("Fais un pense-bête synthétique des dernières conversations actives (plusieurs sujets possibles "
                "travaillés récemment) : pour chaque sujet, rappelle en une ligne de quoi il retournait et où on "
                "en était. Puis résume : (1) ce que les mises à jour ont changé, (2) les relations entre les Harness, "
                "(3) les actions qui requièrent un humain. Si une seule conversation est active, dis-le simplement.")
    return cached_explanation(facts, question, binary=binary)
