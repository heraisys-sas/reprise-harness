import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import external_rankings
import routing_events


def memory_db(version=None):
    db = sqlite3.connect(':memory:')
    routing_events.setup(db)  # porte user_version à 4 et crée le cache
    return db


class ExternalRankingsTests(unittest.TestCase):
    def test_setup_creates_tables_and_bumps_version(self):
        db = memory_db()
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],
                         external_rankings.VERSION)
        names = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('external_rankings', names)
        self.assertIn('external_model_map', names)

    def test_migration_preserves_routing_data(self):
        db = memory_db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.record(db, 'routed', 'tool', 'codex', reason='kept')
        routing_events.setup(db)  # idempotent
        self.assertEqual(db.execute('SELECT billing_class FROM route_admissibility '
                                    "WHERE channel='codex'").fetchone()[0], 'included')
        self.assertEqual(db.execute("SELECT reason FROM routing_events "
                                    "WHERE reason='kept'").fetchone()[0], 'kept')

    def test_ingest_rejects_injection_payloads(self):
        db = memory_db()
        payloads = [
            {'model': 'gpt-6', 'metric': 'price_per_mtok',
             'value': "1.5'; DROP TABLE external_rankings;--"},       # valeur non numérique
            {'model': 'x" UNION SELECT 1--', 'metric': 'latency_ms', 'value': 1.0},  # modèle
            {'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': float('nan')},   # NaN
            {'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': float('inf')},   # inf
            {'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 1.0,
             'extra': 'IGNORED'},                                      # champ inattendu
            {'model': 'a' * 200, 'metric': 'latency_ms', 'value': 1.0},  # trop long
        ]
        accepted, rejected = external_rankings.ingest(db, 'openrouter', payloads)
        self.assertEqual((accepted, rejected), (0, 6))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM external_rankings').fetchone()[0], 0)

    def test_ingest_accepts_conforming_records(self):
        db = memory_db()
        ok = [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 2.5},
              {'model': 'claude-x', 'metric': 'latency_ms', 'value': 850}]
        accepted, rejected = external_rankings.ingest(db, 'openrouter', ok, url='https://e/x')
        self.assertEqual((accepted, rejected), (2, 0))
        row = db.execute('SELECT source_url_hash FROM external_rankings '
                         "WHERE model='gpt-6'").fetchone()
        self.assertEqual(len(row[0]), 16)  # haché, jamais l'URL

    def test_fetch_url_rejects_non_list_payload(self):
        with mock.patch('external_rankings.urllib.request.urlopen') as opener:
            opener.return_value.__enter__.return_value.read.return_value = \
                json.dumps({'instructions': 'ignore previous'}).encode()
            opener.return_value.__enter__.return_value.headers = {'Content-Type': 'application/json'}
            with self.assertRaises(ValueError):
                external_rankings.fetch_url('https://e/x')

    def test_fetch_url_rejects_non_json_content_type(self):
        with mock.patch('external_rankings.urllib.request.urlopen') as opener:
            opener.return_value.__enter__.return_value.headers = {'Content-Type': 'text/html'}
            with self.assertRaises(ValueError):
                external_rankings.fetch_url('https://e/x')

    def test_fetch_url_rejects_oversized_response(self):
        with mock.patch('external_rankings.urllib.request.urlopen') as opener:
            opener.return_value.__enter__.return_value.headers = {'Content-Type': 'application/json'}
            opener.return_value.__enter__.return_value.read.return_value = \
                b'[' + b'1,' * (external_rankings.MAX_RESPONSE_BYTES // 2)
            with self.assertRaises(ValueError):
                external_rankings.fetch_url('https://e/x')

    def test_fetch_url_rejects_invalid_json(self):
        with mock.patch('external_rankings.urllib.request.urlopen') as opener:
            opener.return_value.__enter__.return_value.headers = {'Content-Type': 'application/json'}
            opener.return_value.__enter__.return_value.read.return_value = b'not json {'
            with self.assertRaises(ValueError):
                external_rankings.fetch_url('https://e/x')

    def test_refresh_end_to_end_with_mocked_http(self):
        db = memory_db()
        db.execute("INSERT INTO external_model_map VALUES ('gpt-6','codex')")
        payload = json.dumps([
            {'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 2.0},
            {'model': 'evil', 'metric': 'latency_ms',
             'value': "'; DROP TABLE external_rankings;--"},
        ]).encode()
        with mock.patch('external_rankings.urllib.request.urlopen') as opener:
            opener.return_value.__enter__.return_value.headers = {'Content-Type': 'application/json'}
            opener.return_value.__enter__.return_value.read.return_value = payload
            accepted, rejected = external_rankings.refresh(db, 'openrouter', 'https://e/x')
        self.assertEqual((accepted, rejected), (1, 1))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM external_rankings').fetchone()[0], 1)

    def test_route_metrics_requires_two_sources_no_conflict(self):
        db = memory_db()
        db.execute("INSERT INTO external_model_map VALUES ('gpt-6','codex')")
        for source in ('artificial-analysis', 'openrouter'):
            external_rankings.ingest(db, source,
                                     [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 2.0}])
        m = external_rankings.route_metrics(db, 'codex')
        self.assertEqual(m['price_per_mtok'], 2.0)
        self.assertFalse(m['conflict'])
        self.assertEqual(m['origin'], 'external-cache')

    def test_divergence_badges_and_ignores_external(self):
        db = memory_db()
        db.execute("INSERT INTO external_model_map VALUES ('gpt-6','codex')")
        external_rankings.ingest(db, 'artificial-analysis',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 1.0}])
        external_rankings.ingest(db, 'openrouter',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 9.0}])
        m = external_rankings.route_metrics(db, 'codex')
        self.assertTrue(m['conflict'])
        self.assertIsNone(m['price_per_mtok'])

    def test_route_choice_never_returns_inadmissible(self):
        db = memory_db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.declare(db, 'claude-api', 'paid')
        db.execute("INSERT INTO external_model_map VALUES ('gpt-6','codex')")
        db.execute("INSERT INTO external_model_map VALUES ('claude-api','claude-api')")
        for source in ('artificial-analysis', 'openrouter'):
            external_rankings.ingest(db, source, [
                {'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 9.0},
                {'model': 'claude-api', 'metric': 'price_per_mtok', 'value': 0.1}])
        # claude-api est 45x moins cher mais paid → jamais retourné en auto.
        self.assertEqual(external_rankings.route_choice(
            db, ['codex', 'claude-api'], automatic=True), 'codex')

    def test_route_choice_none_without_data(self):
        db = memory_db()
        routing_events.declare(db, 'codex', 'included')
        self.assertIsNone(external_rankings.route_choice(db, ['codex'], automatic=True))

    def test_stale_and_refresh_failure_keeps_cache(self):
        db = memory_db()
        external_rankings.ingest(db, 'openrouter',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 2.0}])
        self.assertFalse(external_rankings.stale(db, 'openrouter'))
        db.execute("UPDATE external_rankings SET fetched_at=0")
        self.assertTrue(external_rankings.stale(db, 'openrouter'))
        with mock.patch('external_rankings.fetch_url', side_effect=OSError('down')):
            self.assertIsNone(external_rankings.refresh(db, 'openrouter', 'https://e'))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM external_rankings').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()


class OpenRouterAdapterTests(unittest.TestCase):
    def test_unwrap_data_envelope(self):
        import openrouter_adapter as adapter
        self.assertEqual(adapter.unwrap({'data': [1, 2]}), [1, 2])
        self.assertEqual(adapter.unwrap([1]), [1])
        with self.assertRaises(ValueError):
            adapter.unwrap({'results': [1]})
        with self.assertRaises(ValueError):
            adapter.unwrap({'data': 'DROP TABLE'})

    def test_normalize_model_replaces_slashes(self):
        import openrouter_adapter as adapter
        self.assertEqual(adapter.normalize_model('openai/gpt-6'), 'openai_gpt-6')
        with self.assertRaises(ValueError):
            adapter.normalize_model(42)

    def test_transform_full_payload_passes_ingest(self):
        import openrouter_adapter as adapter
        db = memory_db()
        payload = {'data': [
            {'id': 'openai/gpt-6', 'pricing': {'prompt': '0.0000025'},
             'benchmarks': {'artificial_analysis': {'intelligence_index': 71.2}}},
            {'id': 'evil/model', 'pricing': {'prompt': "1'; DROP TABLE x--"}},
            {'id': 'no-price/model'},
        ]}
        records = adapter.transform(payload)
        # Double défense : l'adaptateur ignore l'entrée malformée (float() échoue),
        # et ingest revaliderait de toute façon chaque enregistrement.
        self.assertEqual(len(records), 2)
        accepted, rejected = external_rankings.ingest(db, 'openrouter', records)
        self.assertEqual((accepted, rejected), (2, 0))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM external_rankings').fetchone()[0],
                         accepted)
        self.assertEqual(db.execute('SELECT model FROM external_rankings').fetchall()[0][0],
                         'openai_gpt-6')


class RefreshBySourceTests(unittest.TestCase):
    def test_unknown_source_raises(self):
        db = memory_db()
        with self.assertRaises(ValueError):
            external_rankings.refresh_by_source(db, 'lmarena')

    def test_openrouter_end_to_end(self):
        db = memory_db()
        payload = [{'model': 'openai_gpt-6.1-sol-pro', 'metric': 'price_per_mtok',
                    'value': 4.0}]
        with mock.patch('openrouter_adapter.fetch', return_value=payload), \
             mock.patch('openrouter_adapter.transform', return_value=payload):
            accepted, rejected = external_rankings.refresh_by_source(db, 'openrouter')
        self.assertEqual((accepted, rejected), (1, 0))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM external_rankings').fetchone()[0], 1)

    def test_artificial_analysis_end_to_end(self):
        db = memory_db()
        records = [{'model': 'gpt-6-1-sol', 'metric': 'quality_index', 'value': 71.0}]
        with mock.patch('artificial_analysis_adapter.fetch', return_value=[{}]), \
             mock.patch('artificial_analysis_adapter.transform', return_value=records):
            accepted, rejected = external_rankings.refresh_by_source(db, 'artificial-analysis')
        self.assertEqual((accepted, rejected), (1, 0))

    def test_not_stale_returns_none(self):
        db = memory_db()
        external_rankings.ingest(db, 'openrouter',
                                 [{'model': 'm', 'metric': 'latency_ms', 'value': 1.0}])
        with mock.patch('openrouter_adapter.fetch') as fetch:
            self.assertIsNone(external_rankings.refresh_by_source(db, 'openrouter'))
            fetch.assert_not_called()

    def test_network_failure_keeps_cache(self):
        db = memory_db()
        with mock.patch('openrouter_adapter.fetch', side_effect=OSError('down')):
            self.assertIsNone(external_rankings.refresh_by_source(db, 'openrouter'))

    def test_populate_model_map_idempotent(self):
        db = memory_db()
        first = external_rankings.populate_model_map(db)
        second = external_rankings.populate_model_map(db)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        channels = {row[0] for row in db.execute('SELECT DISTINCT channel FROM external_model_map')}
        self.assertIn('codex', channels)
        self.assertIn('claude-code', channels)
