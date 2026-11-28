import hashlib
import io
import sqlite3
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
from expert_mcp import CODEX_CATALOG_TTL, Bridge, error_payload, main
from test_codex_expert import ID

# #253 : BACKEND_MODELS ne porte plus la lineup codex figée — les tests
# simulent le catalogue live avec une lineup locale équivalente.
CODEX_TEST_MODELS = ('gpt-6.2-sol', 'gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra', 'gpt-5.6-terra')

class RequestCalls:
    def call(self, name, args):
        if name not in ('reprise_backends', 'reprise_health', 'reprise_resume', 'reprise_history', 'reprise_requests'):
            args = {'request_key': str(uuid.uuid4()), **args}
        return self.bridge.call(name, args)


# Catalogue Codex simulé (#242) : forme normalisée de `model/list`, efforts
# bas/medium/high pour chaque modèle de la lineup de test.
def simulated_codex_catalog(models, *, source='live', error=None):
    return {'models': [{'model': model, 'displayName': model,
                        'supportedReasoningEfforts': ['low', 'medium', 'high']}
                       for model in models],
            'source': source, 'updated_at': '2026-11-20T15:00:00+00:00', 'error': error}


class BridgeTests(RequestCalls, unittest.TestCase):
    def seed_catalog(self, bridge=None):
        # #242 : admission dynamique — le cache d'instance sert de catalogue
        # simulé ; la cleanup empêche une revalidation de remonter au Client réel.
        bridge = bridge if bridge is not None else self.bridge
        entry = simulated_codex_catalog(CODEX_TEST_MODELS)
        bridge._codex_catalog_cache, bridge._codex_catalog_time = entry, time.monotonic()
        self.addCleanup(lambda: setattr(bridge, '_codex_catalog_time', None))
        return entry

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # #253 : le fichier last-known-good est isolé du vrai chemin utilisateur.
        self.catalog_path = Path(self.temp.name)/'codex-models.json'
        catalog_patcher = patch('expert_mcp.CATALOG_PATH', self.catalog_path)
        catalog_patcher.start()
        self.addCleanup(catalog_patcher.stop)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state')
        self.bridge.program = 'codex'
        self.seed_catalog()
        self.addCleanup(self.bridge.close)
    def test_backends_reports_project_writes_enabled_by_default_issue_252(self):
        codex = self.call('reprise_backends', {})['backends'][0]
        self.assertTrue(codex['workspace_write_authorized'])
        self.assertEqual(codex['modes'], ['expertise', 'implementation'])
        # L'écriture par défaut ne s'étend pas à l'expertise : sans mode ou en
        # mode expertise, le tour Codex reste en lecture seule.
        output = {'status': 'finished', 'resumable': True, 'thread_id': ID, 'response': 'Avis'}
        for extra in ({}, {'mode': 'expertise'}):
            with patch('expert_mcp.review', return_value=output.copy()) as review:
                self.call('reprise_delegate', {'question': 'Review', 'kind': 'review', **extra})
                self.assertFalse(review.call_args.kwargs.get('implementation', False))  # défaut read-only de codex_expert.review
        self.assertEqual(codex['implementation_scope'], str(Path(self.temp.name).resolve()))
        self.assertTrue(codex['writes'])
        self.assertNotIn('implementation_unavailable_reason', codex)
        self.assertNotIn('enable_with', codex)

    def test_new_implementation_invalid_input_is_rejected_before_admission(self):
        with patch('expert_mcp.review') as review:
            with self.assertRaises(ValueError):
                self.call('reprise_delegate', {'question':'Implement','kind':'batch','mode':'implementation','paths':['missing.py']})
            review.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)

    def test_implementation_followup_inherits_mode_after_restart_and_alias_cannot_promote(self):
        output = {'status':'finished', 'resumable':True, 'thread_id':ID, 'response':'Implemented'}
        with patch('expert_mcp.review', return_value=output.copy()) as review:
            self.call('reprise_delegate', {'question':'Implement', 'kind':'batch', 'mode':'implementation'})
            self.assertTrue(review.call_args.kwargs['implementation'])
        restarted = Bridge(self.temp.name, {}, Path(self.temp.name)/'state')
        self.addCleanup(restarted.close)
        restarted.program = 'codex'
        self.seed_catalog(restarted)
        with patch('expert_mcp.review', return_value=output.copy()) as review:
            restarted.call('reprise_followup', {'request_key':'continue-implementation', 'thread_id':ID, 'backend':'codex', 'question':'Continue'})
            self.assertTrue(review.call_args.kwargs['implementation'])
            with self.assertRaises(ValueError):
                restarted.call('codex_followup', {'request_key':'legacy-alias', 'thread_id':ID, 'question':'Continue'})

    def test_legacy_review_cannot_be_promoted_by_prompt_or_mode(self):
        with patch('expert_mcp.review', return_value={'status':'finished','resumable':True,'thread_id':ID,'response':'Review'}):
            self.call('codex_review', {'question':'Review'})
        with self.assertRaises(ValueError):
            self.call('reprise_followup', {'thread_id':ID,'backend':'codex','question':'Implement','mode':'implementation'})

    def test_expected_implementation_edit_is_not_an_input_integrity_failure(self):
        target = Path(self.temp.name) / 'source.py'
        target.write_text('before')
        def implementation(*args, **kwargs):
            target.write_text('after')
            return {'status':'finished','response':'Implemented'}
        with patch('expert_mcp.review', side_effect=implementation):
            result = self.call('reprise_delegate', {'question':'Implement','kind':'batch','mode':'implementation','paths':['source.py'],'request_key':'edit-request'})
        self.assertEqual(result['status'], 'finished')
        self.assertTrue(result['project_files_changed'])
        target.unlink()
        with patch('expert_mcp.review') as review:
            replay = self.bridge.call('reprise_delegate', {'request_key': 'edit-request', 'question':'Implement', 'kind':'batch', 'mode':'implementation', 'paths':['source.py']})
            review.assert_not_called()
        self.assertEqual(replay['status'], 'finished')


    def test_protocol_lists_only_bounded_tools(self):
        self.assertIn('tools', self.bridge.dispatch({'method': 'initialize'})['capabilities'])
        self.assertEqual({item['name'] for item in self.bridge.dispatch({'method': 'tools/list'})['tools']}, {'codex_review','codex_followup','reprise_delegate','reprise_backends','reprise_experts_list','reprise_followup','reprise_resume','reprise_history','reprise_health','reprise_requests','reprise_quotas','reprise_reconcile','reprise_notifications','approval_list','approval_respond','wait_result'})
    def test_selected_tools_are_filtered_and_cannot_be_called_by_name(self):
        restricted = Bridge(self.temp.name, {}, Path(self.temp.name)/'restricted', allowed_tools=['reprise_backends', 'reprise_requests'])
        self.addCleanup(restricted.close)
        self.assertEqual({tool['name'] for tool in restricted.dispatch({'method': 'tools/list'})['tools']}, {'reprise_backends', 'reprise_requests'})
        with patch('expert_mcp.review') as review:
            with self.assertRaises(ValueError):
                restricted.dispatch({'method': 'tools/call', 'params': {'name': 'reprise_delegate', 'arguments': {'question': 'Q', 'kind': 'review', 'request_key': 'must-not-run'}}})
            review.assert_not_called()
        self.assertEqual(restricted.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)

    def test_followup_requires_own_thread(self):
        with self.assertRaises(ValueError):
            self.call('codex_followup', {'thread_id': ID, 'question': 'continue'})

    def test_not_owned_followup_is_typed_issue_100(self):
        for name, backend, extra in [('codex_followup', 'codex', {}),
                                     ('reprise_followup', 'claude-code', {'backend': 'claude-code'})]:
            with self.assertRaises(ValueError) as caught:
                self.call(name, {'thread_id': ID, 'question': 'continue', **extra})
            payload = error_payload(caught.exception)
            self.assertEqual(payload['data']['kind'], 'thread_not_owned')
            self.assertEqual(payload['data']['backend'], backend)
            self.assertEqual(payload['data']['thread_id'], ID)
            self.assertEqual(payload['data']['resume_with'], 'parent_request_key')
            self.assertIn('parent_request_key', payload['message'])
            self.assertIn('reprise_delegate', payload['message'])

    def test_owned_thread_followup_still_allowed_after_issue_100(self):
        with patch('expert_mcp.review', return_value={'status': 'finished', 'resumable': True, 'thread_id': ID, 'response': 'review'}) as review:
            self.call('codex_review', {'question': 'review'})
            self.call('codex_followup', {'thread_id': ID, 'question': 'continue'})
            self.assertEqual(review.call_args.kwargs['thread_id'], ID)

    def test_codex_has_no_local_cap_even_after_restart_and_old_counter(self):
        with patch('expert_mcp.review', return_value={'status': 'quota_blocked'}) as review:
            for _ in range(2): self.call('codex_review', {'question': 'review'})
            other = Bridge(self.temp.name, {}, Path(self.temp.name)/'state')
            other.program = 'codex'
            self.seed_catalog(other)
            try:
                other.db.execute('INSERT OR REPLACE INTO expert_calls VALUES (?,?,?)', (other.scope, int(time.time()//86400), 999))
                other.db.commit()
                for name, args in [('codex_review', {'question': 'review'}),
                                   ('reprise_delegate', {'question': 'review', 'kind': 'review', 'backend': 'codex'})]:
                    self.assertEqual(other.call(name, {'request_key': str(uuid.uuid4()), **args})['status'], 'quota_blocked')
            finally: other.db.close()
            self.assertEqual(review.call_count, 4)
    def test_followup_preserves_thread(self):
        with patch('expert_mcp.review', return_value={'status': 'finished', 'resumable': True, 'thread_id': ID, 'response': 'review'}) as review:
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
                with patch('expert_mcp.review') as review:
                    with self.assertRaises(ValueError):
                        self.call('reprise_delegate', args)
                    review.assert_not_called()
        with patch('expert_mcp.review') as review:
            with self.assertRaises(ValueError):
                self.call('reprise_followup', {'backend': 'codex', 'thread_id': ID, 'question': 'continue', 'chatId': ID})
            review.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)

    def test_followup_refuses_claude_desktop_with_owned_thread(self):
        self.bridge.db.execute('INSERT INTO expert_threads VALUES (?,?)', (self.bridge.scope, ID))
        self.bridge.db.commit()
        with patch('expert_mcp.review') as codex, patch('expert_mcp.claude_review') as claude:
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
        with patch('expert_mcp.Bridge') as bridge_class, \
             patch('sys.stdin', types.SimpleNamespace(buffer=payload)), \
             patch('sys.argv', ['expert_mcp.py', '--project-root', str(self.temp.name),
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
        self.assertEqual(busy['data']['reason_code'], 'bridge_busy')
        self.assertIn('Bridge busy', busy['data']['human_reason'])
        unavailable = error_payload(Unavailable('engine_unavailable', 'Exécutable Codex introuvable.'))
        self.assertEqual(unavailable['code'], -32000)
        self.assertEqual(unavailable['data']['kind'], 'unavailable')
        self.assertIn('backend unavailable', unavailable['data']['human_reason'])
        # A refused request keeps its own code: the three cases stay machine-distinguishable (#53).
        self.assertNotEqual(busy['code'], error_payload(ValueError('bad args'))['code'])

    def test_unknown_reconcile_request_is_not_found_not_generic_refusal(self):
        import expert_mcp
        with self.assertRaises(expert_mcp.RequestNotFound) as caught:
            self.bridge.call('reprise_reconcile', {'request_key': 'unseen-key-for-reconcile'})
        error = error_payload(caught.exception)
        self.assertEqual(error['code'], -32004)
        self.assertEqual(error['data']['kind'], 'not_found')
        self.assertIn('not found', error['message'])

    def test_busy_bridge_refuses_before_dispatch_with_reason_code(self):
        now = time.time()
        for slot in ('0', '1'):
            self.bridge.db.execute('INSERT INTO expert_active_turns VALUES (?,?,?,?,?,?)',
                                   ('codex', slot, None, 'held-%s' % slot, now, now))
        self.bridge.db.commit()
        with patch.object(self.bridge, '_call', side_effect=AssertionError('not dispatched')) as dispatch:
            result = self.call('reprise_delegate', {'question': 'Review', 'kind': 'expertise'})
        dispatch.assert_not_called()
        self.assertEqual(result, {'status': 'busy', 'reason_code': 'bridge_busy',
                                  'automatic_retry': False, 'dispatched': False,
                                  'reason': 'Bridge busy: one active expertise per state directory; no automatic retry.'})

    def test_padded_oversized_followup_rejected_before_budget(self):
        self.bridge.db.execute('INSERT INTO expert_threads VALUES (?,?)', (self.bridge.scope, ID))
        self.bridge.db.commit()
        with patch('expert_mcp.review') as codex, patch('expert_mcp.claude_review') as claude:
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
        script = str(Path(__file__).parents[1]/'expert_mcp.py')
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
            with patch('expert_mcp.review', return_value={'status':'finished','resumable':True,'thread_id':ID}) as review:
                self.bridge.limit=20
                self.call('reprise_delegate',{'kind':kind,'level':level,'question':'Check this contract'})
                self.assertEqual(review.call_args.kwargs['model'],model)
                self.assertEqual(review.call_args.kwargs['effort'], 'high' if level == 'deep' else 'medium')
                self.call('codex_followup',{'thread_id':ID,'question':'Explain'})
                self.assertEqual(review.call_args.kwargs['model'],model)
                self.call('reprise_followup', {'backend':'codex','thread_id':ID,'question':'Continue'})
                self.assertEqual(review.call_args.kwargs['model'], model)
                self.assertEqual(review.call_args.kwargs['effort'], 'high' if level == 'deep' else 'medium')

    def test_delegate_explicit_model_overrides_default(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        for backend, models, target in [
                ('codex', CODEX_TEST_MODELS, 'review'),
                ('claude-code', ('sonnet', 'opus'), 'claude_review')]:
            for model in models:
                with self.subTest(backend=backend, model=model), patch(
                        'expert_mcp.' + target, return_value={'status':'finished'}) as review:
                    result = self.call('reprise_delegate', {
                        'backend':backend, 'kind':'review', 'question':'Review', 'model':model})
                    self.assertEqual(review.call_args.kwargs['model'], model)
                    self.assertEqual(review.call_args.kwargs['effort'], 'medium')
                    self.assertFalse(result['automatic_fallback'])

    def test_explicit_gpt_6_2_sol_route_is_admitted(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        with patch('expert_mcp.review', return_value={'status':'finished'}) as review:
            result = self.call('reprise_delegate', {
                'backend':'codex', 'kind':'review', 'question':'Review', 'model':'gpt-6.2-sol'})
            self.assertEqual(review.call_args.kwargs['model'], 'gpt-6.2-sol')
            self.assertEqual(review.call_args.kwargs['effort'], 'medium')
            self.assertFalse(result['automatic_fallback'])

    def test_invalid_model_rejected_before_request_or_inference(self):
        cases = [('codex', value) for value in ('unknown', 'sonnet', '', None, [], 1)]
        cases += [('claude-code', 'gpt-6.1-sol'), ('ironclaw', 'gpt-6.1-sol'),
                  ('claude-desktop', 'sonnet')]
        with patch('expert_mcp.review') as codex, patch('expert_mcp.claude_review') as claude:
            for backend, model in cases:
                with self.subTest(backend=backend, model=model), self.assertRaisesRegex(
                        ValueError, 'Unknown or unsupported model'):
                    self.call('reprise_delegate', {
                        'backend':backend, 'kind':'review', 'question':'Review', 'model':model})
            codex.assert_not_called()
            claude.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0], 0)
        # #242 : pour codex l'admission est dynamique — modèle absent du
        # catalogue simulé mais présent dans la lineup de test → revalidation
        # forcée (Client appelé une seule fois), toujours absent → refus,
        # aucune inférence ni insertion.
        with patch('expert_mcp.review') as codex, patch('expert_mcp.Client') as client:
            client.return_value.call.side_effect = (
                lambda method, payload: {'data': [{'model': model} for model in CODEX_TEST_MODELS]}
                if method == 'model/list' else {})
            with self.assertRaisesRegex(ValueError, "not in live catalog"):
                self.call('reprise_delegate', {
                    'backend':'codex', 'kind':'review', 'question':'Review', 'model':'ghost-model'})
            client.assert_called_once()
            codex.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0], 0)

    def test_backends_exposes_supported_model_ids_and_schemas(self):
        # #242 : catalogue injoignable → liste vide (aucun repli statique)…
        self.bridge._codex_catalog_cache = simulated_codex_catalog([], source='unavailable', error='model/list failed')
        rows = {row['id']: row for row in self.call('reprise_backends', {})['backends']}
        self.assertEqual(rows['codex']['models'], [])
        self.assertEqual(rows['codex']['models_source'], 'unavailable')
        self.assertEqual(rows['codex']['models_error'], 'model/list failed')
        # …puis catalogue live simulé (cache d'instance) + métadonnées de fraîcheur.
        self.seed_catalog()
        rows = {row['id']: row for row in self.call('reprise_backends', {})['backends']}
        self.assertEqual([item['model'] for item in rows['codex']['models']], list(CODEX_TEST_MODELS))
        self.assertEqual(rows['codex']['models_source'], 'live')
        self.assertEqual(rows['codex']['models_updated_at'], '2026-11-20T15:00:00+00:00')
        self.assertNotIn('models_error', rows['codex'])
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
            with self.subTest(backend=backend), patch('expert_mcp.' + target, side_effect=result) as review:
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
        with patch('expert_mcp.review') as review:
            for name, extra, length in [
                    ('reprise_delegate', {'kind':'review'}, 12000),
                    ('reprise_delegate', {'kind':'review'}, 12001),
                    ('reprise_followup', {'thread_id':ID}, 12001)]:
                with self.subTest(name=name, length=length), self.assertRaises(ValueError):
                    self.call(name, dict(extra, backend='codex', model='gpt-6.1-sol', question='x' * length))
            review.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0], 0)
        # La limite de longueur prime sur l'admission (contrôle ligne 993 avant
        # l'admission du modèle) : la seed garantit que le refus ne vient pas
        # du catalogue (#242 : cleanup antirevalidation).
        self.seed_catalog()
        with patch('expert_mcp.review') as review:
            with self.assertRaisesRegex(ValueError, 'Invalid question or arguments'):
                self.call('reprise_delegate', {'kind':'review', 'backend':'codex',
                                               'model':'gpt-6.1-sol', 'question':'x' * 12001})
            review.assert_not_called()
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0], 0)

    def test_backends_no_inference_or_budget_consumption(self):
        with patch('expert_mcp.review') as review:
            result=self.call('reprise_backends',{})
            review.assert_not_called()
        self.assertFalse(result['automatic_fallback'])
        self.assertFalse(next(item for item in result['backends'] if item['id'] == 'autoclaw')['configured'])
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_calls').fetchone()[0],0)
    def test_invalid_delegation_rejected_before_budget(self):
        for args in [{'kind':'write','question':'x'},{'kind':'review','level':'ultra','question':'x'}]:
            with self.assertRaises(ValueError):self.call('reprise_delegate',args)
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_calls').fetchone()[0],0)

    def test_backends_reflects_catalog_lineup_change_after_cache_expiry(self):
        # (a) #242 : line-up modifié servi après expiration du TTL, jamais la liste statique.
        self.seed_catalog()
        self.assertEqual([item['model'] for item in self.call('reprise_backends', {})['backends'][0]['models']],
                         list(CODEX_TEST_MODELS))
        lineup = ['gpt-6.1-sol', 'gpt-6-luna']
        with patch('expert_mcp.Client') as client:
            client.return_value.call.side_effect = (
                lambda method, payload: {'data': [{'model': model} for model in lineup]}
                if method == 'model/list' else {})
            self.bridge._codex_catalog_time -= CODEX_CATALOG_TTL + 1
            codex = self.call('reprise_backends', {})['backends'][0]
            client.assert_called_once()
        self.assertEqual([item['model'] for item in codex['models']], lineup)
        self.assertEqual(codex['models_source'], 'live')
        self.assertNotIn('models_error', codex)

    def test_catalog_failure_refuses_admission_before_request_creation(self):
        # (b) #242 : model/list en échec → admission refusée avec cause explicite,
        # aucune insertion ; reprise_backends expose un catalogue vide (pas de repli statique).
        self.bridge._codex_catalog_cache = None
        self.bridge._codex_catalog_time = None
        with patch('expert_mcp.Client') as client:
            client.return_value.call.side_effect = OSError('proxy spawn failed')
            with self.assertRaisesRegex(ValueError, 'not in live catalog \\(proxy spawn failed\\)'):
                self.call('reprise_delegate', {'kind':'review', 'question':'Review', 'model':'gpt-6.1-sol'})
            self.assertEqual(client.call_count, 2)  # rafraîchissement + revalidation forcée
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0], 0)
        codex = self.call('reprise_backends', {})['backends'][0]
        self.assertEqual(codex['models'], [])
        self.assertEqual(codex['models_source'], 'unavailable')
        self.assertEqual(codex['models_error'], 'proxy spawn failed')

    def test_unknown_model_revalidated_once_against_live_catalog(self):
        # (c) #242 : revalidation forcée unique — le modèle apparu au catalogue
        # live est admis ; toujours inconnu après revalidation → refus.
        lineup = ['gpt-6.1-sol', 'gpt-6-luna', 'gpt-5.6-terra-new']
        before = self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0]
        rpc = lambda method, payload: {'data': [{'model': model} for model in lineup]} \
            if method == 'model/list' else {}
        with patch('expert_mcp.review', return_value={'status':'finished'}) as review, \
                patch('expert_mcp.Client') as client:
            client.return_value.call.side_effect = rpc
            self.call('reprise_delegate', {'kind':'review', 'question':'Review', 'model':'gpt-5.6-terra-new'})
            self.assertEqual(review.call_args.kwargs['model'], 'gpt-5.6-terra-new')
            client.assert_called_once()
        with patch('expert_mcp.review') as review, patch('expert_mcp.Client') as client:
            client.return_value.call.side_effect = rpc
            with self.assertRaisesRegex(ValueError, 'not in live catalog'):
                self.call('reprise_delegate', {'kind':'review', 'question':'Review', 'model':'ghost-model'})
            review.assert_not_called()
            client.assert_called_once()
        # Une seule insertion : l'appel admis ; le refus final n'en crée pas.
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM delegation_requests').fetchone()[0], before + 1)

    def write_catalog_file(self, data):
        self.catalog_path.write_text(json.dumps(data), encoding='utf-8')

    def test_live_catalog_persists_last_known_file_issue_253(self):
        # (a) #253 : succès live → source 'live' ET écriture du fichier
        # last-known-good ; un échec d'écriture est journalisé sans invalider le live.
        lineup = ['gpt-6.1-sol', 'gpt-6-luna']
        with patch('expert_mcp.Client') as client:
            client.return_value.call.side_effect = (
                lambda method, payload: {'data': [{'model': model,
                                                   'displayName': model,
                                                   'supportedReasoningEfforts': [
                                                       {'reasoningEffort': 'medium'}]}
                                                  for model in lineup]}
                if method == 'model/list' else {})
            entry = self.bridge._codex_catalog(force=True)
        self.assertEqual(entry['source'], 'live')
        self.assertEqual([item['model'] for item in entry['models']], lineup)
        self.assertIsNone(entry['error'])
        data = json.loads(self.catalog_path.read_text(encoding='utf-8'))
        self.assertEqual([item['model'] for item in data['models']], lineup)
        self.assertEqual(data['version'], 1)
        self.assertTrue(data['updated_at'].endswith('+00:00'))
        # Échec d'écriture (parent non répertoire) → live conservé, erreur journalisée.
        blocker = Path(self.temp.name)/'blocker'
        blocker.write_text('x')
        with patch('expert_mcp.Client') as client, \
                patch('expert_mcp.CATALOG_PATH', blocker/'codex-models.json'):
            client.return_value.call.side_effect = (
                lambda method, payload: {'data': [{'model': model} for model in lineup]}
                if method == 'model/list' else {})
            entry = self.bridge._codex_catalog(force=True)
        self.assertEqual(entry['source'], 'live')
        self.assertIn('catalog persistence failed', entry['error'])

    def test_last_known_fallback_when_live_fails_issue_253(self):
        # (b) #253 : codexd indisponible + fichier lisible → source 'last_known',
        # models/updated_at du fichier, erreur live préservée, route résolue,
        # fichier JAMAIS réécrit depuis le contenu last-known.
        stale = {'models': [{'model': 'gpt-6.1-sol', 'displayName': 'Sol',
                             'supportedReasoningEfforts': ['low', 'medium', 'high']},
                            {'model': 'gpt-6-luna', 'displayName': 'Luna',
                             'supportedReasoningEfforts': ['low', 'medium', 'high']}],
                 'updated_at': '2026-11-20T15:00:00+00:00', 'version': 1}
        self.write_catalog_file(stale)
        before = self.catalog_path.read_bytes()
        with patch('expert_mcp.Client', side_effect=ValueError('codexd down')):
            entry = self.bridge._codex_catalog(force=True)
        self.assertEqual(entry['source'], 'last_known')
        self.assertEqual([item['model'] for item in entry['models']], ['gpt-6.1-sol', 'gpt-6-luna'])
        self.assertEqual(entry['updated_at'], '2026-11-20T15:00:00+00:00')
        self.assertEqual(entry['error'], 'codexd down')
        self.assertEqual(self.catalog_path.read_bytes(), before)  # pas de réécriture circulaire
        routing = self.bridge._codex_route(entry, 'default')
        self.assertEqual(routing['model'], 'gpt-6.1-sol')
        self.assertEqual(routing['effort'], 'medium')

    def test_route_effort_fallback_picks_lowest_by_codex_order(self):
        # Revue (MINEUR S6) : repli « le plus bas » selon l'ordre codex ;
        # l'ordre lexicographique aurait choisi 'high' ('high' < 'medium' ASCII).
        item = {'model': 'gpt-6.1-sol', 'supportedReasoningEfforts': ['high', 'xhigh', 'low']}
        self.assertEqual(self.bridge._codex_route_effort(item, 'default')['effort'], 'low')
        item_unknown = {'model': 'gpt-9', 'supportedReasoningEfforts': ['omega', 'alpha']}
        self.assertEqual(self.bridge._codex_route_effort(item_unknown, 'default')['effort'], 'alpha')

    def test_route_effort_prescribed_wins_over_fallback(self):
        item = {'model': 'gpt-6.1-sol', 'supportedReasoningEfforts': ['low', 'high']}
        self.assertEqual(self.bridge._codex_route_effort(item, 'deep')['effort'], 'high')

    def test_unavailable_when_no_or_corrupt_file_and_live_fails_issue_253(self):
        # (c) #253 : fichier absent, corrompu ou sans modèle valide + codexd bas
        # → source 'unavailable', models vide (comportement #242 inchangé).
        for seed in ('absent', 'corrupt', 'empty'):
            with self.subTest(case=seed):
                self.bridge._codex_catalog_cache = None
                self.bridge._codex_catalog_time = None
                if seed == 'corrupt':
                    self.catalog_path.write_text('not json {')
                elif seed == 'empty':
                    self.write_catalog_file({'models': [], 'updated_at': 'x', 'version': 1})
                with patch('expert_mcp.Client', side_effect=ValueError('codexd down')):
                    entry = self.bridge._codex_catalog(force=True)
                self.assertEqual(entry['source'], 'unavailable')
                self.assertEqual(entry['models'], [])
                self.assertEqual(entry['error'], 'codexd down')

    def test_explicit_model_admitted_against_last_known_issue_253(self):
        # (d) #253 : modèle explicite de l'opérateur présent dans le fichier
        # last-known → admis (compromis documenté) ; dispatch résolu depuis
        # last-known quand codexd est indisponible.
        lineup = ['gpt-6.1-sol', 'gpt-6-luna', 'gpt-5.6-terra']
        self.write_catalog_file({'models': [{'model': model, 'displayName': model,
                                             'supportedReasoningEfforts': ['low', 'medium', 'high']}
                                            for model in lineup],
                                 'updated_at': '2026-11-20T15:00:00+00:00', 'version': 1})
        self.bridge._codex_catalog_cache = None
        self.bridge._codex_catalog_time = None
        with patch('expert_mcp.review', return_value={'status':'finished'}) as review, \
                patch('expert_mcp.Client', side_effect=ValueError('codexd down')):
            self.call('reprise_delegate', {'kind':'review', 'question':'Review', 'model':'gpt-6-luna'})
            self.assertEqual(review.call_args.kwargs['model'], 'gpt-6-luna')

    def test_nonpersistent_thread_is_not_registered(self):
        with patch('expert_mcp.review', return_value={'status':'blocked_permission','thread_id':ID,'resumable':False}):
            self.call('codex_review', {'question':'review'})
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM expert_threads').fetchone()[0], 0)

    def test_claude_explicit_route_followup_and_backend_isolation(self):
        self.bridge.claude_program = '/claude'
        self.bridge.claude_subscription_only = True
        with patch('expert_mcp.claude_review', return_value={
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
        with patch('expert_mcp.review') as codex, patch('expert_mcp.claude_review') as claude:
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
        with patch('expert_mcp.review', return_value={'status':'quota_blocked'}):
            self.call('codex_review', {'question':'Review'})
        with patch('expert_mcp.claude_review', return_value={'status':'finished','resumable':False}) as review:
            self.call('reprise_delegate', {'backend':'claude-code','kind':'review','question':'Review'})
            self.call('reprise_delegate', {'backend':'claude-code','kind':'review','question':'Review'})
            self.assertEqual(self.call('reprise_delegate', {'backend':'claude-code','kind':'review','question':'Review'})['status'],'finished')
            self.assertEqual(review.call_count,3)
        self.assertEqual(self.bridge.db.execute('SELECT count(*) FROM claude_expert_routes').fetchone()[0],0)


class ClaudeDesktopBridgeTests(RequestCalls, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state',
                             desktop_socket='/private/tmp/quick.sock')
        self.addCleanup(self.bridge.close)

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

    def test_delegate_claude_desktop_allowed_without_quota_flag(self):
        # claude-desktop = inclus dans l'abonnement : la condition billing_unverified
        # ne s'applique qu'au backend claude-code (payant par clé API).
        self.assertFalse(self.bridge.claude_subscription_only)
        with patch('claude_desktop.review',
                   return_value={'status': 'finished', 'resumable': False, 'thread_id': ID}) as review:
            result = self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise', 'backend': 'claude-desktop'})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['backend'], 'claude-desktop')
        self.assertFalse(result['automatic_fallback'])
        review.assert_called_once()

    def test_delegate_rejects_unknown_backend(self):
        with self.assertRaises(ValueError):
            self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise', 'backend': 'nope'})

    def test_delegate_refuses_claude_desktop_without_desktop_socket(self):
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state-nosock')
        self.addCleanup(self.bridge.close)
        with patch('claude_desktop.review') as review:
            result = self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise', 'backend': 'claude-desktop'})
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertEqual(result['backend'], 'claude-desktop')
        self.assertFalse(result['automatic_fallback'])
        review.assert_not_called()
        self.assertEqual(self.bridge.db.execute(
            "SELECT json_extract(result,'$.status') FROM delegation_requests").fetchone()[0], 'engine_unavailable')

    def test_return_channel_unavailable_retries_taskstore_once(self):
        # Canal de retour None au moment de l'appel : exactement UNE tentative
        # de reconstruction du TaskStore par appel, avant tout refus.
        from unittest.mock import MagicMock
        self.bridge.desktop_tasks = None
        with patch('expert_mcp.TaskStore') as store, \
                patch('claude_desktop.review', return_value={'status': 'finished', 'resumable': False}) as review:
            result = self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise',
                                                    'backend': 'claude-desktop'})
        store.assert_called_once_with(self.bridge.desktop_state, self.bridge.root)
        self.assertEqual(result['status'], 'finished')
        review.assert_called_once()
        # Reconstruction impossible : refus return_channel_unavailable, le refus
        # persiste a chaque appel. call() execute un preflight + un passage reel,
        # soit 2 constructions (2 essais) par appel : contract max 2 constructions
        # par appel (le gate du handler n'en fait qu'une par passage).
        self.bridge.desktop_tasks = None
        store.reset_mock()
        store.side_effect = sqlite3.OperationalError('state unreachable')
        with patch('expert_mcp.TaskStore', store), \
                patch('claude_desktop.review') as review:
            result = self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise',
                                                    'backend': 'claude-desktop'})
            self.assertEqual(store.call_count, 2)
            self.bridge.desktop_tasks = None
            store.reset_mock()
            # Question différente : une demande équivalente incertaine serait
            # dédupliquée ('indeterminate') sans nouvelle tentative.
            result2 = self.call('reprise_delegate', {'question': 'Explique autrement', 'kind': 'expertise',
                                                     'backend': 'claude-desktop'})
            self.assertEqual(store.call_count, 2)
        review.assert_not_called()
        for refused in (result, result2):
            self.assertEqual(refused['status'], 'return_channel_unavailable')
            self.assertEqual(refused['backend'], 'claude-desktop')
            self.assertFalse(refused['automatic_fallback'])
            self.assertIs(refused['lazy_start_attempted'], False)
        self.assertEqual(self.bridge.db.execute(
            "SELECT json_extract(result,'$.status') FROM delegation_requests "
            "WHERE scope=? AND result LIKE '%return_channel_unavailable%' "
            "ORDER BY rowid DESC LIMIT 1",
            (self.bridge.requests.scope,)).fetchone()[0], 'return_channel_unavailable')

    def test_lazy_start_disabled_by_default(self):
        # Cle delegation_lazy_start absente : AUCUN depart paresseux, le refus
        # reste inchange ; lazy_start_attempted est present et vaut False.
        self.bridge.desktop_tasks = None
        with patch('expert_mcp.TaskStore', side_effect=sqlite3.OperationalError('down')), \
                patch('claude_desktop.review') as review, \
                patch('delegation_resume.lazy_start') as lazy:
            result = self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise',
                                                    'backend': 'claude-desktop'})
        review.assert_not_called()
        lazy.assert_not_called()
        self.assertEqual(result['status'], 'return_channel_unavailable')
        self.assertIs(result['lazy_start_attempted'], False)
        self.assertNotIn('lazy_start', result)

    def test_call_opportunistic_resume_processes_due_job(self):
        # Porte opportuniste (lot C #239) : un job du present dans expert.sqlite
        # est traite pendant un appel call() quelconque (ici un refus de canal
        # de retour). Receipt inexistant -> 'done' sans contact natif.
        import delegation_resume
        import delegation_reconcile
        self.bridge.desktop_tasks = None
        delegation_resume.schedule(self.bridge.db, 'opp-req-1', 'codex', str(self.bridge.root),
                                   due_at=1.0)
        with patch('delegation_reconcile.reconcile') as reconcile:
            result = self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise',
                                                    'backend': 'claude-desktop'})
        reconcile.assert_not_called()
        row = self.bridge.db.execute('SELECT status FROM delegation_resume_jobs WHERE request_key=?',
                                     ('opp-req-1',)).fetchone()
        self.assertEqual(row[0], 'done')

    def test_delegate_rejects_arbitrary_fields(self):
        with self.assertRaises(ValueError):
            self.call('reprise_delegate', {'question': 'Explique', 'kind': 'expertise',
                                       'backend': 'claude-desktop', 'hack': 'x'})


class IronclawBridgeTests(RequestCalls, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state')
        self.bridge.program = 'codex'
        self.addCleanup(self.bridge.close)

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
        with patch('expert_mcp.ironclaw.review') as review:
            result = self.call('reprise_delegate', {'backend': 'ironclaw', 'kind': 'expertise', 'question': 'Ping'})
        self.assertEqual(result['status'], 'engine_unavailable')
        review.assert_not_called()
        self.assertEqual(self.bridge.db.execute(
            "SELECT json_extract(result,'$.status') FROM delegation_requests").fetchone()[0], 'engine_unavailable')

    def test_delegate_ironclaw_unconfigured_returns_engine_unavailable(self):
        with patch('expert_mcp.ironclaw.review') as review:
            result = self.call('reprise_delegate', {'backend': 'ironclaw', 'kind': 'expertise', 'question': 'Ping'})
        self.assertEqual(result['status'], 'engine_unavailable')
        review.assert_not_called()
        self.assertEqual(self.bridge.db.execute(
            "SELECT json_extract(result,'$.status') FROM delegation_requests").fetchone()[0], 'engine_unavailable')

    def test_delegate_ironclaw_configured_dispatches_review(self):
        self.bridge.ironclaw_values = {'host': 'ironclaw.example.org', 'port': '22',
                                       'user': 'agent', 'key': '/k'}
        with patch('expert_mcp.ironclaw.review',
                   return_value={'status': 'finished', 'response': 'Réponse'}) as review:
            result = self.call('reprise_delegate', {'backend': 'ironclaw', 'kind': 'expertise', 'question': 'Ping'})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['backend'], 'ironclaw')
        self.assertEqual(result['response'], 'Réponse')
        self.assertFalse(result['automatic_fallback'])
        review.assert_called_once()
        self.assertEqual(self.bridge.db.execute(
            "SELECT json_extract(result,'$.status') FROM delegation_requests").fetchone()[0], 'finished')

    def test_delegate_ironclaw_incomplete_config_returns_engine_unavailable(self):
        self.bridge.ironclaw_values = {'host': 'ironclaw.example.org', 'user': 'agent'}
        with patch('expert_mcp.ironclaw.review') as review:
            result = self.call('reprise_delegate', {'backend': 'ironclaw', 'kind': 'expertise', 'question': 'Ping'})
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertIn('Configuration IronClaw requise', result['reason'])
        self.assertFalse(result['automatic_fallback'])
        review.assert_not_called()

    def test_backends_declare_ironclaw_configured_when_values_are_valid(self):
        self.bridge.ironclaw_values = {'host': 'ironclaw.example.org', 'port': '22',
                                       'user': 'agent', 'key': '/k'}
        row = next(b for b in self.bridge.call('reprise_backends', {})['backends'] if b['id'] == 'ironclaw')
        self.assertTrue(row['configured'])

    def test_followup_ironclaw_rejected(self):
        with self.assertRaises(ValueError):
            self.bridge.call('reprise_followup', {'backend': 'ironclaw', 'thread_id': 'x', 'question': 'continue'})


class CompactReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state')
        self.bridge.program = 'codex'
        self.bridge._codex_catalog_cache = simulated_codex_catalog(CODEX_TEST_MODELS)
        self.bridge._codex_catalog_time = time.monotonic()
        self.addCleanup(self.bridge.close)

    def dispatch(self, name, args, ident=1):
        content = self.bridge.dispatch({'method': 'tools/call', 'id': ident,
            'params': {'name': name, 'arguments': args}})
        wire = json.dumps({'jsonrpc': '2.0', 'id': ident, 'result': content}, ensure_ascii=False)
        self.assertLessEqual(len(wire), 7000)
        return json.loads(content['content'][0]['text'])

    def test_full_then_receipt_replay_preserves_identity_and_result(self):
        args = {'request_key': 'key', 'question': 'Inspect', 'kind': 'review'}
        with patch('expert_mcp.review', return_value={
                'status': 'finished', 'response': 'x' * 20000, 'thread_id': ID}) as run:
            result = self.bridge.call('reprise_delegate', args)
            before = self.bridge.db.execute('SELECT * FROM delegation_requests').fetchall()
            manifest = self.dispatch('reprise_delegate', dict(args, return_mode='receipt'))
            self.assertEqual(manifest, self.dispatch(
                'reprise_requests', {'request_key': 'key', 'view': 'receipt'}))
            self.assertEqual(self.bridge.call('reprise_delegate', dict(args, return_mode='full')), result)
            self.assertEqual(self.bridge.db.execute('SELECT * FROM delegation_requests').fetchall(), before)
            run.assert_called_once()
        with patch('expert_mcp.review', return_value={'status': 'finished', 'response': 'y' * 20000}) as run:
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
                with patch('expert_mcp.review', return_value={
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
        with patch('expert_mcp.claude_review', return_value={
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
        with patch('expert_mcp.review') as run:
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
        with patch('expert_mcp.review') as run:
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
        for result in (None, {'status': 'running'}, {'status': 'needs_review'}):
            if result is not None:
                self.bridge.requests.finish('key', result)
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.dispatch('reprise_requests', {
                    'request_key': 'key', 'view': 'receipt', 'acknowledge': True})
        # Issue #53 arbitration: an awaiting_human receipt (blocked_permission) is
        # reconcilable by acknowledgement, which records it and resets acknowledged.
        self.bridge.requests.finish('key', {'status': 'blocked_permission'})
        receipt = self.dispatch('reprise_requests', {
            'request_key': 'key', 'view': 'receipt', 'acknowledge': True})
        self.assertEqual(receipt['state'], 'recorded')
        self.assertTrue(self.bridge.requests.lookup({'request_key': 'key'})['acknowledged'])
        self.bridge.requests.finish('key', {'status': 'finished'})
        with self.assertRaises(ValueError):
            self.dispatch('reprise_requests', {
                'request_key': 'key', 'view': 'page', 'acknowledge': True})

class ChatGptStateTests(unittest.TestCase):
    """Issue #250 : seul --chatgpt-state nomme le répertoire d'état ChatGPT."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state')
        self.bridge.program = 'codex'
        self.bridge._codex_catalog_cache = simulated_codex_catalog(CODEX_TEST_MODELS)
        self.bridge._codex_catalog_time = time.monotonic()
        self.addCleanup(self.bridge.close)

    def test_refusal_names_chatgpt_state(self):
        self.assertFalse(self.bridge.chatgpt_configured)
        payload = self.bridge.call('reprise_delegate', {
            'request_key': str(uuid.uuid4()), 'backend': 'chatgpt',
            'kind': 'review', 'question': 'Revue'})
        self.assertEqual(payload['status'], 'engine_unavailable')
        self.assertIn('--chatgpt-state', payload['reason'])

    def test_parser_rejects_removed_alias(self):
        # Chemin absolu : les shards CI (#257) lancent les tests avec
        # cwd = reprise/tests/, où le chemin relatif reprise/... est inexistant.
        # argparse refuse l'option inconnue avant toute ouverture d'état.
        script = str(Path(__file__).parents[1] / 'expert_mcp.py')
        completed = subprocess.run(
            [sys.executable, script, '--project-root', self.temp.name,
             '--chatgpt-socket', self.temp.name],
            capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 2)
        self.assertIn('--chatgpt-socket', completed.stderr)


class BridgeFinalizerTests(unittest.TestCase):
    """Le finalizer doit tourner sans close() explicite : pas de méthode liée
    dans weakref.finalize (elle retiendrait self et la collecte échouerait)."""

    def test_bridge_collected_without_close_closes_databases(self):
        import gc
        import weakref as weakref_module
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        bridge = Bridge(temp.name, {}, Path(temp.name)/'state')
        # pre-close sentinel: rexec works now, must fail after collection
        bridge.db.execute('SELECT 1')
        bridge_ref = weakref_module.ref(bridge)
        # Follow the finalizer's captured list through the object to detect closure:
        import sqlite3 as sqlite3_module
        connection = bridge.db
        del bridge
        gc.collect()
        self.assertIsNone(bridge_ref(), 'Bridge doit être collectable sans close()')
        with self.assertRaisesRegex(sqlite3_module.ProgrammingError, 'closed'):
            connection.execute('SELECT 1')


class ChatGptFollowupTests(RequestCalls, unittest.TestCase):
    """Issue #260 : followup ChatGPT — possession levée ou refus typé.

    thread_id = request_key de l'admission chatgpt d'origine ; la levée
    exige une ligne chatgpt_submissions de CE scope dont le reçu durable de
    dispatch est présent. L'au-plus-une-fois reste porté par le request_key
    transmis au ledger Endpoint (au-moins-une-fois côté bridge inchangé).
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.catalog_path = Path(self.temp.name)/'codex-models.json'
        catalog_patcher = patch('expert_mcp.CATALOG_PATH', self.catalog_path)
        catalog_patcher.start()
        self.addCleanup(catalog_patcher.stop)
        # Chemin absolu requis par Bridge (resolve strict) ; bridge et tests
        # partagent le même fichier chatgpt.sqlite.
        self.chatgpt_state = Path(self.temp.name)/'chatgpt-state'
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state',
                             chatgpt_state=str(self.chatgpt_state))
        self.bridge.program = 'codex'
        self.addCleanup(self.bridge.close)

    def seed_chatgpt_submissions(self, request_key, *, dispatch_receipt='receipt-260'):
        database = self.chatgpt_state/'chatgpt.sqlite'
        database.parent.mkdir(parents=True, exist_ok=True)
        ledger = sqlite3.connect(database)
        try:
            with ledger:
                ledger.execute(
                    'CREATE TABLE IF NOT EXISTS chatgpt_submissions ('
                    'scope TEXT NOT NULL, request_key TEXT NOT NULL, prompt TEXT NOT NULL,'
                    ' delivery TEXT, dispatch_receipt TEXT, before_evidence TEXT,'
                    ' after_evidence TEXT, needs_review INTEGER NOT NULL DEFAULT 0,'
                    ' admitted_at REAL NOT NULL, PRIMARY KEY(scope, request_key))')
                ledger.execute(
                    'INSERT INTO chatgpt_submissions (scope, request_key, prompt,'
                    ' delivery, dispatch_receipt, admitted_at) VALUES (?,?,?,?,?,?)',
                    (str(self.bridge.root), request_key, 'question initiale',
                     'admitted', dispatch_receipt, time.time()))
        finally:
            ledger.close()

    def test_unknown_chatgpt_thread_is_typed_thread_not_owned(self):
        with self.assertRaises(ValueError) as caught:
            self.call('reprise_followup', {'thread_id': ID, 'backend': 'chatgpt',
                                           'question': 'continue'})
        payload = error_payload(caught.exception)
        self.assertEqual(payload['data']['kind'], 'thread_not_owned')
        self.assertEqual(payload['data']['backend'], 'chatgpt')
        self.assertEqual(payload['data']['thread_id'], ID)
        self.assertEqual(payload['data']['resume_with'], 'parent_request_key')

    def test_admission_without_dispatch_receipt_is_not_owned(self):
        owned = str(uuid.uuid4())
        self.seed_chatgpt_submissions(owned, dispatch_receipt=None)
        with self.assertRaises(ValueError) as caught:
            self.call('reprise_followup', {'thread_id': owned, 'backend': 'chatgpt',
                                           'question': 'continue'})
        self.assertEqual(error_payload(caught.exception)['data']['kind'],
                         'thread_not_owned')

    def test_admission_from_another_scope_is_not_owned(self):
        # La possession est locale au projet : une admission d'un autre root
        # (scope différent) ne lève pas le refus.
        owned = str(uuid.uuid4())
        self.seed_chatgpt_submissions(owned)
        self.bridge.root = Path(self.temp.name)/'autre-projet'
        try:
            with self.assertRaises(ValueError) as caught:
                self.call('reprise_followup', {'thread_id': owned, 'backend': 'chatgpt',
                                               'question': 'continue'})
            self.assertEqual(error_payload(caught.exception)['data']['kind'],
                             'thread_not_owned')
        finally:
            self.bridge.root = Path(self.temp.name)

    def test_non_string_thread_id_is_refused_before_ownership_lift(self):
        # La validation de schéma (thread_id: string) refuse les identifiants
        # non string avant la levée de possession : refus générique, jamais
        # une possession implicite.
        for ident in (None, 123, ['a']):
            with self.assertRaises(ValueError):
                self.call('reprise_followup', {'thread_id': ident, 'backend': 'chatgpt',
                                               'question': 'continue'})

    def test_owned_chatgpt_followup_dispatches_with_followup_identity(self):
        owned = str(uuid.uuid4())
        self.seed_chatgpt_submissions(owned)
        result = {'status': 'captured', 'thread_id': owned, 'response': 'Suite experte'}
        with patch('expert_mcp.chatgpt_quick.review', return_value=result) as review:
            payload = self.call('reprise_followup', {'request_key': 'follow-260',
                                                     'thread_id': owned,
                                                     'backend': 'chatgpt',
                                                     'question': 'continue'})
        review.assert_called_once()
        # L'au-plus-une-fois du ledger Endpoint est indexé sur l'identité du
        # followup lui-même (request_key actif), pas sur le thread d'origine.
        self.assertEqual(review.call_args.kwargs['request_key'], 'follow-260')
        self.assertEqual(payload['backend'], 'chatgpt')
        self.assertFalse(payload['automatic_fallback'])
        self.assertEqual(payload['thread_id'], owned)

    def test_chatgpt_thread_id_never_leaks_into_codex_control(self):
        # Le rename codex_followup ne s'applique pas à chatgpt : un thread_id
        # d'admission chatgpt absent d'expert_threads ne bloque pas la levée
        # (couvert ci-dessus), et un followup codex reste bloqué sans route.
        with self.assertRaises(ValueError) as caught:
            self.call('reprise_followup', {'thread_id': ID, 'backend': 'codex',
                                           'question': 'continue'})
        self.assertEqual(error_payload(caught.exception)['data']['kind'],
                         'thread_not_owned')

    def test_chatgpt_implementation_mode_still_refused(self):
        owned = str(uuid.uuid4())
        self.seed_chatgpt_submissions(owned)
        with patch('expert_mcp.chatgpt_quick.review') as review:
            with self.assertRaises(ValueError) as caught:
                self.call('reprise_followup', {'thread_id': owned, 'backend': 'chatgpt',
                                               'question': 'Implement', 'mode': 'implementation'})
            review.assert_not_called()
        payload = error_payload(caught.exception)
        self.assertNotEqual(payload['data']['kind'], 'thread_not_owned')

    def test_metaai_followup_refusal_unchanged(self):
        with self.assertRaises(ValueError) as caught:
            self.call('reprise_followup', {'thread_id': ID, 'backend': 'metaai',
                                           'question': 'continue'})
        self.assertNotEqual(error_payload(caught.exception)['data']['kind'],
                            'thread_not_owned')
