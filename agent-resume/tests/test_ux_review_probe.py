import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('ux_review_probe', Path(__file__).parents[1] / 'ux_review_probe.py')
ux = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ux)


class UxReviewProbeTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.attach = Path(tempfile.mkdtemp()) / 'code.swift'
        self.attach.write_text('struct V: View { var body: some View { Text("x") } }')
        self.probe = ux.Probe(self.state, 'Analyze the UI', str(self.attach))

    def test_get_task_carries_attachment(self):
        result = self.probe.call('get_task', {})
        self.assertEqual(result['status'], 'claimed')
        self.assertIn('attachment', result)
        self.assertIn('struct V', result['attachment'])

    def test_submit_result_accepts_long_analysis(self):
        task = self.probe.call('get_task', {})
        analysis = 'x' * 10000
        result = self.probe.call('submit_result', {
            'job_id': task['job_id'], 'attempt_id': task['attempt_id'], 'analysis': analysis})
        self.assertEqual(result['status'], 'accepted')

    def test_oversized_analysis_rejected(self):
        task = self.probe.call('get_task', {})
        with self.assertRaises(ValueError):
            self.probe.call('submit_result', {
                'job_id': task['job_id'], 'attempt_id': task['attempt_id'], 'analysis': 'x' * (ux.MAX_ANALYSIS + 1)})

    def test_completed_returns_stop(self):
        task = self.probe.call('get_task', {})
        self.probe.call('submit_result', {
            'job_id': task['job_id'], 'attempt_id': task['attempt_id'], 'analysis': 'ok'})
        done = self.probe.call('get_task', {})
        self.assertEqual(done['status'], 'completed')

    def test_wrong_attempt_rejected(self):
        task = self.probe.call('get_task', {})
        with self.assertRaises(ValueError):
            self.probe.call('submit_result', {
                'job_id': task['job_id'], 'attempt_id': 'bad', 'analysis': 'ok'})


if __name__ == '__main__':
    unittest.main()
