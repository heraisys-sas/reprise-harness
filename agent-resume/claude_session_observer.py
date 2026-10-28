"""Opt-in, bounded metadata observation. Never dispatches a model turn."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from datetime import datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import claude_desktop_apply as cdp
from claude_quota import parse_pause
from claude_session_store import SessionStore, binding, empty_snapshot, open_database

BUDGET_SECONDS = 5.0
COMMANDS = ('claude-sessions-status', 'claude-observer-config', 'claude-session-register', 'claude-session-remove')

REFUSAL_PATTERN = (r"\b(?:usage|message) limits? (?:reached|exceeded)\b"
                   r"|\byou(?: have|'ve) (?:reached|exceeded) (?:your|the) (?:usage|message) limit\b"
                   r"|\blimite(?: de messages| d['’](?:utilisation|usage))? (?:a été )?atteinte\b"
                   r"|\bvous avez atteint (?:votre|la) limite\b"
                   r"|en pause\s*:\s*votre utilisation gratuite sera de nouveau disponible")
NEGATED_REFUSAL_PATTERN = (r"\b(?:no|not|never)\b.{0,40}(?:limit|reached|exceeded)"
                           r"|\b(?:aucune|pas|non)\b.{0,40}(?:limite|atteint)"
                           r"|\blimite.{0,30}\b(?:pas|non)\b")

# Only visible quota notices, never conversation bodies or composer text.
JS_QUOTA_NOTICE = r"""(() => {
  const at = location.origin === 'https://claude.ai' && location.pathname === __REPRISE_EXPECTED_PATH__
    && !location.search && !location.hash;
  if (!at) return {url: null, notice: null, reset_iso: null, timezone: null, send_disabled: null};
  const notices = [...document.querySelectorAll('[role="alert"], [role="dialog"]')]
    .slice(0, 10).filter(el => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    }).map(el => ({el, text: (el.innerText || '').slice(0, 1024)}))
    .filter(({text}) => new RegExp(__REPRISE_REFUSAL_PATTERN__, 'i').test(text)
      && !new RegExp(__REPRISE_NEGATED_PATTERN__, 'i').test(text));
  const notice = notices.length === 1 ? notices[0] : null;
  const send = document.querySelectorAll('[data-testid="chat-input-send"]');
  return {url: at ? location.href : null,
    notice: notice ? notice.text : null,
    reset_iso: notice ? notice.el.querySelector('time[datetime]')?.getAttribute('datetime') ?? null : null,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    send_disabled: send.length === 1 ? Boolean(send[0].disabled) : null};
})()"""


def notice_script(chat_id):
    chat = cdp.validate_chat_id(chat_id)
    if chat is None:
        raise ValueError('Existing chat UUID required')
    return (JS_QUOTA_NOTICE.replace('__REPRISE_EXPECTED_PATH__', json.dumps('/chat/' + chat))
            .replace('__REPRISE_REFUSAL_PATTERN__', json.dumps(REFUSAL_PATTERN))
            .replace('__REPRISE_NEGATED_PATTERN__', json.dumps(NEGATED_REFUSAL_PATTERN)))


def parse_observation(raw, now):
    raw = raw if isinstance(raw, dict) else {}
    text = raw.get('notice')
    text = text if isinstance(text, str) and len(text) <= 1024 else ''
    explicit = bool(re.search(REFUSAL_PATTERN, text, re.I) and not re.search(NEGATED_REFUSAL_PATTERN, text, re.I))
    result = dict(observation_status='observed' if explicit or raw.get('send_disabled') is True else 'unavailable',
                  blocked=True if explicit else None, confidence='explicit_text' if explicit else
                  'heuristic' if raw.get('send_disabled') is True else 'unknown',
                  failure=None if explicit else 'unrecognized_notice' if text else 'notice_absent', resets_at=None, displayed_reset_time=None)
    if not explicit:
        return result
    displayed = re.search(r'(?<!\d)((?:[01]?\d|2[0-3]):[0-5]\d)(?!\d)', text)
    if displayed:
        hour, minute = displayed[1].split(':')
        result['displayed_reset_time'] = f'{int(hour):02d}:{minute}'
    iso = raw.get('reset_iso')
    if isinstance(iso, str) and len(iso) <= 80:
        try:
            stamp = datetime.fromisoformat(iso.replace('Z', '+00:00'))
            if stamp.tzinfo is not None:
                result['resets_at'] = stamp.timestamp() if stamp.timestamp() > 0 else None
        except (ValueError, OverflowError, OSError):
            pass
    if result['resets_at'] is None:
        zone = raw.get('timezone')
        try:
            if isinstance(zone, str) and len(zone) <= 100:
                result['resets_at'] = parse_pause(text, now, ZoneInfo(zone))
        except (KeyError, ValueError, OverflowError, OSError):
            pass
    return result


def resolve_binding(root, project_root=None, quick_state=None, *, home=None):
    if project_root is not None or quick_state is not None:
        if project_root is None or quick_state is None:
            return None, 'binding_mismatch'
        try:
            return binding(project_root, quick_state, 'explicit'), None
        except (OSError, ValueError):
            return None, 'binding_mismatch'
    import claude_integration
    try:
        home, config, managed = claude_integration.paths(home or Path.home())
        manifest = claude_integration.metadata(managed)
        if manifest is None:
            return None, 'not_configured'
        settings = claude_integration.read_config(config)
        name = claude_integration.managed_name(settings.get('mcpServers', {}), manifest)
        entry = settings.get('mcpServers', {}).get(name)
        if (manifest.get('status') != 'installed' or not isinstance(entry, dict)
                or manifest.get('entry_sha256') != claude_integration.entry_hash(entry)):
            return None, 'binding_mismatch'
        args = entry.get('args', [])
        if not isinstance(args, list):
            return None, 'binding_mismatch'
        for flag, expected in (('--state', manifest.get('quick_state')), ('--project-root', manifest.get('project'))):
            if args.count(flag) != 1 or args.index(flag) + 1 >= len(args) or args[args.index(flag) + 1] != expected:
                return None, 'binding_mismatch'
        from app_paths import state_location
        if (Path(manifest['state']).resolve() != state_location(root).resolve()
                or Path(manifest['quick_state']).resolve() != state_location(root, 'quick-state').resolve()):
            return None, 'binding_mismatch'
        return binding(manifest['project'], manifest['quick_state'], 'managed'), None
    except (OSError, ValueError, KeyError, TypeError):
        return None, 'binding_mismatch'


def _timeout(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('Observation budget exhausted')
    return min(.5, remaining)


def _instance(store, port, metadata):
    if not isinstance(metadata, dict):
        return None
    try:
        url = urlsplit(metadata.get('webSocketDebuggerUrl', ''))
        if (url.scheme != 'ws' or url.hostname not in ('127.0.0.1', 'localhost') or url.port != port
                or url.username or url.password or url.query or url.fragment
                or not re.fullmatch(r'/devtools/browser/[A-Za-z0-9_-]{1,128}', url.path)):
            return None
        return store.registry.opaque('cdp_instance', [store.scope, port, url.path])
    except (ValueError, TypeError):
        return None


def _unavailable(store, sessions, route, now, deadline, reason='source_unavailable'):
    for session in sessions:
        if time.monotonic() >= deadline:
            break
        session['observation'].update(presence='unavailable', target_count=0, last_checked_at=now)
        with store.db:
            retained = store.observe(session, route['revision'])
        if retained:
            store.registry.record_claude(store.scope, route['port'], session['session_key'],
                                         dict(observation_status='unavailable', failure=reason), observed_at=now)


def refresh(store, state, *, now, if_due=False):
    route = store.route()
    if not route['enabled']:
        return 'disabled'
    if if_due and route['next_refresh_at'] is not None and now < route['next_refresh_at']:
        return 'not_due'
    with (Path(state) / 'claude-observer.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'busy'
        route = store.route()
        if not route['enabled']:
            return 'disabled'
        if if_due and route['next_refresh_at'] is not None and now < route['next_refresh_at']:
            return 'not_due'
        sessions, deadline = store.sessions(), time.monotonic() + BUDGET_SECONDS
        store.db.execute('PRAGMA busy_timeout=100')
        try:
            targets = cdp.read_targets(route['port'], timeout=_timeout(deadline))
            metadata = cdp.local_json(route['port'], '/json/version', timeout=_timeout(deadline))
            instance = _instance(store, route['port'], metadata)
            instance_status = 'changed' if route['instance_key'] and instance != route['instance_key'] else 'observed' if instance else 'unknown'
            grouped = {}
            known = {s['chat_id'] for s in sessions}
            for target in targets:
                path = cdp._page_path(target.get('url'))
                if target.get('type') == 'page' and path and path.startswith('/chat/') and path[6:] in known:
                    grouped.setdefault(path[6:], []).append(target)
            # Oldest observations first: a bounded pass cannot starve later sessions.
            sessions.sort(key=lambda s: s['observation'].get('last_checked_at') or 0)
            partial = False
            for session in sessions:
                if time.monotonic() >= deadline:
                    partial = True
                    break
                if store.route()['revision'] != route['revision']:
                    return 'not_due'
                pages = grouped.get(session['chat_id'], [])
                presence = 'not_open' if not pages else 'ambiguous' if len(pages) != 1 else 'open'
                session['observation'].update(presence=presence, target_count=len(pages), last_checked_at=now)
                observation = dict(observation_status='unavailable', failure='session_not_open' if not pages else 'target_ambiguous')
                if len(pages) == 1:
                    session['observation']['last_seen_at'] = now
                    client = None
                    try:
                        client = cdp.CDP(route['port'], timeout=_timeout(deadline),
                                         chat_id=session['chat_id'], target_id=pages[0].get('id'))
                        raw = client.evaluate(notice_script(session['chat_id']))
                        if not isinstance(raw, dict) or cdp._page_path(raw.get('url')) != '/chat/' + session['chat_id']:
                            raise cdp.WSError('Target changed')
                        observation = parse_observation(raw, now)
                    except (OSError, ValueError, cdp.WSError, TimeoutError):
                        observation = dict(observation_status='unavailable', failure='source_unavailable')
                    finally:
                        cdp._close(client)
                # Recheck authorization/config after I/O; never recreate a removed association.
                if store.route()['revision'] != route['revision']:
                    return 'not_due'
                with store.db:
                    retained = store.observe(session, route['revision'])
                if retained:
                    store.registry.record_claude(store.scope, route['port'], session['session_key'], observation, observed_at=now)
            route.update(instance_key=instance, instance_status=instance_status, failure_count=0,
                         refresh_status='partial' if partial else 'refreshed', next_refresh_at=now + route['interval_seconds'])
        except (OSError, ValueError, cdp.WSError, TimeoutError):
            if store.route()['revision'] != route['revision']:
                return 'not_due'
            _unavailable(store, sessions, route, now, deadline)
            failures = route['failure_count'] + 1
            route.update(failure_count=failures, refresh_status='unavailable',
                         next_refresh_at=now + min(900, 60 * 2 ** min(failures - 1, 4)))
        if store.route()['revision'] == route['revision']:
            route['last_refresh_at'] = now
            with store.db:
                store.save_route_if_current(route)
        return route['refresh_status']


def status(root, *, project_root=None, quick_state=None, refresh_now=False, refresh_if_due=False,
           operation=None, chat_id=None, enabled=None, port=None, interval_seconds=None, now=None, home=None):
    stamp = time.time() if now is None else now
    resolved, error = resolve_binding(root, project_root, quick_state, home=home)
    if error:
        return empty_snapshot(error)
    project, state, descriptor = resolved
    write = bool(operation or ((refresh_now or refresh_if_due) and (state / 'quick.sqlite').is_file()))
    db = open_database(state, write=write)
    if db is None:
        result = empty_snapshot(binding=descriptor)
        result.update(status='ok', project_root=project)
        return result
    try:
        store = SessionStore(db, project, initialize=write)
        if operation == 'configure':
            store.configure(enabled=enabled, port=port, interval_seconds=interval_seconds)
        elif operation == 'register':
            store.register(chat_id, source='operator_desktop_confirmed', now=stamp)
        elif operation == 'remove':
            store.remove(chat_id)
        refreshed = refresh(store, state, now=stamp, if_due=refresh_if_due) if refresh_now or refresh_if_due else 'cached'
        return store.snapshot(descriptor, now=stamp, refresh_status=refreshed)
    finally:
        db.close()


def main(root, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in COMMANDS:
        command = sub.add_parser(name)
        command.add_argument('--project-root')
        command.add_argument('--quick-state')
        if name == 'claude-sessions-status':
            mode = command.add_mutually_exclusive_group()
            mode.add_argument('--refresh', action='store_true')
            mode.add_argument('--refresh-if-due', action='store_true')
        elif name == 'claude-observer-config':
            command.add_argument('--enabled', required=True, choices=('true', 'false'))
            command.add_argument('--port', type=int)
            command.add_argument('--interval-seconds', type=int)
        else:
            command.add_argument('--chat', required=True)
    args = parser.parse_args(argv)
    operation = {'claude-observer-config': 'configure', 'claude-session-register': 'register',
                 'claude-session-remove': 'remove'}.get(args.command)
    try:
        result = status(root, project_root=args.project_root, quick_state=args.quick_state,
                        refresh_now=getattr(args, 'refresh', False), refresh_if_due=getattr(args, 'refresh_if_due', False),
                        operation=operation, chat_id=getattr(args, 'chat', None),
                        enabled=getattr(args, 'enabled', 'false') == 'true', port=getattr(args, 'port', None),
                        interval_seconds=getattr(args, 'interval_seconds', None))
    except (OSError, ValueError, sqlite3.Error):
        result = empty_snapshot('unavailable', failure='operation_refused')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0
