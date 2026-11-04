import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import apple_capability_discovery as discovery


class AppleCapabilityDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'project'
        self.project.mkdir()

    def test_exact_manifest_project_match_is_applicable_without_claiming_client_loaded(self):
        calls = []

        def inspect(path):
            calls.append(path)
            return {
                'project': path,
                'connector_name': 'reprise-harness',
                'configuration_written': True,
                'managed': True,
                'client_loaded': 'unknown',
                'home': '/private/secret',
            }

        result = discovery.discover(self.project, inspector=inspect)
        connector = result['connectors'][0]
        self.assertEqual(calls, [str(self.project.resolve())])
        self.assertEqual(result['project_status'], 'existing_directory')
        self.assertEqual(result['authorization_status'], 'host_verification_required')
        self.assertEqual(result['project_identity_status'], 'host_verification_required')
        self.assertTrue(connector['applicable'])
        self.assertEqual(connector['id'], 'reprise-harness')
        self.assertEqual(connector['observed_name'], 'reprise-harness')
        self.assertEqual(connector['applicability_basis'], 'manifest_project_scope_match_only')
        self.assertEqual(connector['operational_status'], 'unknown')
        self.assertEqual(connector['configuration_status'], 'configured')
        self.assertIs(connector['managed'], True)
        self.assertEqual(connector['client_loaded'], 'unknown')
        self.assertEqual(connector['capabilities'], [])
        self.assertNotIn(str(self.project), repr(result))
        self.assertNotIn('/private/secret', repr(result))

    def test_project_mismatch_is_never_reported_as_applicable(self):
        result = discovery.discover(
            self.project,
            inspector=lambda path: {
                'project': path + '-other',
                'configuration_written': True,
                'managed': True,
            },
        )
        connector = result['connectors'][0]
        self.assertFalse(connector['applicable'])
        self.assertEqual(connector['applicability_basis'], 'manifest_project_scope_match_only')
        self.assertEqual(connector['operational_status'], 'unknown')
        self.assertEqual(connector['status'], 'project_mismatch')
        self.assertEqual(connector['configuration_status'], 'configured')
        self.assertIs(connector['managed'], True)
        self.assertEqual(connector['client_loaded'], 'unknown')

    def test_missing_scope_is_unknown_not_a_project_mismatch(self):
        for scope in (None, ''):
            with self.subTest(scope=scope):
                result = discovery.discover(self.project, inspector=lambda _path: {
                    'project': scope, 'configuration_written': False, 'managed': False,
                })
                connector = result['connectors'][0]
                self.assertEqual(connector['status'], 'project_scope_unknown')
                self.assertFalse(connector['applicable'])
                self.assertEqual(connector['observed_name'], 'unknown')

    def test_historical_name_is_distinct_from_logical_id(self):
        result = discovery.discover(self.project, inspector=lambda path: {
            'project': path, 'connector_name': 'reprise',
            'configuration_written': True, 'managed': True,
        })
        connector = result['connectors'][0]
        self.assertEqual(connector['id'], 'reprise-harness')
        self.assertEqual(connector['observed_name'], 'reprise')
        self.assertTrue(connector['applicable'])
        self.assertTrue(connector['managed'])

    def test_unrecognized_connector_name_is_not_disclosed(self):
        result = discovery.discover(self.project, inspector=lambda path: {
            'project': path, 'connector_name': 'private-token-name',
        })
        self.assertEqual(result['connectors'][0]['observed_name'], 'unknown')
        self.assertNotIn('private-token-name', repr(result))

    def test_inspection_error_is_neutral_and_does_not_leak_exception(self):
        def fail(_path):
            raise RuntimeError('/private/secret/token=abc')

        result = discovery.discover(self.project, inspector=fail)
        connector = result['connectors'][0]
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(connector['applicable'])
        self.assertEqual(connector['configuration_status'], 'unknown')
        self.assertEqual(connector['managed'], 'unknown')
        self.assertEqual(connector['client_loaded'], 'unknown')
        self.assertNotIn('/private/secret', repr(result))
        self.assertNotIn('abc', repr(result))

    def test_only_static_skill_provenance_is_returned_without_inference(self):
        calls = []
        result = discovery.discover(
            self.project,
            inspector=lambda path: calls.append(path) or {
                'project': path, 'configuration_written': False, 'managed': False,
            },
        )
        self.assertEqual(calls, [str(self.project.resolve())])
        self.assertEqual(result['inference'], 'not_requested')
        self.assertEqual(result['side_effects'], 'none')
        self.assertEqual(
            [(skill['id'], skill['source']) for skill in result['skills']],
            [('checkpoint-review', 'apple_builtin'), ('project-summary', 'apple_builtin')],
        )

    def test_invalid_project_does_not_call_inspector(self):
        calls = []
        result = discovery.discover(self.project / 'missing', inspector=lambda path: calls.append(path))
        self.assertEqual(calls, [])
        self.assertEqual(result['status'], 'invalid_project')
        self.assertEqual(result['authorization_status'], 'host_verification_required')
        self.assertEqual(result['connectors'][0]['client_loaded'], 'unknown')


if __name__ == '__main__':
    unittest.main()
