"""Offline contract tests for the dedicated ChatGPT Desktop CDP pipe probe.

No ChatGPT.app launch, operator session, network access or real prompt.
"""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import chatgpt_desktop_pipe as adapter
import claude_cdp_pipe


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


if __name__ == '__main__':
    unittest.main()
