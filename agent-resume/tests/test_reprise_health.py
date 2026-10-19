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
            self.assertIn(state, ('running', 'stopped', 'unknown'))

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

    def test_summarize_includes_explanation_key(self):
        with patch.object(rh, 'explain', return_value={'available': False, 'answer': None}):
            result = rh.summarize('.')
        self.assertIn('explanation', result)
        self.assertIn('actions_for_human', result)
        self.assertIn('versions', result)


if __name__ == '__main__':
    unittest.main()
