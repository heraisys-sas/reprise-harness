"""Desktop-declared sessions and passive observations in the existing quick.sqlite."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from claude_desktop import validate_chat_id
from quota_knowledge import Registry

CAPABILITIES = dict(dispatch_supported=False, admission_confirmed=False, mcp_return_supported=False,
                    automatic_resume_supported=False, action_eligible=False)


def default_route():
    return dict(enabled=False, transport='cdp-web', port=None, interval_seconds=60,
                route_key=None, instance_status='unknown', instance_key=None,
                refresh_status='disabled', last_refresh_at=None, next_refresh_at=None,
                coverage='open_tabs_only', failure_count=0, revision=0)


def table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


class SessionStore:
    def __init__(self, db, project_root, *, initialize=True):
        project = Path(project_root).resolve(strict=True)
        if not project.is_dir():
            raise ValueError('Project directory required')
        self.db, self.scope = db, str(project)
        if initialize:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS claude_observer_routes(scope TEXT PRIMARY KEY,data TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS claude_associated_sessions(scope TEXT,chat_id TEXT,data TEXT NOT NULL,PRIMARY KEY(scope,chat_id))')
        self.registry = Registry(db, initialize=initialize) if initialize or table_exists(db, 'quota_identity_meta') else None

    def route(self):
        if not table_exists(self.db, 'claude_observer_routes'):
            return default_route()
        row = self.db.execute('SELECT data FROM claude_observer_routes WHERE scope=?', (self.scope,)).fetchone()
        return dict(default_route(), **json.loads(row[0])) if row else default_route()

    def save_route(self, route):
        self.db.execute('INSERT OR REPLACE INTO claude_observer_routes VALUES (?,?)',
                        (self.scope, json.dumps(route, allow_nan=False)))

    def save_route_if_current(self, route):
        return self.db.execute("UPDATE claude_observer_routes SET data=? WHERE scope=? "
                               "AND json_extract(data,'$.revision')=?",
                               (json.dumps(route, allow_nan=False), self.scope, route['revision'])).rowcount == 1

    def observe(self, session, revision):
        return self.db.execute("UPDATE claude_associated_sessions SET data=json_set(data,'$.observation',json(?)) "
                               "WHERE scope=? AND chat_id=? AND json_extract(data,'$.registration.registered_at')=? "
                               "AND EXISTS(SELECT 1 FROM claude_observer_routes WHERE scope=? "
                               "AND json_extract(data,'$.revision')=?)",
                               (json.dumps(session['observation']), self.scope, session['chat_id'],
                                session['registration']['registered_at'], self.scope, revision)).rowcount == 1

    def configure(self, *, enabled, port=None, interval_seconds=None):
        if type(enabled) is not bool:
            raise ValueError('Explicit observation choice required')
        if port is not None and (type(port) is not int or not 1 <= port <= 65535):
            raise ValueError('Invalid local CDP port')
        if interval_seconds is not None and (type(interval_seconds) is not int or not 15 <= interval_seconds <= 3600):
            raise ValueError('Observation interval must be 15..3600 seconds')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            route = self.route()
            selected = port if port is not None else route['port']
            if enabled and selected is None:
                raise ValueError('Explicit local CDP port required')
            changed = selected != route['port']
            route.update(enabled=enabled, port=selected,
                         interval_seconds=interval_seconds if interval_seconds is not None else route['interval_seconds'],
                         route_key=self.registry.claude_route(self.scope, selected) if selected else None,
                         revision=route['revision'] + 1, next_refresh_at=None,
                         refresh_status='cached' if enabled else 'disabled')
            if changed:
                route.update(instance_key=None, instance_status='unknown', last_refresh_at=None, failure_count=0)
                for session in self.sessions():
                    session['observation'] = dict(presence='not_observed', target_count=0, last_seen_at=None)
                    self.save_session(session)
            self.save_route(route)
        return route

    def register(self, chat_id, *, source, now=None):
        chat = validate_chat_id(chat_id)
        if chat is None or source not in ('operator_desktop_confirmed', 'desktop_mcp_declared'):
            raise ValueError('Desktop origin declaration and chat UUID required')
        stamp = time.time() if now is None else now
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT data FROM claude_associated_sessions WHERE scope=? AND chat_id=?',
                                  (self.scope, chat)).fetchone()
            if row:
                return json.loads(row[0])
            session = dict(chat_id=chat, session_key=self.registry.opaque('desktop_session', [self.scope, chat]),
                           registration=dict(origin='claude-desktop', source=source, identity_status='declared',
                                             attested=False, registered_at=stamp),
                           observation=dict(presence='not_observed', target_count=0, last_seen_at=None),
                           action_eligible=False)
            self.save_session(session)
        return session

    def remove(self, chat_id):
        chat = validate_chat_id(chat_id)
        if chat is None:
            raise ValueError('Chat UUID required')
        with self.db:
            self.db.execute('DELETE FROM claude_associated_sessions WHERE scope=? AND chat_id=?', (self.scope, chat))

    def save_session(self, session):
        self.db.execute('INSERT OR REPLACE INTO claude_associated_sessions VALUES (?,?,?)',
                        (self.scope, session['chat_id'], json.dumps(session, allow_nan=False)))

    def sessions(self):
        if not table_exists(self.db, 'claude_associated_sessions'):
            return []
        return [json.loads(row[0]) for row in self.db.execute(
            'SELECT data FROM claude_associated_sessions WHERE scope=? ORDER BY chat_id', (self.scope,))]

    def snapshot(self, binding, *, now=None, refresh_status=None):
        stamp = time.time() if now is None else now
        route = self.route()
        sessions = self.sessions()
        for session in sessions:
            session['quota'] = self.registry.latest_claude(
                self.scope, route['port'], session['session_key'], now=stamp,
                registered_at=session['registration']['registered_at'])
            # A failed route read invalidates current availability even when the
            # bounded writer could not append a failure for every known session.
            if route['refresh_status'] == 'unavailable':
                quota = session['quota']
                windows = [w for pool in quota['pools'] for w in pool['windows']]
                reset = next((w['resets_at'] for w in windows if w['resets_at'] is not None), None)
                history = quota.get('historical_reset')
                if reset is not None:
                    history = dict(resets_at=reset, source=quota['source'], observed_at=quota['observed_at'],
                                   source_observed_at=quota['source_observed_at'], historical_only=True)
                quota.update(blocked=None, observation_status='unavailable', confidence='unknown',
                             source=None, reset_status='unknown', displayed_reset_time=None,
                             pools=[], historical_reset=history, failure='source_unavailable')
        route['last_refresh_status'] = route['refresh_status']
        if refresh_status:
            route['refresh_status'] = refresh_status
        for internal in ('failure_count', 'revision'):
            route.pop(internal, None)
        return dict(schema_version=1, status='ok', project_root=self.scope, binding=binding,
                    route=route, capabilities=dict(CAPABILITIES), sessions=sessions)


def empty_snapshot(status='not_configured', *, binding=None, failure=None):
    result = dict(schema_version=1, status=status, project_root=None,
                  binding=binding or dict(status='unresolved', quick_state=None, state_sha256=None),
                  route=default_route(), capabilities=dict(CAPABILITIES), sessions=[])
    for internal in ('failure_count', 'revision'):
        result['route'].pop(internal, None)
    if failure:
        result['failure'] = failure
    return result


def binding(project, quick_state, source):
    project, state = Path(project).expanduser(), Path(quick_state).expanduser()
    if not project.is_absolute() or not state.is_absolute():
        raise ValueError('Absolute project and quick state paths required')
    project, state = project.resolve(strict=True), state.resolve()
    if not project.is_dir() or not state.is_absolute():
        raise ValueError('Project and quick state required')
    return str(project), state, dict(status=source, quick_state=str(state),
                                    state_sha256=hashlib.sha256(str(state).encode()).hexdigest())


def open_database(state, *, write=False):
    state = Path(state)
    if write:
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        return sqlite3.connect(state / 'quick.sqlite', timeout=2)
    if not (state / 'quick.sqlite').is_file():
        return None
    return sqlite3.connect((state / 'quick.sqlite').as_uri() + '?mode=ro', uri=True, timeout=.2)
