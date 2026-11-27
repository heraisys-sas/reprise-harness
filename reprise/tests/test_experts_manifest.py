"""Lot 2 (#258) : manifeste expert déclaratif experts-manifest.json.

Contrat validé : « le manifeste décrit, la disponibilité se mesure ». Manifeste
absent (#276) ou invalide = ValueError au démarrage du bridge (fail-fast).
Capacité par backend (#259), porte manifeste au preflight (backend désactivé,
déclaré sans handler ou inconnu du manifeste → engine_unavailable, jamais de
fallthrough vers codex), reprise_experts_list = déclaration + disponibilité
mesurée, rechargement via reload_experts_manifest.
"""
import json
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import expert_mcp
from expert_mcp import BACKEND_MODELS, Bridge, load_experts_manifest, load_fallback_flag


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


class ExpertsManifestTests(unittest.TestCase):
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

    def bridge(self, document=None):
        if document is not None:
            self.write_manifest(document)
        with self.patched_path():
            bridge = Bridge(self.root, {}, self.state)
        bridge.program = 'codex'
        seed_catalog(bridge)
        self.addCleanup(bridge.close)
        return bridge

    def occupy(self, bridge, backend, slot, key='occupied'):
        now = time.time()
        bridge.db.execute('INSERT INTO expert_active_turns VALUES (?,?,?,?,?,?)',
                          (backend, slot, None, key, now, now))
        bridge.db.commit()

    # — Fichier absent : bundle incomplet (#276, suppression nette du repli {}) —

    def test_missing_manifest_refuses_bridge(self):
        for loader in (load_experts_manifest, load_fallback_flag, expert_mcp.load_default_chain):
            with self.subTest(loader=loader.__name__), self.assertRaisesRegex(ValueError, 'bundle incomplet'):
                loader(self.manifest)
        with self.assertRaisesRegex(ValueError, 'bundle incomplet'):
            self.bridge()

    # — Validation stricte : manifeste invalide = bridge refusé —

    def test_invalid_manifest_raises_value_error(self):
        cases = [
            {'schema_version': 2, 'backends': {'codex': {}}},
            {'schema_version': 1},
            {'schema_version': 1, 'backends': {}},
            {'schema_version': 1, 'backends': {'codex': {'session': 'stdio_thread', 'mystery': 1}}},
            {'schema_version': 1, 'backends': {'codex': {'session': 'stdio_thread', 'capacity': True}}},
            {'schema_version': 1, 'backends': {'codex': {'session': 'stdio_thread', 'capacity': 0}}},
            {'schema_version': 1, 'backends': {'codex': {'session': 'stdio_thread', 'capacity': 1.5}}},
            {'schema_version': 1, 'backends': {'codex': {'session': 'carrier_pigeon'}}},
            {'schema_version': 1, 'backends': {'codex': 'not-a-dict'}},
            {'schema_version': 1, 'backends': {'codex': CODEX}, 'automatic_fallback': 'yes'},
        ]
        for document in cases:
            self.write_manifest(document)
            with self.assertRaises(ValueError, msg=json.dumps(document)):
                load_experts_manifest(self.manifest)
            with self.patched_path():
                with self.assertRaises(ValueError, msg=json.dumps(document)):
                    Bridge(self.root, {}, self.state)

    # — Flag automatic_fallback : dérivation manifeste (sentinel) —

    def test_flag_derived_by_bridge_from_manifest(self):
        on = self.bridge({'schema_version': 1, 'backends': {'codex': CODEX},
                          'automatic_fallback': True})
        self.assertIs(on.automatic_fallback_enabled, True)
        off = self.bridge({'schema_version': 1, 'backends': {'codex': CODEX},
                           'automatic_fallback': False})
        self.assertIs(off.automatic_fallback_enabled, False)

    def test_flag_absent_defaults_false(self):
        # Champ absent du manifeste : False.
        bridge = self.bridge({'schema_version': 1, 'backends': {'codex': CODEX}})
        self.assertIs(bridge.automatic_fallback_enabled, False)
        self.assertIs(load_fallback_flag(self.manifest), False)

    def test_load_fallback_flag_rejects_non_bool(self):
        self.write_manifest({'schema_version': 1, 'backends': {'codex': CODEX},
                             'automatic_fallback': 'yes'})
        with self.assertRaises(ValueError):
            load_fallback_flag(self.manifest)

    # — Capacité par backend (#259) —

    def test_manifest_capacity_honored(self):
        bridge = self.bridge({'schema_version': 1,
                              'backends': {'codex': {**CODEX, 'capacity': 3}}})
        self.assertEqual(bridge.experts['codex']['capacity'], 3)
        for index in range(3):
            acquired = bridge._acquire_slot('reprise_delegate', {'backend': 'codex'})
            self.assertEqual(acquired[1], str(index))
        self.assertIsNone(bridge._acquire_slot('reprise_delegate', {'backend': 'codex'}))

    def test_manifest_singleton_capacity(self):
        bridge = self.bridge({'schema_version': 1,
                              'backends': {'codex': {**CODEX, 'capacity': 1}}})
        self.occupy(bridge, 'codex', '0')
        self.assertIsNone(bridge._acquire_slot('reprise_delegate', {'backend': 'codex'}))

    def test_capacity_defaults_to_one_for_undeclared_backend(self):
        bridge = self.bridge({'schema_version': 1,
                              'backends': {'codex': {**CODEX, 'capacity': 3}}})
        self.occupy(bridge, 'ironclaw', '0')
        self.assertIsNone(bridge._acquire_slot('reprise_delegate', {'backend': 'ironclaw'}))

    # — Porte manifeste au preflight : déclaré ≠ disponible —

    def test_disabled_backend_refused_deterministically(self):
        document = json.loads(json.dumps(FULL_MANIFEST))
        result = self.bridge(document).call(
            'reprise_delegate', {'backend': 'metaai', 'question': 'Q',
                                 'kind': 'expertise', 'request_key': 'probe-disabled'})
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertIn('désactivé', result['reason'])
        self.assertFalse(result['automatic_fallback'])

    def test_backend_unknown_to_manifest_refused(self):
        # metaai absent du manifeste : backend connu de l'enum mais sans
        # déclaration → refus distinct, jamais de fallthrough vers codex.
        # #258 lot 3 : metaai a un handler ; le scénario « absent du manifeste
        # et sans handler » est simulé en le retirant de la table des handlers.
        bridge = self.bridge({'schema_version': 1, 'backends': {'codex': {**CODEX, 'capacity': 2}}})
        patched = tuple(b for b in expert_mcp.EXPERTS_HANDLED_BACKENDS if b != 'metaai')
        with patch.object(expert_mcp, 'EXPERTS_HANDLED_BACKENDS', patched):
            result = bridge.call('reprise_delegate', {'backend': 'metaai', 'question': 'Q',
                                                      'kind': 'expertise', 'request_key': 'probe-unknown'})
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertIn('inconnu du manifeste', result['reason'])

    def test_enabled_metaai_declared_without_handler_refused(self):
        # #258 lot 3 : metaai possède désormais un handler ; le cas réalisable
        # « déclaré mais sans handler de dispatch » est simulé en retirant
        # metaai de la table des handlers — le refus déterministe reste identique.
        bridge = self.bridge({'schema_version': 1, 'backends': {
            'codex': {**CODEX, 'capacity': 2},
            'metaai': {'enabled': True, 'session': 'durable_taskstore'}}})
        with patch.object(expert_mcp, 'EXPERTS_HANDLED_BACKENDS',
                          tuple(b for b in expert_mcp.EXPERTS_HANDLED_BACKENDS if b != 'metaai')):
            result = bridge.call('reprise_delegate', {'backend': 'metaai', 'question': 'Q',
                                                      'kind': 'expertise', 'request_key': 'probe-metaai-on'})
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertIn('sans handler', result['reason'])
        self.assertFalse(result['automatic_fallback'])

    def test_enabled_handled_backend_falls_through_to_program_check(self):
        # codex déclaré enabled mais program absent → refus program check
        # existant (la porte manifeste ne court-circuite pas la disponibilité).
        with self.patched_path():
            self.write_manifest({'schema_version': 1, 'backends': {'codex': {**CODEX, 'capacity': 2}}})
            bridge = Bridge(self.root, {}, self.state)
        self.addCleanup(bridge.close)
        bridge.program = None
        seed_catalog(bridge)
        result = bridge.call('reprise_delegate', {'backend': 'codex', 'question': 'Q',
                                                  'kind': 'expertise', 'request_key': 'probe-noprogram'})
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertEqual(result['reason'], 'Requested executable not configured')

    # — reprise_experts_list : déclaration + disponibilité mesurée —

    def test_experts_list_reflects_manifest_and_measurement(self):
        document = json.loads(json.dumps(FULL_MANIFEST))
        document['backends']['claude-code']['capacity'] = 2
        bridge = self.bridge(document)
        listing = bridge.call('reprise_experts_list', {})
        self.assertEqual(listing['status'], 'finished')
        self.assertFalse(listing['automatic_fallback'])
        self.assertTrue(listing['manifest_loaded'])
        self.assertIn('experts-manifest.json', listing['manifest_path'])
        by_id = {entry['id']: entry for entry in listing['backends']}
        self.assertEqual(set(by_id), set(FULL_MANIFEST['backends']))
        codex = by_id['codex']
        self.assertEqual(codex['capacity'], 2)
        self.assertTrue(codex['resumable'])
        self.assertEqual(codex['session'], 'stdio_thread')
        self.assertTrue(codex['handler'])
        self.assertTrue(codex['measured_available'])
        self.assertEqual(codex['availability'], 'available')
        claude_code = by_id['claude-code']
        self.assertEqual(claude_code['capacity'], 2)
        self.assertTrue(claude_code['handler'])
        self.assertFalse(claude_code['measured_available'])
        self.assertEqual(claude_code['availability'], 'unavailable')
        metaai = by_id['metaai']
        self.assertFalse(metaai['enabled'])
        # #258 lot 3 : metaai a un handler ; sans connexion il reste
        # mesuré indisponible (déclaré ≠ disponible).
        self.assertTrue(metaai['handler'])
        self.assertFalse(metaai['measured_available'])
        self.assertEqual(metaai['availability'], 'unavailable')

    def test_experts_list_rejects_arguments(self):
        bridge = self.bridge({'schema_version': 1, 'backends': {'codex': dict(CODEX)}})
        with self.assertRaises(ValueError):
            bridge.call('reprise_experts_list', {'unexpected': 1})

    # — Rechargement —

    def test_reload_picks_up_modified_manifest(self):
        bridge = self.bridge({'schema_version': 1, 'backends': {'codex': {**CODEX, 'capacity': 2}}})
        self.assertEqual(bridge.experts['codex']['capacity'], 2)
        self.write_manifest({'schema_version': 1, 'backends': {'codex': {**CODEX, 'capacity': 5}}})
        with self.patched_path():
            reloaded = bridge.reload_experts_manifest()
        self.assertEqual(reloaded['codex']['capacity'], 5)
        self.assertEqual(bridge.experts['codex']['capacity'], 5)
        self.write_manifest({'schema_version': 1, 'backends': {'codex': {**CODEX, 'capacity': 0}}})
        with self.patched_path():
            with self.assertRaises(ValueError):
                bridge.reload_experts_manifest()
        # Le manifeste invalide est refusé sans écraser l'état courant.
        self.assertEqual(bridge.experts['codex']['capacity'], 5)

    # — Enveloppe BUSY inchangée (critère de non-régression) —

    def test_busy_envelope_unchanged_with_manifest(self):
        bridge = self.bridge({'schema_version': 1, 'backends': {'codex': {**CODEX, 'capacity': 1}}})
        self.occupy(bridge, 'codex', '0')
        result = bridge.call('reprise_delegate', {'backend': 'codex', 'question': 'Q',
                                                  'kind': 'expertise', 'request_key': 'probe-busy'})
        self.assertEqual(result, {'status': 'busy', 'reason_code': 'bridge_busy',
                                  'automatic_retry': False, 'dispatched': False,
                                  'reason': 'Bridge busy: one active expertise per state directory; no automatic retry.'})


if __name__ == '__main__':
    unittest.main()
