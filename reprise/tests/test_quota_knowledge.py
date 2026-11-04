import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import quota_knowledge as q
from codex_expert_mcp import Bridge


def sample(account='fixture-account-A', used=20, reset=2000):
    return {'accountId': account, 'ordinaryUsageAllowed': True,
            'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 99}},
            'rateLimitsByLimitId': {'codex': {'limitId': 'codex', 'primary': {'usedPercent': used, 'windowDurationMins': 300, 'resetsAt': reset}}}}


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.registry = q.Registry(self.db)

    def record(self, raw, at=1000, engine='codex-server', settings=None):
        return self.registry.record(engine, settings or {}, raw, observed_at=at, source='fixture')

    def latest(self, now=1001, engine='codex-server', settings=None, **kw):
        return self.registry.latest(engine, settings or {}, now=now, **kw)

    def test_pools_do_not_duplicate_legacy_and_are_scoped(self):
        data = sample()
        data['rateLimitsByLimitId']['another'] = {'primary': {'usedPercent': 70}}
        self.record(data)
        result = self.latest()
        self.assertEqual(result['knowledge'], 'observed')
        self.assertEqual(len(result['pools']), 2)
        self.assertNotEqual(result['pools'][0]['quota_pool_key'], result['pools'][1]['quota_pool_key'])
        window = result['pools'][0]['windows'][0]
        self.assertEqual(window['remaining_percent'], 80)
        self.assertEqual(window['duration_minutes'], 300)
        self.assertIsNone(result['effective_remaining_percent'])

    def test_account_change_no_old_measurements_rebound(self):
        self.record(sample())
        a = self.latest()
        self.record(sample('fixture-B', used=80), at=1002)
        b = self.latest(now=1003)
        self.assertNotEqual(a['account_key'], b['account_key'])
        self.assertNotEqual(a['pools'][0]['quota_pool_key'], b['pools'][0]['quota_pool_key'])
        self.assertEqual(b['pools'][0]['windows'][0]['used_percent'], 80)
        self.assertEqual(b['last_successful_observation']['observation_id'], a['observation_id'])

    def test_missing_account_never_inferred_from_email_plan_profile(self):
        raw = sample(None)
        raw.update(email='do-not-store@example.invalid', planType='pro')
        self.record(raw)
        result = self.latest()
        self.assertEqual(result['knowledge'], 'unknown')
        self.assertIsNone(result['account_key'])
        self.assertIsNone(result['pools'][0]['quota_pool_key'])
        self.assertEqual(result['pools'][0]['scope'], 'route_observation')

    def test_provider_identity_and_profile_do_not_imply_quota(self):
        self.record({'tokens': 300, 'accountId': 'ignored'}, engine='harness-cli', settings={'profile': 'fixture'})
        result = self.latest(engine='harness-cli', settings={'profile': 'fixture'})
        self.assertIsNotNone(result['profile_key'])
        self.assertIsNone(result['provider'])
        self.assertEqual(result['knowledge'], 'unknown')
        self.assertEqual(result['pools'], [])
        self.assertEqual(self.latest(engine='harness-cli', settings={'profile': 'other'})['freshness'], 'unobserved')

    def test_opaque_identity_stable_restart_private_salt(self):
        self.record(sample())
        result = self.latest()
        reopened = q.Registry(self.db).latest('codex-server', {}, now=1001)
        self.assertEqual(result['account_key'], reopened['account_key'])
        persisted = str(self.db.execute('SELECT data FROM quota_observations').fetchall())
        self.assertNotIn('fixture-account-A', persisted)
        other = sqlite3.connect(':memory:')
        self.addCleanup(other.close)
        other_registry = q.Registry(other)
        self.assertNotEqual(other_registry.opaque('account', ['openai', 'fixture-account-A']), result['account_key'])

    def test_numeric_validation_unknown_not_zero(self):
        for bad in (None, True, False, '0', float('nan'), float('inf'), -1, 101):
            self.record(sample(used=bad))
            window = self.latest()['pools'][0]['windows'][0]
            self.assertIsNone(window['used_percent'])
            self.assertIsNone(window['remaining_percent'])
            self.assertEqual(self.latest()['knowledge'], 'unknown')
        self.record(sample(used=0))
        self.assertEqual(self.latest()['pools'][0]['windows'][0]['remaining_percent'], 100)

    def test_freshness_is_query_time_and_reset_does_not_restore(self):
        self.record(sample())
        self.assertEqual(self.latest(now=999)['freshness'], 'clock_skew')
        self.assertEqual(self.latest(now=1301)['freshness'], 'stale')
        self.assertEqual(self.latest(now=2001, max_age=5000)['freshness'], 'reset_elapsed')
        self.assertEqual(self.latest(now=2001, max_age=5000)['knowledge'], 'unknown')
        self.record(sample(reset=None))
        self.assertIsNone(self.latest()['pools'][0]['windows'][0]['resets_at'])

    def test_failed_refresh_replaces_current_but_preserves_history(self):
        ident = self.record(sample())
        with patch('quota_knowledge.engines.executable', return_value='/fixture'), patch('quota_knowledge.collect', side_effect=TimeoutError('sensitive data')):
            result = q.status(self.db, refresh=True, now=1010)
        self.assertEqual(result['knowledge'], 'unknown')
        self.assertEqual(result['failure'], 'source_unavailable')
        self.assertEqual(result['last_successful_observation']['observation_id'], ident)
        self.assertEqual(self.db.execute('SELECT count(*) FROM quota_observations').fetchone()[0], 2)
        self.assertNotIn('sensitive data', str(self.db.execute('SELECT data FROM quota_observations').fetchall()))

    def test_no_cache_reuse_after_route_change(self):
        self.record(sample(), settings={'socket': '/private/tmp/a.sock'})
        self.assertEqual(self.latest(settings={'socket': '/private/tmp/b.sock'})['freshness'], 'unobserved')

    def test_bad_pool_map_no_legacy_fallback_and_mismatch_not_merged(self):
        raw = sample()
        raw['rateLimitsByLimitId'] = 'invalid'
        self.record(raw)
        self.assertEqual(self.latest()['pools'], [])
        raw = sample()
        raw['rateLimitsByLimitId']['codex']['limitId'] = 'different'
        self.record(raw)
        self.assertEqual(self.latest()['pools'], [])
        self.assertIn('conflicting_pool_identity', self.latest()['issues'])

    def test_legacy_without_pool_identity_remains_unattributed(self):
        self.record({'accountId': 'fixture', 'rateLimits': {'primary': {'usedPercent': 12}}})
        result = self.latest()
        self.assertEqual(result['knowledge'], 'unknown')
        self.assertEqual(result['pools'][0]['windows'][0]['used_percent'], 12)
        self.assertIsNone(result['pools'][0]['limit_id'])

    def test_permissions_are_strict_and_not_inferred(self):
        for value in (None, 'true', 1, 0):
            raw = sample()
            raw['ordinaryUsageAllowed'] = value
            self.record(raw)
            self.assertIsNone(self.latest()['ordinary_usage_allowed_observed'])

    def test_unknown_source_is_explicit_without_backend_fallback(self):
        with patch('quota_knowledge.collect') as collect:
            result = q.status(self.db, 'claude-desktop', refresh=True, now=1000)
            collect.assert_not_called()
        self.assertEqual(result['failure'], 'quota_source_not_implemented')
        self.assertIsNone(result['account_key'])
        self.assertEqual(result['knowledge'], 'unknown')

    def test_shared_probe_only_reads_quota_metadata(self):
        calls = []
        class Client:
            def __init__(self, *a): pass
            def call(self, name, args):
                calls.append(name)
                return sample() if name == 'account/rateLimits/read' else {}
            def send(self, value): calls.append(value['method'])
            def close(self): calls.append('close')
        with patch('codex_rpc.Client', Client):
            raw = q.collect('codex-server', {}, '/fixture')
        self.assertEqual(raw['accountId'], 'fixture-account-A')
        self.assertEqual(calls, ['initialize', 'initialized', 'account/rateLimits/read', 'close'])

    def test_default_reads_cache_only_and_does_not_touch_scheduler(self):
        with patch('quota_knowledge.collect') as collect:
            result = q.status(self.db)
            collect.assert_not_called()
        self.assertEqual(result['freshness'], 'unobserved')
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('scheduler_meta', tables)

    def test_mcp_quota_lookup_available_while_expertise_lock_held(self):
        import fcntl
        with tempfile.TemporaryDirectory() as root:
            bridge = Bridge(root, {}, Path(root)/'state')
            self.addCleanup(bridge.db.close)
            with bridge.lock_path.open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = bridge.call('reprise_quotas', {})
            self.assertEqual(result['status'], 'finished')
            self.assertEqual(result['quota_knowledge']['knowledge'], 'unknown')

    def test_cli_home_switch_cannot_reuse_cached_account(self):
        with patch.dict('os.environ', {'CODEX_HOME': '/private/tmp/fixture-home-A'}):
            self.record(sample(), engine='codex-cli')
            first = self.latest(engine='codex-cli')
        with patch.dict('os.environ', {'CODEX_HOME': '/private/tmp/fixture-home-B'}):
            other = self.latest(engine='codex-cli')
        self.assertEqual(other['freshness'], 'unobserved')
        self.assertNotEqual(first['route_key'], other['route_key'])

    def test_invalid_pool_identity_not_replaced_by_nested_id(self):
        raw = sample()
        raw['rateLimitsByLimitId'] = {'bad key': raw['rateLimitsByLimitId']['codex'], 'null': None}
        self.record(raw)
        result = self.latest()
        self.assertEqual(result['pools'], [])
        self.assertEqual(result['coverage'], 'partial')
        self.assertIn('invalid_pool_identity', result['issues'])
        self.assertIn('invalid_bucket', result['issues'])

    def test_empty_map_is_not_a_legacy_snapshot(self):
        raw = sample()
        raw['rateLimitsByLimitId'] = {}
        self.record(raw)
        self.assertEqual(self.latest()['pools'], [])

    def test_null_reset_still_preserves_weekly_counter(self):
        raw = sample()
        raw['rateLimitsByLimitId']['codex']['secondary'] = {'usedPercent': 39.5, 'windowDurationMins': 10080, 'resetsAt': None}
        self.record(raw)
        weekly = self.latest()['pools'][0]['windows'][1]
        self.assertEqual(weekly['remaining_percent'], 60.5)
        self.assertEqual(weekly['duration_minutes'], 10080)
        self.assertIsNone(weekly['resets_at'])

    def test_five_hour_observation_expiry_does_not_predict_resume(self):
        self.record(sample(reset=1100))
        fresh = self.latest(now=1050)
        self.assertEqual(fresh['pools'][0]['windows'][0]['duration_minutes'], 300)
        self.assertEqual(fresh['pools'][0]['windows'][0]['resets_at'], 1100)
        expired = self.latest(now=1101)
        self.assertEqual(expired['freshness'], 'reset_elapsed')
        self.assertEqual(expired['knowledge'], 'unknown')
        self.assertIsNone(expired['effective_remaining_percent'])
        self.assertNotIn('retry_at', expired)
