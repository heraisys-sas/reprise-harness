"""Routeur de substitution automatique (expert_router + attempt_substitution).

Contrat opérateur : déclencheurs = quota_blocked, billing_unverified,
engine_unavailable ; les trois portes manifeste (#258 lot 2) restent des refus
définitifs jamais substitués ; jamais ironclaw, jamais backend désactivé,
jamais facturation payante/unknown ; aucune cible → refus d'origine INCHANGÉ ;
flag OFF par défaut ; preflight ne substitue jamais.
"""
import json
import sys
import tempfile
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import apple_expert
import expert_mcp
import external_rankings
import routing_events
from expert_mcp import BACKEND_MODELS, Bridge, load_default_chain


def seed_catalog(bridge):
    bridge._codex_catalog_cache = {
        'models': [{'model': model, 'displayName': model,
                    'supportedReasoningEfforts': ['low', 'medium', 'high']}
                   for model in BACKEND_MODELS['codex']],
        'source': 'live', 'updated_at': '2026-11-20T15:00:00+00:00', 'error': None}
    bridge._codex_catalog_time = time.monotonic()


CODEX = {'session': 'stdio_thread'}
FULL_MANIFEST = {
    'schema_version': 1, 'backends': {
        'codex': {**CODEX, 'capacity': 2, 'resumable': True},
        'claude-code': {'session': 'stdio_thread', 'capacity': 2, 'resumable': True},
        'claude-desktop': {'session': 'durable_taskstore'},
        'chatgpt': {'session': 'window_continuation', 'resumable': True},
        'foundation-models': {'session': 'push_only'},
        'ironclaw': {'session': 'push_only'},
        'metaai': {'session': 'durable_taskstore', 'enabled': False},
    }}


def seed_ranking(db, channel, model, price):
    """Prix corroboré : même modèle vu par 2 sources (seuil route_metrics)."""
    for source in ('artificial-analysis', 'openrouter'):
        accepted, _ = external_rankings.ingest(
            db, source, [{'model': model, 'metric': 'price_per_mtok', 'value': price}], url='')
        assert accepted == 1, (source, model)


class ExpertRouterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        (self.root / 'rollout.jsonl').touch()
        self.state = Path(self.temp.name) / 'state'
        self.manifest = Path(self.temp.name) / 'experts-manifest.json'

    @contextmanager
    def patched_path(self):
        with patch.object(expert_mcp, 'EXPERTS_MANIFEST_PATH', self.manifest):
            yield

    def write_manifest(self, document):
        self.manifest.write_text(json.dumps(document), encoding='utf-8')

    def bridge(self, document=None, **kwargs):
        """Bridge prêt pour substitution : flag opt-in via automatic_fallback_enabled
        (True par défaut) ; automatic_fallback_enabled=None = sentinel → kwarg
        omis, le flag est dérivé du manifeste (load_fallback_flag)."""
        if document is not None:
            self.write_manifest(document)
        if 'automatic_fallback_enabled' in kwargs and kwargs['automatic_fallback_enabled'] is None:
            kwargs.pop('automatic_fallback_enabled')
        else:
            kwargs.setdefault('automatic_fallback_enabled', True)
        with self.patched_path():
            bridge = Bridge(self.root, {}, self.state, **kwargs)
        bridge.program = 'codex'
        seed_catalog(bridge)
        self.addCleanup(bridge.close)
        return bridge

    def occupy(self, bridge, backend, slot, key='occupied'):
        now = time.time()
        bridge.db.execute('INSERT INTO expert_active_turns VALUES (?,?,?,?,?,?)',
                          (backend, slot, None, key, now, now))
        bridge.db.commit()

    def delegate(self, bridge, **extra):
        args = {'question': 'Q', 'kind': 'expertise', 'request_key': 'probe-router'}
        args.update(extra)
        return bridge.call('reprise_delegate', args)

    def codex_admissible(self, bridge):
        routing_events.declare(bridge.db, 'codex', 'included')

    # — Flag OFF par défaut (et validation bool stricte) —

    def test_flag_off_by_default_never_substitutes(self):
        bridge = self.bridge(FULL_MANIFEST, automatic_fallback_enabled=False)
        self.codex_admissible(bridge)
        with patch.object(apple_expert, 'configured', return_value=False):
            result = self.delegate(bridge, backend='foundation-models')
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertIs(result.get('automatic_fallback'), False)
        self.assertNotIn('requested_route', result)
        self.assertNotIn('served_route', result)
        self.assertEqual([e for e in routing_events.recent(bridge.db)
                          if e['type'] == 'routing_substituted'], [])

    def test_non_bool_flag_rejected(self):
        with self.assertRaises(ValueError, msg='Explicit automatic-fallback grant required'):
            self.bridge(FULL_MANIFEST, automatic_fallback_enabled='yes')

    def test_backends_report_reflects_flag(self):
        off = self.bridge(FULL_MANIFEST, automatic_fallback_enabled=False)
        on = self.bridge(FULL_MANIFEST, automatic_fallback_enabled=True)
        self.assertIs(off.call('reprise_backends', {})['automatic_fallback_enabled'], False)
        self.assertIs(on.call('reprise_backends', {})['automatic_fallback_enabled'], True)

    # — Dérivation manifeste du flag (sentinel : kwarg omis) —

    def test_flag_derived_from_manifest_when_kwarg_omitted(self):
        derived_on = self.bridge({**FULL_MANIFEST, 'automatic_fallback': True},
                                 automatic_fallback_enabled=None)
        self.assertIs(derived_on.automatic_fallback_enabled, True)
        self.assertIs(derived_on.call('reprise_backends', {})['automatic_fallback_enabled'], True)
        derived_off = self.bridge({**FULL_MANIFEST, 'automatic_fallback': False},
                                  automatic_fallback_enabled=None)
        self.assertIs(derived_off.automatic_fallback_enabled, False)

    def test_flag_defaults_to_false_when_manifest_field_absent(self):
        bridge = self.bridge(FULL_MANIFEST, automatic_fallback_enabled=None)
        self.assertIs(bridge.automatic_fallback_enabled, False)

    def test_explicit_kwarg_overrides_manifest_flag(self):
        bridge = self.bridge({**FULL_MANIFEST, 'automatic_fallback': True},
                             automatic_fallback_enabled=False)
        self.assertIs(bridge.automatic_fallback_enabled, False)

    def test_derived_flag_enables_substitution_end_to_end(self):
        # Même parcours que test_explicit_failure_substitutes… mais SANS kwarg :
        # le True dérivé du manifeste arme la substitution (garde 957).
        bridge = self.bridge({**FULL_MANIFEST, 'automatic_fallback': True},
                             automatic_fallback_enabled=None)
        self.codex_admissible(bridge)
        bridge.claude_program = None
        seed_ranking(bridge.db, 'codex', 'openai_gpt-6.1-sol-pro', 2.0)
        external_rankings.populate_model_map(bridge.db)
        with patch.object(apple_expert, 'configured', return_value=True), \
             patch.object(apple_expert, 'review',
                          return_value={'status': 'engine_unavailable',
                                        'reason': 'fm unavailable for test'}), \
             patch.object(expert_mcp, 'review',
                          return_value={'status': 'finished', 'response': 'ok',
                                        'thread_id': 't1', 'resumable': True}):
            result = self.delegate(bridge, backend='foundation-models')
        self.assertEqual(result['status'], 'finished')
        self.assertIs(result['automatic_fallback'], True)
        self.assertEqual(result['requested_route'], 'foundation-models')
        self.assertEqual(result['served_route'], 'codex')

    def test_reload_rederives_flag_only_when_not_explicit(self):
        derived = self.bridge({**FULL_MANIFEST, 'automatic_fallback': True},
                              automatic_fallback_enabled=None)
        self.assertIs(derived.automatic_fallback_enabled, True)
        self.write_manifest({**FULL_MANIFEST, 'automatic_fallback': False})
        with self.patched_path():
            derived.reload_experts_manifest()
        self.assertIs(derived.automatic_fallback_enabled, False)
        # Kwarg explicite : la re-dérivation est sautée, même si le manifeste
        # demande True entre-temps.
        explicit = self.bridge({**FULL_MANIFEST, 'automatic_fallback': False},
                               automatic_fallback_enabled=False)
        self.write_manifest({**FULL_MANIFEST, 'automatic_fallback': True})
        with self.patched_path():
            explicit.reload_experts_manifest()
        self.assertIs(explicit.automatic_fallback_enabled, False)

    # — Déclencheurs et portes manifeste définitives —

    def test_trigger_statuses(self):
        import expert_router
        for status in ('quota_blocked', 'billing_unverified', 'engine_unavailable'):
            self.assertTrue(expert_router.is_trigger({'status': status}))
        for status in ('busy', 'admissibility_refused', 'return_channel_unavailable', 'finished'):
            self.assertFalse(expert_router.is_trigger({'status': status}))

    def test_manifest_gate_never_substituted_despite_engine_unavailable(self):
        bridge = self.bridge(FULL_MANIFEST)
        self.codex_admissible(bridge)
        result = self.delegate(bridge, backend='metaai')
        self.assertEqual(result, {'status': 'engine_unavailable', 'backend': 'metaai',
                                  'reason': 'Backend metaai désactivé dans experts-manifest.json (#258 lot 2).',
                                  'automatic_fallback': False, 'request_key': 'probe-router'})
        self.assertEqual([e for e in routing_events.recent(bridge.db)
                          if e['type'] == 'routing_substituted'], [])

    def test_unknown_backend_gate_is_trigger_exception(self):
        # Porte inconnu-du-manifeste : structuralement inatteignable via
        # reprise_delegate (validation en amont) — couverte au niveau
        # expert_router.is_trigger sur le dict synthétisé (#258 lot 2).
        import expert_router
        self.assertFalse(expert_router.is_trigger(
            {'status': 'engine_unavailable',
             'reason': 'Backend mystery inconnu du manifeste experts (#258 lot 2).'}))
        self.assertTrue(expert_router.is_trigger(
            {'status': 'engine_unavailable', 'reason': 'Anything else failed.'}))

    def test_declared_without_handler_gate_is_trigger_exception(self):
        # Porte déclaré-sans-handler (#258 lot 2) : même exception contractuelle,
        # même raison structurelle — refus définitif, jamais substitué.
        import expert_router
        self.assertFalse(expert_router.is_trigger(
            {'status': 'engine_unavailable',
             'reason': 'Backend mystery2 sans handler de dispatch (#258 lot 2).'}))
        self.assertFalse(expert_router.is_trigger(
            {'status': 'engine_unavailable',
             'reason': 'Backend metaai désactivé dans experts-manifest.json (#258 lot 2).'}))

    # — Branchement record_refusal (lot 3 #258) : observation, jamais re-routage —

    def test_observed_refusal_recorded_without_optin(self):
        # Flag OFF : le refus est OBSERVÉ (route_refusals) même sans opt-in —
        # l'observation est un fait, pas un choix de routage ; le refus
        # d'origine reste inchangé (aucune annotation de substitution).
        bridge = self.bridge(FULL_MANIFEST, automatic_fallback_enabled=False)
        self.codex_admissible(bridge)
        with patch.object(apple_expert, 'configured', return_value=False):
            result = self.delegate(bridge, backend='foundation-models')
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertNotIn('requested_route', result)
        self.assertNotIn('served_route', result)
        self.assertEqual(bridge.db.execute(
            'SELECT channel, reason FROM route_refusals').fetchone(),
            ('foundation-models', 'engine_unavailable'))

    def test_observed_refusal_manifest_gate_never_recorded(self):
        # Les trois portes manifeste restent définitives : le marqueur lot 2
        # fait échouer is_trigger → AUCUN refus journalisé (refus structurel,
        # pas une indisponibilité transitoire).
        bridge = self.bridge(FULL_MANIFEST)
        self.codex_admissible(bridge)
        result = self.delegate(bridge, backend='metaai')
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertIsNone(bridge.db.execute(
            'SELECT 1 FROM route_refusals').fetchone())

    def test_final_payload_unchanged_by_observation(self):
        # Porte socket chatgpt (refus déclencheur, backend résolu de la
        # requête) : journalisée, mais l'enveloppe finale reste identique au
        # contrat — l'observation n'ajoute aucune clé au résultat.
        bridge = self.bridge(FULL_MANIFEST)
        result = self.delegate(bridge, backend='chatgpt', request_key='probe-chatgpt')
        self.assertEqual(result, {'status': 'engine_unavailable',
                                  'reason': '--chatgpt-state non configuré',
                                  'request_key': 'probe-chatgpt'})
        self.assertEqual(bridge.db.execute(
            'SELECT channel, reason FROM route_refusals').fetchone(),
            ('chatgpt', 'engine_unavailable'))

    def test_default_chain_skips_vetoed_backend(self):
        # chain_candidates applique le même filtre vetos() que route_choice :
        # un backend avec un refus observé récent est sauté au profit du
        # suivant ; sans refus, il reste premier éligible.
        import expert_router
        bridge = self.bridge(FULL_MANIFEST)
        self.codex_admissible(bridge)
        routing_events.declare(bridge.db, 'foundation-models', 'free')
        with patch.object(apple_expert, 'configured', return_value=True):
            self.assertEqual(expert_router.chain_candidates(
                bridge, ['foundation-models', 'codex']), 'foundation-models')
            routing_events.record_refusal(bridge.db, 'foundation-models', 'engine_unavailable')
            self.assertEqual(expert_router.chain_candidates(
                bridge, ['foundation-models', 'codex']), 'codex')

    def test_recorded_refusal_expires_after_cooldown(self):
        # Cooldown = durée (1800 s), pas une préférence d'ordre définitive :
        # après expiration, la route redevient éligible dans la chaîne.
        import expert_router
        bridge = self.bridge(FULL_MANIFEST)
        self.codex_admissible(bridge)
        routing_events.record_refusal(bridge.db, 'codex', 'quota_blocked')
        self.assertIsNone(expert_router.chain_candidates(bridge, ['codex']))
        bridge.db.execute(
            'UPDATE route_refusals SET ts=?',
            (time.time() - routing_events.REFUSAL_COOLDOWN_S - 1.0,))
        bridge.db.commit()
        self.assertEqual(expert_router.chain_candidates(bridge, ['codex']), 'codex')

    # — Substitution explicite : ordre depuis external_rankings —

    def test_explicit_failure_substitutes_to_cheapest_ranked_candidate(self):
        bridge = self.bridge(FULL_MANIFEST)
        self.codex_admissible(bridge)
        bridge.claude_program = None
        seed_ranking(bridge.db, 'codex', 'openai_gpt-6.1-sol-pro', 2.0)
        external_rankings.populate_model_map(bridge.db)
        with patch.object(apple_expert, 'configured', return_value=True), \
             patch.object(apple_expert, 'review',
                          return_value={'status': 'engine_unavailable',
                                        'reason': 'fm unavailable for test'}), \
             patch.object(expert_mcp, 'review',
                          return_value={'status': 'finished', 'response': 'ok',
                                        'thread_id': 't1', 'resumable': True}):
            result = self.delegate(bridge, backend='foundation-models')
        self.assertEqual(result['status'], 'finished')
        self.assertIs(result['automatic_fallback'], True)
        self.assertEqual(result['requested_route'], 'foundation-models')
        self.assertEqual(result['served_route'], 'codex')
        self.assertEqual(result['backend'], 'codex')

    def test_ranking_order_prefers_cheapest_corroborated_price(self):
        bridge = self.bridge(FULL_MANIFEST)
        routing_events.declare(bridge.db, 'claude-code', 'included')
        self.codex_admissible(bridge)
        bridge.claude_program = 'claude'
        # Porte de facturation (1581-1582) : le dispatch claude-code servi
        # n'aboutit que si l'opérateur a confirmé l'usage gratuit.
        bridge.claude_subscription_only = True
        seed_ranking(bridge.db, 'codex', 'openai_gpt-6.1-sol-pro', 5.0)
        seed_ranking(bridge.db, 'claude-code', 'claude-opus-5', 1.0)
        external_rankings.populate_model_map(bridge.db)
        with patch.object(apple_expert, 'configured', return_value=True), \
             patch.object(apple_expert, 'review',
                          return_value={'status': 'engine_unavailable',
                                        'reason': 'fm unavailable for test'}), \
             patch.object(expert_mcp, 'claude_review',
                          return_value={'status': 'finished', 'response': 'ok'}):
            result = self.delegate(bridge, backend='foundation-models',
                                   request_key='probe-ranking')
        self.assertEqual(result['served_route'], 'claude-code')
        events = [e for e in routing_events.recent(bridge.db) if e['type'] == 'routing_substituted']
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(json.loads(event['requested_route']), {'backend': 'foundation-models'})
        self.assertEqual(json.loads(event['served_route']), {'backend': 'claude-code'})
        self.assertEqual(event['request_key'], 'probe-ranking')
        self.assertEqual(event['admissibility'], 'granted')
        self.assertEqual(event['channel'], 'claude-code')
        self.assertIn('automatic fallback', event['reason'])

    def test_substitution_to_desktop_finished_without_response_is_admission_only(self):
        bridge = self.bridge(FULL_MANIFEST)
        # claude-desktop élu seul : déclaré included et configuré (socket +
        # canal de retour) ; codex reste non déclaré (unknown) pour ne jamais
        # être élu par route_choice.
        routing_events.declare(bridge.db, 'claude-desktop', 'included')
        bridge.desktop_values = {'socket': 'test'}
        seed_ranking(bridge.db, 'claude-desktop', 'claude-opus-5', 1.0)
        external_rankings.populate_model_map(bridge.db)
        with patch.object(apple_expert, 'configured', return_value=True), \
             patch.object(apple_expert, 'review',
                          return_value={'status': 'engine_unavailable',
                                        'reason': 'fm unavailable for test'}), \
             patch.object(expert_mcp.claude_desktop, 'review',
                          return_value={'status': 'finished', 'response': None}):
            result = self.delegate(bridge, backend='foundation-models')
        # Backend SERVI (posé par le handler) : une admission d'un backend
        # atteint par substitution doit aussi porter admission_only.
        self.assertEqual(result['status'], 'finished')
        self.assertIs(result['admission_only'], True)
        self.assertIs(result['automatic_fallback'], True)
        self.assertEqual(result['backend'], 'claude-desktop')
        self.assertEqual(result['served_route'], 'claude-desktop')
        events = [e for e in routing_events.recent(bridge.db)
                  if e['type'] == 'routing_substituted']
        self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0]['served_route']),
                         {'backend': 'claude-desktop'})

    def test_substitution_drops_foreign_model_instead_of_catalog_error(self):
        bridge = self.bridge(FULL_MANIFEST)
        bridge.claude_program = 'claude'
        # Porte de facturation (subscription_only False) : refus
        # billing_unverified sur la cible explicite claude-code ; codex
        # admissible et seul mesurable doit être servi.
        self.codex_admissible(bridge)
        seed_ranking(bridge.db, 'codex', 'openai_gpt-6.1-sol-pro', 2.0)
        external_rankings.populate_model_map(bridge.db)
        with patch.object(expert_mcp, 'review',
                          return_value={'status': 'finished', 'response': 'ok',
                                        'thread_id': 't1', 'resumable': True}):
            result = self.delegate(bridge, backend='claude-code', model='opus')
        # Le modèle 'opus' est valide pour la cible défaillante claude-code
        # mais étranger au catalogue codex : retiré sans condition, sinon
        # _codex_admit_model lève ValueError sur l'élu codex et la
        # substitution meurt en incertain.
        self.assertEqual(result['status'], 'finished')
        self.assertIs(result['automatic_fallback'], True)
        self.assertEqual(result['requested_route'], 'claude-code')
        self.assertEqual(result['served_route'], 'codex')
        self.assertEqual(result['backend'], 'codex')

    def test_served_dispatch_exception_returns_original_refusal(self):
        bridge = self.bridge(FULL_MANIFEST)
        self.codex_admissible(bridge)
        seed_ranking(bridge.db, 'codex', 'openai_gpt-6.1-sol-pro', 2.0)
        external_rankings.populate_model_map(bridge.db)
        with patch.object(apple_expert, 'configured', return_value=True), \
             patch.object(apple_expert, 'review',
                          return_value={'status': 'engine_unavailable',
                                        'reason': 'fm unavailable for test'}), \
             patch.object(expert_mcp, 'review', side_effect=RuntimeError('boom')):
            result = self.delegate(bridge, backend='foundation-models')
        # Une exception du backend servi : le refus d'origine est retourné
        # inchangé (request_key mis à part, posé par finish) — aucune clé de
        # substitution, aucun événement routing_substituted.
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertEqual(result['reason'], 'fm unavailable for test')
        self.assertEqual(result['request_key'], 'probe-router')
        self.assertNotIn('automatic_fallback', result)
        self.assertNotIn('requested_route', result)
        self.assertNotIn('served_route', result)
        self.assertEqual([e for e in routing_events.recent(bridge.db)
                          if e['type'] == 'routing_substituted'], [])

    def test_paid_or_unknown_billing_never_substituted(self):
        bridge = self.bridge(FULL_MANIFEST)
        bridge.claude_program = 'claude'
        bridge.claude_subscription_only = True
        # claude-code : aucune déclaration → unknown ; ironclaw : LOCAL_BILLING
        # 'free' mais contractuellement NEVER_AUTO. Aucun candidat mesurable.
        with patch.object(apple_expert, 'configured', return_value=False):
            result = self.delegate(bridge, backend='foundation-models')
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertIs(result['automatic_fallback'], False)
        self.assertIsNone(result.get('served_route'))
        self.assertNotIn('requested_route', result)
        self.assertEqual([e for e in routing_events.recent(bridge.db)
                          if e['type'] == 'routing_substituted'], [])

    def test_ironclaw_never_served_despite_local_free_billing(self):
        import expert_router
        bridge = self.bridge(FULL_MANIFEST)
        # ironclaw est marqué 'free' par seed_local (LOCAL_BILLING) mais reste
        # exclus : NEVER_AUTO_BACKENDS + aucune branche de configuration.
        self.assertNotIn('ironclaw', expert_router._configured_backends(bridge))
        candidates = expert_router._exclude(
            bridge, 'codex', expert_router._configured_backends(bridge))
        self.assertNotIn('ironclaw', candidates)
        self.assertIsNone(expert_router.substitute_for_target(bridge, 'ironclaw'))

    # — default_chain du manifeste —

    def test_default_chain_missing_field_is_legacy_empty(self):
        self.write_manifest(FULL_MANIFEST)
        self.assertEqual(load_default_chain(self.manifest), [])
        bridge = self.bridge(FULL_MANIFEST)
        self.assertEqual(bridge.default_chain, [])

    def test_default_chain_resolved_at_time_t(self):
        document = json.loads(json.dumps(FULL_MANIFEST))
        document['default_chain'] = ['codex', 'foundation-models']
        bridge = self.bridge(document)
        self.assertEqual(bridge.default_chain, ['codex', 'foundation-models'])
        # Sémantique retenue (décision b) : la chaîne par défaut est résolue
        # POST-déclencheur, pas d'office — la délégation sans cible expresse
        # tente d'abord codex ; son échec déclenche la substitution vers
        # l'entrée suivante, évaluée à l'instant T.
        self.codex_admissible(bridge)
        with patch.object(expert_mcp, 'review',
                          return_value={'status': 'quota_blocked',
                                        'reason': 'quota exhausted'}), \
             patch.object(apple_expert, 'configured', return_value=True), \
             patch.object(apple_expert, 'review',
                          return_value={'status': 'finished', 'response': 'ok'}):
            result = self.delegate(bridge, request_key='probe-chain')
        self.assertEqual(result['status'], 'finished')
        self.assertIs(result['automatic_fallback'], True)
        self.assertEqual(result['requested_route'], 'codex')
        self.assertEqual(result['served_route'], 'foundation-models')
        events = [e for e in routing_events.recent(bridge.db)
                  if e['type'] == 'routing_substituted']
        self.assertEqual(len(events), 1)

    def test_default_chain_first_entry_not_admissible_falls_to_second(self):
        import expert_router
        document = json.loads(json.dumps(FULL_MANIFEST))
        document['default_chain'] = ['codex', 'foundation-models']
        bridge = self.bridge(document)
        # Test unitaire de chain_candidates : codex non déclaré → unknown →
        # fail-closed (jamais candidat) ; foundation-models 'free' (seed_local)
        # et apple configuré → l'entrée suivante est servie.
        with patch.object(apple_expert, 'configured', return_value=True):
            self.assertEqual(
                expert_router.chain_candidates(
                    bridge, ['codex', 'foundation-models'], exclude=None),
                'foundation-models')

    def test_default_chain_no_candidate_preserves_original_refusal(self):
        # Renommé (ex-test_default_chain_second_entry_configured_is_served) :
        # avec la résolution post-déclencheur, ce cas devient « aucune entrée
        # admissible ET configurée à l'instant T » → refus d'origine inchangé.
        document = json.loads(json.dumps(FULL_MANIFEST))
        document['default_chain'] = ['codex', 'foundation-models']
        bridge = self.bridge(document)
        self.codex_admissible(bridge)
        with patch.object(expert_mcp, 'review',
                          return_value={'status': 'quota_blocked',
                                        'reason': 'quota exhausted'}), \
             patch.object(apple_expert, 'configured', return_value=False):
            result = self.delegate(bridge, request_key='probe-chain3')
        self.assertEqual(result['status'], 'quota_blocked')
        # automatic_fallback=False est posé par le handler codex lui-même ;
        # l'invariant testé est l'ABSENCE d'annotation de substitution.
        self.assertIs(result['automatic_fallback'], False)
        self.assertNotIn('served_route', result)
        self.assertNotIn('requested_route', result)
        self.assertEqual(result['request_key'], 'probe-chain3')
        self.assertEqual([e for e in routing_events.recent(bridge.db)
                          if e['type'] == 'routing_substituted'], [])

    def test_default_chain_validation_errors(self):
        self.write_manifest(FULL_MANIFEST)
        base = json.loads(json.dumps(FULL_MANIFEST))
        cases = [
            ('default_chain must be a nonempty list of backend ids',
             {**base, 'default_chain': []}),
            ('default_chain must be a nonempty list of backend ids',
             {**base, 'default_chain': 'codex'}),
            ('default_chain must not repeat a backend',
             {**base, 'default_chain': ['codex', 'codex']}),
            ('default_chain references unknown backends',
             {**base, 'default_chain': ['nowhere']}),
        ]
        for message, document in cases:
            self.write_manifest(document)
            with self.assertRaises(ValueError, msg=json.dumps(document)) as caught:
                load_default_chain(self.manifest)
            self.assertIn(message, str(caught.exception))

    # — Invariants d'exclusion —

    def test_disabled_backend_never_served_by_substitution(self):
        import expert_router
        document = json.loads(json.dumps(FULL_MANIFEST))
        bridge = self.bridge(document)
        # metaai est désactivé dans FULL_MANIFEST : jamais candidat même si
        # admissible et « configuré » artificiellement.
        routing_events.declare(bridge.db, 'metaai', 'included')
        bridge.metaai_values = {'enabled': True}
        bridge.metaai_tasks = types.SimpleNamespace(
            _conn=types.SimpleNamespace(close=lambda: None))
        candidates = expert_router._exclude(
            bridge, 'foundation-models', expert_router._configured_backends(bridge))
        self.assertNotIn('metaai', candidates)
        self.assertNotIn('ironclaw', candidates)

    def test_implementation_mode_never_substituted(self):
        bridge = self.bridge(FULL_MANIFEST, codex_workspace_write=True)
        self.codex_admissible(bridge)
        bridge.claude_program = 'claude'
        seed_ranking(bridge.db, 'claude-code', 'claude-opus-5', 1.0)
        external_rankings.populate_model_map(bridge.db)
        args = {'question': 'Q', 'kind': 'expertise', 'mode': 'implementation',
                'backend': 'codex', 'request_key': 'probe-impl', 'paths': ['rollout.jsonl']}
        with patch.object(expert_mcp, 'review',
                          return_value={'status': 'quota_blocked',
                                        'reason': 'quota exhausted'}) as review_mock:
            result = bridge.call('reprise_delegate', args)
        review_mock.assert_called_once()
        self.assertEqual(result['status'], 'quota_blocked')
        self.assertIs(result['automatic_fallback'], False)
        self.assertEqual([e for e in routing_events.recent(bridge.db)
                          if e['type'] == 'routing_substituted'], [])

    # — Refus d'origine inchangé (byte-identique) quand aucun candidat —

    def test_chatgpt_socket_gate_payload_unchanged_without_candidate(self):
        bridge = self.bridge(FULL_MANIFEST)
        # La gate socket (expert_mcp 1559-1561) se déclenche sur le socket par
        # défaut (falsy) — chatgpt_quick n'est jamais touché, et cette gate ne
        # porte pas de clé backend ; finish ajoute request_key.
        result = self.delegate(bridge, backend='chatgpt', request_key='probe-chatgpt')
        self.assertEqual(result, {'status': 'engine_unavailable',
                                  'reason': '--chatgpt-state non configuré',
                                  'request_key': 'probe-chatgpt'})
        self.assertNotIn('automatic_fallback', result)
        self.assertNotIn('requested_route', result)
        self.assertNotIn('served_route', result)

    def test_busy_envelope_unchanged_with_router(self):
        document = json.loads(json.dumps(FULL_MANIFEST))
        document['backends']['codex'] = {**CODEX, 'capacity': 1}
        bridge = self.bridge(document)
        self.occupy(bridge, 'codex', '0')
        result = self.delegate(bridge, backend='codex', request_key='probe-busy')
        self.assertEqual(result, {'status': 'busy', 'reason_code': 'bridge_busy',
                                  'automatic_retry': False, 'dispatched': False,
                                  'reason': 'Bridge busy: one active expertise per state directory; no automatic retry.'})

    # — Preflight et modes lecture : jamais de substitution —

    def test_preflight_never_substitutes(self):
        bridge = self.bridge(FULL_MANIFEST)
        self.codex_admissible(bridge)
        with patch.object(expert_mcp, 'apple_expert') as apple:
            apple.configured.return_value = True
            result = bridge._call('reprise_delegate',
                                  {'backend': 'foundation-models', 'question': 'Q',
                                   'kind': 'expertise'}, preflight=True)
        self.assertIsNone(result)
        self.assertEqual([e for e in routing_events.recent(bridge.db)
                          if e['type'] == 'routing_substituted'], [])


if __name__ == '__main__':
    unittest.main()
