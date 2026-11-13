"""Noninteractive vault availability is evidence of key access, never cached-data proof."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import vault_status


class VaultStatusTests(unittest.TestCase):
    def test_existing_key_verified_without_authorize_or_seal(self):
        with patch.object(vault_status.vault, 'check', return_value=None) as check, \
             patch.object(vault_status.vault, 'authorize') as authorize, \
             patch.object(vault_status.vault, 'crypt') as crypt:
            self.assertEqual(vault_status.inspect(),
                             {'status': 'verified', 'scope': 'existing_key_access_only'})
        check.assert_called_once_with('search-index-v1')
        authorize.assert_not_called()
        crypt.assert_not_called()

    def test_missing_key_is_not_created_and_status_is_not_success(self):
        with patch.object(vault_status.vault, 'check',
                          side_effect=ValueError('Coffre Reprise indisponible (key_missing)')) as check:
            self.assertEqual(vault_status.inspect(),
                             {'status': 'unavailable', 'reason': 'key_missing'})
        check.assert_called_once_with('search-index-v1')

    def test_keychain_unavailable_is_not_misclassified_as_encryption_verified(self):
        with patch.object(vault_status.vault, 'check',
                          side_effect=ValueError('Coffre Reprise indisponible (keychain_unavailable)')) as check:
            self.assertEqual(vault_status.inspect(),
                             {'status': 'unavailable', 'reason': 'keychain_unavailable'})
        check.assert_called_once()

    def test_unexpected_errors_are_redacted(self):
        for error in (ValueError('SECRET internal error'), OSError('SECRET private filesystem')):
            with self.subTest(error=type(error).__name__), \
                 patch.object(vault_status.vault, 'check', side_effect=error):
                result = vault_status.inspect()
                self.assertEqual(result, {'status': 'error', 'reason': 'check_failed'})
                self.assertNotIn('SECRET', str(result))

    def test_cli_uses_noninteractive_probe_and_distribution_includes_it(self):
        cli = (ROOT / 'reprise').read_text()
        distribution = (ROOT / 'package_helpers.py').read_text()
        self.assertIn("sys.argv[1] == 'vault-check'", cli)
        self.assertIn('vault_status.inspect()', cli)
        self.assertIn("'vault_status.py'", distribution)
        self.assertIn("return crypt(b'', purpose, 'check')", (ROOT / 'vault.py').read_text())
        native = (ROOT / 'swift' / 'Vault.swift').read_text()
        self.assertIn('key(create: false, interactive: false)', native)

    def test_privacy_ui_reports_only_verified_key_and_unverified_cache(self):
        dashboard = (ROOT / 'swift' / 'Dashboard.swift').read_text()
        native = (ROOT / 'swift' / 'Native.swift').read_text()
        self.assertNotIn('status: "Chiffré", ok: true', dashboard)
        self.assertNotIn('status: app.searching ? "En cours" : "Prêt", ok: true', dashboard)
        self.assertIn('status: keyLabel, ok: app.vaultAccessVerified', dashboard)
        self.assertIn('status: app.searching ? "Opération en cours" : "État non vérifié", ok: false', dashboard)
        self.assertIn('app.checkVaultAvailability()', dashboard)
        self.assertIn('perform(["vault-check"])', native)
        self.assertIn('vaultAvailabilityCheckedAt.map { Date().timeIntervalSince($0) <= 120 }', native)
