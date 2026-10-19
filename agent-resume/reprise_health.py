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

import reprise_logs

DEFAULT_LOG_DIR = reprise_logs.DEFAULT_LOG_DIR
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
        try:
            engines.socket_path({'socket': str(path)})
            result[name] = 'running'
        except engines.Unavailable:
            result[name] = 'stopped'
        except Exception:
            result[name] = 'unknown'
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


def summarize(bundle_root, log_dir=DEFAULT_LOG_DIR, binary=None):
    """Health + optional generative explanation, for the Reprise UI and MCP."""
    health = check(bundle_root, log_dir)
    facts = (f"Versions: {health['versions']}. "
             f"Processus à relancer: {health['needs_relaunch'] or 'aucun'}. "
             f"Signatures de logs obsolètes: {health['stale_log_signatures'] or 'aucune'}. "
             f"Actions pour humain: {health['actions_for_human'] or 'aucune'}.")
    health['explanation'] = explain(facts, binary=binary)
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
    report = check(bundle_root, log_dir)
    update_detected = previous is None or current != previous
    report['update_detected'] = update_detected
    report['previous_versions'] = previous
    with db:
        scheduler.put(db, 'health_versions', current)
        if update_detected:
            scheduler.put(db, 'health_report', report)
            scheduler.put(db, 'health_update_at', __import__('time').time())
    return report
