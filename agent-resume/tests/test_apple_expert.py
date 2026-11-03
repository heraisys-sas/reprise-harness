import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import apple_expert
from codex_expert_mcp import Bridge


class AppleExpertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bridge = Bridge(self.root, {}, self.root / 'state')
        self.addCleanup(self.bridge.db.close)
        self.args = {'request_key': 'apple-1', 'kind': 'review', 'question': 'Analyse',
                     'backend': 'foundation-models', 'model': 'system'}

    def test_route_and_durable_replay(self):
        answer = {'status': 'finished', 'backend': 'foundation-models', 'response': 'réponse',
                  'resumable': False, 'automatic_fallback': False}
        with patch('apple_expert.configured', return_value=True), patch('apple_expert.review', return_value=answer) as review:
            first = self.bridge.call('reprise_delegate', self.args)
            restarted = Bridge(self.root, {}, self.root / 'state')
            self.addCleanup(restarted.db.close)
            second = restarted.call('reprise_delegate', self.args)
            self.assertEqual(first, second)
            self.assertEqual(restarted.call('reprise_requests', {'request_key': 'apple-1'})['result'], first)
            self.assertEqual(review.call_count, 1)
            event = restarted.db.execute('SELECT channel,served_route FROM routing_events').fetchone()
            self.assertEqual(event[0], 'foundation-models')
            self.assertEqual(json.loads(event[1]), {'model': 'system'})

    def test_replay_and_key_conflict_when_fm_disappears(self):
        answer = {'status': 'finished', 'response': 'answer'}
        with patch('apple_expert.configured', return_value=True), patch('apple_expert.review', return_value=answer) as infer:
            first = self.bridge.call('reprise_delegate', self.args)
        with patch('apple_expert.configured', return_value=False), patch('apple_expert.review') as infer:
            self.assertEqual(self.bridge.call('reprise_delegate', self.args), first)
            with self.assertRaises(ValueError):
                self.bridge.call('reprise_delegate', self.args | {'question': 'Different'})
            infer.assert_not_called()

    def test_followup_and_implementation_refused(self):
        with self.assertRaises(ValueError):
            self.bridge.call('reprise_followup', {'backend': 'foundation-models', 'thread_id': 't',
                                                   'request_key': 'follow', 'question': 'continue'})
        with self.assertRaises(ValueError):
            self.bridge.call('reprise_delegate', self.args | {'mode': 'implementation'})
        with self.assertRaises(ValueError):
            self.bridge.call('reprise_delegate', self.args | {'level': 'deep'})
        self.assertEqual(self.bridge.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)

    def test_presence_does_not_claim_model_usable(self):
        with patch('apple_expert.configured', return_value=True), patch('apple_expert.review') as infer:
            route = next(x for x in self.bridge.call('reprise_backends', {})['backends'] if x['id'] == 'foundation-models')
            self.assertEqual(route['availability'], 'unknown')
            self.assertFalse(route['inference_verified'])
            self.assertEqual(route['levels'], ['standard'])
            infer.assert_not_called()

    def test_subprocess_uses_stdin_and_bounded_context(self):
        (self.root / 'evidence.txt').write_text('fact')
        with patch('apple_expert.configured', return_value=True), patch('apple_expert.run', return_value=(0, ' answer\n', '')) as run:
            result = apple_expert.review(self.root, 'Question $(bad)', paths=['evidence.txt'])
        self.assertEqual(result['response'], 'answer')
        command, root, env, prompt, timeout = run.call_args.args
        self.assertEqual(command, ['/usr/bin/fm', 'respond', '--model', 'system', '--no-stream'])
        self.assertIn('Question $(bad)', prompt)
        self.assertIn('fact', prompt)
        self.assertNotIn('Question $(bad)', command)
        self.assertEqual(timeout, apple_expert.TIMEOUT_SECONDS)
        os.symlink('/etc/hosts', self.root / 'link')
        self.assertEqual(apple_expert.review(self.root, 'Question', paths=['link'])['status'], 'apple_input_invalid')
        self.assertEqual(apple_expert.review(self.root, 'Question', paths=['../outside'])['status'], 'apple_input_invalid')

    def test_timeout_with_real_child_is_reaped(self):
        executable = self.root / 'fake-fm'
        executable.write_text('#!/bin/sh\nexec /bin/sleep 10\n')
        executable.chmod(0o700)
        with patch('apple_expert.PROGRAM', str(executable)), patch('apple_expert.TIMEOUT_SECONDS', 0.05):
            result = apple_expert.review(self.root, 'Question')
        self.assertEqual(result['status'], 'apple_process_failed')
        self.assertTrue(result['process_reaped'])
        self.assertEqual(result['inference_outcome'], 'unknown')

    def test_errors_never_report_success(self):
        cases = [(1, 'answer', ''), (0, ' \n', '')]
        with patch('apple_expert.configured', return_value=True):
            for value in cases:
                with self.subTest(value=value), patch('apple_expert.run', return_value=value):
                    self.assertEqual(apple_expert.review(self.root, 'Question')['status'], 'apple_process_failed')
            with patch('apple_expert.run', side_effect=TimeoutError):
                result = apple_expert.review(self.root, 'Question')
                self.assertEqual(result['status'], 'apple_process_failed')
                self.assertTrue(result['process_reaped'])
                self.assertEqual(result['inference_outcome'], 'unknown')
            with patch('apple_expert.run', side_effect=OSError):
                self.assertEqual(apple_expert.review(self.root, 'Question')['status'], 'needs_review')

    def test_reaped_failure_replay_and_explicit_retry_after_restart(self):
        with patch('apple_expert.configured', return_value=True), patch('apple_expert.run', return_value=(1, '', 'secret prompt')) as run:
            first = self.bridge.call('reprise_delegate', self.args)
            self.assertEqual(first['status'], 'apple_process_failed')
            self.assertEqual(first['inference_outcome'], 'unknown')
            self.assertNotIn('secret prompt', str(first))
            restarted = Bridge(self.root, {}, self.root / 'state')
            self.addCleanup(restarted.db.close)
            self.assertEqual(restarted.call('reprise_delegate', self.args), first)
            self.assertEqual(restarted.call('reprise_requests', {'request_key': 'apple-1'})['state'], 'recorded')
            self.assertEqual(run.call_count, 1)
            restarted.call('reprise_delegate', self.args | {'request_key': 'explicit-second'})
            self.assertEqual(run.call_count, 2)

    def test_invalid_snapshots_are_recorded_without_start_and_retry_is_explicit(self):
        data = self.root / 'source'
        cases = [('invalid-utf8', lambda: data.write_bytes(b'\xff')),
                 ('oversized', lambda: data.write_bytes(b'x' * 96001)),
                 ('symlink', lambda: (data.write_text('content'), (self.root / 'link').symlink_to(data)))]
        for label, setup in cases:
            data.unlink(missing_ok=True)
            (self.root / 'link').unlink(missing_ok=True)
            setup()
            paths = ['link'] if label == 'symlink' else ['source']
            args = self.args | {'request_key': label, 'paths': paths}
            with self.subTest(label=label), patch('apple_expert.configured', return_value=True), patch('apple_expert.run') as run:
                result = self.bridge.call('reprise_delegate', args)
                self.assertEqual(result['status'], 'apple_input_invalid')
                self.assertIs(result['process_started'], False)
                self.assertEqual(self.bridge.call('reprise_requests', {'request_key': label})['state'], 'recorded')
                self.assertEqual(self.bridge.call('reprise_delegate', args), result)
                with self.assertRaises(ValueError):
                    self.bridge.call('reprise_delegate', args | {'question': 'different'})
                retry = self.bridge.call('reprise_delegate', args | {'request_key': label + '-retry'})
                self.assertEqual(retry['status'], 'apple_input_invalid')
                run.assert_not_called()

    def test_prelaunch_failure_remains_recorded_if_input_changes(self):
        source = self.root / 'source'
        source.write_text('initial')
        def reject(*args, **kwargs):
            source.write_text('changed')
            return {'status': 'apple_input_invalid', 'backend': 'foundation-models', 'process_started': False}
        with patch('apple_expert.configured', return_value=True), patch('apple_expert.review', side_effect=reject):
            result = self.bridge.call('reprise_delegate', self.args | {'paths': ['source']})
        self.assertEqual(result['status'], 'apple_input_invalid')
        self.assertEqual(self.bridge.call('reprise_requests', {'request_key': 'apple-1'})['state'], 'recorded')

    def test_input_change_preserves_proven_failure_and_allows_new_request(self):
        source = self.root / 'source'
        source.write_text('before')
        def fail(*args, **kwargs):
            source.write_text('after')
            return {'status': 'apple_process_failed', 'backend': 'foundation-models',
                    'process_reaped': True, 'inference_outcome': 'unknown'}
        args = self.args | {'paths': ['source']}
        with patch('apple_expert.configured', return_value=True), patch('apple_expert.review', side_effect=fail) as run:
            result = self.bridge.call('reprise_delegate', args)
            self.assertEqual(result['status'], 'apple_process_failed')
            self.assertTrue(result['input_changed'])
            self.assertEqual(self.bridge.call('reprise_requests', {'request_key': 'apple-1'})['state'], 'recorded')
            # Identity includes input evidence: restore original for an identical replay.
            source.write_text('before')
            self.assertEqual(self.bridge.call('reprise_delegate', args), result)
            self.assertEqual(run.call_count, 1)
            again = self.bridge.call('reprise_delegate', args | {'request_key': 'retry'})
            self.assertEqual(again['status'], 'apple_process_failed')
            self.assertEqual(run.call_count, 2)

    def test_input_change_still_invalidates_success(self):
        source = self.root / 'source'
        source.write_text('before')
        def succeed(*args, **kwargs):
            source.write_text('after')
            return {'status': 'finished', 'backend': 'foundation-models', 'response': 'answer'}
        with patch('apple_expert.configured', return_value=True), patch('apple_expert.review', side_effect=succeed):
            result = self.bridge.call('reprise_delegate', self.args | {'paths': ['source']})
        self.assertEqual(result['status'], 'needs_review')
        self.assertEqual(result['original_status'], 'finished')
        self.assertTrue(result['input_changed'])

    def test_unproven_stop_remains_indeterminate_and_blocks_retry(self):
        with patch('apple_expert.configured', return_value=True), patch('apple_expert.run', side_effect=OSError('unknown stop')) as run:
            result = self.bridge.call('reprise_delegate', self.args)
            self.assertEqual(result['status'], 'needs_review')
            restarted = Bridge(self.root, {}, self.root / 'state')
            self.addCleanup(restarted.db.close)
            self.assertEqual(restarted.call('reprise_requests', {'request_key': 'apple-1'})['state'], 'indeterminate')
            again = restarted.call('reprise_delegate', self.args | {'request_key': 'explicit-second'})
            self.assertEqual(again['status'], 'indeterminate')
            self.assertEqual(run.call_count, 1)

    def test_failure_status_without_apple_reaped_proof_stays_uncertain(self):
        for result in ({'status': 'apple_input_invalid', 'backend': 'codex', 'process_started': False},
                       {'status': 'apple_input_invalid', 'backend': 'foundation-models'},
                       {'status': 'apple_process_failed', 'backend': 'codex', 'process_reaped': True},
                       {'status': 'apple_process_failed', 'backend': 'foundation-models'},
                       {'status': 'apple_process_failed', 'backend': 'foundation-models', 'process_reaped': False}):
            with self.subTest(result=result):
                key = 'proof-' + str(len(self.bridge.requests.lookup({})['requests']))
                self.bridge.requests.begin(key, {'tool': 'reprise_delegate', 'question': key})
                self.bridge.requests.finish(key, result)
                self.assertEqual(self.bridge.call('reprise_requests', {'request_key': key})['state'], 'indeterminate')

    def test_unavailable_does_not_call_other_engine(self):
        with patch('apple_expert.configured', return_value=False), patch('codex_expert_mcp.review') as codex:
            result = self.bridge.call('reprise_delegate', self.args)
            self.assertEqual(result['status'], 'engine_unavailable')
            self.assertFalse(result['automatic_fallback'])
            codex.assert_not_called()
            with self.assertRaises(ValueError):
                self.bridge.call('reprise_delegate', self.args | {'request_key': ''})
            self.assertEqual(self.bridge.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
