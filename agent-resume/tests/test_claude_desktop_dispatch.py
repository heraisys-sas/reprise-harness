import hashlib
import json
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
import claude_desktop_applyd
import claude_desktop_quick
import claude_desktop_rpc
from claude_desktop_quick import Endpoint, _probe_socket


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
    """Patch the claude_desktop_apply primitives the applyd daemon uses."""
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

    patchers = [mock.patch.object(claude_desktop_apply, 'CDP', FakeCDP),
                mock.patch.object(claude_desktop_apply, 'state', fake_state),
                mock.patch.object(claude_desktop_apply, 'wait_composer', lambda cdp: True),
                mock.patch.object(claude_desktop_apply, '_prepare', fake_prepare),
                mock.patch.object(claude_desktop_apply, '_check', lambda info, path: None),
                mock.patch.object(claude_desktop_apply, '_dispatch', fake_dispatch)]
    for patcher in patchers:
        patcher.start()
    return patchers


class FakeApplyd(threading.Thread):
    """Fake applyd child: binds a temp socket, greets, one line, one reply."""

    def __init__(self, reply):
        super().__init__(daemon=True)
        self.reply = reply
        self.received = []
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / 'applyd.sock')
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(1)

    def start(self):
        super().start()
        # Wait for accept to be reachable without racing the test.
        for _ in range(50):
            if claude_desktop_quick._probe_socket(self.path):
                return
            threading.Event().wait(0.02)

    def run(self):
        try:
            while True:
                conn, _ = self.sock.accept()
                try:
                    conn.sendall(b'{"hello": true}\n')
                    stream = conn.makefile('rb')
                    for raw in stream:
                        self.received.append(json.loads(raw))
                        if self.reply is None:
                            return  # close without reply; no retry
                        conn.sendall((json.dumps(self.reply, ensure_ascii=False) + '\n').encode())
                except OSError:
                    pass
                finally:
                    conn.close()
        finally:
            self.sock.close()
            self.directory.cleanup()

    def stop(self):
        # Trigger loop end by connecting and closing; thread cleans up itself.
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(self.path)
        except OSError:
            pass
        finally:
            probe.close()


class _RealApplyd:
    """Runs applyd._serve_conn on a temp socket with patched apply primitives."""

    def __init__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / 'applyd.sock')
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.path)
        self.sock.listen(2)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        for _ in range(50):
            if _probe_socket(self.path):
                return
            threading.Event().wait(0.02)

    def _run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                claude_desktop_applyd._serve_conn(conn)
            except OSError:
                pass
            finally:
                conn.close()

    def close(self):
        self.sock.close()
        self.directory.cleanup()


class DaemonDispatchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.apply_patchers = []

    def cleanup_apply_patchers(self):
        for patcher in self.apply_patchers:
            patcher.stop()
        self.apply_patchers = []

    def patch_apply(self, **kwargs):
        self.apply_patchers = patched_apply_module(**kwargs)
        self.addCleanup(self.cleanup_apply_patchers)

    def endpoint(self, applyd_socket=None):
        endpoint = Endpoint(self.directory.name, project_root=self.directory.name,
                            applyd_socket=applyd_socket)
        self.addCleanup(endpoint.close)
        return endpoint

    def admit(self, endpoint, prompt='Tâche orchestrée pour dispatch'):
        key = str(uuid.uuid4())
        endpoint.submit({'request_key': key, 'prompt': prompt, 'chatId': CHAT_ID})
        return key

    def fingerprint(self, prompt):
        return hashlib.sha256(prompt.strip().encode()).hexdigest()

    # -- capabilities ---------------------------------------------------------

    def test_capabilities_report_socket_channel(self):
        endpoint = self.endpoint(applyd_socket=str(Path(self.directory.name) / 'absent.sock'))
        with mock.patch.object(claude_desktop_quick, '_spawn_applyd') as spawn, \
             mock.patch('claude_cdp_pipe._chrome_bin', return_value=sys.executable):
            caps = endpoint.call('quick_entry/capabilities', {})
        self.assertEqual(caps['dispatch_channel'], 'chrome-socket')
        self.assertEqual(caps['dispatch_socket'], str(endpoint.applyd_socket))
        self.assertTrue(caps['dispatch_supported'])
        self.assertFalse(caps['applyd_running'])
        spawn.assert_not_called()

    def test_capabilities_refuse_missing_chrome_and_applyd(self):
        endpoint = self.endpoint(applyd_socket=str(Path(self.directory.name) / 'absent.sock'))
        with mock.patch('claude_cdp_pipe._chrome_bin', return_value='/nonexistent/chrome'):
            caps = endpoint.call('quick_entry/capabilities', {})
        self.assertFalse(caps['dispatch_supported'])
        self.assertFalse(caps['applyd_running'])

    def test_capabilities_refuse_unscoped_dispatch(self):
        endpoint = Endpoint(self.directory.name, applyd_socket=str(Path(self.directory.name) / 'absent.sock'))
        self.addCleanup(endpoint.close)
        with mock.patch('claude_cdp_pipe._chrome_bin', return_value=sys.executable):
            self.assertFalse(endpoint.call('quick_entry/capabilities', {})['dispatch_supported'])

    def test_capabilities_true_when_socket_answers(self):
        daemon = _RealApplyd()
        self.addCleanup(daemon.close)
        endpoint = self.endpoint(applyd_socket=daemon.path)
        caps = endpoint.call('quick_entry/capabilities', {})
        self.assertTrue(caps['dispatch_supported'])
        self.assertTrue(caps['applyd_running'])
        self.assertEqual(caps['dispatch_socket'], daemon.path)

    # -- dispatch over the socket --------------------------------------------

    def test_dispatch_click_observed_is_ok(self):
        prompt = 'Tâche orchestrée pour dispatch'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=self.fingerprint(prompt))],
            dispatch_outcome=claude_desktop_apply._result('envoyé', ok=True,
                                                          dispatch_attempted=True, click_observed=True))
        daemon = _RealApplyd()
        self.addCleanup(daemon.close)
        endpoint = self.endpoint(applyd_socket=daemon.path)
        key = self.admit(endpoint, prompt)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertTrue(result['dispatch_attempted'])
        self.assertTrue(result['dispatch_ok'])
        self.assertEqual(result['dispatch_channel'], 'chrome-socket')
        self.assertEqual(result['dispatch_socket'], daemon.path)
        self.assertEqual(result['stage'], 'envoyé')
        self.assertIs(result['click_observed'], True)
        self.assertTrue(result['admission_only'])
        # prompt staged once via Input.insertText, single click primitive
        self.assertIn(('Input.insertText', {'text': prompt.strip()}), FakeCDP.last.calls)

    def test_dispatch_stage_error_fails(self):
        self.patch_apply(state_sequence=[composer_state(url='https://claude.ai/login')])
        daemon = _RealApplyd()
        self.addCleanup(daemon.close)
        endpoint = self.endpoint(applyd_socket=daemon.path)
        key = self.admit(endpoint, 'Requête sur page login')
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'session')
        self.assertIn('dispatch_error', result)

    def test_dispatch_lost_reply_is_uncertain(self):
        prompt = 'Requête à réponse perdue'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=self.fingerprint(prompt))],
            dispatch_raises=claude_desktop_apply.WSError('Réponse CDP perdue'))
        daemon = _RealApplyd()
        self.addCleanup(daemon.close)
        endpoint = self.endpoint(applyd_socket=daemon.path)
        key = self.admit(endpoint, prompt)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'incertain')
        self.assertNotIn('click_observed', result)

    def test_dispatch_timeout_is_uncertain_without_retry(self):
        daemon = FakeApplyd(reply=None)  # greets then never replies
        daemon.start()
        self.addCleanup(daemon.stop)
        endpoint = self.endpoint(applyd_socket=daemon.path)
        key = self.admit(endpoint, 'Requête silencieuse')
        with mock.patch.dict('os.environ', {'REPRISE_APPLYD_TIMEOUT': '1'}), \
             mock.patch.object(claude_desktop_quick, '_spawn_applyd') as spawn:
            result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        spawn.assert_not_called()
        self.assertEqual(len(daemon.received), 1)
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'incertain')

    def test_dispatch_socket_refused_then_lazy_spawn(self):
        prompt = 'Spawn lazy requis'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=self.fingerprint(prompt))],
            dispatch_outcome=claude_desktop_apply._result('envoyé', ok=True,
                                                          dispatch_attempted=True, click_observed=True))
        daemon = _RealApplyd()
        self.addCleanup(daemon.close)
        endpoint = self.endpoint(applyd_socket=daemon.path)
        key = self.admit(endpoint, prompt)
        # Pretend the socket was cold: _dispatch must go through _spawn_applyd.
        calls = []
        def fake_spawn(path):
            calls.append(path)
            return True
        with mock.patch.object(claude_desktop_quick, '_probe_socket', return_value=False), \
             mock.patch.object(Path, 'exists', return_value=True), \
             mock.patch.object(claude_desktop_quick, '_spawn_applyd', side_effect=fake_spawn):
            result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertEqual(calls, [Path(daemon.path)])
        self.assertTrue(result['dispatch_ok'])

    def test_dispatch_spawn_failure_is_uncertain(self):
        endpoint = self.endpoint(applyd_socket=str(Path(self.directory.name) / 'absent.sock'))
        key = self.admit(endpoint, 'Sans démon')
        with mock.patch.object(Path, 'exists', return_value=True), \
             mock.patch.object(claude_desktop_quick, '_spawn_applyd', return_value=False):
            result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'incertain')
        self.assertIn('spawn', result['dispatch_error'].lower())

    def test_chrome_missing_does_not_spawn(self):
        endpoint = self.endpoint(applyd_socket=str(Path(self.directory.name) / 'absent.sock'))
        key = self.admit(endpoint)
        with mock.patch.object(Path, 'exists', return_value=False), \
             mock.patch.object(claude_desktop_quick, '_spawn_applyd') as spawn:
            result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        spawn.assert_not_called()
        self.assertEqual(result['stage'], 'incertain')
        self.assertIn('Chrome absent', result['dispatch_error'])

    def test_dispatch_unknown_request_key_refused(self):
        endpoint = self.endpoint()
        with mock.patch.object(claude_desktop_quick, '_spawn_applyd', return_value=False) as spawn:
            with self.assertRaises(ValueError):
                endpoint.call('quick_entry/dispatch', {'request_key': 'inconnu'})
            spawn.assert_not_called()

    def test_dispatch_refuses_extra_fields(self):
        endpoint = self.endpoint()
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'k', 'images': ['a']})
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'k', 'filePaths': ['file:///x']})

    def test_dispatch_missing_chat_id_fails_without_socket(self):
        endpoint = self.endpoint()
        key = str(uuid.uuid4())
        endpoint.submit({'request_key': key, 'prompt': 'Sans chatId'})
        with mock.patch.object(claude_desktop_quick, '_spawn_applyd', return_value=False) as spawn:
            result = endpoint.call('quick_entry/dispatch', {'request_key': key})
            self.assertFalse(result['dispatch_ok'])
            self.assertEqual(result['stage'], 'cible')
            spawn.assert_not_called()


class SocketProtocolTests(unittest.TestCase):
    """applyd protocol contract: greeting, bounded payload, per-request errors."""

    def test_oversized_payload_destroys_connection(self):
        daemon = _RealApplyd()
        self.addCleanup(daemon.close)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(daemon.path)
            greeting = sock.makefile('r').readline()
            self.assertTrue(json.loads(greeting).get('hello'))
            # Larger than MAX_LINE on a single line: the daemon closes the
            # socket without replying (EOF, no error object).
            blob = b'x' * (claude_desktop_applyd.MAX_LINE + 1)
            sock.sendall(blob + b'\n')
            sock.shutdown(socket.SHUT_WR)
            self.assertEqual(sock.recv(1024), b'')

    def test_probe_socket_refused_without_file(self):
        self.assertFalse(_probe_socket('/nonexistent/applyd.sock'))

    def test_existing_active_socket_is_not_unlinked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'active.sock'
            with socket.socket(socket.AF_UNIX) as owner:
                owner.bind(str(path))
                owner.listen(1)
                inode = path.lstat().st_ino
                with self.assertRaises(SystemExit):
                    claude_desktop_applyd.bind_socket(path)
                self.assertEqual(inode, path.lstat().st_ino)

    def test_second_owner_refused_by_lock(self):
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'applyd.sock'
            with Path(str(path) + '.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with mock.patch.object(claude_desktop_applyd, 'bind_socket') as bind:
                    with self.assertRaises(SystemExit):
                        claude_desktop_applyd.serve(path)
                    bind.assert_not_called()

    def test_quick_terminate_does_not_kill_chrome(self):
        handlers = []
        def signal_mock(sig, handler):
            handlers.append(handler)
            return None
        def serve_mock(path, endpoint):
            handlers[0](15, None)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(sys, 'argv', ['quick', '--state', directory]), \
             mock.patch.object(claude_desktop_quick.signal, 'signal', side_effect=signal_mock), \
             mock.patch.object(claude_desktop_quick, 'serve', side_effect=serve_mock), \
             mock.patch.object(claude_desktop_quick.os, 'kill') as kill:
            with self.assertRaises(SystemExit):
                claude_desktop_quick.main()
            kill.assert_not_called()

    def test_authenticated_shutdown_does_not_kill_chrome(self):
        with tempfile.TemporaryDirectory() as directory:
            token = 'a' * 64
            token_path = Path(directory) / 'quick-apply-shutdown.token'
            token_path.write_text(token)
            endpoint = Endpoint(directory)
            try:
                with mock.patch.object(claude_desktop_quick.os, 'kill') as kill, \
                     mock.patch.object(claude_desktop_quick, '_cdp_pipe') as cdp:
                    result = endpoint._shutdown({'token': token})
                self.assertEqual(result, {'ok': True})
                kill.assert_not_called()
                cdp.assert_not_called()
                self.assertFalse(token_path.exists())
            finally:
                endpoint.close()

    def test_stage_logging_contains_no_prompt_or_chat_id(self):
        import io
        output = io.StringIO()
        with mock.patch('sys.stderr', output):
            claude_desktop_applyd.log_stage('préparé')
        self.assertEqual(json.loads(output.getvalue())['stage'], 'préparé')
        self.assertNotIn(CHAT_ID, output.getvalue())


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
        _FakeRpcClient.capabilities = {'dispatch_supported': True, 'dispatch_channel': 'chrome-socket'}
        _FakeRpcClient.dispatch = {'dispatch_ok': True, 'stage': 'envoyé', 'click_observed': True,
                                   'dispatch_socket': '/test/applyd.sock'}

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
        self.assertEqual(result['stage'], 'envoyé')
        self.assertTrue(result['click_observed'])
        self.assertEqual(result['dispatch_socket'], '/test/applyd.sock')
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

    def test_cold_capability_allows_single_dispatch_and_no_replay(self):
        _FakeRpcClient.capabilities['applyd_running'] = False
        self.review()
        self.assertEqual(_FakeRpcClient.last.calls.count('quick_entry/dispatch'), 1)
        again = self.review()
        self.assertEqual(again['status'], 'indeterminate')
        self.assertNotIn('quick_entry/dispatch', _FakeRpcClient.last.calls)

    def test_review_cold_applyd_invokes_real_endpoint_dispatch_once(self):
        from desktop_task_store import TaskStore
        state = self.directory.name
        endpoint = Endpoint(state, project_root=state,
                            applyd_socket=str(Path(state) / 'absent.sock'))
        self.addCleanup(endpoint.close)
        tasks = TaskStore(state, state)
        self.addCleanup(tasks.close)

        class EndpointClient:
            def __init__(self, *args, **kwargs):
                pass
            def call(self, method, params):
                return endpoint.call(method, params)
            def close(self):
                pass

        with mock.patch.object(claude_desktop_rpc, 'Client', EndpointClient), \
             mock.patch('claude_cdp_pipe._chrome_bin', return_value=sys.executable), \
             mock.patch.object(claude_desktop_quick, '_probe_socket', return_value=False), \
             mock.patch.object(claude_desktop_quick, '_spawn_applyd', return_value=True) as spawn, \
             mock.patch.object(endpoint, '_socket_apply', return_value={
                 'stage': 'envoyé', 'click_observed': True}) as apply:
            result = claude_desktop.review(state, 'Question orchestrée', request_key='cold-key',
                                           chat_id=CHAT_ID, tasks=tasks)
            replay = endpoint.call('quick_entry/dispatch', {'request_key': 'cold-key'})
        self.assertTrue(result['dispatch_ok'])
        self.assertTrue(result['click_observed'])
        self.assertTrue(replay['dispatch_ok'])
        spawn.assert_called_once()
        apply.assert_called_once()
        # The socket barrier must not consume the separate MCP delivery barrier.
        claimed = tasks.claim('cold-key', 'claim-for-test')
        self.assertEqual(claimed['delivery'], 'handed_over')
        self.assertIn('Question orchestrée', claimed['prompt'])
        tasks.submit('cold-key', claimed['attempt_id'], 'Résultat de test')
        self.assertEqual(tasks.result('cold-key', view='full')['answer'], 'Résultat de test')

    def test_socket_dispatch_reservation_survives_lost_ack_and_restart(self):
        from desktop_task_store import TaskStore
        store = TaskStore(self.directory.name, self.directory.name)
        store.admit('lost-key', 'Question test', CHAT_ID)
        self.assertEqual(store.reserve_socket_dispatch('lost-key')['state'], 'reserved')
        store.close()
        reopened = TaskStore(self.directory.name, self.directory.name)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.reserve_socket_dispatch('lost-key')['state'], 'attempted')

    def test_uncertain_socket_result_is_recorded_and_never_retried(self):
        endpoint = Endpoint(self.directory.name, project_root=self.directory.name,
                            applyd_socket=str(Path(self.directory.name) / 'absent.sock'))
        self.addCleanup(endpoint.close)
        endpoint.submit({'request_key': 'uncertain-key', 'prompt': 'Question test', 'chatId': CHAT_ID})
        with mock.patch.object(claude_desktop_quick, '_probe_socket', return_value=True), \
             mock.patch.object(endpoint, '_socket_apply', return_value={'stage': 'incertain', 'detail': 'ACK perdu'}) as apply:
            first = endpoint.call('quick_entry/dispatch', {'request_key': 'uncertain-key'})
            replay = endpoint.call('quick_entry/dispatch', {'request_key': 'uncertain-key'})
        self.assertEqual(first, replay)
        self.assertEqual(first['stage'], 'incertain')
        apply.assert_called_once()

    def test_concurrent_socket_reservations_are_single_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from desktop_task_store import TaskStore
        store = TaskStore(self.directory.name, self.directory.name)
        store.admit('socket-race', 'Question test', CHAT_ID)
        store.close()
        barrier = threading.Barrier(2, timeout=5)
        def reserve():
            current = TaskStore(self.directory.name, self.directory.name)
            try:
                barrier.wait()
                return current.reserve_socket_dispatch('socket-race')['state']
            finally:
                current.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            states = list(pool.map(lambda _: reserve(), range(2)))
        self.assertCountEqual(states, ['reserved', 'attempted'])

    def test_legacy_tasks_are_never_automatically_redispatched(self):
        import sqlite3
        from desktop_task_store import TaskStore
        db = sqlite3.connect(Path(self.directory.name) / 'quick.sqlite')
        db.execute('''CREATE TABLE desktop_tasks(scope TEXT NOT NULL,request_key TEXT NOT NULL,
            prompt TEXT NOT NULL,chat_id TEXT,claim_key TEXT,attempt_id TEXT,answer TEXT,
            acknowledged INTEGER NOT NULL DEFAULT 0,delivery TEXT,PRIMARY KEY(scope,request_key))''')
        scope = str(Path(self.directory.name).resolve())
        db.execute('INSERT INTO desktop_tasks(scope,request_key,prompt,chat_id) VALUES(?,?,?,?)',
                   (scope, 'old-null', 'Question', CHAT_ID))
        db.execute("INSERT INTO desktop_tasks(scope,request_key,prompt,chat_id,delivery) VALUES(?,?,?,?, 'attempted')",
                   (scope, 'old-attempted', 'Question', CHAT_ID))
        db.commit()
        db.close()
        store = TaskStore(self.directory.name, self.directory.name)
        self.addCleanup(store.close)
        for key in ('old-null', 'old-attempted'):
            self.assertEqual(store.reserve_socket_dispatch(key)['state'], 'legacy_unknown')
            self.assertFalse(store.reserve_review_dispatch(key))

    def test_review_and_claude_mcp_share_explicit_state_outside_project(self):
        from codex_expert_mcp import Bridge
        from claude_mcp import ClaudeMCP
        from desktop_task_store import TaskStore
        base = Path(self.directory.name)
        project, state, expert = base / 'project', base / 'quick-state', base / 'expert-state'
        project.mkdir()
        bridge = Bridge(project, {}, expert, desktop_state=state)
        self.addCleanup(bridge.db.close)
        self.addCleanup(bridge.desktop_tasks.close)
        inbox = TaskStore(state, project)
        self.addCleanup(inbox.close)
        endpoint = Endpoint(state, project_root=project, applyd_socket=str(base / 'absent.sock'))
        self.addCleanup(endpoint.close)
        facade = ClaudeMCP(inbox, bridge)

        class EndpointClient:
            def __init__(self, *args, **kwargs): pass
            def call(self, method, params): return endpoint.call(method, params)
            def close(self): pass

        with mock.patch.object(claude_desktop_rpc, 'Client', EndpointClient), \
             mock.patch('claude_cdp_pipe._chrome_bin', return_value=sys.executable), \
             mock.patch.object(claude_desktop_quick, '_probe_socket', return_value=False), \
             mock.patch.object(claude_desktop_quick, '_spawn_applyd', return_value=True), \
             mock.patch.object(endpoint, '_socket_apply', return_value={'stage': 'envoyé', 'click_observed': True}):
            receipt = claude_desktop.review(project, 'Question partagée', chat_id=CHAT_ID,
                                            request_key='shared-key', tasks=bridge.desktop_tasks)
        self.assertTrue(receipt['dispatch_ok'])
        def call(name, args):
            reply = facade.dispatch({'method': 'tools/call', 'params': {'name': name, 'arguments': args}})
            return json.loads(reply['content'][0]['text'])
        claimed = call('get_task', {'request_key': 'shared-key', 'claim_key': 'claim-key'})
        self.assertEqual(claimed['delivery'], 'handed_over')
        self.assertIn('Question partagée', claimed['prompt'])
        call('submit_result', {'request_key': 'shared-key', 'attempt_id': claimed['attempt_id'], 'answer': 'Résultat partagé'})
        self.assertEqual(bridge.desktop_tasks.result('shared-key', view='full')['answer'], 'Résultat partagé')
        self.assertFalse((project / 'quick-state' / 'quick.sqlite').exists())

    def test_concurrent_reviews_of_same_key_dispatch_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from desktop_task_store import TaskStore
        barrier = threading.Barrier(2, timeout=5)
        calls = []
        original_admit = TaskStore.admit

        def synchronized_admit(store, *args, **kwargs):
            outcome = original_admit(store, *args, **kwargs)
            barrier.wait()
            return outcome

        class Client(_FakeRpcClient):
            def call(self, method, params):
                calls.append(method)
                return super().call(method, params)

        def run():
            store = TaskStore(self.directory.name, self.directory.name)
            try:
                return claude_desktop.review(self.directory.name, 'Question orchestrée',
                                             request_key='race-key', tasks=store)
            finally:
                store.close()

        with mock.patch.object(TaskStore, 'admit', synchronized_admit), \
             mock.patch.object(claude_desktop_rpc, 'Client', Client), \
             ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run(), range(2)))
        self.assertEqual(calls.count('quick_entry/submit'), 1)
        self.assertEqual(calls.count('quick_entry/dispatch'), 1)
        self.assertCountEqual([r['status'] for r in results], ['finished', 'indeterminate'])


if __name__ == '__main__':
    unittest.main()
