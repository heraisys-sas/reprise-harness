import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import reprise_health as rh


class SignaturesTests(unittest.TestCase):
    def test_log_signatures_present_on_real_logs(self):
        sigs = rh.log_signatures()
        self.assertIn('quick_entry_submitted', sigs)
        self.assertIn('mcp_interaction', sigs)
        # On this machine the markers exist; assert shape not fabricated data.
        for name, s in sigs.items():
            self.assertIn('present', s)
            self.assertIn('marker', s)

    def test_missing_signature_flagged_stale(self):
        with patch.object(rh, 'log_signatures', return_value={'x': {'present': False, 'marker': 'gone'}}):
            self.assertEqual(rh.check('.')['stale_log_signatures'], ['x'])


class LivenessTests(unittest.TestCase):
    def test_liveness_reports_states_from_socket(self):
        states = rh.process_liveness()
        self.assertIn('quick-entry-server', states)
        self.assertIn('codex-server', states)
        for state in states.values():
            self.assertIn(state, ('running', 'stopped', 'indeterminate'))

    def test_stopped_server_yields_human_relaunch_action(self):
        with patch.object(rh, 'process_liveness', return_value={'quick-entry-server': 'stopped', 'codex-server': 'running'}):
            result = rh.check('.')
        self.assertEqual(result['needs_relaunch'], ['quick-entry-server'])
        self.assertTrue(any('Relancer' in a for a in result['actions_for_human']))


class ExplainTests(unittest.TestCase):
    def test_missing_helper_degrades_gracefully(self):
        result = rh.explain('faits', binary='/nonexistent/FoundationExplain')
        self.assertFalse(result['available'])
        self.assertIsNone(result['answer'])

    def test_helper_result_passthrough(self):
        with patch('subprocess.run') as run:
            run.return_value.stdout = json.dumps({'available': True, 'answer': 'explication'})
            run.return_value.returncode = 0
            result = rh.explain('faits', binary='/usr/bin/true')
        self.assertTrue(result['available'])
        self.assertEqual(result['answer'], 'explication')

    def test_failed_or_malformed_helper_preserves_deterministic_fallback(self):
        for code, payload in ((1, ''), (0, '[]'), (0, '{}'), (0, '{"available":"yes"}')):
            with self.subTest(code=code, payload=payload), patch('subprocess.run') as run:
                run.return_value.stdout = payload
                run.return_value.returncode = code
                result = rh.explain('faits', binary='/usr/bin/true')
                self.assertFalse(result['available'])
                self.assertIsNone(result['answer'])
                self.assertIn('reason_code', result)

    def test_model_unavailability_reason_is_preserved(self):
        with patch('subprocess.run') as run:
            run.return_value.stdout = json.dumps({'available': False, 'answer': None,
                'reason_code': 'model_not_ready', 'reason': 'Modèle en préparation.'})
            run.return_value.returncode = 0
            self.assertEqual(rh.explain('faits', binary='/usr/bin/true')['reason_code'], 'model_not_ready')

    def test_summarize_is_deterministic_with_features(self):
        result = rh.summarize('.')
        self.assertNotIn('explanation', result)  # FM lives in local analysis now
        self.assertIn('quick_entry_features', result)
        self.assertIn('actions_for_human', result)
        self.assertIn('versions', result)

    def test_quick_entry_features_reports_presence(self):
        feats = rh.quick_entry_features()
        self.assertIn('checks', feats)
        self.assertIn('missing', feats)
        self.assertIn('ready', feats)
        self.assertIn('preferences', feats['checks'])

    def test_local_synthesis_degrades_without_binary(self):
        synth = rh.local_synthesis(binary='/nonexistent/FoundationExplain')
        self.assertFalse(synth['available'])


if __name__ == '__main__':
    unittest.main()


class WatchTests(unittest.TestCase):
    def setUp(self):
        import sqlite3, scheduler, tempfile
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        scheduler.setup(self.db)

    def test_first_run_detects_update_then_stable(self):
        with patch.object(rh, 'read_versions', return_value={'claude-desktop': '1.0'}):
            first = rh.watch(self.db, '.')
        self.assertTrue(first['update_detected'])
        with patch.object(rh, 'read_versions', return_value={'claude-desktop': '1.0'}):
            second = rh.watch(self.db, '.')
        self.assertFalse(second['update_detected'])

    def test_version_change_triggers_update(self):
        with patch.object(rh, 'read_versions', return_value={'claude-desktop': '1.0'}):
            rh.watch(self.db, '.')
        with patch.object(rh, 'read_versions', return_value={'claude-desktop': '2.0'}):
            changed = rh.watch(self.db, '.')
        self.assertTrue(changed['update_detected'])
        self.assertEqual(changed['previous_versions'], {'claude-desktop': '1.0'})

    def test_report_includes_actions_and_explanation_keys(self):
        report = rh.watch(self.db, '.')
        self.assertIn('actions_for_human', report)
        self.assertIn('needs_relaunch', report)


class ReviewFixesTests(unittest.TestCase):
    def test_r4_cache_tied_to_digest_and_marks_historical(self):
        rh._EXPLANATION_CACHE.clear()
        with patch.object(rh, 'explain', return_value={'available': True, 'answer': 'A'}):
            first = rh.cached_explanation('faits A')
        self.assertFalse(first['historical'])
        with patch.object(rh, 'explain', return_value={'available': True, 'answer': 'B'}):
            second = rh.cached_explanation('faits B')
        self.assertFalse(second['historical'])
        # The earlier digest is now historical.
        self.assertTrue(rh._EXPLANATION_CACHE[(rh._digest('faits A'), '')].get('historical'))

    def test_r4_same_digest_served_from_cache(self):
        rh._EXPLANATION_CACHE.clear()
        with patch.object(rh, 'explain', return_value={'available': True, 'answer': 'A'}) as ex:
            rh.cached_explanation('faits X')
            again = rh.cached_explanation('faits X')
        self.assertTrue(again['cached'])
        self.assertEqual(ex.call_count, 1)


class CodexReviewRound2Tests(unittest.TestCase):
    def test_lone_surrogate_rejected(self):
        with self.assertRaises(ValueError):
            rh._digest('\ud800')

    def test_question_part_of_cache_key(self):
        rh._EXPLANATION_CACHE.clear()
        with patch.object(rh, 'explain', side_effect=[{'available': True, 'answer': 'Q1'}, {'available': True, 'answer': 'Q2'}]):
            a = rh.cached_explanation('faits', 'Q1')
            b = rh.cached_explanation('faits', 'Q2')
        self.assertNotEqual(a['answer'], b['answer'])
        self.assertFalse(b['cached'])

    def test_historical_flag_refreshed_on_hit(self):
        rh._EXPLANATION_CACHE.clear()
        with patch.object(rh, 'explain', side_effect=[{'available': True, 'answer': 'A'}, {'available': True, 'answer': 'B'}, {'available': True, 'answer': 'A2'}]):
            rh.cached_explanation('fa', 'Q'); rh.cached_explanation('fb', 'Q'); rh.cached_explanation('fa', 'Q')
        self.assertFalse(rh._EXPLANATION_CACHE[(rh._digest('fa'), 'Q')]['historical'])
        self.assertTrue(rh._EXPLANATION_CACHE[(rh._digest('fb'), 'Q')]['historical'])


class QuickEntryAnomalyTests(unittest.TestCase):
    def test_clean_when_no_update_and_signatures_present(self):
        with patch.object(rh, 'log_signatures', return_value={
                'quick_entry_submitted': {'present': True}, 'quick_entry_routing': {'present': True},
                'quick_entry_guard': {'present': True}}):
            rep = rh.quick_entry_anomalies(previous=None)
        self.assertTrue(rep['clean'])
        self.assertEqual(rep['anomalies'], [])

    def test_update_breakage_when_component_moved_and_signature_gone(self):
        prev = {'swift_addon': {'size': 1, 'mtime': 1}}
        with patch.object(rh, '_component_meta', return_value={'size': 2, 'mtime': 2}), \
             patch.object(rh, 'log_signatures', return_value={
                 'quick_entry_submitted': {'present': False}, 'quick_entry_routing': {'present': True},
                 'quick_entry_guard': {'present': True}}):
            rep = rh.quick_entry_anomalies(previous=prev)
        self.assertFalse(rep['clean'])
        self.assertEqual(rep['anomalies'][0]['kind'], 'update_breakage')

    def test_signature_missing_without_update_is_reported(self):
        with patch.object(rh, 'log_signatures', return_value={
                'quick_entry_submitted': {'present': False}, 'quick_entry_routing': {'present': True},
                'quick_entry_guard': {'present': True}}):
            rep = rh.quick_entry_anomalies(previous=None)
        kinds = {a['kind'] for a in rep['anomalies']}
        self.assertIn('signature_missing', kinds)

    def test_missing_component_reported(self):
        with patch.object(rh, '_component_meta', return_value=None), \
             patch.object(rh, 'log_signatures', return_value={}):
            rep = rh.quick_entry_anomalies(previous=None)
        self.assertTrue(any(a['kind'] == 'missing' for a in rep['anomalies']))


class CodexHealthTests(unittest.TestCase):
    """Verifies the Codex-side health check (patch from Codex, reviewed)."""

    def _features(self, socket_present=True):
        return {'checks': {
            'bundles': {'apps': {'ChatGPT.app': {'meta': {'size': 1, 'mtime': 1}}, 'Codex.app': {'meta': None}}},
            'codex_cli': {'present': True, 'meta': {'size': 1, 'mtime': 1}},
            'control_socket': {'present': socket_present, 'meta': None},
            'shared_mode': {'present': True, 'desktop_version': 'v1'},
        }}

    def test_present_socket_without_file_metadata_is_not_missing(self):
        # A socket has no regular-file metadata; it must not be 'missing' when present.
        report = rh.codex_anomalies(features=self._features(socket_present=True))
        self.assertEqual(report['anomalies'], [])
        self.assertTrue(report['clean'])

    def test_absent_socket_is_missing(self):
        report = rh.codex_anomalies(features=self._features(socket_present=False))
        kinds = {a['kind'] for a in report['anomalies']}
        self.assertIn('missing', kinds)
        self.assertTrue(any(a['component'] == 'control_socket' for a in report['anomalies']))

    def test_update_breakage_when_version_changed_and_socket_gone(self):
        features = self._features(socket_present=False)
        features['checks']['shared_mode']['desktop_version'] = 'v2'
        prev = {'chatgpt_app': {'size': 0, 'mtime': 0}, 'desktop_version': 'v1'}
        report = rh.codex_anomalies(previous=prev, features=features)
        kinds = {a['kind'] for a in report['anomalies']}
        self.assertIn('update_breakage', kinds)

    def test_codex_anomalies_accepts_injected_features(self):
        # No re-reading: injected features are used as-is.
        report = rh.codex_anomalies(features=self._features())
        self.assertIn('components', report)
        self.assertIn('clean', report)
