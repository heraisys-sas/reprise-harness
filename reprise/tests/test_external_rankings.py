import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import external_rankings
import routing_events


class DatabaseTestCase(unittest.TestCase):
    """Base des tests SQLite : toute connexion créée est fermée en fin de test.

    Sans ce cleanup, les fixtures :memory: laissent des connexions ouvertes et
    Python émet des ResourceWarning (et le GC décide du moment de fermeture)."""

    def memory_db(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        routing_events.setup(db)  # crée le journal + le cache, porte user_version
        return db


class ExternalRankingsTests(DatabaseTestCase):
    def test_setup_creates_tables_and_bumps_version(self):
        db = self.memory_db()
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],
                         external_rankings.VERSION)
        names = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn('external_rankings', names)
        self.assertIn('external_model_map', names)

    def test_metrics_and_check_constraint_list_the_same_values(self):
        db = self.memory_db()
        for metric in external_rankings.METRICS:
            external_rankings.ingest(db, 'lmarena',
                                     [{'model': 'm', 'metric': metric, 'value': 1.0}])
        self.assertIn('ttft_ms', external_rankings.METRICS)
        accepted = {row[0] for row in db.execute('SELECT metric FROM external_rankings')}
        self.assertEqual(accepted, set(external_rankings.METRICS))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO external_rankings VALUES "
                       "('lmarena','m','invented_metric',1.0,0,NULL)")

    def test_route_metrics_exposes_ttft_ms_without_breaking_callers(self):
        db = self.memory_db()
        db.execute("INSERT INTO external_model_map VALUES ('gpt-6','codex')")
        db.execute("INSERT INTO external_rankings VALUES "
                   "('artificial-analysis','gpt-6','ttft_ms',139000.0,?,NULL)",
                   (external_rankings.time.time(),))
        m = external_rankings.route_metrics(db, 'codex')
        self.assertEqual(m['ttft_ms'], 139000.0)
        # latency_ms reste distinct et vide : plus aucun TTFT ne l'alimente.
        self.assertIsNone(m['latency_ms'])
        self.assertEqual(set(external_rankings.METRICS) | {'origin', 'conflict'}, set(m))

    def test_migration_v5_latency_becomes_ttft_for_artificial_analysis(self):
        db = self.memory_db()
        # Table v5 (CHECK sans ttft_ms) telle que le schéma précédent la créait.
        db.execute('DROP TABLE external_rankings')
        db.execute("CREATE TABLE external_rankings ("
                   "source TEXT NOT NULL CHECK(source IN "
                   "('artificial-analysis','openrouter','lmarena')), "
                   "model TEXT NOT NULL, "
                   "metric TEXT NOT NULL CHECK(metric IN "
                   "('price_per_mtok','latency_ms','quality_index')), "
                   "value REAL NOT NULL, fetched_at REAL NOT NULL, "
                   "source_url_hash TEXT, PRIMARY KEY (source, model, metric))")
        db.executemany('INSERT INTO external_rankings VALUES (?,?,?,?,?,?)', [
            ('artificial-analysis', 'gpt-6', 'latency_ms', 65505.0, 1.0, None),
            ('artificial-analysis', 'gpt-6', 'price_per_mtok', 8.0, 1.0, None),
            ('openrouter', 'gpt-6', 'latency_ms', 850.0, 1.0, None),
        ])
        db.execute('PRAGMA user_version=5')
        external_rankings.setup(db)
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],
                         external_rankings.VERSION)
        kept = {(row[0], row[1], row[2]): row[3] for row in db.execute(
            'SELECT source, model, metric, value FROM external_rankings')}
        # AA : requalifié en ttft_ms (l'adaptateur y écrivait un TTFT).
        self.assertEqual(kept[('artificial-analysis', 'gpt-6', 'ttft_ms')], 65505.0)
        self.assertNotIn(('artificial-analysis', 'gpt-6', 'latency_ms'), kept)
        # Autre source : valeur non identifiable → conservée telle quelle.
        self.assertEqual(kept[('openrouter', 'gpt-6', 'latency_ms')], 850.0)
        self.assertEqual(kept[('artificial-analysis', 'gpt-6', 'price_per_mtok')], 8.0)
        # L'index de fraîcheur suit la table renommée puis est recréé : il doit
        # viser la nouvelle table, pas rester accroché à la table temporaire.
        index = db.execute("SELECT tbl_name FROM sqlite_master WHERE type='index' "
                           "AND name='external_rankings_fresh'").fetchone()
        self.assertEqual(index[0], 'external_rankings')
        external_rankings.setup(db)  # idempotent
        self.assertEqual(db.execute('SELECT COUNT(*) FROM external_rankings').fetchone()[0], 3)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE "
                                    "name='external_rankings_v5'").fetchone()[0], 0)

    def test_setup_refuses_future_schema_version(self):
        db = self.memory_db()
        db.execute(f'PRAGMA user_version={external_rankings.VERSION + 1}')
        with self.assertRaises(ValueError):
            external_rankings.setup(db)

    def test_migration_preserves_routing_data(self):
        db = self.memory_db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.record(db, 'routed', 'tool', 'codex', reason='kept')
        routing_events.setup(db)  # idempotent
        self.assertEqual(db.execute('SELECT billing_class FROM route_admissibility '
                                    "WHERE channel='codex'").fetchone()[0], 'included')
        self.assertEqual(db.execute("SELECT reason FROM routing_events "
                                    "WHERE reason='kept'").fetchone()[0], 'kept')

    def test_ingest_rejects_injection_payloads(self):
        db = self.memory_db()
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
        db = self.memory_db()
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
        db = self.memory_db()
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
        db = self.memory_db()
        db.execute("INSERT INTO external_model_map VALUES ('gpt-6','codex')")
        for source in ('artificial-analysis', 'openrouter'):
            external_rankings.ingest(db, source,
                                     [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 2.0}])
        m = external_rankings.route_metrics(db, 'codex')
        self.assertEqual(m['price_per_mtok'], 2.0)
        self.assertFalse(m['conflict'])
        self.assertEqual(m['origin'], 'external-cache')

    def test_divergence_badges_and_ignores_external(self):
        db = self.memory_db()
        db.execute("INSERT INTO external_model_map VALUES ('gpt-6','codex')")
        external_rankings.ingest(db, 'artificial-analysis',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 1.0}])
        external_rankings.ingest(db, 'openrouter',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 9.0}])
        m = external_rankings.route_metrics(db, 'codex')
        self.assertTrue(m['conflict'])
        self.assertIsNone(m['price_per_mtok'])

    def test_local_journal_priority_is_absent_characterization(self):
        """Caractérise une ABSENCE, pas la priorité annoncée.

        La docstring de route_metrics (et celle du module) affirment que le
        journal local routing_events prime sur le cache externe. Le code ne lit
        jamais routing_events dans route_metrics : il n'existe aucune colonne de
        durée/TTFT à consommer. Ce test verrouille donc l'état réel — l'origine
        reste 'external-cache' même quand le journal local contient des événements
        — pour que toute implémentation future de la priorité le fasse échouer et
        force la mise à jour de cette attente.
        """
        db = self.memory_db()
        db.execute("INSERT INTO external_model_map VALUES ('gpt-6','codex')")
        for source in ('artificial-analysis', 'openrouter'):
            external_rankings.ingest(db, source,
                                     [{'model': 'gpt-6', 'metric': 'price_per_mtok',
                                       'value': 5.0}])
        for _ in range(5):
            routing_events.record(db, 'routed', 'tool', 'codex', reason='local')
        m = external_rankings.route_metrics(db, 'codex')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM routing_events').fetchone()[0], 5)
        self.assertEqual(m['origin'], 'external-cache')  # pas 'local-journal'
        self.assertEqual(m['price_per_mtok'], 5.0)

    def test_route_choice_never_returns_inadmissible(self):
        db = self.memory_db()
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
        db = self.memory_db()
        routing_events.declare(db, 'codex', 'included')
        self.assertIsNone(external_rankings.route_choice(db, ['codex'], automatic=True))

    def test_stale_and_refresh_failure_keeps_cache(self):
        db = self.memory_db()
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


class OpenRouterAdapterTests(DatabaseTestCase):
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
        db = self.memory_db()
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


class RefreshBySourceTests(DatabaseTestCase):
    def test_unknown_source_raises(self):
        db = self.memory_db()
        with self.assertRaises(ValueError):
            external_rankings.refresh_by_source(db, 'lmarena')

    def test_openrouter_end_to_end(self):
        db = self.memory_db()
        payload = [{'model': 'openai_gpt-6.1-sol-pro', 'metric': 'price_per_mtok',
                    'value': 4.0}]
        with mock.patch('openrouter_adapter.fetch', return_value=payload), \
             mock.patch('openrouter_adapter.transform', return_value=payload):
            accepted, rejected = external_rankings.refresh_by_source(db, 'openrouter')
        self.assertEqual((accepted, rejected), (1, 0))
        self.assertEqual(db.execute('SELECT COUNT(*) FROM external_rankings').fetchone()[0], 1)

    def test_artificial_analysis_end_to_end(self):
        db = self.memory_db()
        records = [{'model': 'gpt-6-1-sol', 'metric': 'quality_index', 'value': 71.0}]
        with mock.patch('artificial_analysis_adapter.fetch', return_value=[{}]), \
             mock.patch('artificial_analysis_adapter.transform', return_value=records):
            accepted, rejected = external_rankings.refresh_by_source(db, 'artificial-analysis')
        self.assertEqual((accepted, rejected), (1, 0))

    def test_not_stale_returns_none(self):
        db = self.memory_db()
        external_rankings.ingest(db, 'openrouter',
                                 [{'model': 'm', 'metric': 'latency_ms', 'value': 1.0}])
        with mock.patch('openrouter_adapter.fetch') as fetch:
            self.assertIsNone(external_rankings.refresh_by_source(db, 'openrouter'))
            fetch.assert_not_called()

    def test_network_failure_keeps_cache(self):
        db = self.memory_db()
        with mock.patch('openrouter_adapter.fetch', side_effect=OSError('down')):
            self.assertIsNone(external_rankings.refresh_by_source(db, 'openrouter'))

    def test_populate_model_map_idempotent(self):
        db = self.memory_db()
        first = external_rankings.populate_model_map(db)
        second = external_rankings.populate_model_map(db)
        self.assertGreater(first, 0)
        self.assertEqual(second, 0)
        channels = {row[0] for row in db.execute('SELECT DISTINCT channel FROM external_model_map')}
        self.assertIn('codex', channels)
        self.assertIn('claude-code', channels)


class RefreshDueTests(DatabaseTestCase):
    """Planification à tolérance de panne : aucun échec ne bloque ni ne vide."""

    RECORDS = [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 3.0}]

    def seed(self, db, source='openrouter'):
        external_rankings.ingest(db, source, self.RECORDS)
        db.execute('UPDATE external_rankings SET fetched_at=0')  # rendre stale
        db.commit()
        self.assertTrue(external_rankings.stale(db, source))

    def call(self, db, **kwargs):
        with mock.patch('openrouter_adapter.fetch', return_value=self.RECORDS), \
             mock.patch('openrouter_adapter.transform', return_value=self.RECORDS), \
             mock.patch('artificial_analysis_adapter.fetch', return_value=[{}]), \
             mock.patch('artificial_analysis_adapter.transform', return_value=self.RECORDS):
            return external_rankings.refresh_due(db, **kwargs)

    def test_success_records_trace_and_zero_failures(self):
        db = self.memory_db()
        report = self.call(db, sources=['openrouter'])
        self.assertEqual(report['openrouter']['status'], 'ok')
        self.assertEqual(report['openrouter']['accepted'], 1)
        self.assertEqual(report['openrouter']['failures'], 0)
        meta = external_rankings.refresh_meta(db, 'openrouter')
        self.assertIsNotNone(meta['last_refresh'])
        self.assertEqual(meta['failures'], 0)
        self.assertIsNone(meta['last_error'])
        self.assertFalse(external_rankings.stale(db, 'openrouter'))

    def test_network_failure_keeps_cache_and_never_raises(self):
        db = self.memory_db()
        self.seed(db)
        with mock.patch('openrouter_adapter.fetch', side_effect=OSError('offline')):
            report = external_rankings.refresh_due(db, sources=['openrouter'])
        self.assertEqual(report['openrouter']['status'], 'failed')
        self.assertEqual(report['openrouter']['failures'], 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM external_rankings "
                                    "WHERE source='openrouter'").fetchone()[0], 1)
        # Trace P0 : la classe de l'erreur seulement, jamais le message/URL.
        self.assertEqual(external_rankings.refresh_meta(db, 'openrouter')['last_error'], 'OSError')

    def test_value_and_unexpected_errors_are_contained(self):
        db = self.memory_db()
        self.seed(db)
        with mock.patch('openrouter_adapter.fetch', side_effect=RuntimeError('boom')):
            report = external_rankings.refresh_due(db, sources=['openrouter'])
        self.assertEqual(report['openrouter']['status'], 'failed')
        self.assertEqual(report['openrouter']['reason'], 'RuntimeError')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM external_rankings').fetchone()[0], 1)

    def test_none_result_is_failure_with_absorbed_class(self):
        """refresh_by_source absorbe l'exception : sa classe est retracée."""
        db = self.memory_db()
        self.seed(db)
        with mock.patch('openrouter_adapter.fetch', side_effect=ValueError('Content-Type')):
            report = external_rankings.refresh_due(db, sources=['openrouter'])
        self.assertEqual(report['openrouter']['status'], 'failed')
        self.assertEqual(report['openrouter']['reason'], 'ValueError')
        self.assertEqual(external_rankings.refresh_meta(db, 'openrouter')['last_error'],
                         'ValueError')

    def test_fresh_source_is_skipped_without_network(self):
        db = self.memory_db()
        external_rankings.ingest(db, 'openrouter', self.RECORDS)
        with mock.patch('openrouter_adapter.fetch') as fetch:
            report = external_rankings.refresh_due(db, sources=['openrouter'])
            fetch.assert_not_called()
        self.assertEqual(report['openrouter']['status'], 'skipped')
        self.assertEqual(report['openrouter']['reason'], 'fresh')

    def test_backoff_grows_then_resets_after_success(self):
        db = self.memory_db()
        base = external_rankings.ttl(db, 'openrouter')
        self.assertEqual(base, external_rankings.DEFAULT_TTL_SECONDS)
        delays = [external_rankings.backoff_delay(base, n) for n in (0, 1, 2, 3, 4, 9)]
        self.assertEqual(delays, [0, base, 2 * base, 4 * base, 4 * base, 4 * base])

        self.seed(db)
        t0 = 1_000_000.0
        with mock.patch('openrouter_adapter.fetch', side_effect=OSError('down')):
            first = external_rankings.refresh_due(db, sources=['openrouter'], now=t0)
        self.assertEqual(first['openrouter']['status'], 'failed')
        # Backoff 1× : une tentative immédiate est différée.
        with mock.patch('openrouter_adapter.fetch') as fetch:
            report = external_rankings.refresh_due(db, sources=['openrouter'], now=t0 + 1)
            fetch.assert_not_called()
        self.assertEqual(report['openrouter']['reason'], 'backoff')
        # Backoff écoulé (t0 + base) : nouvelle tentative, second échec → 2×.
        with mock.patch('openrouter_adapter.fetch', side_effect=OSError('down')):
            second = external_rankings.refresh_due(db, sources=['openrouter'], now=t0 + base + 1)
        self.assertEqual(second['openrouter']['failures'], 2)
        # Un succès remet le compteur à zéro et efface last_error.
        ok = self.call(db, sources=['openrouter'], now=t0 + 3 * base + 2)
        self.assertEqual(ok['openrouter']['status'], 'ok')
        self.assertEqual(external_rankings.refresh_meta(db, 'openrouter')['failures'], 0)
        self.assertIsNone(external_rankings.refresh_meta(db, 'openrouter')['last_error'])

    def test_cap_bounds_backoff_at_four_intervals(self):
        db = self.memory_db()
        base = external_rankings.ttl(db, 'openrouter')
        self.assertEqual(external_rankings.backoff_delay(base, 5), 4 * base)
        repeated = external_rankings.backoff_delay(base, 12)
        self.assertEqual(repeated, 4 * base)

    def test_long_sleep_recovers_immediately(self):
        """Timer arrêté / machine en veille : last_attempt ancien → pas de blocage."""
        db = self.memory_db()
        self.seed(db)
        with mock.patch('openrouter_adapter.fetch', side_effect=OSError('down')):
            external_rankings.refresh_due(db, sources=['openrouter'], now=0.0)
        report = self.call(db, sources=['openrouter'], now=30 * 86400)
        self.assertEqual(report['openrouter']['status'], 'ok')

    def test_concurrent_ticks_do_not_double_write(self):
        """Deux ticks concurrents : un seul rafraîchit, l'autre est 'skipped'."""
        db = self.memory_db()
        self.seed(db)
        now = 5_000_000.0
        first = external_rankings.refresh_due(db, sources=['openrouter'], now=now)
        self.assertEqual(first['openrouter']['status'], 'ok')
        before = db.execute('SELECT COUNT(*), MAX(fetched_at) FROM external_rankings').fetchone()
        with mock.patch('openrouter_adapter.fetch') as fetch:
            second = external_rankings.refresh_due(db, sources=['openrouter'], now=now)
            fetch.assert_not_called()
        self.assertEqual(second['openrouter']['status'], 'skipped')
        after = db.execute('SELECT COUNT(*), MAX(fetched_at) FROM external_rankings').fetchone()
        self.assertEqual(tuple(before), tuple(after))

    def test_lease_blocks_second_tick_while_first_holds_it(self):
        db = self.memory_db()
        self.seed(db)
        now = 6_000_000.0
        self.assertTrue(external_rankings._claim(db, 'openrouter', now))
        with mock.patch('openrouter_adapter.fetch') as fetch:
            report = external_rankings.refresh_due(db, sources=['openrouter'], now=now + 1)
            fetch.assert_not_called()
        self.assertEqual(report['openrouter']['reason'], 'concurrent')
        # Bail expiré (au-delà du bail) : le rafraîchissement redevient possible.
        later = now + external_rankings.REFRESH_LEASE_SECONDS + 1
        report = self.call(db, sources=['openrouter'], now=later)
        self.assertEqual(report['openrouter']['status'], 'ok')

    def test_unknown_source_ignored_without_exception(self):
        db = self.memory_db()
        report = external_rankings.refresh_due(db, sources=['lmarena', 'nope'])
        for source in ('lmarena', 'nope'):
            self.assertEqual(report[source]['status'], 'skipped')
            self.assertEqual(report[source]['reason'], 'unknown-source')
        # Les sources connues restent traitées dans le même passage.
        report = self.call(db)
        self.assertEqual(set(report), set(external_rankings.REFRESH_SOURCES))
        self.assertTrue(all(report[s]['status'] == 'ok' for s in report))

    def test_default_sources_cover_every_adapted_source(self):
        self.assertEqual(set(external_rankings.REFRESH_SOURCES),
                         {'openrouter', 'artificial-analysis'})
