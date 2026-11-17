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
        self._patches = [
            mock.patch.object(adapter, 'launch_cdp_process',
                              return_value=(self.transport, self.process)),
            mock.patch.object(adapter, 'census', side_effect=self.census),
            mock.patch.object(adapter.subprocess, 'run'),
            mock.patch.object(adapter, 'terminate_chrome', new=self.terminate),
            mock.patch.object(adapter.time, 'sleep'),
        ]
        for patch in self._patches:
            patch.__enter__()

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

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=10)
        for patch in reversed(self._patches):
            patch.__exit__(None, None, None)
        return False


class TestGuardedSend(unittest.TestCase):
    """All send tests go through the persistent holder; no Chrome launch."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(folder.name)
        self.root = self.base / 'profiles'
        self.binary = self.base / 'ChatGPT'
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o700)

    def fake(self, *, first=None, staged=None, click=None, turns=None, axtree=None,
             sse=None):
        transport = mock.MagicMock()
        transport.drain_notifications.return_value = sse if sse is not None else []
        first = first if first is not None else {
            'status': 'ready', 'users': 2, 'assistants': 1}
        staged = staged if staged is not None else {
            'status': 'ready', 'users': 2, 'assistants': 1}
        click = click if click is not None else {'clicked': True, 'status': 'clicked'}
        turns = turns if turns is not None else {
            'status': 'observed', 'users': 3, 'assistants': 1,
            'user_confirmed': True, 'composer_emptied': True,
            'assistant_observed': False, 'assistant_text': ''}
        # Default AX tree: stable reply after the prompt marker, so any
        # click-observed receipt completes after two identical polls.
        if axtree is None:
            axtree = ['Sidebar « Revue de code Reprise »', 'synthetic request',
                      'Assistant reply body.']
        calls = {'poll': 0}

        def respond(expression):
            if 'return composerStatus(null);' in expression:
                return first
            if 'return composerStatus(' in expression:
                return staged
            if 'return focusUniqueComposer(' in expression:
                return True
            if 'return clickUniqueSend(' in expression:
                if isinstance(click, Exception):
                    raise click
                return click
            if 'return observeTurns(' in expression:
                if isinstance(turns, Exception):
                    raise turns
                return turns
            raise AssertionError('Unexpected Runtime.evaluate')

        def respond_call(method, params):
            nonlocal calls
            if method == 'Network.enable':
                return {}
            self.assertEqual(method, 'Accessibility.getFullAXTree')
            if isinstance(axtree, Exception):
                raise axtree
            if callable(axtree):
                payload = axtree(calls['poll'])
            else:
                payload = axtree
            calls['poll'] += 1
            return {'nodes': [{'nodeId': str(index), 'name': {'value': line}}
                              for index, line in enumerate(payload)]}

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

    def test_sent_requires_click_user_turn_match_and_emptied_composer(self):
        transport = self.fake()
        result = self.invoke(transport)
        self.assertEqual(result['status'], 'sent')
        self.assertEqual(result['user_message_count_before'], 2)
        self.assertEqual(result['user_message_count_after'], 3)
        self.assertTrue(result['user_confirmed'])
        self.assertTrue(result['composer_emptied'])
        self.assertTrue(result['click_observed'])
        self.assertTrue(result['attempted'])
        transport.insert_text.assert_called_once_with('synthetic request')
        self.assertEqual(sum('return clickUniqueSend(' in c.args[0]
                             for c in transport.evaluate.call_args_list), 1)
        with mock.patch.object(adapter, 'launch_cdp_process') as launch:
            replayed = adapter.send('synthetic request', self.binary, self.root,
                                    'dedicated', request_key='req-218')
        launch.assert_not_called()
        self.assertTrue(replayed['replayed'])
        self.assertEqual(replayed['status'], 'sent')

    def test_confirmed_only_when_new_assistant_is_observed(self):
        transport = self.fake(turns={'status': 'observed', 'users': 3, 'assistants': 2,
                                     'user_confirmed': True, 'composer_emptied': True,
                                     'assistant_observed': True, 'assistant_text': 'reply'})
        receipt = self.invoke(transport)
        self.assertEqual(receipt['status'], 'confirmed')
        self.assertTrue(receipt['response_observed'])
        self.assertEqual(receipt['assistant_excerpt'], 'reply')
        # AX capture (issue #156) still ran after the click: the DOM-excerpt
        # confirmation does not suppress the AXTree completion evidence.
        self.assertTrue(receipt['response_complete'])

    def test_click_transport_loss_is_durable_uncertainty_without_retry(self):
        transport = self.fake(click=RuntimeError('lost after dispatch'))
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
        receipt = self.invoke(self.fake(turns=RuntimeError('lost')))
        self.assertEqual(receipt['status'], 'incertain')
        self.assertTrue(receipt['click_observed'])

    def test_wrong_turn_or_unemptied_composer_is_never_sent(self):
        for index, wrong in enumerate((
            {'users': 4, 'user_confirmed': False, 'composer_emptied': True},
            {'users': 3, 'user_confirmed': False, 'composer_emptied': True},
            {'users': 3, 'user_confirmed': True, 'composer_emptied': False},
        )):
            with self.subTest(wrong=wrong):
                state = {'status': 'observed', 'users': 3, 'assistants': 1,
                         'user_confirmed': True, 'composer_emptied': True,
                         'assistant_observed': False, 'assistant_text': ''} | wrong
                receipt = self.invoke(self.fake(turns=state), request='req-' + str(index))
                self.assertEqual(receipt['status'], 'incertain')
                self.assertTrue(receipt['click_observed'])

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
        self.assertIn('data-testid', source)
        self.assertNotIn('dispatchEvent', source)
        self.assertNotIn('dispatchMouseEvent', source)
        self.assertNotIn('remote-debugging-port', source)

class TestAxtreeResponseCapture(unittest.TestCase):
    """Issue #156 option B: AXTree polling after a click, offline transports."""

    def setUp(self):
        self.holder = TestGuardedSend()
        self.holder.setUp()

    def invoke(self, axtree=None, request='req-ax'):
        transport = self.holder.fake(axtree=axtree)
        return transport, self.holder.invoke(transport, request=request)

    def test_stable_two_polls_complete_and_extract_reply(self):
        transport, receipt = self.invoke(request='req-ax-1')
        self.assertTrue(receipt['response_complete'])
        expected = hashlib.sha256('Assistant reply body.'.encode('utf-8')).hexdigest()
        self.assertEqual(receipt['response_sha256'], expected)
        self.assertEqual(receipt['response_chars'], len('Assistant reply body.'))
        self.assertEqual(receipt['response_excerpt'], 'Assistant reply body.')
        self.assertEqual(receipt['status'], 'sent')
        self.assertGreaterEqual(transport.call.call_count, 2)

    def test_changing_then_stable_polls_complete(self):
        replies = iter(['Assistant reply body.',
                        'Assistant reply body. Also more text.',
                        'Assistant reply body. Also more text.'])

        def axtree(_poll):
            return ['synthetic request', next(replies)]

        transport, receipt = self.invoke(axtree=axtree, request='req-ax-2')
        self.assertTrue(receipt['response_complete'])
        self.assertIn('Also more text', receipt['response_excerpt'])

    def test_timeout_is_not_fatal_and_keeps_status(self):
        def axtree(_poll):
            return ['synthetic request', 'poll-%d unique drift' % _poll]

        transport = self.holder.fake(axtree=axtree)
        with mock.patch.object(adapter.time, 'monotonic',
                               side_effect=itertools.count(0, 200)):
            with HolderHarness(transport, self.holder.binary,
                               self.holder.root):
                receipt = adapter.send('synthetic request', self.holder.binary,
                                       self.holder.root, 'dedicated',
                                       request_key='req-ax-3')
        self.assertFalse(receipt['response_complete'])
        self.assertEqual(receipt['response_reason'], 'axtree_timeout')
        self.assertEqual(receipt['status'], 'sent')
        self.assertTrue(receipt['click_observed'])

    def test_axtree_unavailable_is_bounded_and_keeps_status(self):
        transport, receipt = self.invoke(axtree=RuntimeError('pipe lost'),
                                         request='req-ax-4')
        self.assertFalse(receipt['response_complete'])
        self.assertEqual(receipt['response_reason'], 'axtree_unavailable')
        self.assertEqual(receipt['status'], 'sent')

    def test_no_click_means_no_capture_and_no_optional_fields(self):
        transport = self.holder.fake(
            first={'status': 'selectors_stale', 'composer_count': 0})
        receipt = self.holder.invoke(transport, request='req-ax-5')
        self.assertEqual(receipt['status'], 'refused')
        transport.call.assert_not_called()
        for key in ('response_sha256', 'response_chars', 'response_excerpt',
                    'response_reason'):
            self.assertNotIn(key, receipt)


class TestHolderProtocol(unittest.TestCase):
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
        self.assertEqual(receipt['status'], 'sent')
        self.assertTrue(receipt['click_observed'])
        self.assertTrue(receipt['attempted'])
        transport.insert_text.assert_called_once_with('synthetic request')
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
        self.assertEqual(command[-1], 'serve')
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
        self.assertEqual(first['status'], 'sent')
        self.assertEqual(clicks, 1)
        self.assertTrue(second['replayed'])
        self.assertEqual(second['status'], 'sent')

    def test_holder_not_ready_refuses_without_terminating_chrome(self):
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
        harness.terminate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
