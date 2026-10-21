"""Quota observations only: never admit, resume or route an inference."""
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import time

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
    def __init__(self, db):
        self.db = db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS quota_identity_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute("INSERT OR IGNORE INTO quota_identity_meta VALUES ('local_salt',?)", (os.urandom(32).hex(),))
            db.execute('''CREATE TABLE IF NOT EXISTS quota_observations(
                id INTEGER PRIMARY KEY, route_key TEXT NOT NULL, observed_at REAL NOT NULL,
                data TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS quota_route_observation ON quota_observations(route_key,id)')
        self.salt = bytes.fromhex(db.execute("SELECT value FROM quota_identity_meta WHERE key='local_salt'").fetchone()[0])

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
