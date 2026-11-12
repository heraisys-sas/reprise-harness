"""Offline CDP orchestration: exact destination, drafts and uncertain clicks."""
import hashlib
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_desktop_apply as transport

CHAT = '00000000-0000-0000-0000-000000000001'
OTHER = '00000000-0000-0000-0000-000000000002'
PROMPT = 'Only this bounded prompt'
SHA = hashlib.sha256(PROMPT.encode()).hexdigest()
URL = 'https://claude.ai/chat/' + CHAT


def info(text='', url=URL, text_sha256=None, **extra):
    if text_sha256 is None:
        text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    return dict(url=url, composerPresent=True, text_length=len(text), hasText=bool(text),
                text_sha256=text_sha256, sendDisabled=False, **extra)


def page(url=URL, ident='target-1', ws=None):
    return dict(type='page', id=ident, url=url,
                webSocketDebuggerUrl=ws or f'ws://127.0.0.1:9333/devtools/page/{ident}')


class PromptGuards(unittest.TestCase):
    def test_invalid_inputs_never_connect(self):
        cases = [dict(prompt=''), dict(prompt=' \n '), dict(prompt=None),
                 dict(prompt='x' * (transport.MAX_PROMPT_CHARS + 1)),
                 dict(prompt=PROMPT, chat_id='not-a-chat'), dict(prompt=PROMPT, port=0),
                 dict(prompt=PROMPT, port=True), dict(prompt=PROMPT, settle=float('inf')),
                 dict(prompt=PROMPT, submit='true'), dict(prompt=PROMPT, on_login='automatic'),
                 dict(prompt='surrogate\ud800')]
        with patch.object(transport, 'CDP') as connect:
            for args in cases:
                with self.subTest(args=list(args)):
                    self.assertFalse(transport.apply(**({'chat_id': CHAT} | args))['ok'])
            connect.assert_not_called()

    def test_existing_chat_is_required_without_connecting(self):
        with patch.object(transport, 'CDP') as connect:
            for args in ({}, {'chat_id': None}, {'chat_id': ''}):
                with self.subTest(args=args):
                    result = transport.apply(PROMPT, **args)
                    self.assertEqual(result['stage'], 'paramètres')
                    self.assertFalse(result['dispatch_attempted'])
                    self.assertFalse(result['staged'])
            connect.assert_not_called()

    def test_bound_is_preserved(self):
        with patch.object(transport, 'CDP', side_effect=OSError('private error')) as connect:
            result = transport.apply('x' * transport.MAX_PROMPT_CHARS, CHAT)
        self.assertEqual(result['stage'], 'connexion')
        self.assertNotIn('private error', json.dumps(result))
        connect.assert_called_once()


class TargetSelection(unittest.TestCase):
    def test_origin_is_exact_and_no_other_page_is_adopted(self):
        for url in ('https://evil.test/?claude.ai', 'https://claude.ai.evil.test/chat/' + CHAT,
                    'https://claude.ai@evil.test/', 'http://claude.ai/chat/' + CHAT,
                    URL + '?secret=value', URL + '#secret', 'about:blank'):
            with self.subTest(url=url):
                self.assertIsNone(transport.CDP._pick_page([page(url)]))
        self.assertIsNone(transport.CDP._pick_page([]))

    def test_exact_chat_preferred_and_ambiguous_tabs_refused(self):
        expected = page()
        self.assertEqual(transport.CDP._pick_page([page('https://claude.ai/chat/' + OTHER), expected], CHAT), expected)
        self.assertIsNone(transport.CDP._pick_page([expected, page(ident='second')], CHAT))
        self.assertIsNone(transport.CDP._pick_page([expected, page('https://claude.ai/new', ident='second')]))

    def test_debugger_url_must_match_local_port_and_target(self):
        urls = ['ws://evil.test:9333/devtools/page/target-1',
                'ws://127.0.0.1:9444/devtools/page/target-1',
                'ws://127.0.0.1:9333/devtools/page/other',
                'ws://user@127.0.0.1:9333/devtools/page/target-1',
                'ws://127.0.0.1:9333/devtools/page/target-1?private=value',
                'ws://127.0.0.1:invalid/devtools/page/target-1']
        with patch.object(transport, 'WebSocket') as connect:
            for url in urls:
                with self.subTest(url=url), patch.object(transport.CDP, '_targets', return_value=[page(ws=url)]):
                    with self.assertRaises(transport.WSError):
                        transport.CDP()
            connect.assert_not_called()

    def test_selected_page_connection_is_pinned(self):
        with patch.object(transport.CDP, '_targets', return_value=[page()]), \
                patch.object(transport, 'WebSocket') as connect:
            client = transport.CDP(chat_id=CHAT)
        self.assertEqual(client.target_id, 'target-1')
        connect.assert_called_once_with('127.0.0.1', 9333, '/devtools/page/target-1', timeout=10.0)

    def test_navigation_error_is_not_ignored_or_echoed(self):
        client = transport.CDP.__new__(transport.CDP)
        client.call = Mock(return_value={'errorText': 'private navigation details'})
        with self.assertRaises(transport.WSError) as caught:
            client.navigate(URL)
        self.assertNotIn('private', str(caught.exception))

class ApplyOrchestration(unittest.TestCase):
    def run_apply(self, snapshots=None, send=None, prepare=True, source=None, **kwargs):
        self.client = Mock()
        snapshots = [info() if source is None else source] + ([info(), info(PROMPT)] if snapshots is None else snapshots)
        with patch.object(transport, 'CDP', return_value=self.client), \
                patch.object(transport, 'wait_composer', return_value=True), \
                patch.object(transport, 'state', side_effect=snapshots), \
                patch.object(transport, '_prepare', return_value=prepare) as ready, \
                patch.object(transport, '_send', return_value=send or {'clicked': True, 'stage': 'envoyé'}) as click, \
                patch.object(transport.time, 'sleep'), \
                patch.object(transport, 'raise_for_login') as lifecycle:
            result = transport.apply(PROMPT, CHAT, settle=0, **kwargs)
        self.ready, self.click, self.lifecycle = ready, click, lifecycle
        self.client.close.assert_called_once()
        return result

    def test_exact_text_is_inserted_and_click_is_only_observed(self):
        result = self.run_apply()
        self.client.call.assert_called_once_with('Input.insertText', {'text': PROMPT})
        self.click.assert_called_once_with(self.client, '/chat/' + CHAT, SHA)
        self.assertTrue(result['dispatch_attempted'])
        self.assertTrue(result['click_observed'])
        self.assertFalse(result['admission_confirmed'])
        self.assertFalse(result['response_received'])
        self.assertNotEqual(transport.to_submission(result)['status'], 'finished')

    def test_source_draft_is_preserved_before_navigation(self):
        source = info('private source draft', url='https://claude.ai/chat/' + OTHER)
        result = self.run_apply(source=source)
        self.assertEqual(result['stage'], 'brouillon')
        self.client.navigate.assert_not_called()
        self.client.call.assert_not_called()
        self.click.assert_not_called()

    def test_already_targeted_page_is_not_reloaded(self):
        self.run_apply()
        self.client.navigate.assert_not_called()

    def test_empty_source_can_navigate_to_exact_destination(self):
        self.run_apply(source=info(url='https://claude.ai/chat/' + OTHER))
        self.client.navigate.assert_called_once_with(URL)

    def test_redirect_other_chat_and_login_do_not_touch_composer(self):
        for url, stage in [('https://claude.ai/chat/' + OTHER, 'cible'),
                           ('https://evil.test/?claude.ai', 'cible'),
                           ('https://claude.ai/login', 'session')]:
            with self.subTest(stage=stage):
                result = self.run_apply([info(url=url)], on_login='raise')
                self.assertEqual(result['stage'], stage)
                self.client.call.assert_not_called()
                self.ready.assert_not_called()
                self.click.assert_not_called()
                self.lifecycle.assert_not_called()

    def test_existing_draft_including_whitespace_is_preserved(self):
        for draft in ('private draft', ' \n '):
            with self.subTest(draft=repr(draft)):
                result = self.run_apply([info(draft)])
                self.assertEqual(result['stage'], 'brouillon')
                self.client.call.assert_not_called()
                self.click.assert_not_called()
                self.assertNotIn(draft, json.dumps(result))

    def test_target_changed_immediately_before_insertion_is_refused(self):
        result = self.run_apply([info()], prepare=False)
        self.assertEqual(result['stage'], 'cible')
        self.client.call.assert_not_called()
        self.click.assert_not_called()

    def test_insertion_must_match_full_text_not_presence_or_prefix(self):
        for after in ('', PROMPT[:-1], PROMPT + ' user suffix', 'an unrelated draft'):
            with self.subTest(after=after):
                result = self.run_apply([info(), info(after)])
                self.assertEqual(result['stage'], 'empreinte')
                self.assertFalse(result['dispatch_attempted'])
                self.click.assert_not_called()

    def test_target_changed_after_insertion_is_refused(self):
        result = self.run_apply([info(), info(PROMPT, url='https://claude.ai/chat/' + OTHER)])
        self.assertEqual(result['stage'], 'cible')
        self.click.assert_not_called()

    def test_prefill_is_not_a_turn(self):
        result = self.run_apply(submit=False)
        self.assertEqual(result['stage'], 'posé')
        self.assertTrue(result['staged'])
        self.assertEqual(result['detail']['expected_sha256'], SHA)
        self.assertFalse(result['dispatch_attempted'])
        self.click.assert_not_called()
        self.assertEqual(transport.to_submission(result)['status'], 'needs_review')

    def test_send_guard_failure_proves_no_click_but_not_quota(self):
        for stage in ('cible', 'empreinte', 'composeur', 'bloqué', 'text_mismatch', 'readiness_timeout'):
            with self.subTest(stage=stage):
                result = self.run_apply(send={'clicked': False, 'stage': stage})
                self.assertEqual(result['stage'], stage)
                self.assertFalse(result['dispatch_attempted'])
                mapped = transport.to_submission(result)
                self.assertEqual(mapped['status'], 'needs_review')
                self.assertNotIn('Quota', mapped['reason'])

    def test_canonical_text_compares_non_empty_line_sequence(self):
        # Blank lines are layout, not content: ProseMirror renders one LF as
        # an innerText blank line between <p> blocks (issue #108).
        self.assertEqual(transport.canonical_text('a\r\n\r\nb'), 'a\nb')
        self.assertEqual(transport.canonical_text('a\u00a0b \t\n c'), 'a b\n c')
        self.assertEqual(transport.canonical_text('a\n\nb'), transport.canonical_text('a\nb'))
        self.assertEqual(transport.canonical_text('\n a \n \n b \n'), ' a\n b')
        self.assertNotEqual(transport.canonical_text('a\nb'), transport.canonical_text('a\nx'))

    def test_multiline_prompts_use_the_canonical_fingerprint(self):
        # Full orchestration: a multiline prompt reaches _send with the
        # canonical fingerprint (not the raw sha) and dispatches once.
        prompt = 'one\n\ntwo'
        fingerprint = hashlib.sha256(transport.canonical_text(prompt).encode()).hexdigest()
        self.client = Mock()
        with patch.object(transport, 'CDP', return_value=self.client), \
                patch.object(transport, 'wait_composer', return_value=True), \
                patch.object(transport, 'state', side_effect=[info(), info(),
                    info(prompt, text_sha256=fingerprint)]), \
                patch.object(transport, '_prepare', return_value=True) as ready, \
                patch.object(transport, '_send', return_value={'clicked': True, 'stage': 'envoyé'}) as click, \
                patch.object(transport.time, 'sleep'), \
                patch.object(transport, 'raise_for_login'), \
                patch.object(transport, '_confirm', return_value=True):
            result = transport.apply(prompt, CHAT, settle=0)
        self.assertEqual(result['stage'], 'envoyé')
        click.assert_called_once_with(self.client, '/chat/' + CHAT, fingerprint)

    def test_multiline_insertion_mismatch_leaves_zero_sends(self):
        prompt = 'first\n\nsecond'
        for after in ('first\nsecond', prompt + ' extra'):
            with self.subTest(after=repr(after)):
                result = self.run_apply([info(), info(after)])
                self.assertEqual(result['stage'], 'empreinte')
                self.assertFalse(result['dispatch_attempted'])
                self.click.assert_not_called()

    def test_failure_motifs_never_leak_prompt_content(self):
        prompt = 'secret multiline\n\nbody'
        fingerprint = hashlib.sha256(transport.canonical_text(prompt).encode()).hexdigest()
        for outcome in ({'clicked': False, 'stage': 'text_mismatch', 'length': 4,
                         'sha': fingerprint[:8]},
                        {'clicked': False, 'stage': 'readiness_timeout'}):
            with self.subTest(stage=outcome['stage']):
                result = self.run_apply(send=dict(outcome))
                self.assertNotIn(prompt, json.dumps(result))
                self.assertNotIn('secret', json.dumps(result))

    def test_lost_click_response_is_uncertain_and_never_replayed(self):
        self.client = Mock()
        with patch.object(transport, 'CDP', return_value=self.client), \
                patch.object(transport, 'state', side_effect=[info(), info(), info(PROMPT)]), \
                patch.object(transport, 'wait_composer'), patch.object(transport, '_prepare', return_value=True), \
                patch.object(transport, '_send', side_effect=TimeoutError('private')) as send, \
                patch.object(transport.time, 'sleep'):
            result = transport.apply(PROMPT, CHAT, settle=0)
        self.assertEqual(result['stage'], 'incertain')
        self.assertTrue(result['dispatch_attempted'])
        self.assertIsNone(result['staged'])
        send.assert_called_once()
        self.assertNotIn('private', json.dumps(result))

    def test_close_failure_does_not_hide_click_receipt(self):
        client = Mock()
        client.close.side_effect = OSError('private close error')
        with patch.object(transport, 'CDP', return_value=client), \
                patch.object(transport, 'state', return_value=info(PROMPT)), \
                patch.object(transport, '_send', return_value={'stage': 'envoyé', 'clicked': True}):
            result = transport.send_staged(chat_id=CHAT, expected_sha256=SHA)
        self.assertTrue(result['dispatch_attempted'])
        self.assertTrue(result['click_observed'])
        self.assertNotIn('private', json.dumps(result))

    def test_lost_insertion_response_never_attempts_a_click(self):
        self.client = Mock()
        self.client.call.side_effect = TimeoutError('private')
        with patch.object(transport, 'CDP', return_value=self.client), \
                patch.object(transport, 'state', return_value=info()), \
                patch.object(transport, 'wait_composer'), patch.object(transport, '_prepare', return_value=True), \
                patch.object(transport, '_send') as send, patch.object(transport.time, 'sleep'):
            result = transport.apply(PROMPT, CHAT, settle=0)
        self.assertFalse(result['dispatch_attempted'])
        self.assertIsNone(result['staged'])
        send.assert_not_called()


class StagedGuards(unittest.TestCase):
    def test_target_and_hash_are_required_before_io(self):
        with patch.object(transport, 'CDP') as connect:
            with self.assertRaises(TypeError):
                transport.send_staged()
            for args in (dict(chat_id=None, expected_sha256=SHA),
                         dict(chat_id=CHAT, expected_sha256=''),
                         dict(chat_id='bad', expected_sha256=SHA)):
                self.assertFalse(transport.send_staged(**args)['ok'])
            connect.assert_not_called()

    def test_wrong_chat_or_modified_draft_never_clicks(self):
        for snapshot in (info(PROMPT, url='https://claude.ai/chat/' + OTHER),
                         info(PROMPT + ' changed'), info('')):
            with patch.object(transport, 'CDP'), patch.object(transport, 'state', return_value=snapshot), \
                    patch.object(transport, '_send') as send:
                result = transport.send_staged(chat_id=CHAT, expected_sha256=SHA)
            self.assertFalse(result['dispatch_attempted'])
            send.assert_not_called()

    def test_valid_staged_uses_same_guarded_primitive_without_navigation_or_typing(self):
        client = Mock()
        with patch.object(transport, 'CDP', return_value=client), \
                patch.object(transport, 'state', return_value=info(PROMPT)), \
                patch.object(transport, '_send', return_value={'stage': 'envoyé', 'clicked': True}) as send:
            result = transport.send_staged(chat_id=CHAT, expected_sha256=SHA)
        self.assertTrue(result['click_observed'])
        send.assert_called_once_with(client, '/chat/' + CHAT, SHA)
        client.navigate.assert_not_called()
        client.call.assert_not_called()


class DiagnosticsAndLifecycle(unittest.TestCase):
    def test_quota_unknown_even_with_disabled_button(self):
        for disabled in (False, True, None):
            snapshot = info(PROMPT)
            snapshot['sendDisabled'] = disabled
            snapshot.update(text='private draft', dialogs=['private dialog'])
            with patch.object(transport, 'CDP'), patch.object(transport, 'state', return_value=snapshot):
                result = transport.quota()
            self.assertEqual(result['available'], disabled is not None)
            self.assertIsNone(result['blocked'])
            self.assertFalse(result['quota_confirmed'])
            self.assertNotIn('private', json.dumps(result))
            self.assertFalse(result['lifecycle_supported'])
            self.assertEqual(result['lifecycle_reason'], 'lifecycle_unverified')

    def test_missing_browser_is_not_available_or_unblocked(self):
        with patch.object(transport, 'CDP', side_effect=OSError('private endpoint error')):
            result = transport.quota()
        self.assertFalse(result['available'])
        self.assertIsNone(result['blocked'])
        self.assertNotIn('private', json.dumps(result))

    def test_mapping_cannot_promote_click_draft_or_old_quota_hint(self):
        for stage in ('envoyé', 'posé', 'quota', 'bloqué', 'incertain', 'frappe'):
            mapped = transport.to_submission({'ok': True, 'stage': stage, 'detail': {'text': 'private draft'}})
            self.assertEqual(mapped['status'], 'needs_review')
            self.assertIsNone(mapped['response'])
            self.assertFalse(mapped['admission_confirmed'])
            self.assertNotIn('private', json.dumps(mapped))
        self.assertEqual(transport.to_submission({'stage': 'session'})['status'], 'blocked_auth')

    def test_lifecycle_refuses_without_process_commands_or_port_adoption(self):
        with patch.object(transport, 'browser_up', return_value=True) as up:
            self.assertFalse(transport.launch())
            self.assertFalse(transport.shutdown())
            self.assertFalse(transport.raise_for_login())
            up.assert_not_called()
        source = Path(transport.__file__).read_text()
        for forbidden in ('pkill', 'subprocess', 'request_key'):
            self.assertNotIn(forbidden, source)


class EventInsertion(unittest.TestCase):
    """Lot #101: React must observe the insertion; a click is not a send."""

    def test_insert_expression_is_event_driven_not_plain_assignment(self):
        client = Mock()
        client.evaluate.return_value = {'inserted': True, 'length': len(PROMPT)}
        result = transport._insert(client, PROMPT)
        self.assertTrue(result['inserted'])
        expression = client.evaluate.call_args[0][0]
        self.assertIn("execCommand('insertText'", expression)
        self.assertIn("InputEvent('input'", expression)
        self.assertIn('bubbles: true', expression)
        self.assertNotIn('el.innerText =', expression)

    def test_insertion_not_confirmed_is_reported(self):
        client = Mock()
        client.evaluate.return_value = {'inserted': False, 'length': 0}
        self.assertFalse(transport._insert(client, PROMPT)['inserted'])
        client.evaluate.return_value = None
        self.assertFalse(transport._insert(client, PROMPT)['inserted'])

    def test_arm_requires_enabled_button_without_any_fallback(self):
        client = Mock()
        client.evaluate.return_value = {'ready': True}
        self.assertTrue(transport._arm(client)['ready'])
        client.evaluate.return_value = {'ready': False}
        expression = client.evaluate.call_args[0][0]
        # No Enter fallback at all: a synthetic keydown could send by itself.
        self.assertNotIn("KeyboardEvent('keydown'", expression)
        self.assertFalse(transport._arm(client)['ready'])
        client.evaluate.return_value = None
        self.assertFalse(transport._arm(client)['ready'])

    def test_send_expression_uses_canonical_form_and_no_enter_fallback(self):
        client = Mock()
        client.evaluate.return_value = {'stage': 'envoyé', 'clicked': True}
        transport._send(client, '/chat/' + CHAT, SHA)
        expression = client.evaluate.call_args[0][0]
        self.assertIn('canon(', expression)
        self.assertNotIn("KeyboardEvent('keydown'", expression)
        self.assertIn('text_mismatch', expression)
        self.assertIn('readiness_timeout', expression)

    def test_confirm_requires_positive_evidence_or_failure_is_not_confirmed(self):
        client = Mock()
        client.evaluate.return_value = {'confirmed': True}
        self.assertTrue(transport._confirm(client))
        client.evaluate.return_value = {'confirmed': False}
        self.assertFalse(transport._confirm(client))
        client.evaluate.side_effect = TimeoutError('private')
        self.assertFalse(transport._confirm(client))

    def test_click_without_send_evidence_is_uncertain_never_envoye(self):
        client = Mock()
        client.evaluate.side_effect = [{'stage': 'envoyé', 'clicked': True}, {'confirmed': False}]
        with patch.object(transport, 'CDP', return_value=client), \
                patch.object(transport, 'state', return_value=info(PROMPT)):
            result = transport.send_staged(chat_id=CHAT, expected_sha256=SHA)
        self.assertEqual(result['stage'], 'incertain')
        self.assertTrue(result['dispatch_attempted'])
        self.assertTrue(result['click_observed'])
        self.assertEqual(transport.to_submission(result)['status'], 'needs_review')

    def test_uncertain_no_click_outcome_maps_to_uncertain_needs_review(self):
        client = Mock()
        client.evaluate.return_value = {'stage': 'incertain', 'clicked': False}
        with patch.object(transport, 'CDP', return_value=client), \
                patch.object(transport, 'state', return_value=info(PROMPT)):
            result = transport.send_staged(chat_id=CHAT, expected_sha256=SHA)
        self.assertEqual(result['stage'], 'incertain')
        self.assertTrue(result['dispatch_attempted'])
        self.assertFalse(result['click_observed'])
        self.assertEqual(transport.to_submission(result)['status'], 'needs_review')

    def test_click_with_send_evidence_confirmed(self):
        client = Mock()
        client.evaluate.side_effect = [{'stage': 'envoyé', 'clicked': True}, {'confirmed': True}]
        with patch.object(transport, 'CDP', return_value=client), \
                patch.object(transport, 'state', return_value=info(PROMPT)):
            result = transport.send_staged(chat_id=CHAT, expected_sha256=SHA)
        self.assertEqual(result['stage'], 'envoyé')
        self.assertTrue(result['ok'])
        self.assertTrue(result['click_observed'])


class CDPProtocol(unittest.TestCase):
    def client(self):
        client = transport.CDP.__new__(transport.CDP)
        client._id, client.timeout, client.ws = 0, 1, Mock()
        return client

    def test_error_details_are_not_returned(self):
        client = self.client()
        client.ws.receive.return_value = {'id': 1, 'error': {'message': 'private prompt'}}
        with self.assertRaises(transport.WSError) as caught:
            client.call('Runtime.evaluate')
        self.assertNotIn('private', str(caught.exception))

    def test_notifications_are_bounded(self):
        client = self.client()
        client.ws.receive.return_value = {'method': 'notification'}
        with self.assertRaises(transport.WSError):
            client.call('Runtime.evaluate')
        self.assertEqual(client.ws.receive.call_count, 201)

    def test_call_deadline_is_not_reset_by_events(self):
        client = self.client()
        client.ws.receive.side_effect = [{'method': 'event'}, {'id': 1, 'result': {}}]
        with patch.object(transport.time, 'monotonic', side_effect=[0, .1, 1.1]):
            with self.assertRaises(TimeoutError):
                client.call('Runtime.evaluate')
        self.assertEqual(client.ws.receive.call_count, 1)


if __name__ == '__main__':
    unittest.main()
