"""Registre unique des backends (pré-lot A0) : invariants, gardes, dérivations."""
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import backend_registry as registry
import coordinator_jobs as jobs
import routing_events


class RegistryInvariants(unittest.TestCase):
    def test_every_backend_declares_the_five_fields(self):
        for name, item in registry.BACKENDS.items():
            self.assertEqual(set(item), {'billing', 'modes', 'workspace_write',
                                         'consent', 'memory_snapshot'}, name)
            self.assertIn(item['billing'], registry.BILLING_CLASSES, name)
            self.assertIsInstance(item['modes'], tuple, name)
            self.assertTrue(all(mode in ('expertise', 'implementation') for mode in item['modes']), name)
            for flag in ('workspace_write', 'consent', 'memory_snapshot'):
                self.assertIsInstance(item[flag], bool, name)

    def test_codex_is_never_free_by_default(self):
        # D2 : « codex » pas free par défaut ; seule une observation quota
        # (routing_events.populate_from_quota) peut le porter à « included ».
        self.assertEqual(registry.billing('codex'), 'unknown')
        self.assertNotIn('codex', registry.LOCAL_BILLING)

    def test_unknown_backend_bills_unknown_fail_closed(self):
        self.assertFalse(registry.known('openai'))
        self.assertEqual(registry.billing('openai'), 'unknown')
        self.assertEqual(registry.billing('Foundation-Models'), 'unknown')  # pas de repli par casse
        self.assertIsNone(registry.entry(None))
        self.assertEqual(registry.billing(None), 'unknown')

    def test_local_billing_matches_legacy_content(self):
        # Contenu identique à l'ancien dictionnaire codé en dur : dérivation
        # sans changement de comportement.
        self.assertEqual(registry.LOCAL_BILLING,
                         {'foundation-models': 'free', 'ironclaw': 'free',
                          'chatgpt': 'free', 'asi1': 'free', 'asi1-mini': 'free'})
        self.assertEqual({name for name, item in registry.BACKENDS.items()
                          if item['billing'] == 'free'}, set(registry.LOCAL_BILLING))

    def test_jalon_one_is_expertise_only(self):
        # Décision Q2 : expertise seule au jalon 1 du pilote Apple.
        self.assertEqual(registry.entry('foundation-models')['modes'], ('expertise',))

    def test_workspace_write_denied_by_default_everywhere(self):
        # Défaut fermé : le grant #104 sera posé au lot A, jamais déduit.
        for name, item in registry.BACKENDS.items():
            self.assertFalse(item['workspace_write'], name)


class ConsentGuard(unittest.TestCase):
    def test_consent_backends_are_declared_only(self):
        self.assertEqual(registry.consent_backends(), ('foundation-models', 'codex'))

    def test_require_consent_accepts_declared_backends(self):
        for backend in registry.consent_backends():
            self.assertEqual(registry.require_consent_backend(backend), registry.entry(backend))

    def test_require_consent_refuses_unknown_and_undeclared(self):
        for backend in ('openai', '', None, 'chatgpt', 'asi1'):
            with self.assertRaises(ValueError) as caught:
                registry.require_consent_backend(backend)
            self.assertEqual(str(caught.exception), 'Explicit supported consent backend required')

    def test_consent_guard_wired_from_registry(self):
        # La garde de Jobs.consent() provient du registre : un canal connu mais
        # hors consentement (asi1) est refusé avec le même message que la garde
        # historique, avant toute validation d'identité (première instruction
        # de la méthode — aucun accès au magasin, self jamais touché).
        for backend in ('asi1', 'openai', '', None, 'chatgpt'):
            with self.assertRaises(ValueError) as caught:
                jobs.Jobs.consent(object(), 'not-even-a-uuid', 'not-even-a-uuid',
                                  authorization_id='x', expected_revision=0,
                                  expected_proposal_sha256='x', host_context=None,
                                  expected_context_sha256='x', backend=backend)
            self.assertEqual(str(caught.exception), 'Explicit supported consent backend required')

    def test_memory_snapshot_only_for_local_backend(self):
        # Le snapshot mémoire reste lié au seul backend local (fondation).
        self.assertTrue(registry.entry('foundation-models')['memory_snapshot'])
        self.assertFalse(registry.entry('codex')['memory_snapshot'])


class CapabilityEntry(unittest.TestCase):
    def test_entry_shape_matches_snapshot_contract(self):
        for backend in ('foundation-models', 'codex'):
            snapshot = registry.capability_entry(backend)
            self.assertEqual(set(snapshot), {'id', 'configured', 'modes', 'workspace_write_authorized'})
            self.assertEqual(snapshot['id'], backend)
            self.assertIs(snapshot['configured'], True)
            self.assertIsInstance(snapshot['modes'], list)
            self.assertIs(snapshot['workspace_write_authorized'], False)

    def test_codex_declares_implementation_but_write_stays_closed(self):
        snapshot = registry.capability_entry('codex')
        self.assertIn('implementation', snapshot['modes'])
        self.assertIs(snapshot['workspace_write_authorized'], False)

    def test_unknown_backend_refused(self):
        with self.assertRaises(ValueError):
            registry.capability_entry('openai')

    def test_write_authorization_requires_implementation_capable_backend(self):
        with self.assertRaises(ValueError):
            registry.capability_entry('foundation-models', workspace_write_authorized=True)
        granted = registry.capability_entry('codex', workspace_write_authorized=True)
        self.assertIs(granted['workspace_write_authorized'], True)


class RoutingDerivation(unittest.TestCase):
    def test_routing_events_derives_local_billing_from_registry(self):
        self.assertEqual(routing_events.LOCAL_BILLING, registry.LOCAL_BILLING)

    def test_seed_local_declares_free_channels_only(self):
        db = sqlite3.connect(':memory:')
        routing_events.setup(db)
        routing_events.seed_local(db)
        rows = dict(db.execute('SELECT channel, billing_class FROM route_admissibility'))
        self.assertEqual(rows, {name: 'free' for name in registry.LOCAL_BILLING})
        self.assertNotIn('codex', rows)
        db.close()


if __name__ == '__main__':
    unittest.main()
