"""Unit tests for the remote IronClaw delegation adapter."""
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import ironclaw
from engines import Unavailable


class _Proc:
    def __init__(self, stdout='', stderr='', returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class SubmitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        holder = Path(self.temp.name)
        self.keyfile = holder / 'id_ed25519'
        self.keyfile.write_bytes(b'\n-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n')
        self.keyfile.chmod(0o600)
        self.values = {'host': 'instance.example', 'port': '29403', 'user': 'agent',
                       'key': str(self.keyfile)}

    def test_success_pins_ssh_options_and_returns_answer(self):
        with patch.object(ironclaw, '_run', return_value=_Proc(stdout='PONG\n')) as run:
            result = ironclaw.submit('ssh', self.values, 'Réponds PING')
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['response'], 'PONG')
        command = run.call_args.args[0]
        self.assertIn('-o', command)
        self.assertIn('BatchMode=yes', command)
        self.assertIn('-i', command)
        self.assertEqual(command[command.index('-i') + 1], str(self.keyfile))
        self.assertIn('ironclaw run -m', command[-1])
        self.assertNotIn('BEGIN OPENSSH', ' '.join(command))

    def test_remote_command_encodes_prompt_base64(self):
        with patch.object(ironclaw, '_run', return_value=_Proc(stdout='PONG\n')) as run:
            ironclaw.submit('ssh', self.values, 'mot clé étonnant')
        remote = run.call_args.args[0][-1]
        self.assertIn('base64 -d', remote)

    def test_permission_denied_is_engine_unavailable(self):
        with patch.object(ironclaw, '_run', return_value=_Proc(stderr='Permission denied (publickey).', returncode=255)):
            with self.assertRaises(Unavailable) as ctx:
                ironclaw.submit('ssh', self.values, 'x')
        self.assertEqual(ctx.exception.state, 'engine_unavailable')

    def test_timeout_is_reported(self):
        with patch.object(ironclaw, '_run', side_effect=subprocess.TimeoutExpired('ssh', 1)):
            with self.assertRaises(Unavailable) as ctx:
                ironclaw.submit('ssh', self.values, 'x')
        self.assertEqual(ctx.exception.state, 'timed_out')

    def test_nonzero_exit_is_blocked_permission(self):
        with patch.object(ironclaw, '_run', return_value=_Proc(stdout='', stderr='boom', returncode=1)):
            with self.assertRaises(Unavailable) as ctx:
                ironclaw.submit('ssh', self.values, 'x')
        self.assertEqual(ctx.exception.state, 'blocked_permission')

    def test_empty_stdout_is_needs_review(self):
        with patch.object(ironclaw, '_run', return_value=_Proc(stdout='', returncode=0)):
            with self.assertRaises(Unavailable) as ctx:
                ironclaw.submit('ssh', self.values, 'x')
        self.assertEqual(ctx.exception.state, 'needs_review')

    def test_bytes_key_materialized_0600_then_removed(self):
        values = dict(self.values, key=b'\n-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n')
        seen = {}
        def fake_run(command, timeout):
            keyfile = command[command.index('-i') + 1]
            seen['tmp'] = keyfile
            seen['mode'] = stat.S_IMODE(Path(keyfile).stat().st_mode)
            return _Proc(stdout='PONG\n')
        with patch.object(ironclaw, '_run', fake_run):
            ironclaw.submit('ssh', values, 'x')
        self.assertEqual(seen['mode'], 0o600)
        self.assertFalse(Path(seen['tmp']).exists())

    def test_health_reads_shell_and_ui(self):
        with patch.object(ironclaw, '_run', return_value=_Proc(stdout='SHELL_OK\nUI=200\n', returncode=0)) as run:
            result = ironclaw.health('ssh', self.values)
        self.assertTrue(result['connected'])
        self.assertIn('%{http_code}', run.call_args.args[0][-1])
        self.assertIn('SHELL_OK', run.call_args.args[0][-1])


class ReviewTests(unittest.TestCase):
    def test_review_shape_matches_backends(self):
        with patch.object(ironclaw, 'submit', return_value={'status': 'finished', 'response': 'Réponse'}) as submit:
            result = ironclaw.review('/ssh', {'key': '/k'}, '/tmp', 'Question',
                                     kind='expertise', level='standard')
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['response'], 'Réponse')
        self.assertFalse(result['resumable'])
        self.assertFalse(result['automatic_fallback'])
        self.assertEqual(result['model'], 'ironclaw-distant')
        self.assertIn('kind', result)

    def test_unavailable_is_mapped_to_status(self):
        with patch.object(ironclaw, 'submit', side_effect=Unavailable('timed_out', 'non')):
            result = ironclaw.review('/ssh', {'key': '/k'}, '/tmp', 'Question')
        self.assertEqual(result['status'], 'timed_out')
        self.assertEqual(result['reason'], 'non')

    def test_unbounded_question_rejected(self):
        with self.assertRaises(ValueError):
            ironclaw.review('/ssh', {'key': '/k'}, '/tmp', 'x' * 12001)


class ValuesTests(unittest.TestCase):
    def test_missing_key_rejected(self):
        with self.assertRaises(ValueError):
            ironclaw.validate_values({'host': 'h', 'port': '1', 'user': 'u'})

    def test_defaults_applied(self):
        host, port, user, key = ironclaw.validate_values({'key': '/k'})
        self.assertEqual(host, ironclaw.DEFAULT_HOST)
        self.assertEqual(port, ironclaw.DEFAULT_PORT)
        self.assertEqual(user, ironclaw.DEFAULT_USER)


if __name__ == '__main__':
    unittest.main()
