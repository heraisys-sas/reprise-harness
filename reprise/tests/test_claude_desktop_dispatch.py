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
import claude_desktop_quick
import claude_desktop_rpc
from claude_desktop_quick import Endpoint


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
                         dispatch_raises=None, inserted=True):
    """Patch the claude_desktop_apply primitives the daemon dispatch uses."""
    state_calls = {'n': 0}
    insert_calls = getattr(patched_apply_module, 'insert_calls', None)
    if insert_calls is None:
        insert_calls = []
        patched_apply_module.insert_calls = insert_calls
    del insert_calls[:]

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

    def fake_insert(cdp, text):
        patched_apply_module.insert_calls.append((cdp, text))
        return {'inserted': inserted, 'length': len(text)}

    patchers = [mock.patch.object(claude_desktop_apply, 'CDP', FakeCDP),
                mock.patch.object(claude_desktop_apply, 'state', fake_state),
                mock.patch.object(claude_desktop_apply, 'wait_composer', lambda cdp: True),
                mock.patch.object(claude_desktop_apply, '_prepare', fake_prepare),
                mock.patch.object(claude_desktop_apply, '_check', lambda info, path: None),
                mock.patch.object(claude_desktop_apply, '_insert', fake_insert),
                mock.patch.object(claude_desktop_apply, '_dispatch', fake_dispatch)]
    for patcher in patchers:
        patcher.start()
    return patchers


class FakePipe:
    """Faux transport CDP pipe (fd 3/4) : enregistre les appels, jamais de réseau."""

    def __init__(self, fail_first_pick=False):
        self.calls = []
        self.pick_attempts = 0
        self.fail_first_pick = fail_first_pick

    def pick_and_attach(self, chat_id):
        self.pick_attempts += 1
        if self.fail_first_pick and self.pick_attempts == 1:
            raise RuntimeError(f'Page claude.ai/chat/{chat_id} introuvable')
        self.calls.append(('pick_and_attach', chat_id))
        return 'session-1'

    def call(self, method, params=None):
        self.calls.append((method, params))
        return {}

    def insert_text(self, text):
        self.calls.append(('Input.insertText', {'text': text}))

    def close(self):
        self.calls.append(('close', None))


class FakeProc:
    pid = 4242

    def poll(self):
        return None


class FakeCdpModule:
    """Faux module claude_cdp_pipe : launch/terminate tracés, jamais de Chrome."""

    def __init__(self, fail_first_pick=False, chrome_exists=True, busy=None):
        self.pipe = FakePipe(fail_first_pick)
        self.launched = []
        self.terminated = []
        self.chrome_exists = chrome_exists
        self.busy = busy

    def _profile_dir(self):
        return '/tmp/fake-claude-cdp-profile'

    def _hostname_pid_lock(self, profile):
        return (Path('/tmp/fake-claude-cdp.lock'), 'fake-host')

    def check_profile_busy(self, lock_path):
        return self.busy

    def _chrome_bin(self):
        return '/bin/echo' if self.chrome_exists else '/nonexistent/chrome'

    def launch_chrome(self, profile):
        self.launched.append(profile)
        return self.pipe, FakeProc()

    def terminate_chrome(self, proc, timeout=10.0):
        self.terminated.append(proc.pid)


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

    def endpoint(self):
        endpoint = Endpoint(self.directory.name, project_root=self.directory.name)
        self.addCleanup(endpoint.close)
        return endpoint

    def admit(self, endpoint, prompt='Tâche orchestrée pour dispatch'):
        key = str(uuid.uuid4())
        endpoint.submit({'request_key': key, 'prompt': prompt, 'chatId': CHAT_ID})
        return key

    def fingerprint(self, prompt):
        return hashlib.sha256(
            claude_desktop_apply.canonical_text(prompt.strip()).encode()).hexdigest()

    # -- capabilities ---------------------------------------------------------

    def test_capabilities_refuse_unscoped_dispatch(self):
        endpoint = Endpoint(self.directory.name)
        self.addCleanup(endpoint.close)
        with mock.patch.object(claude_desktop_quick, '_cdp_pipe', return_value=FakeCdpModule()):
            self.assertFalse(endpoint.call('quick_entry/capabilities', {})['dispatch_supported'])

    def test_capabilities_report_cdp_pipe_channel(self):
        endpoint = self.endpoint()
        fake = FakeCdpModule()
        with mock.patch.object(claude_desktop_quick, '_cdp_pipe', return_value=fake):
            caps = endpoint.call('quick_entry/capabilities', {})
        self.assertEqual(caps['dispatch_channel'], 'cdp-pipe')
        self.assertTrue(caps['dispatch_supported'])
        self.assertNotIn('dispatch_socket', caps)
        self.assertEqual(fake.launched, [])

    def test_capabilities_refuse_missing_chrome(self):
        endpoint = self.endpoint()
        fake = FakeCdpModule(chrome_exists=False)
        with mock.patch.object(claude_desktop_quick, '_cdp_pipe', return_value=fake):
            caps = endpoint.call('quick_entry/capabilities', {})
        self.assertFalse(caps['dispatch_supported'])

    # -- dispatch over CDP pipes ----------------------------------------------

    def patch_cdp(self, **kwargs):
        fake = FakeCdpModule(**kwargs)
        patcher = mock.patch.object(claude_desktop_quick, '_cdp_pipe', return_value=fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake

    def test_dispatch_click_observed_is_ok(self):
        prompt = 'Tâche orchestrée pour dispatch'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=self.fingerprint(prompt))],
            dispatch_outcome=claude_desktop_apply._result('envoyé', ok=True,
                                                          dispatch_attempted=True, click_observed=True))
        fake = self.patch_cdp()
        endpoint = self.endpoint()
        key = self.admit(endpoint, prompt)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertTrue(result['dispatch_attempted'])
        self.assertTrue(result['dispatch_ok'])
        self.assertEqual(result['dispatch_channel'], 'cdp-pipe')
        self.assertEqual(result['stage'], 'envoyé')
        self.assertIs(result['click_observed'], True)
        self.assertTrue(result['admission_only'])
        # pointer inserted once via the event-driven _insert primitive (#155);
        # single attach, no cold-start retarget
        self.assertIn((fake.pipe, claude_desktop_quick.task_pointer(key).strip()),
                      patched_apply_module.insert_calls)
        self.assertEqual(fake.pipe.pick_attempts, 1)
        self.assertNotIn(('Target.createTarget', {'url': 'https://claude.ai/chat/' + CHAT_ID}), fake.pipe.calls)
        self.assertEqual(fake.terminated, [])  # success preserves the daemon-owned Chrome

    def test_dispatch_pushes_pointer_not_full_prompt(self):
        # #155: the composer receives the short get_task pointer only; the
        # full prompt stays in the TaskStore as get_task's single source.
        prompt = 'Consigne complète qui ne doit PAS aller dans le composeur'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=None)],
            dispatch_outcome=claude_desktop_apply._result('envoyé', ok=True,
                                                          dispatch_attempted=True, click_observed=True))
        self.patch_cdp()
        endpoint = self.endpoint()
        key = self.admit(endpoint, prompt)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertTrue(result['dispatch_ok'])
        inserted = [text for _, text in patched_apply_module.insert_calls]
        self.assertEqual(inserted, [claude_desktop_quick.task_pointer(key).strip()])
        self.assertIn(json.dumps(key), inserted[0])
        self.assertNotIn(prompt, inserted[0])
        # get_task still returns the full prompt
        claimed = endpoint.tasks.claim(key, 'claim-pointer')
        self.assertEqual(claimed["prompt"], prompt)

    def test_submit_delegation_relays_pointer_but_admits_full_prompt(self):
        # #155: the durable submission record (what the relay driver turns into
        # a chat message) carries the pointer; the TaskStore keeps the full
        # prompt as get_task's single source.
        self.patch_apply(state_sequence=[composer_state()])
        self.patch_cdp()
        endpoint = self.endpoint()
        key = str(uuid.uuid4())
        endpoint.submit({'request_key': key, 'prompt': 'Consigne delegation',
                         'chatId': CHAT_ID, 'purpose': 'delegation'})
        row = endpoint.db.execute(
            'SELECT payload FROM quick_submissions WHERE scope=?',
            (endpoint.scope,)).fetchone()
        delivered = json.loads(row[0])
        self.assertEqual(delivered['text'].strip(),
                         claude_desktop_quick.task_pointer(key).strip())
        self.assertIn(json.dumps(key), delivered['text'])
        self.assertNotIn('Consigne delegation', delivered['text'])
        self.assertEqual(endpoint.tasks.claim(key, "claim-same")["prompt"], "Consigne delegation")

    def test_dispatch_cold_start_creates_target_then_attaches(self):
        prompt = 'Démarrage à froid'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=self.fingerprint(prompt))],
            dispatch_outcome=claude_desktop_apply._result('envoyé', ok=True,
                                                          dispatch_attempted=True, click_observed=True))
        fake = self.patch_cdp(fail_first_pick=True)
        endpoint = self.endpoint()
        key = self.admit(endpoint, prompt)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertTrue(result['dispatch_ok'])
        self.assertEqual(fake.pipe.pick_attempts, 2)
        self.assertIn(('Target.createTarget', {'url': 'https://claude.ai/chat/' + CHAT_ID}), fake.pipe.calls)

    def test_dispatch_profile_busy_is_refused_without_attempt(self):
        self.patch_apply(state_sequence=[composer_state()])
        fake = self.patch_cdp(busy={'pid': 1234, 'lock_mtime': 1.0})
        endpoint = self.endpoint()
        key = self.admit(endpoint, 'Profil occupé')
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertFalse(result['dispatch_attempted'])
        self.assertEqual(result['stage'], 'profile_busy')
        self.assertEqual(fake.launched, [])

    def test_dispatch_stage_error_fails_and_terminates_chrome(self):
        self.patch_apply(state_sequence=[composer_state(url='https://claude.ai/login')])
        fake = self.patch_cdp()
        endpoint = self.endpoint()
        key = self.admit(endpoint, 'Requête sur page login')
        with mock.patch.object(claude_desktop_apply, '_check',
                               lambda info, path: 'session'):
            result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'session')
        self.assertEqual(fake.terminated, [4242])

    def test_dispatch_lost_reply_is_uncertain_and_preserves_chrome(self):
        prompt = 'Requête à réponse perdue'
        self.patch_apply(
            state_sequence=[composer_state(), composer_state(text_sha256=self.fingerprint(prompt))],
            dispatch_raises=claude_desktop_apply.WSError('Réponse CDP perdue'))
        fake = self.patch_cdp()
        endpoint = self.endpoint()
        key = self.admit(endpoint, prompt)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'incertain')
        self.assertNotIn('click_observed', result)
        self.assertEqual(fake.terminated, [])  # a possible click preserves Chrome

    def test_dispatch_chrome_missing_is_uncertain(self):
        endpoint = self.endpoint()
        key = self.admit(endpoint, 'Sans Chrome')
        self.patch_cdp(chrome_exists=False)
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'incertain')

    def test_dispatch_missing_chat_id_fails_without_chrome(self):
        endpoint = self.endpoint()
        key = str(uuid.uuid4())
        endpoint.submit({'request_key': key, 'prompt': 'Sans chatId'})
        fake = self.patch_cdp()
        result = endpoint.call('quick_entry/dispatch', {'request_key': key})
        self.assertFalse(result['dispatch_ok'])
        self.assertEqual(result['stage'], 'cible')
        self.assertEqual(fake.launched, [])

    def test_dispatch_unknown_request_key_refused(self):
        endpoint = self.endpoint()
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'inconnu'})

    def test_dispatch_refuses_extra_fields(self):
        endpoint = self.endpoint()
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'k', 'images': ['a']})
        with self.assertRaises(ValueError):
            endpoint.call('quick_entry/dispatch', {'request_key': 'k', 'filePaths': ['file:///x']})


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
        _FakeRpcClient.capabilities = {'dispatch_supported': True, 'dispatch_channel': 'cdp-pipe'}
        _FakeRpcClient.dispatch = {'dispatch_ok': True, 'stage': 'envoyé', 'click_observed': True,
                                   'dispatch_channel': 'cdp-pipe'}

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
        self.assertEqual(result['dispatch_channel'], 'cdp-pipe')
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
        self.review()
        self.assertEqual(_FakeRpcClient.last.calls.count('quick_entry/dispatch'), 1)
        again = self.review()
        self.assertEqual(again['status'], 'indeterminate')
        self.assertNotIn('quick_entry/dispatch', _FakeRpcClient.last.calls)

    def test_review_cold_start_invokes_real_endpoint_dispatch_once(self):
        from desktop_task_store import TaskStore
        state = self.directory.name
        endpoint = Endpoint(state, project_root=state)
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
             mock.patch.object(claude_desktop_quick, '_cdp_pipe', return_value=FakeCdpModule()):
            patchers = patched_apply_module(
                state_sequence=[composer_state()],
                dispatch_outcome=claude_desktop_apply._result(
                    'envoyé', ok=True, dispatch_attempted=True, click_observed=True))
            try:
                result = claude_desktop.review(state, 'Question orchestrée', request_key='cold-key',
                                               chat_id=CHAT_ID, tasks=tasks)
                replay = endpoint.call('quick_entry/dispatch', {'request_key': 'cold-key'})
            finally:
                for patcher in patchers:
                    patcher.stop()
        self.assertTrue(result['dispatch_ok'])
        self.assertTrue(result['click_observed'])
        self.assertTrue(replay['dispatch_ok'])
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

    def test_profile_busy_receipt_is_retryable_other_receipts_stay_frozen(self):
        # A profile_busy receipt is a busy-route state, not a terminal error
        # (decision 2026-11-05): the same request_key dispatches again. Any
        # other recorded outcome keeps frozen idempotence semantics.
        from desktop_task_store import TaskStore
        store = TaskStore(self.directory.name, self.directory.name)
        self.addCleanup(store.close)
        for key, receipt, replay in (('busy-key', {'stage': 'profile_busy'}, 'reserved'),
                                     ('sent-key', {'stage': 'envoyé', 'clicked': True}, 'recorded')):
            store.admit(key, 'Question test', CHAT_ID)
            store.reserve_socket_dispatch(key)
            store.record_socket_dispatch(key, receipt)
            self.assertEqual(store.reserve_socket_dispatch(key)['state'], replay)
        store.record_socket_dispatch('busy-key', {'stage': 'envoyé', 'clicked': True})
        self.assertEqual(store.reserve_socket_dispatch('busy-key')['state'], 'recorded')

    def test_uncertain_pipe_result_is_recorded_and_never_retried(self):
        endpoint = Endpoint(self.directory.name, project_root=self.directory.name)
        self.addCleanup(endpoint.close)
        endpoint.submit({'request_key': 'uncertain-key', 'prompt': 'Question test', 'chatId': CHAT_ID})
        with mock.patch.object(claude_desktop_quick, '_cdp_pipe', return_value=FakeCdpModule()):
            patchers = patched_apply_module(
                state_sequence=[composer_state(), composer_state()],
                dispatch_raises=claude_desktop_apply.WSError('Réponse CDP perdue'))
            try:
                first = endpoint.call('quick_entry/dispatch', {'request_key': 'uncertain-key'})
                replay = endpoint.call('quick_entry/dispatch', {'request_key': 'uncertain-key'})
            finally:
                for patcher in patchers:
                    patcher.stop()
        self.assertEqual(first, replay)
        self.assertEqual(first['stage'], 'incertain')

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
        self.addCleanup(db.close)
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
        endpoint = Endpoint(state, project_root=project)
        self.addCleanup(endpoint.close)
        facade = ClaudeMCP(inbox, bridge)

        class EndpointClient:
            def __init__(self, *args, **kwargs): pass
            def call(self, method, params): return endpoint.call(method, params)
            def close(self): pass

        with mock.patch.object(claude_desktop_rpc, 'Client', EndpointClient), \
             mock.patch.object(claude_desktop_quick, '_cdp_pipe', return_value=FakeCdpModule()):
            patchers = patched_apply_module(
                state_sequence=[composer_state()],
                dispatch_outcome=claude_desktop_apply._result(
                    'envoyé', ok=True, dispatch_attempted=True, click_observed=True))
            try:
                receipt = claude_desktop.review(project, 'Question partagée', chat_id=CHAT_ID,
                                                request_key='shared-key', tasks=bridge.desktop_tasks)
            finally:
                for patcher in patchers:
                    patcher.stop()
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
