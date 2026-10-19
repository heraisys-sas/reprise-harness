from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import sqlite3
import time


def inspect(path, now=None, details=None):
    now = now or time.time()
    last = None
    rates = None
    measured_at = None
    last_stamp = None
    with Path(path).open() as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get('type') != 'event_msg':
                continue
            payload = record.get('payload', {})
            kind = payload.get('type')
            if kind in ('task_started', 'task_complete', 'turn_aborted'):
                last = payload
                last_stamp = record.get("timestamp")
            if kind == 'token_count' and payload.get('rate_limits'):
                rates = payload['rate_limits']
                measured_at = record.get('timestamp')
    if details is not None:
        details.update(active=bool(last and last['type'] == 'task_started' and now - Path(path).stat().st_mtime < 120), rates=rates, measured_at=measured_at)
    if last and last['type'] == 'task_complete' and isinstance(last.get('error'), dict):
        error = last['error']
        stamp = last.get('completed_at')
        if not isinstance(stamp, (int, float)) and last_stamp:
            stamp = dt.datetime.fromisoformat(last_stamp.replace('Z', '+00:00')).timestamp()
        if error.get('codex_error_info') == 'usage_limit_exceeded' and isinstance(stamp, (int, float)):
            resets = [(rates or {}).get(window) or {} for window in ('primary', 'secondary')]
            deadlines = [v['resets_at'] for v in resets if isinstance(v.get('resets_at'), (int, float)) and v.get('used_percent', 0) >= 100]
            return {'state': 'quota_confirmed', 'label': 'Quota atteint · reprise automatique',
                    'retry_at': max(deadlines) + 120 if deadlines else None,
                    'interrupted_at': stamp, 'turn_id': last.get('turn_id')}
        return {'state': 'failed', 'label': 'Exécution terminée avec une erreur', 'retry_at': None}
    if not last or last['type'] == 'task_complete':
        return None
    if last['type'] == 'turn_aborted':
        return {'state': 'stopped', 'label': 'Session interrompue', 'retry_at': None}
    started = last.get('started_at', now)
    if isinstance(started, str):
        started = dt.datetime.fromisoformat(started.replace('Z', '+00:00')).timestamp()
    if now - started < 900:
        return None
    resets = []
    for window in ('primary', 'secondary'):
        value = (rates or {}).get(window) or {}
        if value.get('used_percent', 0) >= 100 and value.get('resets_at', 0) > started:
            resets.append(value['resets_at'])
    if resets:
        return {'state': 'quota_candidate', 'label': 'Quota atteint · reprise à confirmer', 'retry_at': max(resets) + 120}
    return {'state': 'unfinished', 'label': 'Étape non clôturée · à vérifier', 'retry_at': None}


def codex_source(raw):
    if isinstance(raw, str):
        try:
            source = json.loads(raw)
        except ValueError:
            source = raw
    else:
        source = raw
    child = isinstance(source, dict) and 'subagent' in source
    parent = None
    if child and isinstance(source['subagent'], dict):
        spawn = source['subagent'].get('thread_spawn')
        if isinstance(spawn, dict):
            parent = spawn.get('parent_thread_id')
    return {'is_subagent': child, 'parent_session': parent,
            'automatic_resume_supported': isinstance(source, str) and source in ('cli', 'vscode', 'exec')}


def scan(home=None):
    home = Path(home or Path.home() / '.codex')
    state = home / 'state_5.sqlite'
    if not state.exists():
        return {'sessions': [], 'error': 'Les sessions Codex ne sont pas disponibles.'}
    result = []
    active = 0
    quota = None
    try:
        with closing(sqlite3.connect(state.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            rows = db.execute('SELECT id,title,cwd,rollout_path,updated_at,source FROM threads WHERE archived=0 AND updated_at>? ORDER BY updated_at DESC LIMIT 100', (time.time() - 30 * 86400,)).fetchall()
        for ident, title, cwd, path, updated, source in rows:
            try:
                details = {}
                status = inspect(path, details=details)
                active += int(details.get('active', False))
                stamp = details.get('measured_at')
                if details.get('rates') and stamp and (quota is None or stamp > quota['measured_at']):
                    quota = {'rates': details['rates'], 'measured_at': stamp}
            except (OSError, ValueError, TypeError):
                continue
            if status:
                metadata = codex_source(source)
                if metadata['is_subagent'] and status['state'] == 'quota_confirmed':
                    status['label'] = 'Quota atteint · sous-agent géré par son parent'
                elif not metadata['automatic_resume_supported'] and status['state'] == 'quota_confirmed':
                    status['label'] = 'Quota atteint · mode de reprise à vérifier'
                result.append(dict(id=ident, title=title, cwd=cwd, updated=updated, **status, **metadata))
        return {'sessions': result, 'error': None, 'active': active, 'quota': quota}
    except sqlite3.Error:
        return {'sessions': [], 'error': 'Le format des sessions Codex a changé ou est temporairement indisponible.'}


def harness_status(record, last=None, modified=None, now=None):
    now = now or time.time()
    if last and last.get('kind') == 'aborted':
        return {'state': 'stopped', 'label': 'Session interrompue', 'retry_at': None}
    if last and last.get('kind') == 'error':
        code = last.get('code')
        if code in ('QUOTA', 'ACCOUNT_QUOTA'):
            return {'state': 'quota_candidate', 'label': 'Quota atteint · échéance indisponible', 'retry_at': None}
        if code == 'RATE_LIMIT':
            return {'state': 'rate_limited', 'label': 'Limite temporaire du fournisseur', 'retry_at': None}
        return {'state': 'failed', 'label': 'Exécution interrompue par une erreur', 'retry_at': None}
    rows = record.get('rows', {})
    boundary = rows.get('turnBoundary', {}).get('val', {})
    questions = rows.get('userQuestions', {}).get('val', {}).get('questions', {})
    if questions.get('active'):
        return {'state': 'needs_user', 'label': 'Réponse attendue', 'retry_at': None}
    if boundary.get('openTurnStartSeq') is not None and modified and now - modified >= 900:
        return {'state': 'unfinished', 'label': 'Étape non clôturée · à vérifier', 'retry_at': None}
    return None


def scan_harness(home=None):
    import shutil
    import subprocess
    home = Path(home or Path.home() / '.dsh')
    cache = home / 'storages/session_projcache/sessions'
    if not cache.is_dir():
        return {'sessions': [], 'error': 'Les sessions Harness ne sont pas disponibles.'}
    node = shutil.which('node')
    bundled = Path('/Applications/DeepSeek Harness.app/Contents/Resources/runtime/primary-runtime/dependencies/node/bin/node')
    if bundled.is_file():
        node = str(bundled)
    helper = Path(__file__).with_name('harness-events.cjs')
    logs = {}
    for path in (home / 'sessions').glob('*/*/session*.jsonl*'):
        if path.name.endswith(('.jsonl', '.jsonl.zstd')):
            previous = logs.get(path.parent.name)
            try:
                newer = (not previous) or path.stat().st_mtime > previous.stat().st_mtime
            except OSError:
                continue  # unreadable: skip, never re-probe
            if newer:
                logs[path.parent.name] = path
    archived = set()
    try:
        workspace = json.loads((home / 'storages/workspace.json').read_text())
        archived = set(workspace.get('global', {}).get('archivedSessionIds', []))
    except (OSError, ValueError):
        pass
    result = []
    unreadable = 0
    active = 0
    def _mtime(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    for path in sorted(cache.glob('session-*.json'), key=_mtime, reverse=True)[:100]:
        try:
            if path.stem in archived or time.time() - path.stat().st_mtime > 30 * 86400:
                continue
        except OSError:
            unreadable += 1
            continue
        try:
            obj = json.loads(path.read_text())
            record = obj['record']
            last = None
            if node and path.stem in logs:
                output = subprocess.run([node, str(helper), str(logs[path.stem])], capture_output=True, text=True, timeout=5)
                if output.returncode == 0:
                    last = json.loads(output.stdout)
                else:
                    unreadable += 1
            elif path.stem in logs:
                unreadable += 1
            boundary = record.get('rows', {}).get('turnBoundary', {}).get('val', {})
            if boundary.get('openTurnStartSeq') is not None and time.time() - path.stat().st_mtime < 120 and (last is None or last.get('kind') == 'open'):
                active += 1
            status = harness_status(record, last, path.stat().st_mtime)
            if status:
                title = record.get('rows', {}).get('title', {}).get('val')
                result.append(dict(id=path.stem, title=title if isinstance(title, str) else 'Session Harness', cwd=record.get('identity', {}).get('cwd', ''), updated=path.stat().st_mtime, provider='Harness', **status))
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            unreadable += 1
    return {'sessions': result, 'active': active, 'error': f'{unreadable} session(s) Harness n’ont pas pu être analysées complètement.' if unreadable else None}


def scan_all():
    import os
    from local_analysis import read_cache
    analysis = read_cache(Path(os.environ.get('AGENT_RESUME_STATE', Path(__file__).parent / 'state')))
    codex = scan()
    harness = scan_harness()
    claude = scan_claude()
    cowork = scan_cowork()
    autoclaw = scan_autoclaw()
    for row in codex['sessions']:
        row['provider'] = 'Codex'
    errors = [r['error'] for r in (codex, harness, claude, cowork, autoclaw) if r['error']]
    return {'sessions': sorted(codex['sessions'] + harness['sessions'] + claude['sessions'] + autoclaw['sessions'], key=lambda r: r['updated'], reverse=True)[:20], 'error': ' '.join(errors) if errors else None, 'activity': {'Codex': codex.get('active'), 'Harness': harness.get('active'), 'Claude Code': claude.get('active'), 'Cowork': cowork.get('active'), 'AutoClaw': autoclaw.get('active')}, 'codex_quota': codex.get('quota'), 'analysis': analysis}


def inspect_claude(path, now=None):
    now = now or time.time()
    last = None
    title = 'Session Claude Code'
    cwd = ''
    with Path(path).open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('isSidechain'):
                continue
            kind = event.get('type')
            cwd = event.get('cwd', cwd)
            if kind in ('custom-title', 'ai-title'):
                title = event.get('customTitle', event.get('aiTitle', title))
            if kind in ('user', 'assistant') or (kind == 'system' and event.get('subtype') in ('api_error', 'error')):
                last = event
    modified = Path(path).stat().st_mtime
    if not last:
        return {'active': False, 'session': None}
    status = None
    error = last.get('error') or {}
    code = error.get('type') if isinstance(error, dict) else None
    if last.get('type') == 'system' or last.get('isApiErrorMessage'):
        status = {'state': 'rate_limited' if code == 'rate_limit_error' else 'failed', 'label': 'Limite fournisseur détectée' if code == 'rate_limit_error' else 'Erreur enregistrée · à vérifier', 'retry_at': None}
    stop = last.get('message', {}).get('stop_reason')
    pending = last.get('type') == 'user' or (last.get('type') == 'assistant' and stop not in ('end_turn', 'stop_sequence', 'max_tokens'))
    active = pending and status is None and now - modified < 120
    if pending and status is None and now - modified >= 900:
        status = {'state': 'unfinished', 'label': 'Échange non clôturé · à vérifier', 'retry_at': None}
    session = dict(id=Path(path).stem, title=title, cwd=cwd, updated=modified, provider='Claude Code', **status) if status else None
    return {'active': active, 'session': session}


def scan_claude(home=None):
    home = Path(home or Path.home() / '.claude')
    projects = home / 'projects'
    if not projects.is_dir():
        return {'sessions': [], 'active': None, 'error': None}
    rows = []
    active = 0
    failed = 0
    def _mtime(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0  # a protected/unreadable file sorts last and is skipped below
    for path in sorted(projects.glob('*/*.jsonl'), key=_mtime, reverse=True)[:100]:
        try:
            if time.time() - path.stat().st_mtime > 30 * 86400:
                continue
        except OSError:
            failed += 1  # unreadable (e.g. TCC-protected): skip, never re-probe
            continue
        try:
            if path.stat().st_size > 64 * 1024 * 1024:
                failed += 1
                continue
            value = inspect_claude(path)
            active += int(value['active'])
            if value['session']:
                rows.append(value['session'])
        except (OSError, ValueError, TypeError, AttributeError):
            failed += 1
    return {'sessions': rows, 'active': active, 'error': f'{failed} session(s) Claude Code illisible(s).' if failed else None}


def cowork_active(record, now=None):
    now = time.time() if now is None else now
    if record.get('isArchived') or record.get('isAgentCompleted') is True:
        return False
    stamp = record.get('lastActivityAt')
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool):
        return False
    age = now - stamp / 1000
    return 0 <= age < 120


def scan_cowork(home=None):
    home = Path(home or Path.home() / 'Library/Application Support/Claude')
    directory = home / 'local-agent-mode-sessions'
    if not directory.is_dir():
        return {'active': None, 'error': None}
    sessions = {}
    failed = 0
    for path in directory.glob('*/*/local_*.json'):
        try:
            if path.stat().st_size > 2 * 1024 * 1024:
                failed += 1
                continue
            record = json.loads(path.read_text())
            ident = record.get('sessionId')
            if not isinstance(ident, str):
                continue
            active = cowork_active(record)
            sessions[ident] = sessions.get(ident, False) or active
        except (OSError, ValueError, TypeError, AttributeError):
            failed += 1
    return {'active': sum(sessions.values()), 'error': f'{failed} métadonnée(s) Cowork illisible(s).' if failed else None}


def autoclaw_session(record, now=None):
    import math
    now = time.time() if now is None else now
    stamp = record.get('updatedAt', record.get('lastInteractionAt'))
    if not isinstance(stamp, (int, float)) or isinstance(stamp, bool) or not math.isfinite(stamp):
        return None
    updated = stamp / 1000
    age = now - updated
    if age < 0 or age > 30 * 86400:
        return None
    status = record.get('status')
    stopped = bool(record.get('autoclawUserStoppedAt')) or status == 'killed'
    active = status == 'running' and age < 120 and not stopped and not record.get('endedAt')
    state = None
    if stopped:
        state = ('stopped', 'Arrêt volontaire ou session arrêtée')
    elif status == 'failed':
        state = ('failed', 'Exécution en échec · à vérifier')
    elif status == 'running' and age >= 900:
        state = ('unfinished', 'Exécution déclarée active mais silencieuse · à vérifier')
    summary = None
    if state or active:
        summary = dict(id=record.get('sessionId', ''), title='Session AutoClaw', provider='AutoClaw', updated=updated, cwd='', state=state[0] if state else 'active', label=state[1] if state else 'Activité récente', retry_at=None)
        for source, target in [('totalTokens', 'tokens'), ('estimatedCostUsd', 'cost_usd')]:
            value = record.get(source)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                summary[target] = value
        for source in ('modelProvider', 'model'):
            if isinstance(record.get(source), str):
                summary[source] = record[source]
    return {'active': active, 'summary': summary}


def scan_autoclaw(home=None):
    home = Path(home or Path.home() / '.openclaw-autoclaw')
    if not (home / 'agents').is_dir():
        return {'sessions': [], 'active': None, 'error': None}
    entries = {}
    errors = 0
    for path in (home / 'agents').glob('*/sessions/sessions.json'):
        try:
            if path.stat().st_size > 32 * 1024 * 1024:
                errors += 1
                continue
            index = json.loads(path.read_text())
            for record in index.values():
                ident = record.get('sessionId')
                if not isinstance(ident, str) or not ident:
                    continue
                result = autoclaw_session(record)
                if result:
                    old = entries.get(ident)
                    stamp = record.get('updatedAt', record.get('lastInteractionAt', 0))
                    if old is None or stamp > old[0]:
                        entries[ident] = (stamp, result)
        except (OSError, ValueError, TypeError, AttributeError):
            errors += 1
    return {'sessions': [v['summary'] for _, v in entries.values() if v['summary']], 'active': sum(v['active'] for _, v in entries.values()), 'error': f'{errors} index AutoClaw indisponible(s).' if errors else None}
