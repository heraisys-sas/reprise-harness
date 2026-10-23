import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ironclaw_ssh import Endpoint, ProbeError, probe, request


class IronclawSshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.token = Path(self.temp.name) / 'url'
        self.token.write_text('http://localhost:3000/#token=private-test-value')
        self.token.chmod(0o600)
        self.endpoint = Endpoint('host.example', 'agent', 29403, '/tmp/key', str(self.token))

    def response(self, body, code=200, returncode=0):
        return subprocess.CompletedProcess([], returncode, json.dumps(body)+'\n'+str(code), 'private-test-value')

    @patch('ironclaw_ssh.subprocess.run')
    def test_valid_probe_redacts_and_uses_stdin(self, run):
        run.return_value = self.response({'session_channel_extension_id': 'private-channel',
                                         'token': 'private-test-value', 'features': {}})
        result = probe(self.endpoint)
        self.assertTrue(result['authenticated'])
        self.assertNotIn('private', repr(result))
        args, kwargs = run.call_args
        self.assertNotIn('private-test-value', repr(args))
        self.assertIn('Authorization: Bearer private-test-value', kwargs['input'])
        self.assertEqual(args[0][-1], '/usr/bin/curl --disable --config -')
        self.assertNotIn('shell', kwargs)

    @patch('ironclaw_ssh.subprocess.run')
    def test_http_auth_failure_is_not_success(self, run):
        run.return_value = self.response({}, 401)
        self.assertFalse(probe(self.endpoint)['authenticated'])

    @patch('ironclaw_ssh.subprocess.run')
    def test_missing_channel_is_not_success(self, run):
        run.return_value = self.response({})
        with self.assertRaises(ProbeError): probe(self.endpoint)

    @patch('ironclaw_ssh.subprocess.run')
    def test_transport_and_timeout_errors_are_sanitized(self, run):
        for outcome in (self.response({}, returncode=255),
                        subprocess.TimeoutExpired(['private-test-value'], 20, output='private-test-value')):
            run.side_effect = outcome if isinstance(outcome, Exception) else None
            run.return_value = outcome
            with self.assertRaises(ProbeError) as error: probe(self.endpoint)
            self.assertNotIn('private-test-value', str(error.exception))

    @patch('ironclaw_ssh.subprocess.run')
    def test_rejects_untrusted_token_and_endpoint_before_execution(self, run):
        for value in ('http://localhost/#token=a%0Ab', 'http://localhost/?token=a#token=b'):
            self.token.write_text(value)
            with self.assertRaises(ProbeError): probe(self.endpoint)
        self.token.chmod(0o644)
        with self.assertRaises(ProbeError): probe(self.endpoint)
        with self.assertRaises(ProbeError): probe(Endpoint('-oProxyCommand=bad','agent',22,'/tmp/key',str(self.token)))
        run.assert_not_called()

    @patch('ironclaw_ssh.subprocess.run')
    def test_invalid_json_and_oversize(self, run):
        for output in ('not-json\n200', 'x'*65543):
            run.return_value = subprocess.CompletedProcess([],0,output,'')
            with self.assertRaises(ProbeError): probe(self.endpoint)

    @patch('ironclaw_ssh.subprocess.run')
    def test_get_request_builds_bounded_config_without_payload(self, run):
        run.return_value = self.response({'requests': []})
        value = request(self.endpoint, 'GET', '/api/webchat/v2/threads/abc/timeline?limit=100')
        self.assertEqual(value, {'requests': []})
        args, kwargs = run.call_args
        self.assertEqual(args[0][-1], '/usr/bin/curl --disable --config -')
        config = kwargs['input']
        lines = config.splitlines()
        self.assertEqual(lines[0], 'url = "http://127.0.0.1:3000/api/webchat/v2/threads/abc/timeline?limit=100"')
        self.assertEqual(lines[1], 'request = "GET"')
        self.assertEqual(lines[2], 'proxy = ""')
        self.assertEqual(lines[5], 'max-time = 15')
        self.assertEqual(lines[7], 'max-filesize = 1048576')
        self.assertIn('Authorization: Bearer private-test-value', config)
        self.assertNotIn('Content-Type', config)
        self.assertNotIn('data =', config)
        self.assertNotIn('private-test-value', repr(args))

    @patch('ironclaw_ssh.subprocess.run')
    def test_post_request_sends_headers_and_escapes_payload(self, run):
        run.return_value = self.response({'state': 'recorded'})
        payload = {'client_action_id': 'action-1', 'thread_id': 'thread-1', 'content': 'hello'}
        request(self.endpoint, 'POST', '/api/webchat/v2/channels/channel-1/messages', payload, allow_submit=True)
        config = run.call_args.kwargs['input']
        self.assertIn('header = "Content-Type: application/json"', config)
        self.assertIn('data = "{\\"client_action_id\\": \\"action-1\\", \\"thread_id\\": \\"thread-1\\", \\"content\\": \\"hello\\"}"', config)
        payload = {'client_action_id': 'action-1', 'thread_id': 'thread-1', 'content': 'a"b\\c'}
        request(self.endpoint, 'POST', '/api/webchat/v2/channels/channel-1/messages', payload, allow_submit=True)
        config = run.call_args.kwargs['input']
        self.assertIn('\\"a\\\\\\"b\\\\\\\\c\\"', config)
        self.assertNotIn(json.dumps(payload, ensure_ascii=True), config)

    @patch('ironclaw_ssh.subprocess.run')
    def test_post_without_opt_in_is_refused_before_transport(self, run):
        with self.assertRaises(ProbeError):
            request(self.endpoint, 'POST', '/api/webchat/v2/threads', {'client_action_id': 'action-1'})
        run.assert_not_called()

    @patch('ironclaw_ssh.subprocess.run')
    def test_oversize_request_body_is_refused_before_transport(self, run):
        payload = {'client_action_id': 'action-1', 'thread_id': 'thread-1', 'content': 'x' * 70000}
        with self.assertRaises(ProbeError):
            request(self.endpoint, 'POST', '/api/webchat/v2/threads', payload, allow_submit=True)
        run.assert_not_called()


if __name__ == '__main__': unittest.main()
