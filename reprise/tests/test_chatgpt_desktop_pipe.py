"""Offline contract tests for the dedicated ChatGPT Desktop CDP pipe probe.

No ChatGPT.app launch, operator session, network access or real prompt.
"""
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

# Sharded CI runs each module in its own process (ci_test.py): the parent
# path cannot rely on another module having inserted it first (#236).
sys.path.insert(0, str(Path(__file__).parents[1]))
import chatgpt_desktop_pipe as adapter
import claude_cdp_pipe

# Standard long reply shared by the confirmed-capture fixtures: must stay above
# adapter.MIN_USEFUL_REPLY_CHARS non-blank chars (issue #228).
REPLY_FIXTURE_BODY = ('Assistant reply body with enough useful content to pass '
                      'the usable-reply threshold of the capture contract.')


class TestCdpContract(unittest.TestCase):
    def test_chatgpt_arguments_do_not_expose_devtools_tcp_or_codex(self):
        args = adapter.build_args(Path('/tmp/reprise-profile-fixture'))
        self.assertIn('--remote-debugging-pipe', args)
        self.assertIn('--user-data-dir=/tmp/reprise-profile-fixture', args)
        self.assertFalse(any('remote-debugging-port' in arg for arg in args))
        self.assertFalse(any('codex' in arg.lower() for arg in args))

    def test_main_target_selection_is_exact_and_unique(self):
        valid = {'targetId': 'main', 'type': 'page', 'url': 'app://-/index.html'}
        self.assertEqual(adapter.select_main([valid]), ('main', 'ready'))
        self.assertEqual(adapter.select_main([valid, valid])[1], 'target_ambiguous')
        self.assertEqual(adapter.select_main([dict(valid, url='https://chatgpt.com/')])[1], 'target_missing')
        self.assertEqual(adapter.select_main([dict(valid, type='webview')])[1], 'target_missing')
        self.assertEqual(adapter.select_main([dict(valid, targetId='')])[1], 'target_unidentified')

    def test_census_attaches_one_target_without_reading_messages(self):
        transport = mock.MagicMock()
        transport.call.return_value = {'targetInfos': [
            {'targetId': 'main', 'type': 'page', 'url': adapter.MAIN_URL}]}
        transport.evaluate.return_value = {'href': adapter.MAIN_URL, 'ready': 'complete',
                                           'composer_count': 1}
        report = adapter.census(transport)
        self.assertEqual(report['status'], 'ready')
        transport.attach_to.assert_called_once_with('main')
        self.assertNotIn('innerText', adapter.RENDERER_PROBE)
        self.assertNotIn('Input.dispatchKeyEvent', adapter.RENDERER_PROBE)
        transport.evaluate.assert_called_once_with(adapter.RENDERER_PROBE)

    def test_census_refuses_missing_or_ambiguous_target_without_attach(self):
        transport = mock.MagicMock()
        transport.call.return_value = {'targetInfos': [
            {'targetId': 'one', 'type': 'page', 'url': adapter.MAIN_URL},
            {'targetId': 'two', 'type': 'page', 'url': adapter.MAIN_URL}]}
        self.assertEqual(adapter.census(transport)['status'], 'target_ambiguous')
        transport.attach_to.assert_not_called()

    def test_census_never_calls_a_send_method(self):
        transport = mock.MagicMock()
        transport.call.return_value = {'targetInfos': [
            {'targetId': 'main', 'type': 'page', 'url': adapter.MAIN_URL}]}
        transport.evaluate.return_value = {'href': adapter.MAIN_URL, 'composer_count': 0}
        result = adapter.census(transport)
        self.assertEqual(result['status'], 'selectors_stale')
        self.assertEqual(transport.call.call_args.args[0], 'Target.getTargets')
        self.assertNotIn('dispatch', str(transport.mock_calls).lower())
        self.assertNotIn('insertText', str(transport.mock_calls))


class TestOwnedProfile(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'profiles'
        self.binary = self.base / 'ChatGPT'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)

    def test_inspect_does_not_create_profile_or_spawn(self):
        report = adapter.inspect(self.binary, self.root, 'dedicated')
        self.assertEqual(report['status'], 'inspect_only')
        self.assertFalse(report['launch_attempted'])
        self.assertFalse(self.root.exists())

    def test_profile_names_and_symlinks_refused(self):
        for name in ('', '.', '../default', 'x/y', 'has space', 'a' * 65):
            with self.subTest(name=name), self.assertRaises(ValueError):
                adapter.profile_path(self.root, name)
        self.root.mkdir(mode=0o700)
        (self.root / 'alias').symlink_to(self.base)
        with self.assertRaises(ValueError):
            adapter.profile_path(self.root, 'alias')

    def test_probe_only_owns_spawned_process_and_cleans_lock(self):
        transport = mock.MagicMock()
        process = mock.Mock(pid=123456)
        process.poll.return_value = None
        response = {'status': 'ready', 'transport': 'cdp-pipe', 'ready': True}
        with mock.patch.object(adapter, 'launch_cdp_process', return_value=(transport, process)) as launch, \
             mock.patch.object(adapter, 'census', return_value=response), \
             mock.patch.object(adapter, 'terminate_chrome') as terminate:
            result = adapter.probe(self.binary, self.root, 'dedicated')
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['launch_attempted'])
        self.assertFalse(result['operator_isolation_verified'])
        self.assertTrue(self.root.joinpath('dedicated').is_dir())
        self.assertFalse(self.root.joinpath('.dedicated.cdp-pipe.lock').exists())
        self.assertEqual(stat.S_IMODE((self.root / 'dedicated').stat().st_mode), 0o700)
        command = launch.call_args.args
        self.assertEqual(command[0], self.binary)
        self.assertIn('--remote-debugging-pipe', command[1])
        terminate.assert_called_once_with(process, timeout=3)
        transport._reader.close.assert_called_once()
        transport._writer.close.assert_called_once()

    def test_launch_failure_is_bounded_and_does_not_leave_lock(self):
        with mock.patch.object(adapter, 'launch_cdp_process',
                               side_effect=RuntimeError('cannot spawn')):
            result = adapter.probe(self.binary, self.root, 'dedicated')
        self.assertEqual(result['status'], 'launch_failed')
        self.assertEqual(result['reason'], 'RuntimeError')
        self.assertFalse(self.root.joinpath('.dedicated.cdp-pipe.lock').exists())
        self.assertFalse(result['operator_isolation_verified'])

    def test_missing_target_is_retried_without_a_second_launch(self):
        process = mock.Mock(pid=123457)
        process.poll.return_value = None
        transport = mock.MagicMock()
        with mock.patch.object(adapter, 'launch_cdp_process',
                               return_value=(transport, process)) as launch, \
             mock.patch.object(adapter, 'census',
                               side_effect=[{'status': 'target_missing', 'ready': False},
                                            {'status': 'ready', 'ready': True}]) as census, \
             mock.patch.object(adapter.time, 'sleep') as sleeper, \
             mock.patch.object(adapter, 'terminate_chrome'):
            result = adapter.probe(self.binary, self.root, 'dedicated')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(census.call_count, 2)
        self.assertEqual(launch.call_count, 1)
        sleeper.assert_called_once_with(0.25)

    def test_existing_profile_lock_refuses_launch_without_adopting(self):
        self.root.mkdir(mode=0o700)
        lock = self.root / '.dedicated.cdp-pipe.lock'
        lock.write_text('other-owner')
        with mock.patch.object(adapter, 'launch_cdp_process') as launch:
            with self.assertRaises(FileExistsError):
                adapter.probe(self.binary, self.root, 'dedicated')
        launch.assert_not_called()
        self.assertEqual(lock.read_text(), 'other-owner')

    def test_unprivate_root_fails_closed(self):
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            adapter.probe(self.binary, self.root, 'dedicated')

    def test_missing_binary_does_not_create_profile(self):
        report = adapter.probe(self.base / 'missing', self.root, 'dedicated')
        self.assertEqual(report['status'], 'binary_unavailable')
        self.assertFalse(report['launch_attempted'])
        self.assertFalse(self.root.exists())


class TestReChatGPTLaunch(unittest.TestCase):
    """Contract of the Dashboard button: same launch mechanics as the bridge.

    The dedicated clone is launched over --remote-debugging-pipe with the
    dedicated profile; our pipe ends are closed without killing the child;
    'launched' is never conflated with a verified ChatGPT screen.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'profiles'
        self.binary = self.base / 'ReChatGPT'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)

    def test_dedicated_resolvers_point_at_clone_and_profile(self):
        clone = self.base / 'ReChatGPT.app'
        clone.write_text('')
        with mock.patch.object(adapter, 'RECHATGPT_BINARY', clone):
            self.assertEqual(adapter.dedicated_binary(), clone)
            clone.unlink()
            self.assertEqual(adapter.dedicated_binary(), adapter.DEFAULT_BINARY)
        self.assertEqual(adapter.dedicated_profile(self.root), self.root / 'dedicated')

    def test_launch_uses_clone_binary_with_bridge_args_and_verified_screen(self):
        transport = mock.MagicMock()
        process = mock.Mock(pid=123458)
        process.poll.return_value = None
        with mock.patch.object(adapter, 'dedicated_binary', return_value=self.binary), \
             mock.patch.object(adapter, 'launch_cdp_process',
                               return_value=(transport, process)) as launch, \
             mock.patch.object(adapter, 'census',
                               return_value={'status': 'ready', 'ready': True}), \
             mock.patch.object(adapter, 'terminate_chrome') as terminate:
            result = adapter.launch_rechatgpt(profile_root=self.root)
        self.assertEqual(result['status'], 'launched')
        self.assertTrue(result['verified'])
        self.assertEqual(result['pid'], 123458)
        command = launch.call_args.args
        self.assertEqual(command[0], self.binary)
        self.assertEqual(command[1], ['--remote-debugging-pipe',
                                      f'--user-data-dir={self.root / "dedicated"}',
                                      '--no-first-run'])
        self.assertTrue(self.root.joinpath('dedicated').is_dir())
        self.assertEqual(stat.S_IMODE((self.root / 'dedicated').stat().st_mode), 0o700)
        self.assertFalse(self.root.joinpath('.dedicated.cdp-pipe.lock').exists())
        terminate.assert_not_called()
        transport.close.assert_called_once()

    def test_screen_never_registered_is_launched_but_unverified(self):
        transport = mock.MagicMock()
        transport.call.return_value = {'targetId': 'created-1'}
        process = mock.Mock(pid=123459)
        process.poll.return_value = None
        with mock.patch.object(adapter, 'dedicated_binary', return_value=self.binary), \
             mock.patch.object(adapter, 'launch_cdp_process',
                               return_value=(transport, process)), \
             mock.patch.object(adapter, 'census',
                               return_value={'status': 'target_missing', 'ready': False}), \
             mock.patch.object(adapter, 'time', AcceleratedTime()):
            result = adapter.launch_rechatgpt(profile_root=self.root, timeout=5.0)
        self.assertEqual(result['status'], 'launched_screen_unverified')
        self.assertFalse(result['verified'])
        self.assertEqual(result['census_status'], 'target_missing')
        transport.call.assert_called_once_with('Target.createTarget', {'url': adapter.MAIN_URL})

    def test_create_target_failure_stays_unverified_without_raising(self):
        transport = mock.MagicMock()
        transport.call.side_effect = EOFError('pipe closed')
        process = mock.Mock(pid=123460)
        process.poll.return_value = None
        with mock.patch.object(adapter, 'dedicated_binary', return_value=self.binary), \
             mock.patch.object(adapter, 'launch_cdp_process',
                               return_value=(transport, process)), \
             mock.patch.object(adapter, 'census',
                               return_value={'status': 'target_missing', 'ready': False}), \
             mock.patch.object(adapter, 'time', AcceleratedTime()):
            result = adapter.launch_rechatgpt(profile_root=self.root, timeout=0.0)
        self.assertEqual(result['status'], 'launched_screen_unverified')
        self.assertFalse(result['verified'])
        self.assertEqual(result['detail'], 'createTarget_failed:EOFError')
        self.assertFalse(self.root.joinpath('.dedicated.cdp-pipe.lock').exists())

    def test_ambiguous_main_page_is_refused_as_success(self):
        transport = mock.MagicMock()
        process = mock.Mock(pid=123461)
        process.poll.return_value = None
        with mock.patch.object(adapter, 'dedicated_binary', return_value=self.binary), \
             mock.patch.object(adapter, 'launch_cdp_process',
                               return_value=(transport, process)), \
             mock.patch.object(adapter, 'census',
                               return_value={'status': 'target_ambiguous', 'ready': False}), \
             mock.patch.object(adapter, 'time', AcceleratedTime()):
            result = adapter.launch_rechatgpt(profile_root=self.root, timeout=0.0)
        self.assertEqual(result['status'], 'launched_screen_unverified')
        self.assertFalse(result['verified'])
        self.assertEqual(result['census_status'], 'target_ambiguous')
        transport.call.assert_not_called()

    def test_pipe_failure_during_census_closes_transport_without_killing_child(self):
        transport = mock.MagicMock()
        process = mock.Mock(pid=123462)
        process.poll.return_value = None
        with mock.patch.object(adapter, 'dedicated_binary', return_value=self.binary), \
             mock.patch.object(adapter, 'launch_cdp_process',
                               return_value=(transport, process)), \
             mock.patch.object(adapter, 'census', side_effect=EOFError('pipe closed')), \
             mock.patch.object(adapter, 'terminate_chrome') as terminate:
            result = adapter.launch_rechatgpt(profile_root=self.root, timeout=0.0)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['detail'], 'pipe_unavailable:EOFError')
        self.assertEqual(result['pid'], 123462)
        self.assertFalse(result['verified'])
        terminate.assert_not_called()
        transport.close.assert_called_once()
        self.assertFalse(self.root.joinpath('.dedicated.cdp-pipe.lock').exists())

    def test_immediate_child_exit_is_reported(self):
        transport = mock.MagicMock()
        process = mock.Mock(pid=123463)
        process.poll.return_value = 3
        process.returncode = 3
        with mock.patch.object(adapter, 'dedicated_binary', return_value=self.binary), \
             mock.patch.object(adapter, 'launch_cdp_process',
                               return_value=(transport, process)):
            result = adapter.launch_rechatgpt(profile_root=self.root)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['detail'], 'exited_immediately_code_3')
        self.assertFalse(result['verified'])
        transport.close.assert_called_once()
        self.assertFalse(self.root.joinpath('.dedicated.cdp-pipe.lock').exists())

    def test_launch_failure_is_bounded_and_cleans_lock(self):
        with mock.patch.object(adapter, 'dedicated_binary', return_value=self.binary), \
             mock.patch.object(adapter, 'launch_cdp_process',
                               side_effect=RuntimeError('cannot spawn')):
            result = adapter.launch_rechatgpt(profile_root=self.root)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['detail'], 'launch_failed:RuntimeError')
        self.assertFalse(result['verified'])
        self.assertFalse(self.root.joinpath('.dedicated.cdp-pipe.lock').exists())
        self.assertTrue(self.root.joinpath('dedicated').is_dir())

    def test_existing_profile_lock_refuses_dedicated_launch(self):
        self.root.mkdir(mode=0o700)
        lock = self.root / '.dedicated.cdp-pipe.lock'
        lock.write_text('other-owner')
        with mock.patch.object(adapter, 'dedicated_binary', return_value=self.binary), \
             mock.patch.object(adapter, 'launch_cdp_process') as launch:
            result = adapter.launch_rechatgpt(profile_root=self.root)
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['detail'], 'lock_busy')
        launch.assert_not_called()
        self.assertEqual(lock.read_text(), 'other-owner')

    def test_holder_active_reports_already_active_without_second_launch(self):
        holder = mock.MagicMock()
        with mock.patch.object(adapter, '_holder_connect', return_value=holder), \
             mock.patch.object(adapter, '_holder_exchange',
                               return_value={'status': 'ready', 'verified': True}) as exchange, \
             mock.patch.object(adapter, 'launch_cdp_process') as launch:
            result = adapter.launch_rechatgpt(profile_root=self.root)
        holder.close.assert_called_once()
        exchange.assert_called_once_with(holder, {'cmd': 'ensure'})
        self.assertEqual(result['status'], 'already_active')
        self.assertTrue(result['verified'])
        self.assertIsNone(result['pid'])
        launch.assert_not_called()
        self.assertFalse(self.root.joinpath('dedicated').exists())

    def test_holder_that_cannot_recover_chrome_reports_error(self):
        holder = mock.MagicMock()
        with mock.patch.object(adapter, '_holder_connect', return_value=holder), \
             mock.patch.object(adapter, '_holder_exchange',
                               return_value={'status': 'holder_not_ready',
                                             'verified': False}), \
             mock.patch.object(adapter, 'launch_cdp_process') as launch:
            result = adapter.launch_rechatgpt(profile_root=self.root)
        holder.close.assert_called_once()
        self.assertEqual(result['status'], 'error')
        self.assertFalse(result['verified'])
        self.assertEqual(result['detail'], 'holder_chrome_unrecoverable')
        launch.assert_not_called()
        self.assertFalse(self.root.joinpath('dedicated').exists())

    def test_live_holder_wins_the_race_without_a_second_process(self):
        transport = mock.MagicMock()
        with HolderHarness(transport, self.binary, self.root):
            with mock.patch.object(adapter, 'launch_cdp_process',
                                   return_value=(mock.MagicMock(), mock.Mock(pid=99))) as launch:
                result = adapter.launch_rechatgpt(profile_root=self.root)
        self.assertEqual(result['status'], 'already_active')
        launch.assert_not_called()


class TestServeBinaryResolution254(unittest.TestCase):
    """#254: the serve route resolves the clone for the dedicated profile.

    launch_cdp_process fails fast (OSError) so serve() returns
    'refused'/'launch_failed' and the captured call reveals the chosen binary.
    """

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'profiles'
        self.default = self.base / 'ChatGPT'
        self.clone = self.base / 'ReChatGPT'
        for binary in (self.default, self.clone):
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o700)

    def _serve_chosen_binary(self, binary, name, clone=None):
        with mock.patch.object(adapter, 'DEFAULT_BINARY', self.default), \
             mock.patch.object(adapter, 'RECHATGPT_BINARY',
                               self.clone if clone is None else clone), \
             mock.patch.object(adapter, 'launch_cdp_process',
                               side_effect=OSError('offline')) as launch:
            result = adapter.serve(binary=binary, profile_root=self.root, name=name)
        self.assertEqual(result, {'status': 'refused', 'reason': 'launch_failed'})
        return launch.call_args.args[0]

    def test_dedicated_prefers_clone_when_present(self):
        self.assertEqual(self._serve_chosen_binary(None, 'dedicated'), self.clone)

    def test_dedicated_falls_back_to_default_without_clone(self):
        self.assertEqual(self._serve_chosen_binary(None, 'dedicated',
                                                   clone=self.base / 'absent'),
                         self.default)

    def test_explicit_binary_wins_over_clone(self):
        explicit = self.base / 'Explicit'
        explicit.write_text('#!/bin/sh\nexit 0\n')
        explicit.chmod(0o700)
        self.assertEqual(self._serve_chosen_binary(explicit, 'dedicated'), explicit)

    def test_non_dedicated_uses_default_even_with_clone(self):
        self.assertEqual(self._serve_chosen_binary(None, 'default'), self.default)


class TestGenericPipeLauncher(unittest.TestCase):
    def test_real_fd_3_4_echo(self):
        """Run intrusive fd3/4 wiring in a bounded *separate* test process.

        A native descriptor or signal bug must not terminate the unittest
        runner, so CI can still report the exact failure/stderr.
        """
        inner = r'''
import json
import os
import sys
import claude_cdp_pipe

child_script = r"""
import json
import os
frame = bytearray()
while True:
    char = os.read(3, 1)
    if not char:
        break
    if char != b'\0':
        frame.extend(char)
        continue
    message = json.loads(frame)
    os.write(4, json.dumps({'id': message['id'],
                            'result': {'echo': message['method']}}).encode() + b'\0')
    frame.clear()
"""
transport, process = claude_cdp_pipe.launch_cdp_process(
    sys.executable, ['-u', '-c', child_script], label='synthetic')
try:
    assert transport.call('Browser.getVersion') == {'echo': 'Browser.getVersion'}
finally:
    claude_cdp_pipe.terminate_chrome(process, timeout=1)
    transport._reader.close()
    transport._writer.close()
print('CDP-PIPE-OK', flush=True)
'''
        proc = subprocess.run(
            [sys.executable, '-B', '-c', inner],
            capture_output=True, text=True, timeout=25, cwd=Path(__file__).parents[1])
        self.assertEqual(proc.returncode, 0,
                         f'inner launch rc={proc.returncode}; stdout={proc.stdout!r}; stderr={proc.stderr!r}')
        self.assertIn('CDP-PIPE-OK', proc.stdout)




class AcceleratedTime:
    """Issue #234: an accelerated clock handed to the adapter only.

    Patching `adapter.time.<attr>` rewrites the REAL global `time` module,
    which the test module shares: the HolderHarness readiness wait
    (`time.monotonic() + 10`) collapsed to ~10 ticks, `send()` saw no socket
    and spawned a detached holder (13 unreaped Popen). Exposing a shim as
    `adapter.time` accelerates only the adapter's own deadlines and keeps the
    test's clock, socket timeouts and sleeps real.
    """

    _tick = adapter.COMPLETION_POLL_INTERVAL_SECONDS

    def __init__(self, start=1000.0):
        self._now = start

    def monotonic(self):
        self._now += self._tick
        return self._now

    @staticmethod
    def sleep(_seconds):
        return None

    def __getattr__(self, name):
        return getattr(time, name)


class HolderHarness:
    """Run the real serve() loop offline: mocked launch/census, live socket."""

    def __init__(self, transport, binary, root, name='dedicated', census=None):
        self.transport = transport
        self.binary, self.root, self.name = binary, root, name
        self.process = mock.Mock(pid=81218)
        self.process.poll.return_value = None
        self.census = census if census is not None else (
            lambda transport: {'status': 'ready', 'ready': True})
        self.stop = threading.Event()
        self.result = None
        self.terminate = None
        self._patches = []

    def __enter__(self):
        self.terminate = mock.MagicMock()
        # Issue #234: _dispatch measures the 20 s stream-quiet window and the
        # settle window through time.monotonic — left alone, each quiet wait
        # sleeps the real window while busy-polling (~186 s and ~5.7 Go RSS
        # for TestRenderedConversationCapture). Accelerated clock, scoped to
        # the adapter so this harness' own readiness wait stays real.
        self._patches = [
            mock.patch.object(adapter, 'launch_cdp_process',
                              return_value=(self.transport, self.process)),
            mock.patch.object(adapter, 'census', side_effect=self.census),
            mock.patch.object(adapter.subprocess, 'run'),
            mock.patch.object(adapter, 'terminate_chrome', new=self.terminate),
            mock.patch.object(adapter, 'time', AcceleratedTime()),
        ]
        entered = []
        try:
            for patch in self._patches:
                patch.__enter__()
                entered.append(patch)
        except BaseException:
            # Issue #234: ne jamais laisser un patch entré (socket live, thread
            # non encore démarré) fuiter si un patch suivant échoue.
            for patch in reversed(entered):
                patch.__exit__(None, None, None)
            raise

        def run():
            self.result = adapter.serve(binary=self.binary, profile_root=self.root,
                                        name=self.name, stop=self.stop)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        path = adapter._holder_socket_path(self.root)
        while time.monotonic() < deadline:
            probe = adapter._holder_connect(path, timeout=0.2)
            if probe is not None:
                probe.close()
                break
            time.sleep(0.01)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop.set()
        self.thread.join(timeout=10)
        leaked = self.thread.is_alive()
        for patch in reversed(self._patches):
            patch.__exit__(None, None, None)
        # Issue #234: un thread serve() non joint est une fuite visible, pas un
        # silence ; masqué seulement si le test échouait déjà (pas de nouvelle
        # exception par-dessus l'exception en cours).
        if leaked and exc_type is None:
            raise AssertionError(
                'HolderHarness: thread serve() non jointe en 10 s — fuite de ressources')
        return False


class TestGuardedSend(unittest.TestCase):
    """All send tests go through the persistent holder; no Chrome launch."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        # Exposed so a hosting TestCase (TestRenderedConversationCapture reuses
        # this setUp on a plain object) can register the cleanup as well:
        # addCleanup on a non-TestCase never runs and the dir is then finalized
        # by gc (issue #234, "TemporaryDirectory implicitly cleaned up").
        self._folder = folder
        self.base = Path(folder.name)
        self.root = self.base / 'profiles'
        self.binary = self.base / 'ChatGPT'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)

    def fake(self, *, first=None, staged=None, click=None, body=None, after=None,
             sse=None, observer=True, quiet_ms=None, enter=None, mode=None,
             menu=None):
        transport = mock.MagicMock()
        transport.drain_notifications.return_value = sse if sse is not None else []
        first = first if first is not None else {'status': 'ready'}
        staged = staged if staged is not None else {'status': 'ready'}
        click = click if click is not None else {'clicked': True, 'status': 'clicked'}
        # Default rendered body: prompt echo, label, stable reply, footer. The
        # static page is mutation-quiet immediately (STREAM_QUIET_MS), which is
        # the live completeness evidence for an already-rendered reply. The
        # reply length stays above MIN_USEFUL_REPLY_CHARS (issue #228).
        default_body = ('Vous avez dit : synthetic request ChatGPT a dit : '
                        'Assistant reply body with enough useful content to pass '
                        'the usable-reply threshold of the capture contract. '
                        'ChatGPT peut faire des erreurs. '
                        'Réponse terminée')
        quiet_ms_default = adapter.STREAM_QUIET_MS
        clicked = {'done': False}
        states = {'poll': 0, 'mode_read': 0, 'menu_poll': 0}

        # Issue #248 LOT 2: answers for the mode-selector evaluations. Default
        # is an already-active ChatGPT trigger (zero clicks). A list feeds the
        # mode evaluations in call order (probe, trigger click, item click,
        # re-probe); shorthand strings expand to probe answers.
        mode = mode if mode is not None else 'chatgpt'

        def mode_answer(value):
            if isinstance(value, Exception):
                raise value
            if callable(value):
                value = value(states['mode_read'])
            if isinstance(value, str):
                if value == 'chatgpt':
                    return {'state': 'ChatGPT',
                            'candidates': [{'tag': 'button', 'text': 'ChatGPT',
                                            'aria_expanded': 'false',
                                            'data_state': 'closed',
                                            'rect': {'x': 62, 'y': 53,
                                                     'w': 84, 'h': 32}}]}
                if value == 'codex':
                    return {'state': 'Codex',
                            'candidates': [{'tag': 'button', 'text': 'Codex',
                                            'aria_expanded': 'false',
                                            'data_state': 'closed',
                                            'rect': {'x': 62, 'y': 53,
                                                     'w': 84, 'h': 32}}]}
                if value == 'missing':
                    return {'state': None, 'candidates': []}
                if value == 'ambiguous':
                    return {'state': 'ChatGPT',
                            'candidates': [{'tag': 'button', 'text': 'ChatGPT'},
                                           {'tag': 'button', 'text': 'Codex'}]}
                if value == 'unknown':
                    return {'state': 'Turbo',
                            'candidates': [{'tag': 'button', 'text': 'Turbo'}]}
            return value

        def next_mode():
            value = mode
            if isinstance(mode, list):
                value = mode[min(states['mode_read'], len(mode) - 1)]
            states['mode_read'] += 1
            return mode_answer(value)

        # Issue #248 correctif 2: menu-poll answers come from a stream of
        # their own, independent of the mode stream. Default: the Radix menu
        # is open on the first probe (one single menu evaluate per switch).
        menu = menu if menu is not None else {'open': True,
                                              'sample': {'items': 2,
                                                         'popper_present': True,
                                                         'popper_text': 'ChatGPT Codex'}}

        def next_menu():
            value = menu
            if isinstance(value, list):
                value = value[min(states['menu_poll'], len(value) - 1)]
            states['menu_poll'] += 1
            if isinstance(value, Exception):
                raise value
            if callable(value):
                return value(states['menu_poll'])
            return value

        def body_text():
            if isinstance(body, Exception):
                raise body
            if callable(body):
                return body(states['poll'])
            if isinstance(body, list):
                text = body[min(states['poll'], len(body) - 1)]
            else:
                text = default_body if body is None else body
            states['poll'] += 1
            return text

        def respond(expression):
            if 'document.title' in expression:
                return adapter.WINDOW_TITLE
            if 'return composerStatus(null);' in expression:
                if clicked['done'] and after is not None:
                    return after
                return first
            if 'return composerStatus(' in expression:
                return staged
            if 'return focusUniqueComposer(' in expression:
                return True
            if 'return selectComposerContent(' in expression:
                return True
            if 'return clickUniqueSend(' in expression:
                clicked['done'] = True
                if isinstance(click, Exception):
                    raise click
                return click
            if 'return bodyProbe();' in expression:
                ms = quiet_ms(states['poll']) if callable(quiet_ms) else \
                    (quiet_ms if quiet_ms is not None else quiet_ms_default)
                rendered = body_text()
                probe = {'url': adapter.MAIN_URL, 'ready': 'complete',
                         'stream_len': 1 if observer else 0, 'quiet_ms': ms}
                return probe | (rendered if isinstance(rendered, dict) else {'text': rendered})
            if '__reprise_observer' in expression:
                return 'armed' if observer else None
            if '__reprise_mode_probe' in expression:
                return next_mode()
            if '__reprise_mode_trigger' in expression:
                return next_mode()
            if '__reprise_mode_menu' in expression:
                return next_menu()
            if '__reprise_mode_item' in expression:
                return next_mode()
            raise AssertionError('Unexpected Runtime.evaluate')

        def respond_call(method, params):
            if method == 'Input.dispatchKeyEvent':
                clicked['done'] = True
                if isinstance(enter, Exception):
                    raise enter
                return {}
            if method == 'Target.getTargets':
                return {'targetInfos': []}
            if method == 'Target.closeTarget':
                return {}
            self.assertEqual(method, 'Network.enable')
            return {}

        transport.evaluate.side_effect = respond
        transport.call.side_effect = respond_call
        return transport

    def invoke(self, transport, request='req-218'):
        with HolderHarness(transport, self.binary, self.root):
            result = adapter.send('synthetic request', self.binary, self.root,
                                  'dedicated', request_key=request)
        self.assertFalse(result['operator_isolation_verified'])
        return result

    def test_prompt_source_validation_without_launch(self):
        missing = self.base / 'missing'
        self.assertEqual(adapter._read_prompt(str(missing))[1], 'prompt_unreadable')
        blank = self.base / 'blank'
        blank.write_text(' \n ')
        self.assertEqual(adapter._read_prompt(str(blank))[1], 'prompt_required')
        large = self.base / 'large'
        large.write_text('a' * 20001)
        self.assertEqual(adapter._read_prompt(str(large))[1], 'prompt_too_large')
        max_size = self.base / 'max'
        max_size.write_text('🍇' * 20000, encoding='utf-8')
        self.assertEqual(len(adapter._read_prompt(str(max_size))[0]), 20000)

    def test_cli_prompt_absent_and_empty_are_usage_64(self):
        with mock.patch.object(adapter, 'launch_cdp_process') as launch, \
             mock.patch('sys.stdout', new_callable=io.StringIO) as output, \
             mock.patch('sys.stderr', new_callable=io.StringIO) as error:
            self.assertEqual(adapter.main(['send']), 64)
            self.assertEqual(json.loads(output.getvalue())['reason'], 'prompt_required')
            self.assertIn('usage:', error.getvalue())
        launch.assert_not_called()
        empty = self.base / 'empty'
        empty.write_text('')
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(adapter.main(['send', '--prompt-file', str(empty)]), 64)
            self.assertEqual(json.loads(output.getvalue())['status'], 'refused')

    def test_cli_overlarge_is_refused_exit_2(self):
        huge = self.base / 'huge'
        huge.write_text('a' * 20001)
        with mock.patch('sys.stdout', new_callable=io.StringIO) as output, \
             mock.patch.object(adapter, 'launch_cdp_process') as launch:
            self.assertEqual(adapter.main(['send', '--prompt-file', str(huge)]), 2)
        self.assertEqual(json.loads(output.getvalue())['reason'], 'prompt_too_large')
        launch.assert_not_called()

    def test_selectors_stale_and_draft_conflict_refuse_without_click(self):
        for first in (
            {'status': 'selectors_stale', 'composer_count': 0},
            {'status': 'selectors_stale', 'composer_count': 2},
            {'status': 'selectors_stale', 'send_button_count': 0},
            {'status': 'selectors_stale', 'send_button_count': 2},
            {'status': 'draft_conflict'},
        ):
            with self.subTest(first=first):
                transport = self.fake(first=first)
                result = self.invoke(transport)
                self.assertEqual(result['status'], 'refused')
                self.assertFalse(result['attempted'])
                self.assertFalse(result['click_observed'])
                self.assertFalse(any('return clickUniqueSend(' in c.args[0]
                                     for c in transport.evaluate.call_args_list))
                transport.insert_text.assert_not_called()

    def test_disabled_draft_never_clicks(self):
        transport = self.fake(staged={'status': 'send_disabled'})
        result = self.invoke(transport)
        self.assertEqual(result['status'], 'refused')
        self.assertEqual(result['reason'], 'send_disabled')
        self.assertFalse(result['attempted'])
        self.assertEqual(transport.insert_text.call_count, 1)

    def test_confirmed_requires_click_echo_and_emptied_composer(self):
        transport = self.fake()
        result = self.invoke(transport)
        self.assertEqual(result['status'], 'confirmed')
        self.assertIsNone(result['user_message_count_before'])
        self.assertIsNone(result['user_message_count_after'])
        self.assertTrue(result['user_confirmed'])
        self.assertTrue(result['composer_emptied'])
        self.assertTrue(result['click_observed'])
        self.assertTrue(result['attempted'])
        self.assertTrue(result['response_observed'])
        self.assertTrue(result['response_complete'])
        self.assertEqual(result['response_anchor'], 'footer')
        self.assertEqual(result['response_source'], 'rendered-conversation')
        self.assertEqual(result['response_text'], 'Assistant reply body with '
                                                  'enough useful content to pass '
                                                  'the usable-reply threshold of '
                                                  'the capture contract.')
        transport.insert_text.assert_called_once_with('synthetic request')
        self.assertEqual(result['dispatch_action'], 'enter')
        with mock.patch.object(adapter, 'launch_cdp_process') as launch:
            replayed = adapter.send('synthetic request', self.binary, self.root,
                                    'dedicated', request_key='req-218')
        launch.assert_not_called()
        self.assertTrue(replayed['replayed'])
        self.assertEqual(replayed['status'], 'confirmed')

    def test_durable_receipt_keeps_captured_reply_bounded(self):
        # Tune (26 nov. 2026): the durable receipt carries the captured reply
        # text so a captured_incomplete receipt can be reconciled later
        # without re-reading the conversation. Excerpt kept for diagnosis.
        self.invoke(self.fake(), request='req-receipt-strip')
        path = adapter._receipt_path(self.root / 'dedicated', 'req-receipt-strip')
        data = json.loads(path.read_text())
        self.assertEqual(data['response_text'], 'Assistant reply body with '
                                                'enough useful content to pass '
                                                'the usable-reply threshold of '
                                                'the capture contract.')
        self.assertEqual(data['response_excerpt'], 'Assistant reply body with '
                                                   'enough useful content to pass '
                                                   'the usable-reply threshold of '
                                                   'the capture contract.')

    def test_durable_receipt_large_reply_roundtrip(self):
        # Tune (26 nov. 2026): a receipt holding a full-size captured reply
        # (up to MAX_CAPTURE_CHARS) persists and reloads; the old 32 KiB
        # loader window is replaced by MAX_RECEIPT_BYTES.
        reply = 'x' * adapter.MAX_CAPTURE_CHARS
        receipt = dict(adapter._send_receipt())
        receipt['prompt_sha256'] = 'a' * 64
        receipt['response_text'] = reply
        receipt['response_excerpt'] = reply[:adapter.RESPONSE_EXCERPT_CHARS]
        path = adapter._receipt_path(self.root / 'dedicated',
                                     'req-large-roundtrip')
        path.parent.mkdir(parents=True, exist_ok=True)
        adapter._persist_receipt(path, receipt, initial=True)
        reloaded = adapter._load_receipt(path, 'a' * 64)
        self.assertEqual(reloaded['response_text'], reply)
        self.assertEqual(reloaded['response_excerpt'],
                         receipt['response_excerpt'])

    def test_capture_digest_ignores_animated_glyphs(self):
        # Tune (26 nov. 2026): spinner frames and painted cursors do not
        # change the digest; real content changes always do.
        base = 'Réponse complète du délégué.'
        self.assertEqual(adapter._capture_digest(base + ' ⠋'),
                         adapter._capture_digest(base + ' ⠙'))
        self.assertEqual(adapter._capture_digest(base + '\u258d'),
                         adapter._capture_digest(base))
        self.assertNotEqual(adapter._capture_digest(base),
                            adapter._capture_digest(base + ' suite'))

    def test_click_transport_loss_is_durable_uncertainty_without_retry(self):
        transport = self.fake(enter=RuntimeError('lost after dispatch'),
                              click=RuntimeError('button fallback lost'))
        receipt = self.invoke(transport)
        self.assertEqual(receipt['status'], 'incertain')
        self.assertTrue(receipt['attempted'])
        self.assertFalse(receipt['click_observed'])
        with mock.patch.object(adapter, 'launch_cdp_process') as launch:
            repeated = adapter.send('synthetic request', self.binary, self.root,
                                    'dedicated', request_key='req-218')
        launch.assert_not_called()
        self.assertEqual(repeated['status'], 'incertain')
        self.assertTrue(repeated['replayed'])

    def test_observer_transport_loss_after_click_is_uncertain(self):
        receipt = self.invoke(self.fake(body=RuntimeError('lost')))
        self.assertEqual(receipt['status'], 'incertain')
        self.assertTrue(receipt['click_observed'])

    def test_unemptied_composer_is_never_sent(self):
        for index, after in enumerate((
            {'status': 'send_disabled'},
            {'status': 'selectors_stale', 'composer_count': 1},
        )):
            with self.subTest(after=after):
                receipt = self.invoke(self.fake(after=after),
                                      request='req-after-' + str(index))
                self.assertEqual(receipt['status'], 'incertain')
                self.assertTrue(receipt['click_observed'])
                self.assertFalse(receipt['composer_emptied'])

    def test_request_key_conflict_fails_closed(self):
        self.invoke(self.fake(), request='one-key')
        with mock.patch.object(adapter, 'launch_cdp_process') as launch:
            conflict = adapter.send('another request', self.binary, self.root,
                                    'dedicated', request_key='one-key')
        launch.assert_not_called()
        self.assertEqual(conflict['status'], 'incertain')
        self.assertEqual(conflict['reason'], 'receipt_conflict_or_unreadable')

    def test_renderer_source_never_uses_dom_input_events_or_tcp(self):
        source = adapter.SEND_HELPERS
        self.assertIn('buttons[0].click()', source)
        self.assertIn('bodyProbe', source)
        self.assertNotIn('dispatchEvent', source)
        self.assertNotIn('dispatchMouseEvent', source)
        self.assertNotIn('remote-debugging-port', source)

class TestRenderedConversationCapture(unittest.TestCase):
    """Issue #226: rendered-conversation capture after a click, offline transports."""

    def setUp(self):
        self.holder = TestGuardedSend()
        self.holder.setUp()
        # holder.setUp() is run on a plain object: its own addCleanup never
        # executes, so the TemporaryDirectory would be finalized by gc —
        # register it on the running TestCase (issue #234).
        self.addCleanup(self.holder._folder.cleanup)

    def invoke(self, body=None, request='req-ax', quiet_ms=None):
        transport = self.holder.fake(body=body, quiet_ms=quiet_ms)
        return transport, self.holder.invoke(transport, request=request)

    def test_stable_two_polls_complete_and_extract_reply(self):
        transport, receipt = self.invoke(request='req-ax-1')
        self.assertTrue(receipt['response_complete'])
        self.assertEqual(receipt['status'], 'confirmed')
        expected = hashlib.sha256(REPLY_FIXTURE_BODY.encode('utf-8')).hexdigest()
        self.assertEqual(receipt['response_sha256'], expected)
        self.assertEqual(receipt['response_chars'], len(REPLY_FIXTURE_BODY))
        self.assertEqual(receipt['response_excerpt'], REPLY_FIXTURE_BODY)
        # The Network domain is enabled before staging; no AXTree call remains.
        self.assertEqual(sum(c.args and c.args[0] == 'Accessibility.getFullAXTree'
                             for c in transport.call.call_args_list), 0)
        self.assertTrue(any(c.args and c.args[0] == 'Network.enable'
                            for c in transport.call.call_args_list))

    def test_changing_then_stable_polls_complete(self):
        long_reply = ('Premier jet. Et la suite du contenu, rédigée assez long '
                      'pour dépasser franchement le seuil de capture utile.')
        body = ['Vous avez dit : synthetic request ChatGPT a dit : Premier jet.',
                'Vous avez dit : synthetic request ChatGPT a dit : ' + long_reply,
                'Vous avez dit : synthetic request ChatGPT a dit : ' + long_reply]
        # Mutations stay active while the text changes, then go quiet.
        quiet_ms = lambda poll: None if poll < 3 else adapter.STREAM_QUIET_MS
        transport, receipt = self.invoke(body=body, request='req-ax-2', quiet_ms=quiet_ms)
        self.assertTrue(receipt['response_complete'])
        self.assertIn('Et la suite', receipt['response_excerpt'])

    def test_sentinel_cut_takes_priority_over_footer(self):
        prompt = 'Question ?'
        body = ('Vous avez dit : Question ? ChatGPT a dit : Réponse '
                + adapter.REPLY_SENTINEL + ' ChatGPT peut faire des erreurs.')
        reply, anchor, _total = adapter.extract_reply(body, prompt)
        self.assertEqual(anchor, 'sentinel')
        self.assertEqual(reply, 'Réponse')

    def test_sentinel_anchored_short_reply_passes_the_usable_gate(self):
        # Issue #236: REPRISE-CAPTURE-FIN is the strict completion proof the
        # delegate was instructed to emit — a short reply anchored on it is a
        # complete answer, not a fragment (no capture_too_short degradation).
        prompt = 'Question ?'
        body = ('Vous avez dit : Question ? ChatGPT a dit : Oui. '
                + adapter.REPLY_SENTINEL + ' ChatGPT peut faire des erreurs.')
        reply, anchor, total = adapter.extract_reply(body, prompt)
        self.assertEqual(anchor, 'sentinel')
        self.assertEqual(reply, 'Oui.')
        self.assertEqual(total, len('Oui.'))
        self.assertEqual(adapter._usable_reply(reply, anchor), (True, None))

    def test_prompt_echo_missing_is_never_extracted(self):
        reply, anchor, _total = adapter.extract_reply('Texte sans écho.', 'Question ?')
        self.assertIsNone(reply)
        self.assertEqual(anchor, 'prompt_echo_missing')

    def test_quiet_stream_completes_with_drifting_text(self):
        def body(_poll):
            return ('Vous avez dit : synthetic request ChatGPT a dit : drift-%d '
                    'with additional rendered conversation text, long enough to '
                    'pass the usable-reply capture threshold.') % _poll

        transport = self.holder.fake(body=body, quiet_ms=adapter.STREAM_QUIET_MS)
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            receipt = adapter.send('synthetic request', self.holder.binary,
                                   self.holder.root, 'dedicated',
                                   request_key='req-ax-quiet')
        self.assertTrue(receipt['response_complete'])
        self.assertEqual(receipt['completion'], 'quiet')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertIn('drift-', receipt['response_excerpt'])

    def test_timeout_is_not_fatal_and_keeps_sent_status(self):
        def body(_poll):
            return ('Vous avez dit : synthetic request ChatGPT a dit : drift-%d '
                    'with additional rendered conversation text, long enough to '
                    'pass the usable-reply capture threshold.') % _poll

        transport = self.holder.fake(body=body, quiet_ms=lambda _poll: None)
        # The clock jump MUST be applied inside the harness: patching
        # adapter.time before __enter__ rewrites the shared global time
        # module, collapsing the readiness wait and spawning a real holder
        # (unreaped Popen, issue #234).
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            with mock.patch.object(adapter.time, 'monotonic',
                                   side_effect=itertools.count(0, 200)):
                receipt = adapter.send('synthetic request', self.holder.binary,
                                       self.holder.root, 'dedicated',
                                       request_key='req-ax-3')
        self.assertFalse(receipt['response_complete'])
        self.assertEqual(receipt['completion'], 'timeout')
        self.assertEqual(receipt['status'], 'sent')
        self.assertTrue(receipt['click_observed'])

    # Issue #228 regression tests: UI chrome must never validate a receipt.

    def test_turn_not_completed_with_chrome_capture_is_captured_incomplete(self):
        # Case a1: no ws turn-complete, no DOM marker, DOM never quiet — the
        # capture read only a fragment plus UI chrome before the clock ran out.
        def body(_poll):
            return {'text': 'Vous avez dit : synthetic request ChatGPT a dit : '
                            'Voici un début de réponse.', 'chrome_seen': True}

        transport = self.holder.fake(body=body, quiet_ms=lambda _poll: None)
        # Clock jump inside the harness only (issue #234): patching the shared
        # global time module before __enter__ collapses the readiness wait.
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            with mock.patch.object(adapter.time, 'monotonic',
                                   side_effect=itertools.count(0, 200)):
                receipt = adapter.send('synthetic request', self.holder.binary,
                                       self.holder.root, 'dedicated',
                                       request_key='req-228-a1')
        self.assertFalse(receipt['turn_completed'])
        self.assertFalse(receipt['response_complete'])
        self.assertEqual(receipt['status'], 'captured_incomplete')
        self.assertEqual(receipt['reason'], 'capture_too_short')
        # Raw excerpt kept for diagnosis, cut before the chrome label; the
        # hash covers the captured fragment, not a completed reply.
        self.assertEqual(receipt['response_excerpt'], 'Voici un début de réponse.')
        self.assertEqual(receipt['response_sha256'],
                         hashlib.sha256('Voici un début de réponse.'.encode('utf-8')).hexdigest())

    def test_capture_during_thinking_indicator_is_captured_incomplete(self):
        # Case a2: turn completed but the DOM still shows only the thinking
        # chrome — never a confirmed receipt.
        def body(_poll):
            return {'text': 'Vous avez dit : synthetic request ChatGPT a dit : ',
                    'chrome_seen': True}

        transport = self.holder.fake(body=body, quiet_ms=adapter.STREAM_QUIET_MS)
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            receipt = adapter.send('synthetic request', self.holder.binary,
                                   self.holder.root, 'dedicated',
                                   request_key='req-228-a2')
        self.assertFalse(receipt['response_complete'])
        self.assertEqual(receipt['status'], 'captured_incomplete')
        self.assertEqual(receipt['reason'], 'ui_chrome_capture')
        self.assertTrue(receipt['click_observed'])
        # Idempotence: the receipt is durable, replay never re-sends.
        with mock.patch.object(adapter, 'launch_cdp_process') as launch:
            replayed = adapter.send('synthetic request', self.holder.binary,
                                    self.holder.root, 'dedicated',
                                    request_key='req-228-a2')
        launch.assert_not_called()
        self.assertTrue(replayed['replayed'])
        self.assertEqual(replayed['status'], 'captured_incomplete')

    # Issue #228: chrome-line cut on raw text, DOM re-read budget, receipt
    # criteria (usable gate unchanged).

    def test_cut_chrome_lines_matches_only_full_lines(self):
        self.assertEqual(adapter._cut_chrome_lines('Réponse utile.\nAuto\nfin'),
                         ('Réponse utile.', True))
        self.assertEqual(adapter._cut_chrome_lines('Réponse utile.\nAUTO'),
                         ('Réponse utile.', True))
        self.assertEqual(adapter._cut_chrome_lines('Automatic retry.\nfin'),
                         ('Automatic retry.\nfin', False))
        self.assertEqual(adapter._cut_chrome_lines('La réponse terminée arrive.'),
                         ('La réponse terminée arrive.', False))
        self.assertEqual(adapter._cut_chrome_lines('Effort de réflexion'),
                         ('', True))
        self.assertEqual(adapter._cut_chrome_lines('Sans chrome'),
                         ('Sans chrome', False))

    def test_extract_reply_cuts_chrome_lines_from_assistant_text(self):
        # The full-line cut runs on the RAW rendered text: _normalize welds
        # every line together, so a cut after normalization could never match.
        body = ('Auto-détection et réglages conservés. '
                'Assistant reply body with enough useful content to pass '
                'the usable-reply threshold of the capture contract.')
        text = body + '\nEffort de réflexion\nAuto\nRéponse terminée'
        reply, anchor, total = adapter.extract_reply(None, 'Question ?',
                                                     assistant_text=text)
        self.assertEqual(anchor, 'chrome_line')
        self.assertEqual(reply, body)
        self.assertEqual(total, len(body))
        for label in ('Effort de réflexion', 'Réponse terminée'):
            self.assertNotIn(label, reply)
        # «Auto» inside the reply's own words is never a cut point.
        self.assertIn('Auto-détection', reply)
        # Chrome-only assistant text yields no reply at all.
        reply2, anchor2, _total2 = adapter.extract_reply(
            None, 'Question ?', assistant_text='Effort de réflexion\nAuto')
        self.assertIsNone(reply2)
        self.assertEqual(anchor2, 'chrome_line')

    def test_assistant_text_chrome_never_reaches_the_captured_reply(self):
        reply_body = ('Assistant reply body with enough useful content to pass '
                      'the usable-reply threshold of the capture contract.')

        def body(_poll):
            return {'text': 'Vous avez dit : synthetic request ChatGPT a dit : '
                            + reply_body,
                    'assistant_text': (reply_body + '\nEffort de réflexion\n'
                                       'Auto\nRéponse terminée'),
                    'assistant_selector': 'div.assistant'}

        transport = self.holder.fake(body=body, quiet_ms=lambda _poll: None)
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            receipt = adapter.send('synthetic request', self.holder.binary,
                                   self.holder.root, 'dedicated',
                                   request_key='req-228-chrome')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertTrue(receipt['response_complete'])
        self.assertEqual(receipt['completion'], 'dom-marker')
        self.assertEqual(receipt['capture_source'], 'assistant_text')
        self.assertEqual(receipt['assistant_selector'], 'div.assistant')
        self.assertEqual(receipt['response_text'], reply_body)
        for label in ('Effort de réflexion', 'Auto', 'Réponse terminée'):
            self.assertNotIn(label, receipt['response_excerpt'])

    def test_turn_completed_before_render_rereads_and_dispatches_once(self):
        # The ws event arrives while the DOM still shows only the thinking
        # chrome; the bounded re-read picks the rendered reply afterwards.
        rendered = ('Vous avez dit : synthetic request ChatGPT a dit : '
                    + REPLY_FIXTURE_BODY)
        reads = {'n': 0}

        def body(_poll):
            reads['n'] += 1
            if reads['n'] == 1:
                return {'text': 'Vous avez dit : synthetic request ChatGPT a dit : ',
                        'chrome_seen': True}
            return {'text': rendered}

        sse = [{'params': {'response': {'payloadData':
                                         'event: conversation-turn-complete'}}}]
        transport = self.holder.fake(body=body, quiet_ms=lambda _poll: None,
                                     sse=sse)
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            receipt = adapter.send('synthetic request', self.holder.binary,
                                   self.holder.root, 'dedicated',
                                   request_key='req-228-reread')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertTrue(receipt['response_complete'])
        self.assertEqual(receipt['completion'], 'turn-complete')
        self.assertEqual(receipt['response_text'], REPLY_FIXTURE_BODY)
        self.assertGreaterEqual(receipt['capture_reread_passes'], 1)
        self.assertNotIn('capture_reread_exhausted', receipt)
        # Exactly one dispatch: the primary Enter (2 key events) and the
        # button-click fallback never fires — the re-read never re-submits.
        self.assertEqual(receipt['dispatch_action'], 'enter')
        self.assertEqual(
            sum(c.args and c.args[0] == 'Input.dispatchKeyEvent'
                for c in transport.call.call_args_list), 2)
        self.assertFalse(any('return clickUniqueSend(' in (c.args[0] if c.args else '')
                             for c in transport.evaluate.call_args_list))

    def test_reread_budget_exhausted_keeps_captured_incomplete(self):
        # A usable capture that never settles twice within the re-read budget
        # stays captured_incomplete, with the reason kept on the receipt.
        rendered = ('Vous avez dit : synthetic request ChatGPT a dit : '
                    + REPLY_FIXTURE_BODY)

        def body(_poll):
            return {'text': rendered}

        sse = [{'params': {'response': {'payloadData':
                                         'event: conversation-turn-complete'}}}]
        transport = self.holder.fake(body=body, quiet_ms=lambda _poll: None,
                                     sse=sse)
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            with mock.patch.object(adapter.time, 'monotonic',
                                   side_effect=itertools.count(0, 200)):
                receipt = adapter.send('synthetic request', self.holder.binary,
                                       self.holder.root, 'dedicated',
                                       request_key='req-228-budget')
        self.assertEqual(receipt['status'], 'captured_incomplete')
        self.assertEqual(receipt['reason'], 'capture_reread_budget_exhausted')
        self.assertFalse(receipt['response_complete'])
        self.assertTrue(receipt['turn_completed'])
        self.assertEqual(receipt['completion'], 'turn-complete')
        self.assertTrue(receipt['capture_reread_exhausted'])
        self.assertEqual(receipt['capture_reread_budget_seconds'],
                         adapter.CAPTURE_REREAD_BUDGET_SECONDS)
        self.assertEqual(receipt['capture_reread_passes'], 0)
        self.assertEqual(receipt['capture_source'], 'body_text')
        # Raw capture kept for diagnosis; exactly one dispatch, no re-send.
        self.assertEqual(receipt['response_text'], REPLY_FIXTURE_BODY)
        self.assertTrue(receipt['response_observed'])
        self.assertEqual(receipt['dispatch_action'], 'enter')
        self.assertEqual(
            sum(c.args and c.args[0] == 'Input.dispatchKeyEvent'
                for c in transport.call.call_args_list), 2)
        self.assertFalse(any('return clickUniqueSend(' in (c.args[0] if c.args else '')
                             for c in transport.evaluate.call_args_list))

    def test_usable_reply_gate_unchanged(self):
        self.assertEqual(adapter._usable_reply(None, None),
                         (False, 'no_reply_text'))
        self.assertEqual(adapter._usable_reply('Trop court.', 'end'),
                         (False, 'capture_too_short'))
        self.assertEqual(adapter._usable_reply('   \n\t  ', 'end'),
                         (False, 'capture_too_short'))
        long_reply = 'x' * adapter.MIN_USEFUL_REPLY_CHARS
        self.assertEqual(adapter._usable_reply(long_reply, 'end'), (True, None))
        self.assertEqual(adapter._usable_reply('Court.', 'sentinel'), (True, None))
        self.assertEqual(adapter._usable_reply('Court.', 'cut_before_sentinel'),
                         (False, 'capture_cut_before_sentinel'))

    # Issue #236 regression tests: declared capture completeness.

    def test_short_sentinel_reply_is_confirmed_with_output_complete(self):
        # The bridge prompt instructs the delegate to end with the sentinel;
        # a short sentinel-anchored reply is a complete answer (#228 follow-up:
        # no more capture_too_short on the systematic bridge flow).
        def body(_poll):
            return ('Vous avez dit : synthetic request ChatGPT a dit : Oui. '
                    + adapter.REPLY_SENTINEL + ' ChatGPT peut faire des erreurs.')

        transport = self.holder.fake(body=body, quiet_ms=adapter.STREAM_QUIET_MS)
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            receipt = adapter.send('synthetic request', self.holder.binary,
                                   self.holder.root, 'dedicated',
                                   request_key='req-236-short')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertTrue(receipt['response_complete'])
        self.assertEqual(receipt['response_anchor'], 'sentinel')
        self.assertTrue(receipt['output_complete'])
        self.assertNotIn('output_truncated', receipt)
        self.assertEqual(receipt['response_text'], 'Oui.')

    def test_truncated_capture_is_declared_and_never_confirmed(self):
        # Issue #236: a reply longer than the defensive capture ceiling is
        # declared truncated (output_truncated / response_full_length /
        # capture_limit_characters) and never validates the receipt.
        def body(_poll):
            return ('Vous avez dit : synthetic request ChatGPT a dit : '
                    + 'long reply body. ' * 6000
                    + adapter.REPLY_SENTINEL + ' ChatGPT peut faire des erreurs.')

        transport = self.holder.fake(body=body, quiet_ms=adapter.STREAM_QUIET_MS)
        with HolderHarness(transport, self.holder.binary, self.holder.root):
            receipt = adapter.send('synthetic request', self.holder.binary,
                                   self.holder.root, 'dedicated',
                                   request_key='req-236-trunc')
        self.assertFalse(receipt['response_complete'])
        self.assertEqual(receipt['status'], 'captured_incomplete')
        self.assertEqual(receipt['reason'], 'capture_truncated')
        self.assertTrue(receipt['output_truncated'])
        self.assertFalse(receipt['output_complete'])
        self.assertEqual(receipt['capture_limit_characters'], adapter.MAX_CAPTURE_CHARS)
        self.assertEqual(receipt['response_chars'], adapter.MAX_CAPTURE_CHARS)
        self.assertGreater(receipt['response_full_length'], adapter.MAX_CAPTURE_CHARS)
        self.assertTrue(receipt['response_observed'])

    def test_unavailable_body_is_uncertain(self):
        transport, receipt = self.invoke(body=RuntimeError('pipe lost'),
                                         request='req-ax-4')
        self.assertEqual(receipt['status'], 'incertain')
        self.assertTrue(receipt['click_observed'])
        self.assertFalse(receipt['response_observed'])

    def test_no_click_means_no_capture_and_no_optional_fields(self):
        transport = self.holder.fake(
            first={'status': 'selectors_stale', 'composer_count': 0})
        receipt = self.holder.invoke(transport, request='req-ax-5')
        self.assertEqual(receipt['status'], 'refused')
        # Aucune activation de domaine capture sur un refus (l'inventaire
        # Target.getTargets du holder au démarrage est la seule exception).
        self.assertNotIn('Network.enable',
                         [c.args[0] for c in transport.call.call_args_list])
        self.assertNotIn('Accessibility.getFullAXTree',
                         [c.args[0] for c in transport.call.call_args_list])
        for key in ('response_sha256', 'response_chars', 'response_excerpt',
                    'response_anchor', 'response_source'):
            self.assertNotIn(key, receipt)


PROMPT_246 = ('Question #246 ? Termine ta réponse par la ligne\n'
              + adapter.REPLY_SENTINEL)


class TestExtractReplyPromptEchoOnly246(unittest.TestCase):
    """Issue #246: a single sentinel that belongs to the prompt echo means
    the reply is not rendered yet — the capture loop must keep polling,
    never confirm the user preamble as the reply."""

    def test_single_sentinel_prompt_echo_only_returns_prompt_echo_only(self):
        body = ('Vous avez dit :\n' + PROMPT_246)
        reply, anchor, total = adapter.extract_reply(
            None, PROMPT_246, assistant_text=body)
        self.assertEqual((reply, anchor, total), (None, 'prompt_echo_only', 0))

    def test_multi_sentinel_reply_is_still_extracted(self):
        body = ('Vous avez dit :\n' + PROMPT_246
                + '\nVous avez dit :\n' + PROMPT_246
                + '\nChatGPT a dit :\nRéponse finale '
                + adapter.REPLY_SENTINEL)
        reply, anchor, _total = adapter.extract_reply(
            None, PROMPT_246, assistant_text=body)
        self.assertIsNotNone(reply)
        self.assertEqual(anchor, 'sentinel')
        self.assertIn('Réponse finale', reply)

    def test_single_sentinel_without_prompt_echo_is_still_extracted(self):
        body = ('ChatGPT a dit :\nRéponse directe '
                + adapter.REPLY_SENTINEL)
        reply, anchor, _total = adapter.extract_reply(
            None, PROMPT_246, assistant_text=body)
        self.assertIsNotNone(reply)
        self.assertEqual(anchor, 'sentinel')
        self.assertEqual(reply, 'Réponse directe')

    def test_multiturn_prior_sentinel_with_echo_only_rejected(self):
        # Issue #246: live probe c9 — in a multi-turn aggregate the previous
        # sentinel is the PRIOR turn's reply; the cut segment holds only the
        # current prompt echo («Vous avez dit :»), no assistant label, so
        # the reply has not rendered yet.
        prompt_turn_2 = 'Question suivante #246 ? Termine ta réponse par la ligne\n' + adapter.REPLY_SENTINEL
        body = ('Vous avez dit :\n'
                + PROMPT_246
                + '\nChatGPT a dit :\nRéponse tour un '
                + adapter.REPLY_SENTINEL
                # The echo of the current prompt ends with the sentinel the
                # prompt itself carries; nothing renders after it (probe c9).
                + '\nVous avez dit :\n' + prompt_turn_2)
        reply, anchor, total = adapter.extract_reply(
            None, prompt_turn_2, assistant_text=body)
        self.assertEqual((reply, anchor, total), (None, 'prompt_echo_only', 0))

    def test_multiturn_rendered_reply_still_confirmed(self):
        # Issue #246: same multi-turn structure but the second reply has
        # rendered — the cut segment carries «ChatGPT a dit :» before the
        # last sentinel, so the capture stays valid (probes c6/c8).
        prompt_turn_2 = 'Question suivante #246 ? Termine ta réponse par la ligne\n' + adapter.REPLY_SENTINEL
        body = ('Vous avez dit :\n'
                + PROMPT_246
                + '\nChatGPT a dit :\nRéponse tour un '
                + adapter.REPLY_SENTINEL
                + '\nVous avez dit :\n' + prompt_turn_2
                + '\nChatGPT a dit :\nRéponse tour deux '
                + adapter.REPLY_SENTINEL)
        reply, anchor, _total = adapter.extract_reply(
            None, prompt_turn_2, assistant_text=body)
        self.assertIsNotNone(reply)
        self.assertEqual(anchor, 'sentinel')
        self.assertIn('Réponse tour deux', reply)

    # Live probe c11 (20/11/2026): the consultative wrapper carries the
    # sentinel INSIDE the prompt («End your answer with the exact line …»),
    # so the rendered echo contains that internal sentinel and the LAST
    # sentinel sits mid-prompt; the cut segment is the echo truncated before
    # «. Question: …» — the full-prompt rfind of the guard can never match.
    MID_PROMPT_246 = ('Reprise consultative question (request_key="rk-246-c11"). '
                      'End your answer with the exact line '
                      + adapter.REPLY_SENTINEL
                      + '. Question: météo imaginaire du tour ?')

    def test_multiturn_internal_sentinel_truncated_echo_rejected(self):
        prompt_turn_2 = self.MID_PROMPT_246
        body = ('Vous avez dit :\n'
                + PROMPT_246
                + '\nChatGPT a dit :\nRéponse tour un '
                + adapter.REPLY_SENTINEL
                # The echo of the current prompt renders IN FULL (internal
                # sentinel included) and the reply has not rendered yet: the
                # last sentinel is the internal one, mid-prompt.
                + '\nVous avez dit :\n' + prompt_turn_2)
        reply, anchor, total = adapter.extract_reply(
            None, prompt_turn_2, assistant_text=body)
        self.assertEqual((reply, anchor, total), (None, 'prompt_echo_only', 0))

    def test_multiturn_internal_sentinel_rendered_reply_still_confirmed(self):
        prompt_turn_2 = self.MID_PROMPT_246
        body = ('Vous avez dit :\n'
                + PROMPT_246
                + '\nChatGPT a dit :\nRéponse tour un '
                + adapter.REPLY_SENTINEL
                + '\nVous avez dit :\n' + prompt_turn_2
                + '\nChatGPT a dit :\nRéponse tour deux '
                + adapter.REPLY_SENTINEL)
        reply, anchor, _total = adapter.extract_reply(
            None, prompt_turn_2, assistant_text=body)
        self.assertIsNotNone(reply)
        self.assertEqual(anchor, 'sentinel')
        self.assertIn('Réponse tour deux', reply)

    # Live probe c14 (20/11/2026): the probe itself pre-wrapped an already
    # wrapped prompt, so the rendered echo holds TWO internal sentinels. The
    # cut between them yields the wrapper-1 tail («. Question:\n\n <head>»)
    # which ends with the prompt head but carries NO user chrome — the c14
    # receipt answered confirmed with a 249-char wrapper fragment.
    WRAPPER_HEAD_246 = ('Reprise consultative question (request_key="deploy-246-target-c14"). '
                        'ChatGPT has no Reprise tools: answer directly in this conversation '
                        '— the reply is captured from it by the Reprise holder. '
                        'End your answer with the exact line ')

    def test_double_wrapped_partial_echo_without_chrome_rejected(self):
        prompt = (self.WRAPPER_HEAD_246 + adapter.REPLY_SENTINEL
                  + '. Question:\n\n' + self.WRAPPER_HEAD_246
                  + adapter.REPLY_SENTINEL
                  + '. Question:\n\nSonde de ciblage c14 ?')
        # The echo renders only up to the FIRST internal sentinel (partial
        # render of the body_text capture); no assistant reply, no chrome.
        body = prompt[:prompt.find(adapter.REPLY_SENTINEL) + len(adapter.REPLY_SENTINEL)]
        reply, anchor, total = adapter.extract_reply(
            None, prompt, assistant_text=body)
        self.assertEqual((reply, anchor, total), (None, 'prompt_echo_only', 0))

    def test_double_wrapped_full_echo_without_chrome_rejected(self):
        prompt = (self.WRAPPER_HEAD_246 + adapter.REPLY_SENTINEL
                  + '. Question:\n\n' + self.WRAPPER_HEAD_246
                  + adapter.REPLY_SENTINEL
                  + '. Question:\n\nSonde de ciblage c14 ?')
        body = 'Vous avez dit :\n' + prompt
        reply, anchor, total = adapter.extract_reply(
            None, prompt, assistant_text=body)
        self.assertEqual((reply, anchor, total), (None, 'prompt_echo_only', 0))

    def test_chromeless_prompt_echo_with_rendered_reply_still_confirmed(self):
        # Negative control: a genuine reply after a chrome-less echo must not
        # be swallowed by the v4 head-cut guard.
        prompt = (self.WRAPPER_HEAD_246 + adapter.REPLY_SENTINEL
                  + '. Question:\n\nSonde de ciblage c14 ?')
        body = (prompt
                + '\n\nChatGPT a dit :\nLa cible est la conversation sélectionnée, troisième tour de sondes. '
                + adapter.REPLY_SENTINEL)
        reply, anchor, _total = adapter.extract_reply(
            None, prompt, assistant_text=body)
        self.assertIsNotNone(reply)
        self.assertEqual(anchor, 'sentinel')
        self.assertIn('La cible est', reply)
    """Holder pattern: send delegates, serve owns Chrome, never terminates it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.root = self.base / 'profiles'
        self.binary = self.base / 'ChatGPT'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)

    def holder_test_guarded(self):
        return TestGuardedSend()

    def test_serve_dispatches_on_live_transport_and_answers_valid_receipt(self):
        guarded = self.holder_test_guarded()
        transport = guarded.fake()
        with HolderHarness(transport, self.binary, self.root) as harness:
            receipt = adapter.send('synthetic request', self.binary, self.root,
                                   'dedicated', request_key='req-holder-1')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertTrue(receipt['click_observed'])
        self.assertTrue(receipt['attempted'])
        transport.insert_text.assert_called_once_with('synthetic request')
        self.assertEqual(receipt['dispatch_action'], 'enter')
        harness.terminate.assert_not_called()
        self.assertIsNone(harness.result.get('reason'))

    def test_send_uses_responsive_holder_socket_and_never_launches_chrome(self):
        receipt = {'status': 'sent', 'click_observed': True, 'attempted': True}
        fake_sock = mock.MagicMock()
        with mock.patch.object(adapter, '_holder_connect', return_value=fake_sock) as connect, \
             mock.patch.object(adapter, '_holder_exchange', return_value=receipt) as exchange, \
             mock.patch.object(adapter, 'launch_cdp_process') as launch, \
             mock.patch.object(adapter.subprocess, 'Popen') as popen, \
             mock.patch.object(adapter.time, 'sleep'):
            result = adapter.send('synthetic request', self.binary, self.root,
                                  'dedicated', request_key='req-holder-2')
        self.assertEqual(result, receipt)
        launch.assert_not_called()
        popen.assert_not_called()
        self.assertEqual(connect.call_args.kwargs.get('timeout'), 2.0)
        self.assertEqual(exchange.call_args.args[1],
                         {'prompt': 'synthetic request', 'request_key': 'req-holder-2'})

    def test_send_spawns_detached_holder_when_socket_is_absent(self):
        receipt = {'status': 'sent', 'click_observed': True, 'attempted': True}
        fake_sock = mock.MagicMock()
        with mock.patch.object(adapter, '_holder_connect',
                               side_effect=[None, None, None, fake_sock]) as connect, \
             mock.patch.object(adapter, '_holder_exchange', return_value=receipt), \
             mock.patch.object(adapter.subprocess, 'Popen') as popen, \
             mock.patch.object(adapter.time, 'sleep') as sleep:
            result = adapter.send('synthetic request', self.binary, self.root,
                                  'dedicated', request_key='req-holder-3')
        self.assertEqual(result, receipt)
        command = popen.call_args.args[0]
        self.assertEqual(command[0], sys.executable)
        self.assertEqual(command[command.index('serve') + 1], '--binary')
        self.assertEqual(command[command.index('--binary') + 1], str(self.binary))
        self.assertEqual(command[command.index('--profile') + 1], 'dedicated')
        self.assertEqual(command[command.index('--profile-root') + 1], str(self.root))
        self.assertTrue(popen.call_args.kwargs.get('start_new_session'))
        # 1 initial probe + 1 connect per start attempt; the sleep loop skips
        # its final iteration, hence 2 sleeps for 3 post-spawn attempts.
        self.assertEqual(connect.call_count, 4)
        self.assertEqual(sleep.call_count, 2)

    def test_second_same_request_key_is_replayed_without_redispatch(self):
        guarded = self.holder_test_guarded()
        transport = guarded.fake()
        with HolderHarness(transport, self.binary, self.root):
            first = adapter.send('synthetic request', self.binary, self.root,
                                 'dedicated', request_key='req-holder-4')
            clicks = sum('return clickUniqueSend(' in call.args[0]
                         for call in transport.evaluate.call_args_list)
            second = adapter.send('synthetic request', self.binary, self.root,
                                  'dedicated', request_key='req-holder-4')
        self.assertEqual(first['status'], 'confirmed')
        self.assertTrue(second['replayed'])
        self.assertEqual(second['status'], 'confirmed')

    def test_degraded_transport_relaunches_own_child_then_refuses(self):
        guarded = self.holder_test_guarded()
        transport = guarded.fake()
        calls = itertools.count()

        def census(_transport):
            if next(calls) == 0:
                return {'status': 'ready', 'ready': True}
            raise RuntimeError('pipe degraded')

        with HolderHarness(transport, self.binary, self.root, census=census) as harness:
            receipt = adapter.send('synthetic request', self.binary, self.root,
                                   'dedicated', request_key='req-holder-5')
        self.assertEqual(receipt['status'], 'refused')
        self.assertEqual(receipt['reason'], 'holder_not_ready')
        self.assertFalse(receipt['attempted'])
        self.assertFalse(receipt['click_observed'])
        # Recovery touches only the holder's OWN child, never the operator app.
        self.assertEqual(harness.terminate.call_count, 1)
        self.assertIs(harness.terminate.call_args.args[0], harness.process)

    def test_dead_transport_triggers_bounded_relaunch_and_recovery(self):
        guarded = self.holder_test_guarded()
        transport = guarded.fake()
        calls = {'n': 0}

        def census(_transport):
            calls['n'] += 1
            if calls['n'] in (1, 3):
                return {'status': 'target_missing', 'ready': False}
            return {'status': 'ready', 'ready': True}

        with HolderHarness(transport, self.binary, self.root, census=census) as harness:
            receipt = adapter.send('synthetic request', self.binary, self.root,
                                   'dedicated', request_key='req-relaunch')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertTrue(harness.terminate.called)
        self.assertGreaterEqual(calls['n'], 4)


class TestHolderEnsure247(unittest.TestCase):
    """Issue #247: a holder can outlive its Chrome; 'ensure' verifies or repairs."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.root = self.base / 'profiles'
        self.binary = self.base / 'ChatGPT'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)

    def process(self, pid):
        child = mock.Mock(pid=pid)
        child.poll.return_value = None
        return child

    def test_ready_transport_answers_verified_without_relaunch(self):
        runtime = {'transport': mock.MagicMock(), 'proc': self.process(9001)}
        with mock.patch.object(adapter, 'census',
                               return_value={'status': 'ready', 'ready': True}) as census, \
             mock.patch.object(adapter, '_holder_relaunch') as relaunch:
            result = adapter._holder_ensure(runtime, self.binary, self.root)
        self.assertEqual(result, {'status': 'ready', 'verified': True})
        census.assert_called_once_with(runtime['transport'])
        relaunch.assert_not_called()

    def test_dead_transport_relaunches_once_then_ready(self):
        runtime = {'transport': mock.MagicMock(), 'proc': self.process(9002)}
        with mock.patch.object(adapter, 'census',
                               side_effect=[EOFError('pipe closed'),
                                            {'status': 'ready', 'ready': True}]) as census, \
             mock.patch.object(adapter, '_holder_relaunch',
                               return_value=mock.MagicMock()) as relaunch:
            result = adapter._holder_ensure(runtime, self.binary, self.root)
        self.assertEqual(result, {'status': 'ready', 'verified': True})
        relaunch.assert_called_once_with(runtime, self.binary, self.root)
        self.assertEqual(census.call_count, 2)

    def test_relaunch_that_never_becomes_ready_is_holder_not_ready(self):
        runtime = {'transport': mock.MagicMock(), 'proc': self.process(9003)}
        with mock.patch.object(adapter, 'census', side_effect=EOFError('pipe dead')), \
             mock.patch.object(adapter, '_holder_relaunch',
                               return_value=mock.MagicMock()) as relaunch, \
             mock.patch.object(adapter, 'time', AcceleratedTime()):
            result = adapter._holder_ensure(runtime, self.binary, self.root)
        self.assertEqual(result, {'status': 'holder_not_ready', 'verified': False})
        relaunch.assert_called_once()

    def test_serve_connection_routes_ensure_command_to_holder_ensure(self):
        conn = mock.MagicMock()
        runtime = {'transport': mock.MagicMock(), 'proc': self.process(9004)}
        with mock.patch.object(adapter, '_read_holder_frame',
                               return_value={'cmd': 'ensure'}), \
             mock.patch.object(adapter, '_holder_ensure',
                               return_value={'status': 'ready', 'verified': True}) as ensure, \
             mock.patch.object(adapter, '_holder_dispatch') as dispatch:
            adapter._serve_connection(conn, runtime, self.binary, self.root)
        ensure.assert_called_once_with(runtime, self.binary, self.root)
        dispatch.assert_not_called()
        frame = json.loads(conn.sendall.call_args.args[0].decode('utf-8'))
        self.assertEqual(frame, {'status': 'ready', 'verified': True})


class TestHolderDiag248(unittest.TestCase):
    """Issue #248 LOT 1: read-only 'diag' holder command (bounded DOM excerpt)."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.root = self.base / 'profiles'
        self.binary = self.base / 'ChatGPT'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)

    def process(self, pid):
        child = mock.Mock(pid=pid)
        child.poll.return_value = None
        return child

    def test_serve_connection_routes_diag_command_without_dispatch(self):
        conn = mock.MagicMock()
        runtime = {'transport': mock.MagicMock(), 'proc': self.process(9005)}
        diag = {'status': 'ok', 'diag': {'href': adapter.MAIN_URL,
                                        'title': 'ReChatGPT',
                                        'candidates': [], 'truncated': False}}
        with mock.patch.object(adapter, '_read_holder_frame',
                               return_value={'cmd': 'diag'}), \
             mock.patch.object(adapter, '_holder_diag', return_value=diag) as diag_call, \
             mock.patch.object(adapter, '_holder_dispatch') as dispatch:
            adapter._serve_connection(conn, runtime, self.binary, self.root)
        diag_call.assert_called_once_with(runtime)
        dispatch.assert_not_called()
        frame = json.loads(conn.sendall.call_args.args[0].decode('utf-8'))
        self.assertEqual(frame, diag)

    def test_census_refusal_does_not_evaluate(self):
        transport = mock.MagicMock()
        state = {'status': 'target_ambiguous'}
        with mock.patch.object(adapter, 'census', return_value=state):
            result = adapter._holder_diag({'transport': transport})
        self.assertEqual(result, {'status': 'target_ambiguous', 'diag': None})
        transport.evaluate.assert_not_called()

    def test_evaluate_error_returns_failure_receipt(self):
        transport = mock.MagicMock()
        transport.evaluate.side_effect = TimeoutError('timed out')
        with mock.patch.object(adapter, 'census',
                               return_value={'status': 'ready'}):
            result = adapter._holder_diag({'transport': transport})
        self.assertEqual(result, {'status': 'diag_failed', 'diag': None})
        transport.evaluate.assert_called_once_with(adapter.DIAG_PROBE)

    def test_large_excerpt_truncates_candidate_list_under_limit(self):
        candidate = {'tag': 'button', 'text': 'ChatGPT',
                     'data': {'data-testid': 'x' * 100},
                     'class': 'x' * 120}
        transport = mock.MagicMock()
        transport.evaluate.return_value = {
            'href': adapter.MAIN_URL, 'title': 'ReChatGPT',
            'candidates': [candidate.copy() for _ in range(200)]}
        with mock.patch.object(adapter, 'census',
                               return_value={'status': 'ready'}):
            result = adapter._holder_diag({'transport': transport})
        self.assertEqual(result['status'], 'ok')
        encoded = json.dumps(result['diag'], ensure_ascii=False,
                             separators=(',', ':')).encode('utf-8')
        self.assertLessEqual(len(encoded), adapter.DIAG_MAX_BYTES)
        self.assertTrue(result['diag']['truncated'])
        self.assertLess(len(result['diag']['candidates']), 200)


class TestChatGptMode248(TestGuardedSend):
    """Issue #248 LOT 2: the pure ChatGPT mode is verified before any send."""

    def codex_probe(self):
        return {'state': 'Codex',
                'candidates': [{'tag': 'button', 'text': 'Codex',
                                'aria_expanded': 'false',
                                'data_state': 'closed'}]}

    def chatgpt_probe(self):
        return {'state': 'ChatGPT',
                'candidates': [{'tag': 'button', 'text': 'ChatGPT',
                                'aria_expanded': 'false',
                                'data_state': 'closed'}]}

    def menu_open(self):
        return {'open': True,
                'sample': {'items': 2, 'popper_present': True,
                           'popper_text': 'ChatGPT Codex'}}

    def menu_closed(self):
        return {'open': False, 'sample': {'items': 0, 'popper_present': False,
                                          'popper_text': None}}

    def expressions(self, transport):
        return [call.args[0] for call in transport.evaluate.call_args_list]

    def test_already_chatgpt_answers_verified_without_clicks(self):
        transport = mock.MagicMock()
        transport.evaluate.return_value = self.chatgpt_probe()
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertEqual(result, {'verified': True, 'mode': 'ChatGPT'})
        transport.evaluate.assert_called_once_with(adapter.MODE_PROBE)

    def test_codex_switch_clicks_trigger_then_item_then_rereads(self):
        transport = mock.MagicMock()
        transport.evaluate.side_effect = [
            self.codex_probe(), {'clicked': True}, self.menu_open(),
            {'clicked': True}, self.chatgpt_probe()]
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertEqual(result, {'verified': True, 'mode': 'ChatGPT'})
        calls = self.expressions(transport)
        self.assertEqual(len(calls), 5)
        self.assertIn('__reprise_mode_trigger', calls[1])
        self.assertIn('__reprise_mode_menu', calls[2])
        self.assertIn('__reprise_mode_item', calls[3])
        self.assertEqual(calls[4], adapter.MODE_PROBE)

    def test_sidebar_chip_mentioning_codex_does_not_block_switch(self):
        # Live receipt (#248): a sidebar chip "Trouver une skill UX/UI pour
        # Codex" shares the page with the real trigger; a substring filter
        # turned it into a false selector_ambiguous refusal. Exact mode
        # labels only: the chip is ignored and the switch proceeds.
        chip = {'tag': 'button', 'text': 'Trouver une skill UX/UI pour Codex',
                'rect': {'x': 92, 'y': 813, 'w': 204, 'h': 20}}
        codex_with_chip = {'state': 'Codex',
                           'candidates': [self.codex_probe()['candidates'][0],
                                          chip]}
        transport = mock.MagicMock()
        transport.evaluate.side_effect = [
            codex_with_chip, {'clicked': True}, self.menu_open(),
            {'clicked': True}, self.chatgpt_probe()]
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertEqual(result, {'verified': True, 'mode': 'ChatGPT'})
        calls = self.expressions(transport)
        self.assertEqual(len(calls), 5)

    def test_chip_mentioning_codex_alone_is_not_a_codex_trigger(self):
        # Defense in depth: even if a non-exact list reaches classify()
        # (stale probe answer), the chip is never treated as the Codex
        # trigger — fail closed without any click.
        chip = {'tag': 'button', 'text': 'Trouver une skill UX/UI pour Codex',
                'rect': {'x': 92, 'y': 813, 'w': 204, 'h': 20}}
        transport = mock.MagicMock()
        transport.evaluate.return_value = {'state': chip['text'],
                                           'candidates': [chip]}
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertFalse(result['verified'])
        self.assertEqual(result['reason'], 'unknown_state')
        calls = self.expressions(transport)
        self.assertTrue(all('__reprise_mode_trigger' not in call
                            for call in calls))

    def test_missing_selector_refuses_without_clicks(self):
        transport = mock.MagicMock()
        transport.evaluate.return_value = {'state': None, 'candidates': []}
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertFalse(result['verified'])
        self.assertEqual(result['reason'], 'selector_missing')
        self.assertEqual(result['diag']['candidates'], [])
        self.assertEqual(self.expressions(transport),
                         [adapter.MODE_PROBE, adapter.MODE_PROBE])

    def test_ambiguous_selector_refuses_with_bounded_candidates(self):
        candidates = [{'tag': 'button', 'text': 'ChatGPT'},
                      {'tag': 'button', 'text': 'Codex'}]
        transport = mock.MagicMock()
        transport.evaluate.return_value = {'state': 'ChatGPT',
                                           'candidates': candidates}
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertFalse(result['verified'])
        self.assertEqual(result['reason'], 'selector_ambiguous')
        self.assertEqual(result['diag']['candidates'], candidates)
        self.assertFalse(result['diag']['truncated'])
        self.assertEqual(self.expressions(transport),
                         [adapter.MODE_PROBE, adapter.MODE_PROBE])

    def test_unknown_state_refuses_without_clicks(self):
        transport = mock.MagicMock()
        transport.evaluate.return_value = {
            'state': 'Turbo',
            'candidates': [{'tag': 'button', 'text': 'Turbo'}]}
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertFalse(result['verified'])
        self.assertEqual(result['reason'], 'unknown_state')
        self.assertEqual(self.expressions(transport),
                         [adapter.MODE_PROBE, adapter.MODE_PROBE])

    def test_trigger_click_failure_refuses_click_no_change(self):
        transport = mock.MagicMock()
        transport.evaluate.side_effect = [
            self.codex_probe(), {'clicked': False, 'reason': 'trigger_not_unique'},
            self.codex_probe()]
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertFalse(result['verified'])
        self.assertEqual(result['reason'], 'click_no_change')
        calls = self.expressions(transport)
        self.assertEqual(len(calls), 3)
        self.assertTrue(all('__reprise_mode_item' not in call
                            for call in calls[1:]))

    def test_item_click_failure_refuses_click_no_change(self):
        transport = mock.MagicMock()
        transport.evaluate.side_effect = [
            self.codex_probe(), {'clicked': True}, self.menu_open(),
            {'clicked': False, 'reason': 'item_not_unique'},
            self.codex_probe()]
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertFalse(result['verified'])
        self.assertEqual(result['reason'], 'click_no_change')
        calls = self.expressions(transport)
        self.assertEqual(len(calls), 5)
        self.assertIn('__reprise_mode_item', calls[3])

    def test_reread_still_codex_refuses_click_no_change(self):
        transport = mock.MagicMock()
        transport.evaluate.side_effect = [
            self.codex_probe(), {'clicked': True}, self.menu_open(),
            {'clicked': True}, self.codex_probe(), self.codex_probe()]
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertFalse(result['verified'])
        self.assertEqual(result['reason'], 'click_no_change')
        self.assertEqual(result['diag']['candidates'],
                         self.codex_probe()['candidates'])
        self.assertEqual(transport.evaluate.call_count, 6)

    def test_menu_opens_on_a_later_poll_and_switch_succeeds(self):
        # Correctif 2 (#248): the Radix menu may not be listed on the first
        # poll; a later probe inside the bounded window completes the switch.
        transport = mock.MagicMock()
        transport.evaluate.side_effect = [
            self.codex_probe(), {'clicked': True}, self.menu_closed(),
            self.menu_open(), {'clicked': True}, self.chatgpt_probe()]
        with mock.patch.object(adapter.time, 'sleep') as fake_sleep:
            result = adapter.ensure_chatgpt_mode(transport)
        self.assertEqual(result, {'verified': True, 'mode': 'ChatGPT'})
        calls = self.expressions(transport)
        self.assertEqual(len(calls), 6)
        self.assertIn('__reprise_mode_menu', calls[2])
        self.assertIn('__reprise_mode_menu', calls[3])
        self.assertIn('__reprise_mode_item', calls[4])
        fake_sleep.assert_called_once_with(adapter.MENU_POLL_INTERVAL_S)

    def test_menu_never_opened_refuses_menu_not_opened_without_item_click(self):
        # Correctif 2 (#248): if the Radix menu never opens within the bounded
        # window, refuse menu_not_opened — no item click is attempted, and the
        # diagnostic carries the bounded menu sample for the receipt.
        transport = mock.MagicMock()
        transport.evaluate.side_effect = [
            self.codex_probe(), {'clicked': True},
            self.menu_closed(), self.menu_closed(), self.menu_closed(),
            self.codex_probe()]
        clock = {'now': 0.0}
        with mock.patch.object(adapter.time, 'monotonic',
                               side_effect=lambda: clock['now']), \
                mock.patch.object(
                    adapter.time, 'sleep',
                    side_effect=lambda _s: clock.update(
                        {'now': clock['now'] + 0.3})):
            result = adapter.ensure_chatgpt_mode(transport)
        self.assertFalse(result['verified'])
        self.assertEqual(result['reason'], 'menu_not_opened')
        self.assertEqual(result['diag']['menu'], self.menu_closed()['sample'])
        calls = self.expressions(transport)
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[0], adapter.MODE_PROBE)
        self.assertTrue(all('__reprise_mode_menu' in call
                            for call in calls[2:5]))
        self.assertTrue(all('__reprise_mode_item' not in call
                            for call in calls))
        self.assertEqual(calls[5], adapter.MODE_PROBE)

    def test_evaluate_error_refuses_cdp_error(self):
        transport = mock.MagicMock()
        transport.evaluate.side_effect = TimeoutError('pipe closed')
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertEqual(result, {'verified': False, 'reason': 'cdp_error',
                                  'detail': 'TimeoutError', 'diag': None})

    def test_non_dict_probe_answer_refuses_cdp_error(self):
        transport = mock.MagicMock()
        transport.evaluate.return_value = 'garbage'
        result = adapter.ensure_chatgpt_mode(transport)
        self.assertEqual(result['reason'], 'cdp_error')
        self.assertEqual(result['detail'], 'ValueError')
        self.assertIsNone(result['diag'])

    def test_holder_send_confirms_with_mode_and_mode_verified(self):
        transport = self.fake()
        result = self.invoke(transport, request='req-248-mode-ok')
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['mode'], 'ChatGPT')
        self.assertTrue(result['mode_verified'])

    def test_holder_codex_switch_confirms_after_mode_change(self):
        transport = self.fake(mode=['codex', {'clicked': True},
                                    {'clicked': True}, 'chatgpt'])
        result = self.invoke(transport, request='req-248-mode-switch')
        self.assertEqual(result['status'], 'confirmed')
        self.assertEqual(result['mode'], 'ChatGPT')
        self.assertTrue(result['mode_verified'])
        calls = self.expressions(transport)
        self.assertTrue(any('__reprise_mode_trigger' in call for call in calls))
        self.assertTrue(any('__reprise_mode_menu' in call for call in calls))
        self.assertTrue(any('__reprise_mode_item' in call for call in calls))

    def test_holder_mode_refusal_never_dispatches(self):
        scenarios = {
            'selector_missing': {'mode': 'missing'},
            'selector_ambiguous': {'mode': 'ambiguous'},
            'unknown_state': {'mode': 'unknown'},
            'click_no_change': {'mode': ['codex', {'clicked': True},
                                         {'clicked': True}, 'codex']},
            'menu_not_opened': {'mode': ['codex', {'clicked': True},
                                         'codex'],
                                'menu': {'open': False,
                                         'sample': {'items': 0,
                                                    'popper_present': False,
                                                    'popper_text': None}}},
            'cdp_error': {'mode': TimeoutError('pipe closed')},
        }
        for reason, scenario in scenarios.items():
            with self.subTest(reason=reason):
                transport = self.fake(mode=scenario['mode'],
                                      menu=scenario.get('menu'))
                result = self.invoke(transport, request='req-248-mode-refusal')
                self.assertEqual(result['status'], 'refused')
                self.assertEqual(result['reason'], reason)
                self.assertNotIn('dispatch_action', result)
                self.assertNotIn('mode', result)
                self.assertNotIn('mode_verified', result)
                self.assertIsNone(result.get('prompt_sha256'))
                transport.insert_text.assert_not_called()
                sent = self.expressions(transport)
                self.assertFalse(any('return focusUniqueComposer(' in call
                                     for call in sent))


# Issue #280: the review prompt cites the sentinel in its instruction; a
# clipped or re-rendered echo then escaped every #246 guard and the cited
# sentinel confirmed the echoed prompt as the reply (cas a2/a3, 27/11/2026).
PROMPT_280 = ('Revue du commit f959fa0 : vérifie le budget d\'attente et '
              'réponds par une ligne VERDICT: ACCORD ou VERDICT: REFUS suivie '
              'de tes motifs, puis termine ta réponse par la ligne\n'
              + adapter.REPLY_SENTINEL)
# Rendered echo without its first words (collapsed user message).
CLIPPED_ECHO_280 = PROMPT_280[len('Revue du commit f959fa0 : '):]


class TestCapturePromptEcho280(unittest.TestCase):
    """Issue #280: an echo of the prompt is never confirmed as the reply."""

    def setUp(self):
        self.holder = TestGuardedSend()
        self.holder.setUp()
        self.addCleanup(self.holder._folder.cleanup)

    def send(self, body, request):
        transport = self.holder.fake(body=body, quiet_ms=adapter.STREAM_QUIET_MS)
        with HolderHarness(transport, self.holder.binary, self.holder.root), \
                mock.patch.object(adapter, 'time', AcceleratedTime()):
            receipt = adapter.send(PROMPT_280, self.holder.binary,
                                   self.holder.root, 'dedicated',
                                   request_key=request)
        return transport, receipt

    def test_body_text_clipped_echo_after_prior_turn_is_not_confirmed(self):
        # Body path: the previous reply ends with the sentinel, the current
        # echo is clipped and carries no «Vous avez dit» label, no reply yet.
        body = ('ChatGPT a dit :\nRéponse du tour précédent '
                + adapter.REPLY_SENTINEL + '\n' + CLIPPED_ECHO_280)
        transport, receipt = self.send(body, 'req-280-body')
        self.assertEqual(receipt['status'], 'captured_incomplete')
        self.assertEqual(receipt['reason'], 'capture_is_prompt_echo')
        self.assertFalse(receipt['response_complete'])
        transport.insert_text.assert_called_once_with(PROMPT_280)

    def test_assistant_container_echo_is_captured_incomplete(self):
        # Anchor-independent gate: even a DOM-anchored capture made of the
        # prompt words fails with the dedicated reason, never re-sent.
        body = {'text': CLIPPED_ECHO_280, 'assistant_text': CLIPPED_ECHO_280}
        transport, receipt = self.send(body, 'req-280-assistant')
        self.assertEqual(receipt['status'], 'captured_incomplete')
        self.assertEqual(receipt['reason'], 'capture_is_prompt_echo')
        self.assertFalse(receipt['response_complete'])
        transport.insert_text.assert_called_once_with(PROMPT_280)

    def test_rendered_reply_is_still_confirmed(self):
        body = ('Vous avez dit :\n' + PROMPT_280
                + '\nChatGPT a dit :\nVERDICT: ACCORD\nLe budget est borné '
                'et le reçu reste explicite en cas de dépassement. '
                + adapter.REPLY_SENTINEL)
        _transport, receipt = self.send(body, 'req-280-reply')
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertTrue(receipt['response_text'].startswith('VERDICT: ACCORD'))

    def test_is_prompt_echo_thresholds(self):
        self.assertTrue(adapter.is_prompt_echo(CLIPPED_ECHO_280, PROMPT_280))
        self.assertTrue(adapter.is_prompt_echo(PROMPT_280, PROMPT_280))
        # A short verdict repeating proposed words is not judged.
        self.assertFalse(adapter.is_prompt_echo('VERDICT: ACCORD', PROMPT_280))
        # A reply quoting part of the prompt but adding its own content.
        quoting = ('Tu demandes de vérifier le budget d\'attente. VERDICT: '
                   'REFUS : le délai de 30 s est dépassé sur les longues '
                   'réponses et le reçu ne signale pas la cause exacte.')
        self.assertFalse(adapter.is_prompt_echo(quoting, PROMPT_280))
        self.assertEqual(adapter._usable_reply(CLIPPED_ECHO_280, 'sentinel',
                                               PROMPT_280),
                         (False, 'capture_is_prompt_echo'))


if __name__ == '__main__':
    unittest.main()
