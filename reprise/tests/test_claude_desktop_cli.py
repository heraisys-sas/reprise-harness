import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_desktop
import claude_desktop_cli


class FakeClient:
    def __init__(self, *args, **kw):
        self.calls = []
        self.closed = False
        FakeClient.last = self

    def call(self, method, params):
        self.calls.append(method)
        return {}

    def send(self, message):
        self.calls.append(message['method'])

    def close(self):
        self.closed = True


class CliTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp(prefix='claude-desktop-cli-'))

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = claude_desktop_cli.main(list(argv))
        return code, json.loads(out.getvalue())

    def test_status_discovers_candidates_and_exits_zero(self):
        code, result = self.run_cli('status', '--state', str(self.state))
        self.assertEqual(code, 0)
        self.assertEqual(result['mode'], 'auto')
        self.assertIn('candidates', result)
        self.assertTrue(any(c['path'] == str(claude_desktop.DEFAULT_SOCKET) for c in result['candidates']))
        self.assertTrue((self.state / 'connection.sqlite').exists())

    def test_probe_success_without_model_call(self):
        with patch('claude_desktop_rpc.Client', FakeClient), patch.object(claude_desktop_cli.engines, 'executable', return_value='/usr/bin/supervisor'):
            code, result = self.run_cli('probe', '--socket', '/private/tmp/private.sock')
        self.assertEqual(code, 0)
        self.assertTrue(result['connected'])
        self.assertFalse(result['model_called'])
        self.assertEqual(FakeClient.last.calls, ['initialize', 'initialized'])
        self.assertTrue(FakeClient.last.closed)

    def test_probe_failure_is_structured_and_exit_one(self):
        with patch('claude_desktop_rpc.Client', side_effect=ValueError('bad protocol')), patch.object(claude_desktop_cli.engines, 'executable', return_value='/usr/bin/supervisor'):
            code, result = self.run_cli('probe', '--socket', '/private/tmp/private.sock')
        self.assertEqual(code, 1)
        self.assertFalse(result['connected'])
        self.assertIn('reason', result)

    def test_configure_saves_verified_socket_then_restores_auto(self):
        with patch('claude_desktop_rpc.Client', FakeClient), patch.object(claude_desktop_cli.engines, 'executable', return_value='/usr/bin/supervisor'):
            code, result = self.run_cli('configure', '--state', str(self.state), '--socket', '/private/tmp/chosen.sock')
        self.assertEqual(code, 0)
        self.assertTrue(result['saved'])
        code, status = self.run_cli('status', '--state', str(self.state))
        self.assertEqual(status['configured_socket'], '/private/tmp/chosen.sock')
        code, result = self.run_cli('configure', '--state', str(self.state))
        self.assertEqual(code, 0)
        self.assertTrue(result['saved'])
        code, status = self.run_cli('status', '--state', str(self.state))
        self.assertEqual(status['mode'], 'auto')
        self.assertIsNone(status['configured_socket'])

    def test_configure_rejected_socket_keeps_previous_config(self):
        with patch('claude_desktop_rpc.Client', FakeClient), patch.object(claude_desktop_cli.engines, 'executable', return_value='/usr/bin/supervisor'):
            self.run_cli('configure', '--state', str(self.state), '--socket', '/private/tmp/old.sock')
        with patch('claude_desktop_rpc.Client', side_effect=ValueError('refused')), patch.object(claude_desktop_cli.engines, 'executable', return_value='/usr/bin/supervisor'):
            code, result = self.run_cli('configure', '--state', str(self.state), '--socket', '/private/tmp/new.sock')
        self.assertEqual(code, 1)
        self.assertFalse(result['saved'])
        code, status = self.run_cli('status', '--state', str(self.state))
        self.assertEqual(status['configured_socket'], '/private/tmp/old.sock')

    def test_unavailable_is_structured_not_a_traceback(self):
        with patch.object(claude_desktop_cli.engines, 'executable', return_value=None):
            code, result = self.run_cli('probe', '--socket', '/private/tmp/private.sock')
        self.assertEqual(code, 1)
        self.assertFalse(result['connected'])
        self.assertIn('reason', result)

    def test_value_error_envelope_is_status_failed(self):
        with patch('claude_desktop.probe', side_effect=ValueError('bad input')):
            code, result = self.run_cli('probe', '--socket', '/private/tmp/private.sock')
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('bad input', result['reason'])


if __name__ == '__main__':
    unittest.main()
