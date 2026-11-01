import socket
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_desktop
import claude_desktop_apply
import claude_desktop_quick
import claude_desktop_rpc
from claude_desktop_quick import Endpoint, _probe_cdp


CHAT_ID = '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3'
PAGE_URL = 'https://claude.ai/chat/' + CHAT_ID


class FakeCDP:
    """Faux transport CDP : enregistre les évaluations, jamais de réseau."""

    def __init__(self, port=None, timeout=None, *, chat_id=None, target_id=None):
        self.calls = []
        FakeCDP.last = self

    def call(self, method, params=None):
        self.calls.append((method, params))
        return {}

    def navigate(self, url):
        self.calls.append(('Page.navigate', {'url': url}))

    def close(self):
        self.calls.append(('close', None))


def composer_state(url=PAGE_URL, text_sha256=None, text_length=0):
    return {'url': url, 'changed': False, 'composerPresent': True,
            'text_sha256': text_sha256, 'text_length': text_length,
            'hasText': bool(text_length), 'sendDisabled': False}


def patched_apply_module(state_sequence=None, prepared=True, dispatch_outcome=None,
                         dispatch_raises=None):
    """Patch the claude_desktop_apply primitives the daemon uses."""
    state_calls = {'n': 0}

    def fake_state(cdp):
        index = min(state_calls['n'], len(state_sequence) - 1)
        state_calls['n'] += 1
        return state_sequence[index]

    def fake_prepare(cdp, path):
        return prepared

    def fake_dispatch(cdp, path, fingerprint):
        if dispatch_raises is not None:
            raise dispatch_raises
        return dispatch_outcome

    def fake_insert(cdp, path, fingerprint):  # unused; insert goes via cdp.call
        return None

    patchers = [mock.patch.object(claude_desktop_apply, 'CDP', FakeCDP),
                mock.patch.object(claude_desktop_apply, 'state', fake_state),
                mock.patch.object(claude_desktop_apply, 'wait_composer', lambda cdp: True),
                mock.patch.object(claude_desktop_apply, '_prepare', fake_prepare),
                mock.patch.object(claude_desktop_apply, '_check', lambda info, path: None),
                mock.patch.object(claude_desktop_apply, '_dispatch', fake_dispatch)]
    for patcher in patchers:
        patcher.start()
    return patchers


class DaemonDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patchers = []
        self.apply_patchers = []

    def cleanup_apply_patchers(self):
        for patcher in self.apply_patchers:
            patcher.stop()
        self.apply_patchers = []

    def patch_apply(self, **kwargs):
        self.apply_patchers = patched_apply_module(**kwargs)
        self.addCleanup(self.cleanup_apply_patchers)

    def endpoint(self):
        endpoint = Endpoint(self.directory.name, project_root=self.directory.name)
        self.addCleanup(endpoint.close)
        return endpoint

    def admit(self, endpoint, prompt='Tâche orchestrée pour dispatch'):
        key = str(uuid.uuid4())
        endpoint.submit({'request_key': key, 'prompt': prompt, 'chatId': CHAT_ID})
        return key

    def fingerprint(self, prompt):
        import hashlib
        return hashlib.sha256(prompt.strip().encode()).hexdigest()

    # -- capabilities ---------------------------------------------------------

    def test_capabilities_report_cdp_channel(self):
        endpoint = self.endpoint()
        caps = endpoint.call('quick_entry/capabilities', {})
        self.assertEqual(caps['dispatch_channel'], 'cdp')
        self.assertEqual(caps['cdp_port'], claude_desktop_apply.DEFAULT_PORT)
        self.assertNotIn('dispatch_socket', caps)

    def test_capabilities_true_when_cdp_port_listens(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        with mock.patch.object(claude_desktop_apply, 'DEFAULT_PORT', port):
            endpoint = self.endpoint()
            caps = endpoint.call('quick_entry/capabilities', {})
            self.assertTrue(caps['dispatch_supported'])
            self.assertEqual(caps['cdp_port'], port)

    def test_capabilities_false_when_cdp_port_refused(self):
        with mock.patch.object(claude_desktop_apply, 'DEFAULT_PORT', 1):  # port refusé
            endpoint = self.endpoint()
            caps = endpoint.call('quick_entry/capabilities', {})
            self.assertFalse(caps['dispatch_supported'])
            self.assertIn('refused connection', caps['reason'])

    # -- dispatch -------------------------------------------------------------

    def test_dispatch_click_observed_is_ok(self):
        prompt = 'Tâche orchestrée pour dispatch'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=self.fingerprint(prompt))],
            dispatch_outcome=claude_desktop_apply._result('envoyé', ok=True,
                                                          dispatch_attempted=True, click_observed=True))
        endpoint = self.endpoint()
        key = self.admit(endpoint, prompt)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertTrue(result['dispatch_attempted'])
        self.assertTrue(result['dispatch_ok'])
        self.assertEqual(result['dispatch_channel'], 'cdp')
        self.assertEqual(result['stage'], 'envoyé')
        self.assertTrue(result['admission_only'])
        # prompt staged once via Input.insertText, single click primitive
        self.assertIn(('Input.insertText', {'text': prompt.strip()}), FakeCDP.last.calls)

    def test_dispatch_send_button_absent_fails(self):
        prompt = 'Requête sans bouton'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=self.fingerprint(prompt))],
            dispatch_outcome=claude_desktop_apply._result('composeur', dispatch_attempted=False))
        endpoint = self.endpoint()
        key = self.admit(endpoint, prompt)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertTrue(result['dispatch_attempted'])
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'composeur')
        self.assertIn('dispatch_error', result)

    def test_dispatch_login_page_reports_session(self):
        self.patch_apply(state_sequence=[composer_state(url='https://claude.ai/login')])
        endpoint = self.endpoint()
        key = self.admit(endpoint, 'Requête sur page login')
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'session')
        FakeCDP.last  # CDP was opened then closed
        self.assertIn(('close', None), FakeCDP.last.calls)

    def test_dispatch_uncertain_exception_keeps_uncertainty(self):
        prompt = 'Requête à réponse CDP perdue'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=self.fingerprint(prompt))],
            dispatch_raises=claude_desktop_apply.WSError('Réponse CDP perdue'))
        endpoint = self.endpoint()
        key = self.admit(endpoint, prompt)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'incertain')
        self.assertIn('dispatch_error', result)

    def test_dispatch_unknown_request_key_refused(self):
        endpoint = self.endpoint()
        with mock.patch.object(claude_desktop_apply, 'CDP', mock.MagicMock()) as cdp_factory:
            with self.assertRaises(ValueError):
                endpoint.call('quick_entry/dispatch', {'request_key': 'inconnu'})
            cdp_factory.assert_not_called()

    def test_dispatch_refuses_extra_fields(self):
        self.patch_apply()
        endpoint = self.endpoint()
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'k', 'images': ['a']})
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'k', 'filePaths': ['file:///x']})

    def test_dispatch_missing_chat_id_fails_without_cdp(self):
        endpoint = self.endpoint()
        key = str(uuid.uuid4())
        endpoint.submit({'request_key': key, 'prompt': 'Sans chatId'})
        with mock.patch.object(claude_desktop_apply, 'CDP', mock.MagicMock()) as cdp_factory:
            result = endpoint.call('quick_entry/dispatch', {'request_key': key})
            self.assertFalse(result['dispatch_ok'])
            self.assertEqual(result['stage'], 'cible')
            cdp_factory.assert_not_called()


class CdpProbeTests(unittest.TestCase):
    def test_probe_live_port(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        self.addCleanup(listener.close)
        self.assertTrue(_probe_cdp(listener.getsockname()[1]))

    def test_probe_refused_port_is_stopped(self):
        self.assertFalse(_probe_cdp(1))

    def test_probe_bad_port_is_stopped(self):
        self.assertFalse(_probe_cdp('not-a-port'))


class _FakeRpcClient:
    def __init__(self, *args, **kw):
        self.calls = []
        _FakeRpcClient.last = self

    def call(self, method, params):
        self.calls.append(method)
        if method == 'quick_entry/submit':
            return {'status': 'finished', 'submission_id': 'sub-1'}
        if method == 'quick_entry/capabilities':
            return _FakeRpcClient.capabilities
        if method == 'quick_entry/dispatch':
            return _FakeRpcClient.dispatch
        return {}

    def close(self):
        pass


class ReviewDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        _FakeRpcClient.capabilities = {'dispatch_supported': True, 'dispatch_channel': 'cdp'}
        _FakeRpcClient.dispatch = {'dispatch_ok': True, 'stage': 'envoyé'}

    def review(self):
        from desktop_task_store import TaskStore
        tasks = TaskStore(self.directory.name, self.directory.name)
        self.addCleanup(tasks.close)
        with mock.patch.object(claude_desktop_rpc, 'Client', _FakeRpcClient):
            return claude_desktop.review(self.directory.name, 'Question orchestrée', kind='expertise',
                                         level='standard', request_key='rk-1', tasks=tasks)

    def test_receipt_reflects_dispatch_attempted(self):
        result = self.review()
        self.assertEqual(result['status'], 'finished')
        self.assertTrue(result['dispatch_attempted'])
        self.assertTrue(result['dispatch_ok'])
        self.assertTrue(result['admission_only'])
        self.assertIsNone(result['response'])

    def test_failed_dispatch_keeps_admission_only(self):
        _FakeRpcClient.dispatch = {'dispatch_ok': False, 'dispatch_error': 'refusé'}
        result = self.review()
        self.assertEqual(result['status'], 'finished')
        self.assertTrue(result['dispatch_attempted'])
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['dispatch_error'], 'refusé')
        self.assertTrue(result['admission_only'])

    def test_no_dispatch_without_capability(self):
        _FakeRpcClient.capabilities = {'dispatch_supported': False}
        result = self.review()
        self.assertEqual(result['status'], 'finished')
        self.assertNotIn('dispatch_attempted', result)
        self.assertTrue(result['admission_only'])


if __name__ == '__main__':
    unittest.main()
