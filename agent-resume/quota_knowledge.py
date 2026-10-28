"""Quota observations only: never admit, resume or route an inference."""
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone

import engines

DEFAULT_MAX_AGE = 300


def number(value, minimum=0, maximum=float('inf')):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        return False


def token(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', value) else None


class Registry:
    def __init__(self, db, *, initialize=True):
        self.db = db
        if initialize:
            self._setup()
        self.salt = bytes.fromhex(db.execute("SELECT value FROM quota_identity_meta WHERE key='local_salt'").fetchone()[0])

    def _setup(self):
        db = self.db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS quota_identity_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO quota_identity_meta VALUES ('local_salt',?)", (os.urandom(32).hex(),))
            db.execute('''CREATE TABLE IF NOT EXISTS quota_observations(
                id INTEGER PRIMARY KEY, route_key TEXT NOT NULL, observed_at REAL NOT NULL,
                data TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS quota_route_observation ON quota_observations(route_key,id)')

    def opaque(self, kind, value):
        if value is None:
            return None
        return hmac.new(self.salt, json.dumps([kind, value], sort_keys=True).encode(), hashlib.sha256).hexdigest()

    def route(self, engine, settings):
        normalized = dict(settings)
        if engine == 'codex-cli':
            normalized['codex_home'] = str(Path(os.environ.get('CODEX_HOME', Path.home()/'.codex')).expanduser().resolve())
        if engine in ('codex-server', 'codex-parent'):
            normalized['socket'] = str(Path(normalized.get('socket', engines.SOCKET)).absolute())
            normalized.pop('parent', None)  # Session ancestry is not an account route.
        return self.opaque('route', [engine, normalized])

    def record(self, engine, settings, raw, *, observed_at=None, source=None, failure=None):
        now = time.time() if observed_at is None else observed_at
        if not number(now):
            raise ValueError('Finite observation time required')
        raw = raw if isinstance(raw, dict) else None
        route = self.route(engine, settings)
        supported = engine in ('codex-cli', 'codex-server', 'codex-parent')
        account_id = raw.get('accountId') if raw and supported else None
        if not isinstance(account_id, str) or not account_id.strip() or len(account_id) > 512:
            account_id = None
        provider = 'openai' if supported else None
        account = self.opaque('account', [provider, account_id]) if account_id else None
        permission = raw.get('ordinaryUsageAllowed') if raw and supported else None
        permission = permission if isinstance(permission, bool) else None
        pools = []
        issues = []
        if raw and supported:
            buckets = raw.get('rateLimitsByLimitId')
            if isinstance(buckets, dict):
                entries = list(buckets.items())[:100]
                if len(buckets) > 100:
                    issues.append('pool_list_truncated')
            elif buckets is None:
                bucket = raw.get('rateLimits')
                entries = [(bucket.get('limitId') if isinstance(bucket, dict) else None, bucket)]
            else:
                entries = []
                issues.append('invalid_pool_map')
            for reported_id, bucket in entries:
                if not isinstance(bucket, dict):
                    issues.append('invalid_bucket')
                    continue
                limit_id = token(reported_id)
                nested_id = token(bucket.get('limitId'))
                if isinstance(buckets, dict) and not limit_id:
                    issues.append('invalid_pool_identity')
                    continue
                if bucket.get('limitId') is not None and not nested_id:
                    issues.append('invalid_pool_identity')
                    continue
                if reported_id is not None and bucket.get('limitId') is not None and reported_id != bucket['limitId']:
                    issues.append('conflicting_pool_identity')
                    continue
                limit_id = limit_id or nested_id
                windows = []
                for name in ('primary', 'secondary'):
                    value = bucket.get(name)
                    if value is None:
                        continue
                    if not isinstance(value, dict):
                        issues.append('invalid_window')
                        continue
                    used, duration, reset = (value.get(k) for k in ('usedPercent', 'windowDurationMins', 'resetsAt'))
                    if not number(used, maximum=100):
                        issues.append('unknown_or_invalid_used_percent')
                    if duration is not None and not number(duration, minimum=1):
                        issues.append('invalid_duration')
                    if reset is not None and not number(reset, minimum=1):
                        issues.append('invalid_reset')
                    used = used if number(used, maximum=100) else None
                    duration = duration if number(duration, minimum=1) else None
                    reset = reset if number(reset, minimum=1) else None
                    windows.append({'name': name, 'used_percent': used,
                                    'remaining_percent': 100 - used if used is not None else None,
                                    'duration_minutes': duration, 'resets_at': reset})
                pools.append({'limit_id': limit_id,
                              'quota_pool_key': self.opaque('pool', [provider, account_id, limit_id]) if account and limit_id else None,
                              'scope': 'account_pool' if account and limit_id else 'route_observation',
                              'windows': windows})
        data = {'schema_version': 1, 'engine': engine, 'route_key': route,
                'profile_key': self.opaque('profile', [engine, settings['profile']]) if settings.get('profile') else None,
                'provider': provider, 'account_key': account, 'identity_status': 'known' if account else 'unknown',
                'account_identity_source': 'account/rateLimits/read.accountId' if account else None,
                'source': source, 'source_observed_at': None, 'observed_at': now,
                'observation_status': 'observed' if raw is not None and supported else 'unavailable',
                'coverage': 'partial' if issues else 'reported_fields',
                'failure': failure, 'ordinary_usage_allowed_observed': permission,
                'pools': pools, 'issues': sorted(set(issues)),
                'usage_totals': None, 'user_budget': None, 'local_daily_cap': None}
        if data['observation_status'] == 'observed' and not pools and permission is None:
            data['observation_status'] = 'invalid'
            data['failure'] = 'empty_or_invalid_measurement'
        # Persist only normalized, allowlisted data. Raw responses/account IDs never enter SQLite.
        with self.db:
            cursor = self.db.execute('INSERT INTO quota_observations(route_key,observed_at,data) VALUES (?,?,?)',
                                     (route, now, json.dumps(data, allow_nan=False)))
        return cursor.lastrowid

    def latest(self, engine, settings, *, now=None, max_age=DEFAULT_MAX_AGE):
        now = time.time() if now is None else now
        if not number(now) or not number(max_age, minimum=1):
            raise ValueError('Finite clock and positive max_age required')
        route = self.route(engine, settings)
        row = self.db.execute('SELECT id,data FROM quota_observations WHERE route_key=? ORDER BY id DESC LIMIT 1', (route,)).fetchone()
        if not row:
            return {'engine': engine, 'route_key': route, 'knowledge': 'unknown', 'freshness': 'unobserved',
                    'account_key': None, 'profile_key': self.opaque('profile', [engine, settings['profile']]) if settings.get('profile') else None,
                    'provider': None, 'observed_at': None, 'pools': [], 'ordinary_usage_allowed_observed': None}
        data = json.loads(row[1])
        previous = self.db.execute("SELECT id,observed_at FROM quota_observations WHERE route_key=? AND id<? AND json_extract(data,'$.observation_status')='observed' ORDER BY id DESC LIMIT 1", (route, row[0])).fetchone()
        age = now - data['observed_at']
        freshness = 'recent_read' if 0 <= age <= max_age else 'clock_skew' if age < 0 else 'stale'
        windows = [w for pool in data['pools'] for w in pool['windows']]
        if freshness == 'recent_read' and any(w['resets_at'] is not None and w['resets_at'] <= now for w in windows):
            freshness = 'reset_elapsed'
        known_windows = any(w['used_percent'] is not None for w in windows)
        scoped = data['account_key'] is not None and all(pool['quota_pool_key'] for pool in data['pools'])
        knowledge = 'observed' if data['observation_status'] == 'observed' and freshness == 'recent_read' and known_windows and scoped else 'unknown'
        return dict(data, observation_id=row[0], last_successful_observation={'observation_id': previous[0], 'observed_at': previous[1], 'historical_only': True} if previous else None, freshness=freshness, age_seconds=age,
                    max_age_seconds=max_age, knowledge=knowledge,
                    effective_remaining_percent=None,
                    note='Observation only; no admission or recovery decision. A recent read does not prove provider-side freshness.')


    def claude_route(self, project, port):
        return self.route('claude-desktop', {'project_root': project, 'transport': 'cdp-web', 'port': port})

    def record_claude(self, project, port, session_key, observation, *, observed_at):
        if not number(observed_at) or not isinstance(session_key, str):
            raise ValueError('Valid session and observation time required')
        raw = observation if isinstance(observation, dict) else {}
        confidence = raw.get('confidence')
        confidence = confidence if confidence in ('unknown', 'heuristic', 'explicit_text', 'structured') else 'unknown'
        status = raw.get('observation_status')
        status = status if status in ('observed', 'unavailable', 'invalid') else 'unavailable'
        reset = raw.get('resets_at')
        reset = reset if number(reset, minimum=1) else None
        if reset is not None:
            try:
                datetime.fromtimestamp(reset, timezone.utc)
            except (ValueError, OverflowError, OSError):
                reset = None
        reason = raw.get('failure')
        reasons = {'notice_absent', 'source_unavailable', 'session_not_open', 'target_ambiguous',
                   'instance_changed', 'unrecognized_notice', 'budget_exhausted', 'route_changed'}
        reason = reason if reason in reasons else None
        displayed = raw.get('displayed_reset_time')
        if not isinstance(displayed, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', displayed):
            displayed = None
        blocked = True if raw.get('blocked') is True and confidence in ('explicit_text', 'structured') and status == 'observed' else None
        data = {'schema_version': 1, 'engine': 'claude-desktop', 'provider': 'anthropic',
                'route_key': self.claude_route(project, port), 'session_key': session_key,
                'account_key': None, 'profile_key': None, 'identity_status': 'unknown',
                'account_identity_source': None, 'knowledge': 'unknown',
                'source': 'claude_cdp.visible_quota_notice' if status == 'observed' else None,
                'source_observed_at': None, 'observed_at': observed_at,
                'observation_status': status, 'confidence': confidence, 'blocked': blocked,
                'reset_status': 'known' if reset is not None else 'ambiguous' if displayed else 'unknown',
                'displayed_reset_time': displayed, 'failure': reason, 'coverage': 'partial',
                'issues': ['account_unattributed', 'pool_unattributed', 'quantity_unreported'],
                'ordinary_usage_allowed_observed': None, 'effective_remaining_percent': None,
                'usage_totals': None, 'user_budget': None, 'local_daily_cap': None,
                'pools': [{'limit_id': None, 'quota_pool_key': None, 'scope': 'session_observation',
                           'windows': [{'name': 'reported', 'used_percent': None,
                                        'remaining_percent': None, 'duration_minutes': None,
                                        'resets_at': reset}]}] if status == 'observed' else []}
        with self.db:
            row = self.db.execute('INSERT INTO quota_observations(route_key,observed_at,data) VALUES (?,?,?)',
                                  (data['route_key'], observed_at, json.dumps(data, allow_nan=False)))
        return row.lastrowid

    def latest_claude(self, project, port, session_key, *, now, registered_at=0, max_age=DEFAULT_MAX_AGE):
        if not number(now) or not number(max_age, minimum=1):
            raise ValueError('Finite clock and positive max_age required')
        route = self.claude_route(project, port)
        row = self.db.execute("SELECT id,data FROM quota_observations WHERE route_key=? AND observed_at>=? "
                              "AND json_extract(data,'$.session_key')=? ORDER BY id DESC LIMIT 1",
                              (route, registered_at, session_key)).fetchone()
        if row is None:
            return {'engine': 'claude-desktop', 'provider': 'anthropic', 'route_key': route,
                    'session_key': session_key, 'account_key': None, 'identity_status': 'unknown',
                    'knowledge': 'unknown', 'source': None, 'observed_at': None, 'source_observed_at': None,
                    'observation_status': 'unavailable', 'freshness': 'unobserved', 'confidence': 'unknown',
                    'blocked': None, 'reset_status': 'unknown', 'displayed_reset_time': None,
                    'pools': [], 'issues': ['account_unattributed', 'pool_unattributed', 'quantity_unreported'],
                    'historical_reset': None, 'failure': None}
        data = json.loads(row[1])
        age = now - data['observed_at']
        freshness = 'recent_read' if 0 <= age <= max_age else 'clock_skew' if age < 0 else 'stale'
        reset = next((w['resets_at'] for p in data['pools'] for w in p['windows'] if w['resets_at'] is not None), None)
        if reset is not None and reset <= now:
            data['reset_status'] = 'elapsed'
            freshness = 'reset_elapsed' if freshness == 'recent_read' else freshness
        if freshness != 'recent_read':
            data['blocked'] = None
        historical = self.db.execute("SELECT data FROM quota_observations WHERE route_key=? AND observed_at>=? "
                                     "AND json_extract(data,'$.session_key')=? AND id<? "
                                     "AND json_extract(data,'$.reset_status')='known' ORDER BY id DESC LIMIT 1",
                                     (route, registered_at, session_key, row[0])).fetchone()
        history = None
        if historical and reset is None:
            old = json.loads(historical[0])
            history = {'resets_at': old['pools'][0]['windows'][0]['resets_at'], 'source': old['source'],
                       'observed_at': old['observed_at'], 'source_observed_at': old['source_observed_at'],
                       'historical_only': True}
        return dict(data, observation_id=row[0], freshness=freshness, age_seconds=age,
                    max_age_seconds=max_age, historical_reset=history)


def collect(engine, settings, program):
    """Read native quota metadata without starting a model turn or persistent daemon."""
    if engine == 'codex-cli':
        from quota import read_limits
        return read_limits(program)
    from codex_rpc import Client
    client = None
    try:
        client = Client(program, settings)
        client.call('initialize', {'clientInfo': {'name': 'reprise-quota-observer', 'version': '0.1.0'}})
        client.send({'method': 'initialized'})
        return client.call('account/rateLimits/read', {})
    finally:
        if client:
            client.close()


def status(db, engine='codex-server', settings=None, *, refresh=False, now=None, max_age=DEFAULT_MAX_AGE):
    if engine not in engines.ENGINES or engine == 'codex':
        raise ValueError('Choose an explicit engine/transport; no automatic route selection')
    if not isinstance(refresh, bool):
        raise ValueError('Boolean refresh required')
    settings = engines.options(engine, settings or {})
    registry = Registry(db)
    if refresh:
        source = None
        raw, failure = None, None
        if engine not in ('codex-cli', 'codex-server', 'codex-parent'):
            failure = 'quota_source_not_implemented'
        else:
            source = 'codex_stdio.account/rateLimits/read' if engine == 'codex-cli' else 'codex_proxy.account/rateLimits/read'
            program = engines.executable(engine)
            if not program:
                failure = 'engine_unavailable'
            else:
                try:
                    raw = collect(engine, settings, program)
                    if not isinstance(raw, dict):
                        failure = 'no_measurement'
                except (OSError, ValueError, TimeoutError):
                    # Do not persist exception text: it may contain private paths or backend data.
                    failure = 'source_unavailable'
        registry.record(engine, settings, raw, observed_at=now, source=source, failure=failure)
    return registry.latest(engine, settings, now=now, max_age=max_age)
