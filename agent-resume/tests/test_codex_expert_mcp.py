import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import Bridge, error_payload, main
from test_codex_expert import ID

class RequestCalls:
    def call(self, name, args):
        if name not in ('reprise_backends', 'reprise_health', 'reprise_resume', 'reprise_history', 'reprise_requests'):
            args = {'request_key': str(uuid.uuid4()), **args}
        return self.bridge.call(name, args)


class BridgeTests(RequestCalls, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state', daily_limit=2)
        self.bridge.program = 'codex'
        self.addCleanup(self.bridge.db.close)
    def test_implementation_requires_installed_grant_before_admission(self):
        with patch('codex_expert_mcp.review') as review:
            with self.assertRaises(ValueError):
                self.call('reprise_delegate', {'question':'Implement', 'kind':'batch', 'mode':'implementation'})
            review.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)
        self.assertFalse(self.call('reprise_backends', {})['backends'][0]['writes'])

    def test_new_implementation_invalid_input_is_rejected_before_admission(self):
        self.bridge.codex_workspace_write = True
        with patch('codex_expert_mcp.review') as review:
            with self.assertRaises(ValueError):
                self.call('reprise_delegate', {'question':'Implement','kind':'batch','mode':'implementation','paths':['missing.py']})
            review.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)

    def test_implementation_followup_inherits_mode_after_restart_and_alias_cannot_promote(self):
        self.bridge.codex_workspace_write = True
        output = {'status':'finished', 'resumable':True, 'thread_id':ID, 'response':'Implemented'}
        with patch('codex_expert_mcp.review', return_value=output.copy()) as review:
            self.call('reprise_delegate', {'question':'Implement', 'kind':'batch', 'mode':'implementation'})
            self.assertTrue(review.call_args.kwargs['implementation'])
        restarted = Bridge(self.temp.name, {}, Path(self.temp.name)/'state', codex_workspace_write=True)
        self.addCleanup(restarted.db.close)
        restarted.program = 'codex'
        with patch('codex_expert_mcp.review', return_value=output.copy()) as review:
            restarted.call('reprise_followup', {'request_key':'continue-implementation', 'thread_id':ID, 'backend':'codex', 'question':'Continue'})
            self.assertTrue(review.call_args.kwargs['implementation'])
            with self.assertRaises(ValueError):
                restarted.call('codex_followup', {'request_key':'legacy-alias', 'thread_id':ID, 'question':'Continue'})
        restarted.codex_workspace_write = False
        with self.assertRaises(ValueError):
            restarted.call('reprise_followup', {'request_key':'revoked', 'thread_id':ID, 'backend':'codex', 'question':'Continue'})

    def test_legacy_review_cannot_be_promoted_by_prompt_or_mode(self):
        self.bridge.codex_workspace_write = True
        with patch('codex_expert_mcp.review', return_value={'status':'finished','resumable':True,'thread_id':ID,'response':'Review'}):
            self.call('codex_review', {'question':'Review'})
        with self.assertRaises(ValueError):
            self.call('reprise_followup', {'thread_id':ID,'backend':'codex','question':'Implement','mode':'implementation'})

    def test_expected_implementation_edit_is_not_an_input_integrity_failure(self):
        self.bridge.codex_workspace_write = True
        target = Path(self.temp.name) / 'source.py'
        target.write_text('before')
        def implementation(*args, **kwargs):
            target.write_text('after')
            return {'status':'finished','response':'Implemented'}
        with patch('codex_expert_mcp.review', side_effect=implementation):
            result = self.call('reprise_delegate', {'question':'Implement','kind':'batch','mode':'implementation','paths':['source.py'],'request_key':'edit-request'})
        self.assertEqual(result['status'], 'finished')
        self.assertTrue(result['project_files_changed'])
        target.unlink()
        with patch('codex_expert_mcp.review') as review:
            replay = self.bridge.call('reprise_delegate', {'request_key': 'edit-request', 'question':'Implement', 'kind':'batch', 'mode':'implementation', 'paths':['source.py']})
            review.assert_not_called()
        self.assertEqual(replay['status'], 'finished')


    def test_protocol_lists_only_bounded_tools(self):
        self.assertIn('tools', self.bridge.dispatch({'method': 'initialize'})['capabilities'])
        self.assertEqual({item['name'] for item in self.bridge.dispatch({'method': 'tools/list'})['tools']}, {'codex_review','codex_followup','reprise_delegate','reprise_backends','reprise_followup','reprise_resume','reprise_history','reprise_health','reprise_requests','reprise_quotas','reprise_reconcile'})
    def test_selected_tools_are_filtered_and_cannot_be_called_by_name(self):
        restricted = Bridge(self.temp.name, {}, Path(self.temp.name)/'restricted', allowed_tools=['reprise_backends', 'reprise_requests'])
        self.addCleanup(restricted.db.close)
        self.assertEqual({tool['name'] for tool in restricted.dispatch({'method': 'tools/list'})['tools']}, {'reprise_backends', 'reprise_requests'})
        with patch('codex_expert_mcp.review') as review:
            with self.assertRaises(ValueError):
                restricted.dispatch({'method': 'tools/call', 'params': {'name': 'reprise_delegate', 'arguments': {'question': 'Q', 'kind': 'review', 'request_key': 'must-not-run'}}})
            review.assert_not_called()
        self.assertEqual(restricted.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)

    def test_followup_requires_own_thread(self):
        with self.assertRaises(ValueError):
            self.call('codex_followup', {'thread_id': ID, 'question': 'continue'})
    def test_codex_has_no_local_cap_even_after_restart_and_old_counter(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'quota_blocked'}) as review:
            for _ in range(2): self.call('codex_review', {'question': 'review'})
            other = Bridge(self.temp.name, {}, Path(self.temp.name)/'state', daily_limit=2)
            other.program = 'codex'
            try:
                other.db.execute('INSERT OR REPLACE INTO expert_calls VALUES (?,?,?)', (other.scope, int(time.time()//86400), 999))
                other.db.commit()
                for name, args in [('codex_review', {'question': 'review'}),
                                   ('reprise_delegate', {'question': 'review', 'kind': 'review', 'backend': 'codex'})]:
                    self.assertEqual(other.call(name, {'request_key': str(uuid.uuid4()), **args})['status'], 'quota_blocked')
            finally: other.db.close()
            self.assertEqual(review.call_count, 4)
    def test_followup_preserves_thread(self):
        with patch('codex_expert_mcp.review', return_value={'status': 'finished', 'resumable': True, 'thread_id': ID, 'response': 'review'}) as review:
            self.call('codex_review', {'question': 'review'})
            result = self.bridge.dispatch({'method': 'tools/call', 'params': {'name': 'codex_followup', 'arguments': {'thread_id': ID, 'question': 'explain', 'request_key': 'followup'}}})
            self.assertFalse(result['isError'])
            self.assertEqual(review.call_args.kwargs['thread_id'], ID)
    def test_arbitrary_parameters_refused(self):
        with self.assertRaises(ValueError):
            self.call('codex_review', {'question': 'review', 'cwd': '/'})

    def test_delegate_rejects_quick_apply_parameters_outside_claude_desktop(self):
        cases = [('codex', {'chatId': ID}), ('claude-code', {'chatId': ID}), ('ironclaw', {'chatId': ID}),
                 (None, {'chatId': ID}), ('codex', {'filePaths': ['x.py']}),
                 ('codex', {'chatId': ID, 'filePaths': []}), ('codex', {'chatId': None}), ('codex', {'filePaths': ''})]
        for backend, extra in cases:
            args = {'question': 'review', 'kind': 'review', **extra}
            if backend:
                args['backend'] = backend
            with self.subTest(backend=backend, extra=sorted(extra)):
                with patch('codex_expert_mcp.review') as review:
                    with self.assertRaises(ValueError):
                        self.call('reprise_delegate', args)
                    review.assert_not_called()
        with patch('codex_expert_mcp.review') as review:
            with self.assertRaises(ValueError):
                self.call('reprise_followup', {'backend': 'codex', 'thread_id': ID, 'question': 'continue', 'chatId': ID})
            review.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)

    def test_followup_refuses_claude_desktop_with_owned_thread(self):
        self.bridge.db.execute('INSERT INTO expert_threads VALUES (?,?)', (self.bridge.scope, ID))
        self.bridge.db.commit()
        with patch('codex_expert_mcp.review') as codex, patch('codex_expert_mcp.claude_review') as claude:
            with self.assertRaises(ValueError) as ctx:
                self.call('reprise_followup', {'backend': 'claude-desktop', 'thread_id': ID, 'question': 'continue'})
            self.assertIn('Quick Apply', str(ctx.exception))
            codex.assert_not_called()
            claude.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)

    def test_main_envelope_keeps_ids_details_and_continues_after_errors(self):
        old_umask = os.umask(0o077)
        self.addCleanup(os.umask, old_umask)
        def line(ident, name, arguments):
            return json.dumps({'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call',
                               'params': {'name': name, 'arguments': arguments}}, ensure_ascii=False) + '\n'
        payload = io.BytesIO((line('req-1', 'reprise_delegate', {'request_key': 'k1', 'question': 'Q', 'kind': 'review'})
                              + line('x' * 300, 'reprise_delegate', {'request_key': 'k2', 'return_mode': 'receipt'})
                              + line('req-3', 'reprise_requests', {'request_key': 'k3', 'view': 'receipt'})).encode('utf-8'))
        with patch('codex_expert_mcp.Bridge') as bridge_class, \
             patch('sys.stdin', types.SimpleNamespace(buffer=payload)), \
             patch('sys.argv', ['codex_expert_mcp.py', '--project-root', str(self.temp.name),
                                '--state', str(Path(self.temp.name) / 'state')]), \
             redirect_stdout(io.StringIO()) as out:
            bridge_class.return_value.dispatch.side_effect = [
                ValueError('Quick Apply « indisponible » : socket absent.'),
                ValueError('chatId and filePaths are only valid with backend claude-desktop'),
                {'content': [{'type': 'text', 'text': '{"status":"finished"}'}]}]
            main()
        replies = [json.loads(item) for item in out.getvalue().splitlines()]
        self.assertEqual([reply['id'] for reply in replies], ['req-1', None, 'req-3'])
        self.assertEqual(replies[0]['error']['code'], -32602)
        self.assertTrue(replies[0]['error']['data']['error'].startswith('ValueError: '))
        self.assertIn('« indisponible »', replies[0]['error']['data']['error'])
        self.assertIn('« indisponible »', out.getvalue())
        self.assertNotIn('\\u00ab', out.getvalue())
        self.assertNotIn('error', replies[2])
        self.assertEqual(replies[2]['result']['content'][0]['text'], '{"status":"finished"}')
        self.assertEqual(bridge_class.return_value.dispatch.call_count, 3)

    def test_error_payload_details_and_bounds_refusals(self):
        payload = error_payload(ValueError('chatId and filePaths are only valid with backend claude-desktop'))
        self.assertEqual(payload['code'], -32602)
        self.assertEqual(payload['data']['kind'], 'refused')
        self.assertEqual(payload['message'], 'Request refused or unavailable; no automatic retry.')
        self.assertTrue(payload['data']['error'].startswith('ValueError: '))
        self.assertIn('claude-desktop', payload['data']['error'])
        self.assertLessEqual(len(error_payload(OSError('x' * 500))['data']['error']), 200)

    def test_error_payload_distinguishes_busy_from_unavailable(self):
        from engines import Unavailable
        busy = error_payload(BlockingIOError('lock held'))
        self.assertEqual(busy['code'], -32000)
        self.assertEqual(busy['data']['kind'], 'busy')
        unavailable = error_payload(Unavailable('engine_unavailable', 'Exécutable Codex introuvable.'))
        self.assertEqual(unavailable['code'], -32000)
        self.assertEqual(unavailable['data']['kind'], 'unavailable')
        # A refused request keeps its own code: the three cases stay machine-distinguishable (#53).
        self.assertNotEqual(busy['code'], error_payload(ValueError('bad args'))['code'])

    def test_padded_oversized_followup_rejected_before_budget(self):
        self.bridge.db.execute('INSERT INTO expert_threads VALUES (?,?)', (self.bridge.scope, ID))
        self.bridge.db.commit()
        with patch('codex_expert_mcp.review') as codex, patch('codex_expert_mcp.claude_review') as claude:
            for name, extra in [('codex_followup', {}), ('reprise_followup', {'backend':'codex'}),
                                ('reprise_followup', {'backend':'claude-code'})]:
                for question in (' '*12000+'x', 'x'+' '*12000):
                    with self.assertRaises(ValueError):
                        self.call(name, {'thread_id':ID, 'question':question, **extra})
            codex.assert_not_called()
            claude.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_calls').fetchone()[0],0)

    def test_sigterm_unwinds_active_cli_before_bridge_exit(self):
        root = Path(self.temp.name)
        ready, completed = root/'ready', root/'completed'
        program = root/'fake-claude'
        # Mimic CLI preflight, then a long inference with an observable side effect.
        program.write_text('#!' + sys.executable + '\n' +
            'import sys,json,time\nfrom pathlib import Path\n' +
            'if "--help" in sys.argv: print("--safe-mode --restricted --strict-mcp-config --permission-prompts --tools --disable-slash-commands --no-chrome --session-id --effort")\n' +
            'elif "auth" in sys.argv: print(json.dumps({"loggedIn":True,"authMethod":"claude.ai","apiProvider":"firstParty"}))\n' +
            'else:\n Path(' + repr(str(ready)) + ').touch()\n time.sleep(.8)\n Path(' + repr(str(completed)) + ').touch()\n')
        program.chmod(0o700)
        script = str(Path(__file__).parents[1]/'codex_expert_mcp.py')
        command = [sys.executable, script, '--project-root', str(root), '--state', str(root/'signal-state'),
                   '--claude-program', str(program), '--claude-included-quota-confirmed']
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True) as bridge:
            try:
                bridge.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
                    'name':'reprise_delegate','arguments':{'backend':'claude-code','kind':'expertise','question':'Review','request_key':'signal'}}})+'\n')
                bridge.stdin.flush()
                deadline = time.monotonic()+5
                while not ready.exists() and bridge.poll() is None and time.monotonic()<deadline:
                    time.sleep(.01)
                self.assertTrue(ready.exists(), 'Fixture inference did not start')
                bridge.terminate()
                self.assertEqual(bridge.wait(timeout=5), 143)
                time.sleep(.9)
                self.assertFalse(completed.exists(), 'Inference survived bridge SIGTERM')
            finally:
                if bridge.poll() is None:
                    bridge.terminate()
                    bridge.wait(timeout=5)
    def test_levels_route_and_followup_preserves_model(self):
        for kind,level,model in [('expertise','standard','gpt-6.1-sol'),('review','deep','gpt-6-astra'),('batch','standard','gpt-6-luna')]:
            with patch('codex_expert_mcp.review', return_value={'status':'finished','resumable':True,'thread_id':ID}) as review:
                self.bridge.limit=20
                self.call('reprise_delegate',{'kind':kind,'level':level,'question':'Check this contract'})
                self.assertEqual(review.call_args.kwargs['model'],model)
                self.call('codex_followup',{'thread_id':ID,'question':'Explain'})
                self.assertEqual(review.call_args.kwargs['model'],model)
                self.call('reprise_followup', {'backend':'codex','thread_id':ID,'question':'Continue'})
                self.assertEqual(review.call_args.kwargs['model'], model)
                self.assertEqual(review.call_args.kwargs['effort'], 'high' if level == 'deep' else 'medium')

    def test_delegate_explicit_model_overrides_default(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        for backend, models, target in [
                ('codex', ('gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra'), 'review'),
                ('claude-code', ('sonnet', 'opus'), 'claude_review')]:
            for model in models:
                with self.subTest(backend=backend, model=model), patch(
                        'codex_expert_mcp.' + target, return_value={'status':'finished'}) as review:
                    result = self.call('reprise_delegate', {
                        'backend':backend, 'kind':'review', 'question':'Review', 'model':model})
                    self.assertEqual(review.call_args.kwargs['model'], model)
                    self.assertEqual(review.call_args.kwargs['effort'], 'medium')
                    self.assertFalse(result['automatic_fallback'])

    def test_invalid_model_rejected_before_request_or_inference(self):
        cases = [('codex', value) for value in ('unknown', 'sonnet', '', None, [], 1)]
        cases += [('claude-code', 'gpt-6.1-sol'), ('ironclaw', 'gpt-6.1-sol'),
                  ('claude-desktop', 'sonnet')]
        with patch('codex_expert_mcp.review') as codex, patch('codex_expert_mcp.claude_review') as claude:
            for backend, model in cases:
                with self.subTest(backend=backend, model=model), self.assertRaisesRegex(
                        ValueError, 'Unknown or unsupported model'):
                    self.call('reprise_delegate', {
                        'backend':backend, 'kind':'review', 'question':'Review', 'model':model})
            codex.assert_not_called()
            claude.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0], 0)

    def test_backends_exposes_supported_model_ids_and_schemas(self):
        rows = {row['id']: row for row in self.call('reprise_backends', {})['backends']}
        self.assertEqual(rows['codex']['models'], ['gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra'])
        self.assertEqual(rows['claude-code']['models'], ['sonnet', 'opus'])
        for backend in ('ironclaw', 'claude-desktop', 'autoclaw'):
            self.assertEqual(rows[backend]['models'], [])
        tools = {tool['name']: tool for tool in self.bridge.dispatch({'method':'tools/list'})['tools']}
        for name in ('reprise_delegate', 'reprise_followup'):
            self.assertEqual(tools[name]['inputSchema']['properties']['model']['type'], 'string')
            self.assertNotIn('model', tools[name]['inputSchema']['required'])

    def test_followup_preserves_overrides_and_persists_model(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        def result(*args, **kwargs):
            return {'status':'finished', 'resumable':True, 'thread_id':ID, 'model':kwargs['model']}
        for backend, target, first, second in [
                ('codex', 'review', 'gpt-6-astra', 'gpt-6.1-sol'),
                ('claude-code', 'claude_review', 'opus', 'sonnet')]:
            with self.subTest(backend=backend), patch('codex_expert_mcp.' + target, side_effect=result) as review:
                self.call('reprise_delegate', {
                    'backend':backend, 'kind':'review', 'level':'deep', 'question':'Review'})
                followup = {'backend':backend, 'thread_id':ID, 'question':'Continue'}
                self.call('reprise_followup', followup)
                self.assertEqual(review.call_args.kwargs['model'], first)
                self.call('reprise_followup', dict(followup, model=second))
                self.assertEqual(review.call_args.kwargs['model'], second)
                self.assertEqual(review.call_args.kwargs['effort'], 'high')
                self.call('reprise_followup', followup)
                self.assertEqual(review.call_args.kwargs['model'], second)
                before = review.call_count
                with self.assertRaisesRegex(ValueError, 'Unknown or unsupported model'):
                    self.call('reprise_followup', dict(followup, model='unknown'))
                self.assertEqual(review.call_count, before)

    def test_explicit_model_keeps_question_limits(self):
        with patch('codex_expert_mcp.review') as review:
            for name, extra, length in [
                    ('reprise_delegate', {'kind':'review'}, 12000),
                    ('reprise_delegate', {'kind':'review'}, 12001),
                    ('reprise_followup', {'thread_id':ID}, 12001)]:
                with self.subTest(name=name, length=length), self.assertRaises(ValueError):
                    self.call(name, dict(extra, backend='codex', model='gpt-6.1-sol', question='x' * length))
            review.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0], 0)

    def test_backends_no_inference_or_budget_consumption(self):
        with patch('codex_expert_mcp.review') as review:
            result=self.call('reprise_backends',{})
            review.assert_not_called()
        self.assertFalse(result['automatic_fallback'])
        self.assertFalse(result['backends'][1]['configured'])
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_calls').fetchone()[0],0)
    def test_invalid_delegation_rejected_before_budget(self):
        for args in [{'kind':'write','question':'x'},{'kind':'review','level':'ultra','question':'x'}]:
            with self.assertRaises(ValueError):self.call('reprise_delegate',args)
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_calls').fetchone()[0],0)

    def test_nonpersistent_thread_is_not_registered(self):
        with patch('codex_expert_mcp.review', return_value={'status':'blocked_permission','thread_id':ID,'resumable':False}):
            self.call('codex_review', {'question':'review'})
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_threads').fetchone()[0], 0)

    def test_claude_explicit_route_followup_and_backend_isolation(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        with patch('codex_expert_mcp.claude_review', return_value={
                'status':'finished','resumable':True,'thread_id':ID,'model':'claude-sonnet-test'}) as review:
            self.call('reprise_delegate', {'backend':'claude-code','kind':'expertise','question':'Review'})
            self.assertEqual(review.call_args.kwargs['model'], 'sonnet')
            self.call('reprise_followup', {'backend':'claude-code','thread_id':ID,'question':'Continue'})
            self.assertEqual(review.call_args.kwargs['model'], 'claude-sonnet-test')
            with self.assertRaises(ValueError):
                self.call('codex_followup', {'thread_id':ID,'question':'Continue'})
            self.bridge.claude_program = '/other-claude'
            with self.assertRaises(ValueError):
                self.call('reprise_followup', {'backend':'claude-code','thread_id':ID,'question':'Continue'})

    def test_claude_disabled_never_falls_back_or_consumes_budget(self):
        with patch('codex_expert_mcp.review') as codex, patch('codex_expert_mcp.claude_review') as claude:
            result = self.call('reprise_delegate', {'backend':'claude-code','kind':'expertise','question':'Review'})
            self.assertEqual(result['status'],'engine_unavailable')
            self.bridge.claude_program = '/claude'
            result = self.call('reprise_delegate', {'backend':'claude-code','kind':'expertise','question':'Review'})
            self.assertEqual(result['status'],'billing_unverified')
            codex.assert_not_called()
            claude.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_calls').fetchone()[0],0)

    def test_codex_does_not_consume_claude_budget_and_failed_session_not_owned(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        with patch('codex_expert_mcp.review', return_value={'status':'quota_blocked'}):
            self.call('codex_review', {'question':'Review'})
        with patch('codex_expert_mcp.claude_review', return_value={'status':'finished','resumable':False}) as review:
            self.call('reprise_delegate', {'backend':'claude-code','kind':'review','question':'Review'})
            self.call('reprise_delegate', {'backend':'claude-code','kind':'review','question':'Review'})
            self.assertEqual(self.call('reprise_delegate', {'backend':'claude-code','kind':'review','question':'Review'})['status'],'finished')
            self.assertEqual(review.call_count,3)
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM claude_expert_routes').fetchone()[0],0)


class ClaudeDesktopBridgeTests(RequestCalls, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state', daily_limit=8,
                             desktop_socket='/private/tmp/quick.sock')
        self.addCleanup(self.bridge.db.close)

    def test_backends_include_claude_desktop(self):
        ids = {b['id'] for b in self.call('reprise_backends', {})['backends']}
        self.assertIn('claude-desktop', ids)

    def test_delegate_routes_to_quick_entry_endpoint(self):
        with patch('claude_desktop.review', return_value={'status': 'finished', 'resumable': False,
                   'thread_id': ID, 'dispatch': 'dispatchOnQuickEntrySubmit', 'model': 'claude-desktop'}) as review:
            result = self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise',
                                       'backend': 'claude-desktop', 'chatId': ID})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['backend'], 'claude-desktop')
        self.assertEqual(result['dispatch'], 'dispatchOnQuickEntrySubmit')
        self.assertEqual(review.call_args.kwargs['chat_id'], ID)

    def test_delegate_rejects_unknown_backend(self):
        with self.assertRaises(ValueError):
            self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise', 'backend': 'nope'})

    def test_delegate_refuses_claude_desktop_without_desktop_socket(self):
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state-nosock', daily_limit=8)
        self.addCleanup(self.bridge.db.close)
        with patch('claude_desktop.review') as review:
            result = self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise', 'backend': 'claude-desktop'})
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertEqual(result['backend'], 'claude-desktop')
        self.assertFalse(result['automatic_fallback'])
        review.assert_not_called()
        self.assertEqual(self.bridge.db.execute(
            "SELECT json_extract(result,'$.status') FROM delegation_requests").fetchone()[0], 'engine_unavailable')

    def test_delegate_rejects_arbitrary_fields(self):
        with self.assertRaises(ValueError):
            self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise',
                                       'backend': 'claude-desktop', 'hack': 'x'})


class IronclawBridgeTests(RequestCalls, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state', daily_limit=2)
        self.bridge.program = 'codex'
        self.addCleanup(self.bridge.db.close)

    def test_backends_declare_ironclaw_unconfigured(self):
        result = self.bridge.call('reprise_backends', {})
        row = next(b for b in result['backends'] if b['id'] == 'ironclaw')
        self.assertFalse(row['configured'])
        self.assertFalse(result['automatic_fallback'])

    def test_legacy_configuration_does_not_enable_unverified_backend(self):
        self.bridge.ironclaw_values = {'key': '/'}
        row = next(b for b in self.bridge.call('reprise_backends', {})['backends'] if b['id'] == 'ironclaw')
        self.assertFalse(row['configured'])

    def test_legacy_cli_cannot_bypass_durable_admission(self):
        self.bridge.ironclaw_values = {'key': '/'}
        with patch('codex_expert_mcp.ironclaw.review') as review:
            result = self.call('reprise_delegate', {'backend': 'ironclaw', 'kind': 'expertise', 'question': 'Ping'})
        self.assertEqual(result['status'], 'engine_unavailable')
        review.assert_not_called()
        self.assertEqual(self.bridge.db.execute(
            "SELECT json_extract(result,'$.status') FROM delegation_requests").fetchone()[0], 'engine_unavailable')

    def test_delegate_ironclaw_unconfigured_returns_engine_unavailable(self):
        with patch('codex_expert_mcp.ironclaw.review') as review:
            result = self.call('reprise_delegate', {'backend': 'ironclaw', 'kind': 'expertise', 'question': 'Ping'})
        self.assertEqual(result['status'], 'engine_unavailable')
        review.assert_not_called()
        self.assertEqual(self.bridge.db.execute(
            "SELECT json_extract(result,'$.status') FROM delegation_requests").fetchone()[0], 'engine_unavailable')

    def test_followup_ironclaw_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge.call('reprise_followup', {'backend': 'ironclaw', 'thread_id': 'x', 'question': 'continue'})


class CompactReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state')
        self.bridge.program = 'codex'
        self.addCleanup(self.bridge.db.close)

    def dispatch(self, name, args, ident=1):
        content = self.bridge.dispatch({'method': 'tools/call', 'id': ident,
            'params': {'name': name, 'arguments': args}})
        wire = json.dumps({'jsonrpc': '2.0', 'id': ident, 'result': content}, ensure_ascii=False)
        self.assertLessEqual(len(wire), 7000)
        return json.loads(content['content'][0]['text'])

    def test_full_then_receipt_replay_preserves_identity_and_result(self):
        args = {'request_key': 'key', 'question': 'Inspect', 'kind': 'review'}
        with patch('codex_expert_mcp.review', return_value={
                'status': 'finished', 'response': 'x' * 20000, 'thread_id': ID}) as run:
            result = self.bridge.call('reprise_delegate', args)
            before = self.bridge.db.execute('SELECT * FROM delegation_requests').fetchall()
            manifest = self.dispatch('reprise_delegate', dict(args, return_mode='receipt'))
            self.assertEqual(manifest, self.dispatch(
                'reprise_requests', {'request_key': 'key', 'view': 'receipt'}))
            self.assertEqual(self.bridge.call('reprise_delegate', dict(args, return_mode='full')), result)
            self.assertEqual(self.bridge.db.execute('SELECT * FROM delegation_requests').fetchall(), before)
            run.assert_called_once()
        with patch('codex_expert_mcp.review', return_value={'status': 'finished', 'response': 'y' * 20000}) as run:
            manifest = self.dispatch('reprise_delegate', dict(args, request_key='new', return_mode='receipt'))
            self.assertTrue(manifest['available'])
            self.assertEqual(self.bridge.call('reprise_requests', {'request_key': 'new'})['result']['response'],
                             'y' * 20000)
            run.assert_called_once()

    def test_all_inference_routes_return_receipts_without_replay(self):
        for name in ('reprise_delegate', 'codex_review', 'reprise_followup', 'codex_followup'):
            with self.subTest(name=name):
                key = 'route-' + name
                args = {'request_key': key, 'question': name}
                if name == 'reprise_delegate':
                    args['kind'] = 'review'
                if 'followup' in name:
                    args['thread_id'] = ID
                    self.bridge.db.execute('INSERT OR IGNORE INTO expert_threads VALUES (?,?)',
                                           (self.bridge.scope, ID))
                    self.bridge.db.commit()
                    if name == 'reprise_followup':
                        args['backend'] = 'codex'
                with patch('codex_expert_mcp.review', return_value={
                        'status': 'finished', 'response': 'é😀\\"' * 15000}) as run:
                    receipt = self.dispatch(name, dict(args, return_mode='receipt'))
                    self.assertTrue(receipt['available'])
                    self.assertGreater(receipt['total_length'], 48000)
                    self.assertEqual(receipt, self.dispatch(name, dict(args, return_mode='receipt')))
                    # Above the full-mode delivery limit the default mode returns the same manifest.
                    full = self.bridge.call(name, dict(args, return_mode='full'))
                    self.assertEqual((full['return_mode_effective'], full['result_sha256']),
                                     ('receipt', receipt['result_sha256']))
                    self.assertNotIn('response', full)
                    run.assert_called_once()

    def test_claude_continuation_compact_replay(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        with patch('codex_expert_mcp.claude_review', return_value={
                'status': 'finished', 'response': 'é😀' * 30000,
                'thread_id': ID, 'model': 'sonnet', 'resumable': True}) as run:
            first = self.dispatch('reprise_delegate', {
                'request_key': 'claude-first', 'question': 'Inspect', 'kind': 'review',
                'backend': 'claude-code', 'return_mode': 'receipt'})
            args = {'request_key': 'claude-next', 'question': 'Continue',
                    'thread_id': ID, 'backend': 'claude-code', 'return_mode': 'receipt'}
            followup = self.dispatch('reprise_followup', args)
            self.assertEqual(followup, self.dispatch('reprise_followup', args))
            self.assertTrue(first['available'])
            self.assertGreater(followup['total_length'], 48000)
            self.assertEqual(run.call_count, 2)

    def test_complete_envelope_bound_and_stable_pages_with_id_reserve(self):
        key = '\x01' * 127 + 'k'
        sample = '"\\\n\r\t\x00\x01e\u0301é😀'
        self.bridge.requests.begin(key, {'question': 'A'})
        saved = self.bridge.requests.finish(key, {
            'status': 'finished', 'response': sample * 1500, 'details': sample * 1500})
        manifest = self.dispatch('reprise_requests', {'request_key': key, 'view': 'receipt'})
        parts, offset = [], 0
        with patch('codex_expert_mcp.review') as run:
            while True:
                args = {'request_key': key, 'view': 'page', 'offset': offset,
                        'max_chars': 6000, 'result_sha256': manifest['result_sha256']}
                page = self.dispatch('reprise_requests', args)
                self.assertEqual(page, self.dispatch('reprise_requests', args, ident='x' * 254))
                self.assertEqual(page, self.dispatch('reprise_requests', args, ident='"\\' * 50))
                self.assertFalse(page['acknowledged'])
                self.assertEqual(page['returned_length'], len(page['text']))
                parts.append(page['text'])
                if not page['has_more']:
                    self.assertIsNone(page['next_offset'])
                    break
                self.assertGreater(page['returned_length'], 0)
                self.assertEqual(page['next_offset'], offset + len(page['text']))
                offset = page['next_offset']
            run.assert_not_called()
        text = ''.join(parts)
        self.assertEqual(text, json.dumps(saved, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
        self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(), manifest['result_sha256'])
        args = {'request_key': key, 'view': 'page'}
        first = self.dispatch('reprise_requests', args)
        receipt = self.dispatch('reprise_requests', {
            'request_key': key, 'view': 'receipt', 'acknowledge': True})
        self.assertTrue(receipt['acknowledged'])
        self.assertNotIn('result', receipt)
        self.assertNotIn('text', receipt)
        later = self.dispatch('reprise_requests', args, ident='x' * 254)
        self.assertEqual(first['text'], later['text'])
        self.assertEqual(first['next_offset'], later['next_offset'])
        self.assertFalse(self.bridge.db.in_transaction)

    def test_invalid_modes_ids_and_acknowledgement_states(self):
        args = {'request_key': 'key', 'question': 'Inspect', 'kind': 'review'}
        with patch('codex_expert_mcp.review') as run:
            for mode in ('other', None, True, 1):
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    self.bridge.call('reprise_delegate', dict(args, return_mode=mode))
            for name in ('codex_followup', 'reprise_followup',
                         'reprise_requests', 'reprise_backends'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.bridge.call(name, {'request_key': 'key', 'question': 'Inspect',
                                           'return_mode': 'receipt'})
            for ident in ('x' * 255, '\x01' * 43):
                with self.subTest(ident=ident), self.assertRaises(ValueError):
                    self.dispatch('reprise_delegate', dict(args, return_mode='receipt'), ident=ident)
            run.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0], 0)
        self.bridge.requests.begin('key', {'question': 'A'})
        for result in (None, {'status': 'running'}, {'status': 'needs_review'},
                       {'status': 'blocked_permission'}):
            if result is not None:
                self.bridge.requests.finish('key', result)
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.dispatch('reprise_requests', {
                    'request_key': 'key', 'view': 'receipt', 'acknowledge': True})
        self.bridge.requests.finish('key', {'status': 'finished'})
        with self.assertRaises(ValueError):
            self.dispatch('reprise_requests', {
                'request_key': 'key', 'view': 'page', 'acknowledge': True})
