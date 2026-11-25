"""Tests du lot ① du routeur de délégation : vetos, task_class, non-fusion.

Convention : reprise.tests.test_external_rankings (DatabaseTestCase avec
memory_db : sqlite :memory: + routing_events.setup). Aucune donnée payante
n'est jamais éligible ; aucun test ne brancher expert_mcp (lot ① : aucun
branchement automatique).
"""
import sqlite3
import sys
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import external_rankings
import routing_events


class DatabaseTestCase(unittest.TestCase):
    """Base des tests SQLite : toute connexion créée est fermée en fin de test."""

    def memory_db(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        routing_events.setup(db)  # crée journal + cache + route_refusals
        return db


def seed_channel(db, channel, model, metric, value, sources=('artificial-analysis', 'openrouter')):
    """Insère le mapping canal→modèle puis ingère la métrique depuis sources.
    Mapping idempotent (OR IGNORE) : un même modèle peut être ensemencé
    avec plusieurs métriques dans un même test."""
    db.execute("INSERT OR IGNORE INTO external_model_map VALUES (?,?)", (model, channel))
    for source in sources:
        external_rankings.ingest(db, source,
                                 [{'model': model, 'metric': metric, 'value': value}])


class QualityIndexTests(DatabaseTestCase):
    """Exigence 1 : quality_index accepté d'une source unique ; divergence
    qualité jetant quality_index seul, sans tuer le prix."""

    def test_quality_single_source_is_used(self):
        db = self.memory_db()
        seed_channel(db, 'codex', 'gpt-6', 'quality_index', 71.0,
                     sources=('artificial-analysis',))
        m = external_rankings.route_metrics(db, 'codex')
        self.assertEqual(m['quality_index'], 71.0)
        self.assertFalse(m['conflict'])

    def test_quality_divergence_drops_quality_only_price_survives(self):
        db = self.memory_db()
        seed_channel(db, 'codex', 'gpt-6', 'price_per_mtok', 2.0)
        external_rankings.ingest(db, 'artificial-analysis',
                                 [{'model': 'gpt-6', 'metric': 'quality_index', 'value': 60.0}])
        external_rankings.ingest(db, 'openrouter',
                                 [{'model': 'gpt-6', 'metric': 'quality_index', 'value': 95.0}])
        m = external_rankings.route_metrics(db, 'codex')
        self.assertTrue(m['conflict'])
        self.assertIsNone(m['quality_index'])
        self.assertEqual(m['price_per_mtok'], 2.0)
        self.assertEqual(m['origin'], 'external-cache')

    def test_price_divergence_still_drops_everything(self):
        db = self.memory_db()
        seed_channel(db, 'codex', 'gpt-6', 'quality_index', 71.0,
                     sources=('artificial-analysis',))
        external_rankings.ingest(db, 'artificial-analysis',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 1.0}])
        external_rankings.ingest(db, 'openrouter',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 9.0}])
        m = external_rankings.route_metrics(db, 'codex')
        self.assertTrue(m['conflict'])
        self.assertIsNone(m['price_per_mtok'])
        # Abandon historique global : la qualité (pourtant saine) est perdue
        # avec le prix — comportement inchangé hors quality_index.
        self.assertIsNone(m['quality_index'])

    def test_no_averaging_on_divergent_price(self):
        db = self.memory_db()
        db.execute("INSERT INTO external_model_map VALUES ('gpt-6','codex')")
        external_rankings.ingest(db, 'artificial-analysis',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 1.0}])
        external_rankings.ingest(db, 'openrouter',
                                 [{'model': 'gpt-6', 'metric': 'price_per_mtok', 'value': 3.0}])
        m = external_rankings.route_metrics(db, 'codex')
        self.assertTrue(m['conflict'])
        self.assertIsNone(m['price_per_mtok'])  # jamais (1+3)/2 = 2.0


class VetosTests(DatabaseTestCase):
    """Exigence 2 : vetos(db, channel, ttl_s=None) — paid/unknown en auto,
    quota-perime via ttl, registre route_refusals + refus-recent."""

    def test_vetos_paid(self):
        db = self.memory_db()
        routing_events.declare(db, 'claude-api', 'paid')
        self.assertEqual(routing_events.vetos(db, 'claude-api'), ['paid'])

    def test_vetos_unknown_fail_closed(self):
        db = self.memory_db()
        # Canal jamais déclaré : défaut fail-closed.
        self.assertEqual(routing_events.vetos(db, 'never-declared'), ['unknown'])

    def test_vetos_free_and_included_fresh_no_veto(self):
        db = self.memory_db()
        routing_events.declare(db, 'local-free', 'free')
        routing_events.declare(db, 'sub', 'included')
        self.assertEqual(routing_events.vetos(db, 'local-free'), [])
        self.assertEqual(routing_events.vetos(db, 'sub'), [])

    def test_vetos_quota_perime_after_ttl(self):
        db = self.memory_db()
        routing_events.declare(db, 'sub', 'included')
        db.execute('UPDATE route_admissibility SET updated_at=? WHERE channel=?',
                   (time.time() - 86401.0, 'sub'))
        db.commit()
        self.assertEqual(routing_events.vetos(db, 'sub'), ['quota-perime'])

    def test_vetos_quota_perime_respects_explicit_ttl(self):
        db = self.memory_db()
        routing_events.declare(db, 'sub', 'included')
        db.execute('UPDATE route_admissibility SET updated_at=? WHERE channel=?',
                   (time.time() - 90.0, 'sub'))
        db.commit()
        # ttl_s=60 : périmé (90 > 60). ttl_s=120 : encore frais (90 < 120).
        self.assertEqual(routing_events.vetos(db, 'sub', ttl_s=60.0), ['quota-perime'])
        self.assertEqual(routing_events.vetos(db, 'sub', ttl_s=120.0), [])

    def test_vetos_quota_perime_never_for_free_routes(self):
        db = self.memory_db()
        routing_events.declare(db, 'free', 'free')
        db.execute('UPDATE route_admissibility SET updated_at=0 WHERE channel=?', ('free',))
        db.commit()
        # free n'a pas de quota : jamais quota-perime, même updated_at=0.
        self.assertEqual(routing_events.vetos(db, 'free'), [])

    def test_vetos_refus_recent(self):
        db = self.memory_db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.record_refusal(db, 'codex', 'quota_blocked')
        self.assertEqual(routing_events.vetos(db, 'codex'), ['refus-recent'])

    def test_vetos_refus_old_ignored(self):
        db = self.memory_db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.record_refusal(db, 'codex', 'quota_blocked')
        db.execute('UPDATE route_refusals SET ts=?', (time.time() - 3601.0,))
        db.commit()
        self.assertEqual(routing_events.vetos(db, 'codex'), [])

    def test_record_refusal_requires_nonempty_reason(self):
        db = self.memory_db()
        for bad in ('', '   ', None, 42):
            with self.assertRaises(ValueError):
                routing_events.record_refusal(db, 'codex', bad)
        routing_events.record_refusal(db, 'codex', '  quota  ')
        self.assertEqual(db.execute('SELECT reason FROM route_refusals').fetchone()[0],
                         'quota')  # trim appliqué

    def test_vetos_never_widens_admissibility(self):
        """Un veto n'est jamais un passe-droit : paid/unknown vetos sont un
        sous-ensemble strict des routes refusées par admissible(auto)."""
        db = self.memory_db()
        for channel, billing in (('sub', 'included'), ('free', 'free'),
                                 ('pay', 'paid')):
            routing_events.declare(db, channel, billing)
        # 'ghost' n'est jamais déclaré : billing lu = unknown (fail-closed).
        # paid → veto paid ; included/free → pas de veto billing. Cohérent
        # avec admissible(automatic=True).
        self.assertEqual(routing_events.vetos(db, 'sub'), [])
        self.assertEqual(routing_events.vetos(db, 'free'), [])
        self.assertEqual(routing_events.vetos(db, 'pay'), ['paid'])
        self.assertEqual(routing_events.vetos(db, 'ghost'), ['unknown'])


class TaskClassTests(DatabaseTestCase):
    """Exigence 3 : route_choice(..., task_class=None|rapide|exigeant|interactif)."""

    def _two_channels(self, db):
        routing_events.declare(db, 'codex', 'included')
        routing_events.declare(db, 'fm', 'free')
        seed_channel(db, 'codex', 'gpt-6', 'price_per_mtok', 2.0)
        seed_channel(db, 'fm', 'gpt-fm', 'price_per_mtok', 5.0)

    def test_task_class_none_keeps_historical_behavior(self):
        db = self.memory_db()
        self._two_channels(db)
        self.assertEqual(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True), 'codex')  # le moins cher

    def test_task_class_none_strict_no_quality_requirement(self):
        db = self.memory_db()
        self._two_channels(db)
        # Aucune donnée qualité nulle part : task_class=None choisit quand
        # même le moins cher (pas d'exigence implicite de qualité).
        self.assertEqual(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True, task_class=None), 'codex')

    def test_task_class_invalid_raises(self):
        db = self.memory_db()
        with self.assertRaises(ValueError):
            external_rankings.route_choice(db, ['codex'], task_class='urgente')

    def test_exigeant_requires_quality_above_threshold(self):
        db = self.memory_db()
        self._two_channels(db)
        seed_channel(db, 'codex', 'gpt-6', 'quality_index', 75.0,
                     sources=('artificial-analysis',))
        self.assertEqual(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True, task_class='exigeant'), 'codex')

    def test_exigeant_rejects_quality_below_threshold(self):
        db = self.memory_db()
        self._two_channels(db)
        seed_channel(db, 'codex', 'gpt-6', 'quality_index', 69.0,
                     sources=('artificial-analysis',))
        # codex est le moins cher mais sous QUALITY_MIN_EXIGEANT=70 :
        # éliminé, jamais replié dessus, et fm (sans qualité) non plus.
        self.assertIsNone(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True, task_class='exigeant'))

    def test_exigeant_rejects_missing_quality(self):
        db = self.memory_db()
        self._two_channels(db)
        # Aucune qualité mesurée : exigeant ne peut rien élire.
        self.assertIsNone(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True, task_class='exigeant'))

    def test_interactif_requires_latency_and_ttft_under_thresholds(self):
        db = self.memory_db()
        self._two_channels(db)
        seed_channel(db, 'codex', 'gpt-6', 'latency_ms', 1200.0)
        seed_channel(db, 'codex', 'gpt-6', 'ttft_ms', 900.0)
        self.assertEqual(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True, task_class='interactif'), 'codex')

    def test_interactif_rejects_latency_above_threshold(self):
        db = self.memory_db()
        self._two_channels(db)
        seed_channel(db, 'codex', 'gpt-6', 'latency_ms', 3001.0)
        seed_channel(db, 'codex', 'gpt-6', 'ttft_ms', 900.0)
        self.assertIsNone(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True, task_class='interactif'))

    def test_interactif_rejects_ttft_above_threshold(self):
        db = self.memory_db()
        self._two_channels(db)
        seed_channel(db, 'codex', 'gpt-6', 'latency_ms', 1200.0)
        seed_channel(db, 'codex', 'gpt-6', 'ttft_ms', 1501.0)
        self.assertIsNone(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True, task_class='interactif'))

    def test_interactif_rejects_missing_metrics(self):
        db = self.memory_db()
        self._two_channels(db)
        seed_channel(db, 'codex', 'gpt-6', 'latency_ms', 1200.0)
        # ttft_ms manquant : rejeté (pas d'extrapolation, pas de repli).
        self.assertIsNone(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True, task_class='interactif'))

    def test_rapide_prefers_local_free_when_corroborated(self):
        db = self.memory_db()
        self._two_channels(db)
        # fm est 'free', plus cher (5.0) que codex (2.0) : rapide → fm.
        self.assertEqual(external_rankings.route_choice(
            db, ['codex', 'fm'], automatic=True, task_class='rapide'), 'fm')

    def test_rapide_falls_back_to_cheapest_without_local_free(self):
        db = self.memory_db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.declare(db, 'other', 'included')
        seed_channel(db, 'codex', 'gpt-6', 'price_per_mtok', 2.0)
        seed_channel(db, 'other', 'gpt-other', 'price_per_mtok', 1.0)
        self.assertEqual(external_rankings.route_choice(
            db, ['codex', 'other'], automatic=True, task_class='rapide'), 'other')

    def test_rapide_never_prefers_paid_or_ironclaw(self):
        db = self.memory_db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.declare(db, 'ironclaw', 'free')
        routing_events.declare(db, 'pay', 'paid')
        seed_channel(db, 'codex', 'gpt-6', 'price_per_mtok', 2.0)
        seed_channel(db, 'ironclaw', 'gpt-ic', 'price_per_mtok', 0.0)
        seed_channel(db, 'pay', 'gpt-pay', 'price_per_mtok', 0.01)
        # ironclaw exclu contractuellement (NEVER_AUTO) ; pay inadmissible
        # en auto : rapide élit codex.
        self.assertEqual(external_rankings.route_choice(
            db, ['codex', 'ironclaw', 'pay'], automatic=True,
            task_class='rapide'), 'codex')


class NonMergeTests(DatabaseTestCase):
    """Exigence 4 : non-fusion claude-desktop vs claude-code."""

    def test_claude_desktop_and_claude_code_stay_separate(self):
        db = self.memory_db()
        routing_events.declare(db, 'claude-desktop', 'included')
        routing_events.declare(db, 'claude-code', 'included')
        # Modèles externes distincts par canal, prix distincts.
        seed_channel(db, 'claude-desktop', 'claude-opus-5', 'price_per_mtok', 1.0)
        seed_channel(db, 'claude-code', 'claude-opus-5-code', 'price_per_mtok', 3.0)
        m_desktop = external_rankings.route_metrics(db, 'claude-desktop')
        m_code = external_rankings.route_metrics(db, 'claude-code')
        self.assertEqual(m_desktop['price_per_mtok'], 1.0)
        self.assertEqual(m_code['price_per_mtok'], 3.0)
        # Élections indépendantes : chaque canal garde son propre prix.
        self.assertEqual(external_rankings.route_choice(
            db, ['claude-desktop'], automatic=True), 'claude-desktop')
        self.assertEqual(external_rankings.route_choice(
            db, ['claude-code'], automatic=True), 'claude-code')
        # Choix global : le prix distinct prouve qu'aucune fusion n'a eu lieu.
        self.assertEqual(external_rankings.route_choice(
            db, ['claude-desktop', 'claude-code'], automatic=True),
            'claude-desktop')


class SyntheseV3Tests(DatabaseTestCase):
    """Synthèse v3 : choix arbitrés des deux propositions.
    - cooldown 1800 s (proposition Meta AI, refus transitoire) ;
    - seuils interactif 2000/1000 ms (répartition Meta AI) ;
    - vetos consommés dans route_choice pour une classe explicite (défense
      AutoCoder, étendue) ; ironclaw exclu en auto pour tout chemin ;
    - pass-through task_class dans expert_router.substitute_for_target."""

    def test_refus_recent_cooldown_1800(self):
        db = self.memory_db()
        routing_events.declare(db, 'codex', 'included')
        routing_events.record_refusal(db, 'codex', 'quota_blocked')
        self.assertEqual(routing_events.vetos(db, 'codex'), ['refus-recent'])
        self.assertEqual(routing_events.REFUSAL_COOLDOWN_S, 1800)

    def test_vetos_filtrés_dans_route_choice_par_classe(self):
        db = self.memory_db()
        routing_events.declare(db, 'codex', 'included')
        seed_channel(db, 'codex', 'gpt-6', 'price_per_mtok', 2.0)
        # Sans refus : codex élu. Avec un refus récent : veto → None,
        # jamais re-substitué automatiquement.
        self.assertEqual(external_rankings.route_choice(
            db, ['codex'], automatic=True, task_class='rapide'), 'codex')
        routing_events.record_refusal(db, 'codex', 'quota_blocked')
        self.assertIsNone(external_rankings.route_choice(
            db, ['codex'], automatic=True, task_class='rapide'))
        # Chemin historique (task_class=None) inchangé : pas de vetos.
        self.assertEqual(external_rankings.route_choice(
            db, ['codex'], automatic=True), 'codex')

    def test_substitute_for_target_passe_task_class(self):
        import expert_router
        db = self.memory_db()
        routing_events.declare(db, 'codex', 'included')
        seed_channel(db, 'codex', 'gpt-6', 'quality_index', 40.0,
                     sources=('artificial-analysis',))
        seed_channel(db, 'codex', 'gpt-6', 'price_per_mtok', 2.0)
        bridge = types.SimpleNamespace(
            db=db, program='codex', claude_program=None, desktop_tasks=None,
            chatgpt_socket=None, chatgpt_tasks=None, metaai_values=None,
            metaai_tasks=None, experts={})
        # Sans classe : codex élu (moins cher). Classe exigeante : qualité
        # 40 < 70 → None (le pass-through est effectif).
        self.assertEqual(expert_router.substitute_for_target(
            bridge, 'claude-desktop', task_class=None), 'codex')
        self.assertIsNone(expert_router.substitute_for_target(
            bridge, 'claude-desktop', task_class='exigeant'))


if __name__ == '__main__':
    unittest.main()
