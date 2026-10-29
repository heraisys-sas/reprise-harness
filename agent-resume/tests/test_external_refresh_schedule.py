"""Branchement timer du cache externe : tolérance de panne côté scheduler.

`scheduler.refresh_external` est le point d'accroche appelé par le tick. Il ne
doit jamais lever ni bloquer, et son compte-rendu doit rester consultable dans
scheduler_meta. Les tests sont hermétiques : aucune requête réseau réelle.
"""
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import external_rankings
import routing_events
import scheduler


class RefreshExternalHookTests(unittest.TestCase):
    def memory_db(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        routing_events.setup(db)  # journal + cache externe
        scheduler.setup(db)
        return db

    def test_report_is_persisted_for_status(self):
        db = self.memory_db()
        records = [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 2.0}]
        with mock.patch('openrouter_adapter.fetch', return_value=records), \
             mock.patch('openrouter_adapter.transform', return_value=records), \
             mock.patch('artificial_analysis_adapter.fetch', return_value=[{}]), \
             mock.patch('artificial_analysis_adapter.transform', return_value=records):
            report = scheduler.refresh_external(db, now=1_000_000.0)
        self.assertEqual(report['openrouter']['status'], 'ok')
        self.assertEqual(report['artificial-analysis']['status'], 'ok')
        self.assertEqual(scheduler.get(db, 'external_rankings_last'), report)

    def test_failure_never_raises_and_keeps_cache(self):
        db = self.memory_db()
        external_rankings.ingest(db, 'openrouter',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 2.0}])
        db.execute('UPDATE external_rankings SET fetched_at=0')
        db.commit()
        with mock.patch('openrouter_adapter.fetch', side_effect=OSError('offline')), \
             mock.patch('artificial_analysis_adapter.fetch', side_effect=OSError('offline')):
            report = scheduler.refresh_external(db, now=2_000_000.0)
        self.assertEqual(report['openrouter']['status'], 'failed')
        self.assertEqual(db.execute("SELECT COUNT(*) FROM external_rankings "
                                    "WHERE source='openrouter'").fetchone()[0], 1)

    def test_unexpected_error_in_refresh_is_contained(self):
        db = self.memory_db()
        with mock.patch('external_rankings.refresh_due', side_effect=RuntimeError('boom')):
            report = scheduler.refresh_external(db)
        self.assertEqual(report, {'error': 'RuntimeError'})
        self.assertEqual(scheduler.get(db, 'external_rankings_last'), {'error': 'RuntimeError'})

    def test_missing_cache_table_is_created_then_refreshed(self):
        """La base du tick peut ne pas connaître le cache : setup est idempotent."""
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        scheduler.setup(db)  # aucun routing_events/external_rankings
        records = [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 3.0}]
        with mock.patch('openrouter_adapter.fetch', return_value=records), \
             mock.patch('openrouter_adapter.transform', return_value=records), \
             mock.patch('artificial_analysis_adapter.fetch', return_value=[{}]), \
             mock.patch('artificial_analysis_adapter.transform', return_value=records):
            report = scheduler.refresh_external(db, now=3_000_000.0)
        self.assertEqual(report['openrouter']['status'], 'ok')
        self.assertEqual(db.execute('SELECT COUNT(*) FROM external_rankings').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
