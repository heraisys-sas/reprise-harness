import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from claude_inbox import (MAX_RESULT_CHARS, CollectedResult, InboxError, acknowledge_result,
                          connect_existing, get_result, list_tasks, run_action, verify_binding)
from desktop_task_store import TaskStore


class FakeClient:
    def __init__(self, project, quick_state):
        self.project = str(project.resolve())
        self.quick_state = str(quick_state.resolve())
        self.store = TaskStore(quick_state.parent / 'server-state', project)
        self.calls = []
        self.listed_tasks = []
        self.empty_page_loop = False
        self.receipt_override = None
        self.page_override = None

    def call(self, method, arguments):
        self.calls.append((method, dict(arguments)))
        if method == 'quick_entry/capabilities':
            return {'task_return_supported': True, 'project_root': self.project,
                    'state_sha256': hashlib.sha256(self.quick_state.encode()).hexdigest()}
        if method == 'quick_entry/list_tasks':
            cursor = int(arguments.get('cursor', '0'))
            if self.empty_page_loop:
                return {'tasks': [], 'high_water': 1,
                        'next_cursor': f'cursor-{cursor + 1}'}
            limit = arguments['limit']
            rows = list(reversed(self.listed_tasks))
            page_rows = rows[cursor:cursor + limit]
            next_cursor = str(cursor + len(page_rows)) if cursor + len(page_rows) < len(rows) else None
            return {'tasks': page_rows, 'high_water': 1, 'next_cursor': next_cursor}
        if method == 'quick_entry/get_result':
            if arguments.get('view') == 'receipt' and self.receipt_override is not None:
                return dict(self.receipt_override)
            if arguments.get('view') == 'page' and self.page_override is not None:
                return dict(self.page_override(arguments))
            return self.store.result(**arguments)
        raise AssertionError(f'Unexpected method: {method}')

    def close(self):
        self.store.close()


class ClientProxy:
    def __init__(self, client):
        self.client = client

    def call(self, method, arguments):
        return self.client.call(method, arguments)

    def close(self):
        pass


class ClaudeInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.quick_state = self.root / 'quick-state'
        self.quick_state.mkdir()
        self.manifest = {'format': 1, 'status': 'installed', 'connector_name': 'reprise-harness',
                         'project': str(self.project), 'quick_state': str(self.quick_state)}
        self.client = FakeClient(self.project, self.quick_state)
        self.addCleanup(self.client.close)

    def admit_finished(self, key, prompt='private prompt', answer='résultat privé'):
        self.client.store.admit(key, prompt, 'chat-secret')
        reservation = self.client.store.reserve(key, 'claim-secret')
        self.client.store.mark_attempted(key, reservation['attempt_id'])
        self.client.store.submit(key, reservation['attempt_id'], answer)

    def test_binding_checks_project_and_state_fingerprint(self):
        caps = self.client.call('quick_entry/capabilities', {})
        self.assertEqual(verify_binding(self.project, self.manifest, caps)['state_sha256'], caps['state_sha256'])
        wrong_project = self.root / 'other'
        wrong_project.mkdir()
        with self.assertRaisesRegex(InboxError, 'autre projet|correspond'):
            verify_binding(wrong_project, self.manifest, caps)
        caps['state_sha256'] = '0' * 64
        with self.assertRaisesRegex(InboxError, 'empreinte'):
            verify_binding(self.project, self.manifest, caps)

    def test_binding_rejects_uninstalled_manifest_and_missing_capability(self):
        manifest = dict(self.manifest, status='removed')
        caps = self.client.call('quick_entry/capabilities', {})
        with self.assertRaises(InboxError):
            verify_binding(self.project, manifest, caps)
        caps['task_return_supported'] = False
        with self.assertRaisesRegex(InboxError, 'collecte'):
            verify_binding(self.project, self.manifest, caps)

    def test_list_tasks_paginates_and_returns_only_safe_metadata(self):
        self.client.store.admit('task-1', 'secret prompt one', 'chat-one')
        self.client.store.admit('task-2', 'secret prompt two', 'chat-two')
        self.client.listed_tasks.extend([
            {'request_key': 'task-1', 'state': 'pending', 'result_available': False, 'acknowledged': False},
            {'request_key': 'task-2', 'state': 'finished', 'result_available': True, 'acknowledged': False},
        ])
        listed = list_tasks(self.client, self.project, self.manifest, page_size=1)
        self.assertEqual([task['request_key'] for task in listed['tasks']], ['task-2', 'task-1'])
        self.assertNotIn('claim_key', json.dumps(listed))
        self.assertNotIn('prompt', json.dumps(listed))
        calls = [args for method, args in self.client.calls if method == 'quick_entry/list_tasks']
        self.assertEqual(len(calls), 2)
        self.assertIn('cursor', calls[1])

    def test_list_tasks_rejects_empty_page_with_distinct_next_cursor(self):
        self.client.empty_page_loop = True
        with self.assertRaisesRegex(InboxError, 'page vide'):
            list_tasks(self.client, self.project, self.manifest, page_size=1, max_tasks=5)
        calls = [args for method, args in self.client.calls if method == 'quick_entry/list_tasks']
        self.assertEqual(len(calls), 1)

    def test_result_is_paginated_and_verified_without_implicit_ack(self):
        answer = 'é' * 170
        self.admit_finished('task-result', answer=answer)
        collected = get_result(self.client, self.project, self.manifest, 'task-result', page_chars=30)
        self.assertIsInstance(collected, CollectedResult)
        self.assertEqual(collected.answer, answer)
        self.assertEqual(collected.total_length, len(collected.canonical_result))
        self.assertEqual(hashlib.sha256(collected.canonical_result.encode()).hexdigest(), collected.result_sha256)
        self.assertFalse(collected.acknowledged)
        self.assertFalse(self.client.store.result('task-result')['acknowledged'])
        self.assertGreater(len([1 for method, args in self.client.calls
                                if method == 'quick_entry/get_result' and args.get('view') == 'page']), 1)

    def test_answer_decodes_escaped_controls_and_unicode_after_canonical_verification(self):
        answer = 'été ☃\n' + '\x01\x02' * 40
        self.admit_finished('task-escaped', answer=answer)
        collected = get_result(self.client, self.project, self.manifest, 'task-escaped', page_chars=9)
        self.assertEqual(collected.answer, answer)
        self.assertIn('\\u0001', collected.canonical_result)
        self.assertEqual(hashlib.sha256(collected.canonical_result.encode('utf-8')).hexdigest(),
                         collected.result_sha256)

    def test_answers_over_one_million_characters_remain_supported(self):
        answer = 'é' * 1_000_001
        self.admit_finished('task-over-one-million', answer=answer)
        collected = get_result(self.client, self.project, self.manifest, 'task-over-one-million')
        self.assertEqual(len(collected.answer), 1_000_001)
        self.assertGreater(collected.total_length, 1_000_000)

    def test_acknowledgement_requires_explicit_request_and_same_verified_result(self):
        self.admit_finished('task-ack')
        collected = get_result(self.client, self.project, self.manifest, 'task-ack')
        with self.assertRaisesRegex(InboxError, 'explicite'):
            acknowledge_result(self.client, self.project, self.manifest, collected)
        self.assertFalse(self.client.store.result('task-ack')['acknowledged'])
        ack = acknowledge_result(self.client, self.project, self.manifest, collected, explicit=True)
        self.assertTrue(ack['acknowledged'])
        self.assertEqual(ack['result_sha256'], collected.result_sha256)

    def test_changed_result_is_rejected_before_acknowledgement(self):
        self.admit_finished('task-changed')
        collected = get_result(self.client, self.project, self.manifest, 'task-changed')
        self.client.receipt_override = {'status': 'finished', 'request_key': 'task-changed',
                                        'available': True, 'total_length': collected.total_length,
                                        'result_sha256': '0' * 64, 'acknowledged': False}
        with self.assertRaisesRegex(InboxError, 'diffère'):
            acknowledge_result(self.client, self.project, self.manifest, collected, explicit=True)
        self.assertFalse(self.client.store.result('task-changed')['acknowledged'])

    def test_page_sha_mismatch_is_rejected(self):
        self.admit_finished('task-page-mismatch')
        def changed_page(args):
            actual = self.client.store.result(**args)
            actual['result_sha256'] = '0' * 64
            return actual
        self.client.page_override = changed_page
        with self.assertRaises(InboxError):
            get_result(self.client, self.project, self.manifest, 'task-page-mismatch')

    def test_page_offset_mismatch_is_rejected(self):
        self.admit_finished('task-offset-mismatch')
        def wrong_offset(args):
            actual = self.client.store.result(**args)
            actual['offset'] = args['offset'] + 1
            return actual
        self.client.page_override = wrong_offset
        with self.assertRaisesRegex(InboxError, 'pages'):
            get_result(self.client, self.project, self.manifest, 'task-offset-mismatch')

    def test_result_over_local_size_limit_is_rejected_before_page_fetch(self):
        self.admit_finished('task-too-large')
        self.client.receipt_override = {'status': 'finished', 'request_key': 'task-too-large',
                                        'available': True, 'total_length': MAX_RESULT_CHARS + 1,
                                        'result_sha256': 'a' * 64, 'acknowledged': False}
        with self.assertRaisesRegex(InboxError, 'dépasse'):
            get_result(self.client, self.project, self.manifest, 'task-too-large')
        self.assertFalse(any(method == 'quick_entry/get_result' and args.get('view') == 'page'
                             for method, args in self.client.calls))

    def test_socket_factory_receives_only_existing_socket_parameters(self):
        captured = {}
        def factory(program, values, timeout):
            captured.update(program=program, values=values, timeout=timeout)
            return self.client
        self.assertIs(connect_existing('/tmp/existing.sock', factory, timeout=3), self.client)
        self.assertEqual(captured, {'program': None, 'values': {'socket': '/tmp/existing.sock'}, 'timeout': 3})

    def test_socket_connection_failure_is_explicitly_unavailable(self):
        def fail(*args, **kwargs):
            raise OSError('no socket')
        with self.assertRaisesRegex(InboxError, 'aucun service') as error:
            connect_existing('/tmp/missing.sock', fail)
        self.assertEqual(error.exception.code, 'unavailable')

    def test_cli_read_returns_decoded_answer_with_proof_metadata(self):
        self.admit_finished('task-cli-read', answer='Answer visible, not an envelope')
        manifest_path = self.root / 'managed.json'
        manifest_path.write_text(json.dumps(self.manifest), encoding='utf-8')
        result = run_action('read', self.project, 'task-cli-read', manifest_path=manifest_path,
                            socket_path='/unused-test-socket', client_factory=lambda *a, **k: ClientProxy(self.client))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['answer'], 'Answer visible, not an envelope')
        self.assertEqual(result['project'], str(self.project.resolve()))
        self.assertEqual(len(result['result_sha256']), 64)

    def test_cli_ack_requires_the_displayed_identity_and_acknowledges_explicitly(self):
        self.admit_finished('task-cli-ack')
        collected = get_result(self.client, self.project, self.manifest, 'task-cli-ack')
        manifest_path = self.root / 'managed.json'
        manifest_path.write_text(json.dumps(self.manifest), encoding='utf-8')
        with self.assertRaisesRegex(InboxError, 'diffère'):
            run_action('ack', self.project, 'task-cli-ack', '0' * 64,
                       collected.total_length, manifest_path, '/unused-test-socket',
                       lambda *a, **k: ClientProxy(self.client), explicit=True)
        self.assertFalse(any(args.get('acknowledge') is True for method, args in self.client.calls
                             if method == 'quick_entry/get_result'))
        acknowledged = run_action('ack', self.project, 'task-cli-ack', collected.result_sha256,
                                  collected.total_length, manifest_path, '/unused-test-socket',
                                  lambda *a, **k: ClientProxy(self.client), explicit=True)
        self.assertEqual(acknowledged['status'], 'ok')
        self.assertTrue(acknowledged['acknowledged'])


if __name__ == '__main__':
    unittest.main()
