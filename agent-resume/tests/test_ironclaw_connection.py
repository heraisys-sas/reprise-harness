import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ironclaw_connection as connection
import state_install


class IronClawConnectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.state = self.base / 'state'
        env = patch.dict(os.environ, {'AGENT_RESUME_STATE': str(self.state)})
        env.start()
        self.addCleanup(env.stop)
        self.value = {'host': 'example.invalid', 'user': 'agent', 'port': 2222,
                      'identity_file': str(self.base / 'key'), 'token_url_file': str(self.base / 'url')}

    def test_configure_requires_prepared_state_and_does_not_copy_credentials(self):
        with self.assertRaises(ValueError):
            connection.configure(self.base, self.value)
        self.assertFalse(self.state.exists())
        state_install.initialize(self.state)
        connection.configure(self.base, self.value)
        path = connection.config_path(self.base)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(path.read_text())['identity_file'], self.value['identity_file'])
        self.assertFalse((self.base / 'key').exists())
        self.assertFalse((self.base / 'url').exists())
        self.assertEqual(connection.endpoint(self.base).host, 'example.invalid')

    def test_followup_dispatches_explicit_parent_without_new_session(self):
        state_install.initialize(self.state)
        connection.configure(self.base, self.value)
        with patch.object(connection, 'Tasks') as cls:
            connection.task_operation(self.base, 'ironclaw-followup',
                                      {'request_key': 'child', 'parent_request_key': 'parent',
                                       'content': 'continue', 'allow_submit': True})
            cls.return_value.followup.assert_called_once_with('child', 'parent', 'continue', allow_submit=True)
            cls.return_value.submit.assert_not_called()

    def test_fail_requires_reason_and_routes_to_local_operator_exit(self):
        state_install.initialize(self.state)
        with patch.object(connection, 'Tasks') as cls, patch.object(connection, 'endpoint') as endpoint:
            connection.task_operation(self.base, 'ironclaw-fail',
                                      {'request_key': 'key', 'reason': 'quota exhausted'})
            cls.return_value.fail.assert_called_once_with('key', 'quota exhausted')
            connection.task_operation(self.base, 'ironclaw-fail', {'request_key': 'key', 'reason': 'ok'})
            self.assertEqual(cls.return_value.fail.call_count, 2)
            endpoint.assert_not_called()
        for value in ({'request_key': 'key'}, {'request_key': 'key', 'reason': '  '},
                      {'request_key': 'key', 'reason': 'x' * 513},
                      {'request_key': 'key', 'reason': 'ok', 'acknowledge': True}):
            with self.assertRaises(ValueError):
                connection.task_operation(self.base, 'ironclaw-fail', value)

    def test_reason_is_reserved_to_operator_fail(self):
        values = (('ironclaw-submit', {'request_key': 'key', 'content': 'q', 'allow_submit': True, 'reason': 'x'}),
                  ('ironclaw-poll', {'request_key': 'key', 'reason': 'x'}),
                  ('ironclaw-result', {'request_key': 'key', 'reason': 'x'}),
                  ('ironclaw-reconcile', {'request_key': 'key', 'reason': 'x'}),
                  ('ironclaw-requests', {'reason': 'x'}),
                  ('ironclaw-followup', {'request_key': 'key', 'parent_request_key': 'p', 'content': 'c', 'allow_submit': True, 'reason': 'x'}))
        with patch.object(connection, 'Tasks') as cls:
            for command, value in values:
                with self.assertRaises(ValueError):
                    connection.task_operation(self.base, command, value)
            cls.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_invalid_or_symbolic_config_cannot_overwrite(self):
        state_install.initialize(self.state)
        connection.configure(self.base, self.value)
        original = connection.config_path(self.base).read_bytes()
        with self.assertRaises(Exception):
            connection.configure(self.base, self.value | {'host': '-oProxyCommand=bad'})
        self.assertEqual(connection.config_path(self.base).read_bytes(), original)
        path = connection.config_path(self.base)
        path.unlink()
        target = self.base / 'preserved'
        target.write_text('preserved')
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            connection.configure(self.base, self.value)
        self.assertEqual(target.read_text(), 'preserved')

    def test_submission_requires_literal_consent_before_registry_open(self):
        with patch.object(connection, 'Tasks') as tasks:
            for value in ({'request_key': 'key', 'content': 'question'},
                          {'request_key': 'key', 'content': 'question', 'allow_submit': 'true'}):
                with self.assertRaises(ValueError):
                    connection.task_operation(self.base, 'ironclaw-submit', value)
            tasks.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_local_receipts_work_without_config_or_network(self):
        state_install.initialize(self.state)
        with patch.object(connection, 'probe') as probe:
            self.assertEqual(connection.task_operation(self.base, 'ironclaw-result', {'request_key': 'missing'}), {'state': 'not_found'})
            self.assertEqual(connection.task_operation(self.base, 'ironclaw-requests', {}), {'requests': [], 'limit': 100})
            probe.assert_not_called()
        self.assertFalse(connection.config_path(self.base).exists())

    def test_uncertain_submission_error_does_not_claim_nothing_was_sent(self):
        from io import StringIO
        with patch.object(sys, 'stdin', StringIO('{"request_key":"key","content":"question","allow_submit":true}')), patch.object(connection, 'task_operation', side_effect=connection.TaskError('unknown')), patch('builtins.print') as output:
            self.assertEqual(connection.main(self.base, ['ironclaw-submit']), 1)
        message = json.loads(output.call_args.args[0])['message']
        self.assertIn('reçu durable', message)
        self.assertNotIn('Aucune tâche soumise', message)

    def test_fail_triage_error_reports_local_decision(self):
        from io import StringIO
        state_install.initialize(self.state)
        with patch.object(sys, 'stdin', StringIO('{"request_key":"key","reason":"  "}')), patch('builtins.print') as output:
            self.assertEqual(connection.main(self.base, ['ironclaw-fail']), 1)
        message = json.loads(output.call_args.args[0])['message']
        self.assertIn('Décision opérateur', message)
        self.assertNotIn('reçu durable', message)
        self.assertNotIn('tâche incertaine', message)

    def test_fail_is_reachable_from_resume_cli_dispatch(self):
        # Issue #91 : le dispatch de `resume` doit router ironclaw-fail vers le module,
        # pas répondre « invalid choice » (argparse) avant d'y arriver.
        import subprocess
        state_install.initialize(self.state)
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / 'resume'), 'ironclaw-fail'],
            input='{"request_key": "key", "reason": "sortie operateur"}',
            capture_output=True, text=True, timeout=60,
            env={**os.environ, 'AGENT_RESUME_STATE': str(self.state)})
        self.assertEqual(process.returncode, 1)
        self.assertNotIn('invalid choice', process.stderr)
        self.assertIn('Décision opérateur', json.loads(process.stdout)['message'])

    def test_inspect_missing_config_has_no_side_effect_or_network(self):
        with patch.object(connection, 'probe') as probe, patch('builtins.print') as output:
            self.assertEqual(connection.main(self.base, ['ironclaw-inspect']), 0)
        self.assertIsNone(json.loads(output.call_args.args[0])['endpoint'])
        self.assertFalse(self.state.exists())
        probe.assert_not_called()
