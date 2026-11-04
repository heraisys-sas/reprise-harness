import json
import os
from unittest.mock import patch
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_orchestrator_memory as fixtures
SOURCE, fake_crypt, uid = fixtures.SOURCE, fixtures.fake_crypt, fixtures.uid
from orchestrator_memory import Memory
import orchestrator_memory as memory
from orchestrator_memory_service import MemoryService, LIST_BUDGET_BYTES, MAX_OUTPUT_BYTES, _size


class ServiceTests(unittest.TestCase):
    def setUp(self):
        fixtures.MemoryTests.setUp(self)
        self.service = MemoryService(self.state, self.p1, memory_factory=lambda path: Memory(path, crypt=fake_crypt))

    def tearDown(self):
        fixtures.MemoryTests.tearDown(self)

    def request(self, op, **fields):
        return self.service.handle({'op': op, **fields})

    def test_operator_fact_crud_retry_and_conflicts(self):
        operation = uid()
        request = {'operation_id': operation, 'text': 'fait privé', 'source': SOURCE}
        created = self.request('create_fact', **request)
        self.assertEqual(created['status'], 'finished')
        self.assertEqual(created, self.request('create_fact', **request))
        self.assertEqual(self.request('create_fact', **(request | {'text': 'autre'}))['status'], 'operation_conflict')
        ident = created['result']['id']
        self.assertEqual(self.request('get_fact', fact_id=ident)['item']['status'], 'asserted')
        page = self.request('list_facts', offset=0)
        self.assertEqual(page['items'][0]['id'], ident)
        self.assertNotIn('text', page['items'][0])
        update = {'operation_id': uid(), 'fact_id': ident, 'expected_revision': 1, 'text': 'modifié', 'source': SOURCE}
        self.assertEqual(self.request('update_fact', **update)['result']['revision'], 2)
        self.assertEqual(self.request('operation_status', operation_id=update['operation_id'])['state'], 'recorded')
        self.assertEqual(self.request('update_fact', **(update | {'operation_id': uid()}))['status'], 'version_conflict')
        delete = {'operation_id': uid(), 'fact_id': ident, 'expected_revision': 2}
        self.assertEqual(self.request('delete_fact', **delete)['status'], 'finished')
        self.assertEqual(self.request('operation_status', operation_id=delete['operation_id'])['state'], 'recorded')
        self.assertEqual(self.request('delete_fact', **delete)['status'], 'finished')
        self.assertEqual(self.request('get_fact', fact_id=ident)['status'], 'not_found')

    def test_checkpoint_selected_project_and_work_enforced(self):
        fact = self.request('create_fact', operation_id=uid(), text='fait', source=SOURCE)['result']['id']
        work = uid()
        params = {'operation_id': uid(), 'work_id': work, 'expected_version': 0, 'fact_ids': [fact],
                  'decisions': [], 'constraints': ['ne pas publier'], 'next_action': 'vérifier',
                  'uncertain_effects': ['envoi inconnu'], 'source': SOURCE}
        result = self.request('create_checkpoint', **params)
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result, self.request('create_checkpoint', **params))
        self.assertEqual(self.request('operation_status', operation_id=params['operation_id'])['state'], 'recorded')
        self.assertEqual(self.request('create_checkpoint', **(params | {'operation_id': uid()}))['status'], 'version_conflict')
        ident = result['result']['id']
        self.assertEqual(self.request('get_checkpoint', work_id=uid(), checkpoint_id=ident)['status'], 'not_found')
        self.assertEqual(self.request('get_checkpoint', work_id=work, checkpoint_id=ident)['item']['payload']['uncertain_effects'], ['envoi inconnu'])
        other = MemoryService(self.state, self.p2, memory_factory=lambda path: Memory(path, crypt=fake_crypt))
        self.assertEqual(other.handle({'op': 'get_checkpoint', 'work_id': work, 'checkpoint_id': ident})['status'], 'not_found')
        self.assertEqual(other.handle({'op': 'get_fact', 'fact_id': fact})['status'], 'not_found')
        self.assertEqual(self.request('list_checkpoints', work_id=work, offset=0)['items'][0]['id'], ident)

    def test_no_overriding_scope_and_no_model_write_interface(self):
        self.assertEqual(self.request('list_facts', offset=0, project_id=self.p2)['status'], 'invalid_request')
        self.assertEqual(self.request('read_selected_checkpoint')['status'], 'invalid_request')
        self.assertEqual(self.request('unknown', text='private')['status'], 'invalid_request')
        self.assertEqual(self.request('create_fact', operation_id=uid(), text='x', source=SOURCE | {'status': 'verified'})['status'], 'invalid_request')

    def test_page_size_bound_and_no_fact_text_in_list(self):
        class ManyFacts:
            def list_facts(self, project, limit, offset):
                return [{'id': str(i), 'revision': 1, 'status': 'asserted', 'source': {'reference': 'é' * 512},
                         'created_at': 0, 'updated_at': 0, 'text': 'secret'} for i in range(offset, min(offset + limit, 40))]
        service = MemoryService(self.state, self.p1, memory_factory=lambda path: ManyFacts())
        page = service.handle({'op': 'list_facts', 'offset': 0})
        self.assertLessEqual(_size(page), LIST_BUDGET_BYTES)
        self.assertNotIn('secret', json.dumps(page))
        self.assertGreater(page['next_offset'], 0)
        next_page = service.handle({'op': 'list_facts', 'offset': page['next_offset']})
        self.assertNotEqual(page['items'][0]['id'], next_page['items'][0]['id'])

    def test_oversized_checkpoint_never_truncated(self):
        class BigCheckpoint:
            def get_checkpoint(self, project, checkpoint):
                return {'id': checkpoint, 'work_id': work, 'payload': {'constraints': ['é' * MAX_OUTPUT_BYTES]}}
        work = uid()
        service = MemoryService(self.state, self.p1, memory_factory=lambda path: BigCheckpoint())
        self.assertEqual(service.handle({'op': 'get_checkpoint', 'work_id': work, 'checkpoint_id': uid()}), {'status': 'item_too_large'})

    def test_error_statuses_are_generic_and_do_not_echo_sensitive_errors(self):
        class Broken:
            def list_facts(self, *args, **kwargs):
                raise ValueError('Coffre Reprise indisponible (SECRET)')
        service = MemoryService(self.state, self.p1, memory_factory=lambda path: Broken())
        result = service.handle({'op': 'list_facts', 'offset': 0})
        self.assertEqual(result, {'status': 'vault_unavailable'})
        self.assertNotIn('SECRET', json.dumps(result))
        class Slow:
            def list_facts(self, *args, **kwargs): raise TimeoutError('private')
        service = MemoryService(self.state, self.p1, memory_factory=lambda path: Slow())
        self.assertEqual(service.handle({'op': 'list_facts', 'offset': 0}), {'status': 'memory_busy'})

    def test_direct_existing_operation_never_misattributed(self):
        operation = uid()
        old = self.store.create_fact(self.p1, operation, 'ancien', SOURCE)
        status = self.request('create_fact', operation_id=operation, text='nouveau', source=SOURCE)
        self.assertEqual(status['status'], 'operation_conflict')
        receipt = self.request('operation_status', operation_id=operation)
        self.assertEqual(receipt['state'], 'recorded_unattributed')
        self.assertEqual(receipt['intent'], 'absent')
        self.assertNotIn('result', receipt)
        self.assertEqual(self.store.get_fact(self.p1, old['id'])['text'], 'ancien')

    def test_race_intent_and_direct_receipt_mismatch_suppresses_result(self):
        operation = uid()
        intended = {'op':'create_fact', 'operation_id':operation, 'text':'nouveau', 'source':SOURCE}
        self.service._reserve_intent(self.store, intended)
        self.store.create_fact(self.p1, operation, 'ancien', SOURCE)
        receipt = self.request('operation_status', operation_id=operation)
        self.assertEqual(receipt['state'], 'identity_mismatch')
        self.assertEqual(receipt['intent'], 'present')
        self.assertNotIn('result', receipt)
        self.assertEqual(self.service.handle(intended)['status'], 'operation_conflict')

    def test_vault_unavailable_prevents_intent_and_mutation(self):
        def locked(data, purpose, operation):
            raise ValueError('Coffre Reprise indisponible (keychain_unavailable)')
        service = MemoryService(self.state, self.p1, memory_factory=lambda path: Memory(path, crypt=locked))
        result = service.handle({'op':'create_fact', 'operation_id':uid(), 'text':'private', 'source':SOURCE})
        self.assertEqual(result, {'status':'vault_unavailable', 'effect':'not_admitted'})
        folder = self.state / 'orchestrator-memory.noindex'
        self.assertFalse((folder / 'memory.enc').exists())
        self.assertEqual(list(folder.rglob('*.enc')), [])
        for item in folder.rglob('*'):
            if item.is_file(): self.assertNotIn(b'private', item.read_bytes())

    def test_parent_sync_failure_refuses_admission_and_retry_syncs_again(self):
        original = os.fsync
        for label in ('state', 'memory'):
            with self.subTest(parent=label):
                operation = uid()
                fields = {'operation_id': operation, 'text': label, 'source': SOURCE}
                path = self.state if label == 'state' else self.state / 'orchestrator-memory.noindex'
                expected = path.stat()
                sync_calls = []
                def fail_parent(fd):
                    info = os.fstat(fd)
                    if (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino):
                        sync_calls.append(True)
                        raise OSError('parent directory not durable')
                    return original(fd)
                for _ in range(2):
                    with patch.object(memory.os, 'fsync', side_effect=fail_parent):
                        self.assertEqual(self.request('create_fact', **fields),
                                         {'status': 'memory_unavailable', 'effect': 'not_admitted'})
                self.assertEqual(len(sync_calls), 2)
                self.assertEqual(self.request('operation_status', operation_id=operation)['state'], 'not_recorded')
                self.assertEqual(self.request('create_fact', **fields)['status'], 'finished')

    def test_intent_fsync_failure_does_not_start_mutation(self):
        operation = uid()
        fields = {'operation_id': operation, 'text': 'must remain absent', 'source': SOURCE}
        original = os.fsync
        def fail_intent_directory(fd):
            info = os.fstat(fd)
            path = self.state / 'orchestrator-memory.noindex' / 'intents'
            if path.exists() and (info.st_dev, info.st_ino) == (path.stat().st_dev, path.stat().st_ino):
                raise OSError('intent persistence failed')
            return original(fd)
        with patch.object(memory.os, 'fsync', side_effect=fail_intent_directory):
            self.assertEqual(self.request('create_fact', **fields), {'status': 'memory_unavailable', 'effect': 'not_admitted'})
        status = self.request('operation_status', operation_id=operation)
        self.assertEqual(status['state'], 'not_recorded')
        self.assertEqual(status['intent'], 'present')
        self.assertEqual(self.request('list_facts', offset=0)['items'], [])
        # The intention exists but its directory sync has failed. A replay must
        # sync it again and must not mutate if that second sync also fails.
        with patch.object(memory.os, 'fsync', side_effect=fail_intent_directory):
            self.assertEqual(self.request('create_fact', **fields),
                             {'status': 'memory_unavailable', 'effect': 'not_admitted'})
            self.assertEqual(self.request('operation_status', operation_id=operation),
                             {'status': 'memory_unavailable'})
        self.assertEqual(self.request('list_facts', offset=0)['items'], [])
        self.assertEqual(self.request('create_fact', **fields)['status'], 'finished')

    def test_uncertain_write_reconciles_with_same_operation_id(self):
        operation = uid()
        fields = {'operation_id': operation, 'text': 'uncertain private content', 'source': SOURCE}
        original = os.fsync
        directory_syncs = 0
        def fail_after_replace(fd):
            nonlocal directory_syncs
            info = os.fstat(fd)
            path = self.state / 'orchestrator-memory.noindex'
            if (info.st_dev, info.st_ino) == (path.stat().st_dev, path.stat().st_ino):
                directory_syncs += 1
                if directory_syncs == 2: raise OSError('private filesystem error')
            return original(fd)
        with patch.object(memory.os, 'fsync', side_effect=fail_after_replace):
            response = self.request('create_fact', **fields)
        self.assertEqual(response, {'status': 'memory_unavailable', 'effect': 'indeterminate'})
        self.assertNotIn('private', json.dumps(response))
        def fail_memory_dir(fd):
            info = os.fstat(fd)
            directory = (self.state / 'orchestrator-memory.noindex').stat()
            if (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino):
                raise OSError('directory sync still failing')
            return original(fd)
        with patch.object(memory.os, 'fsync', side_effect=fail_memory_dir):
            self.assertEqual(self.request('create_fact', **fields),
                             {'status': 'memory_unavailable', 'effect': 'not_admitted'})
            self.assertEqual(self.request('operation_status', operation_id=operation),
                             {'status': 'memory_unavailable'})
        result = self.request('create_fact', **fields)
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result, self.request('create_fact', **fields))
        self.assertEqual(len(self.request('list_facts', offset=0)['items']), 1)

    def test_pagination_limit_is_explicit(self):
        class ManyFacts:
            def list_facts(self, project, limit, offset):
                return [{'id': str(i), 'revision': 1, 'status': 'asserted', 'source': {},
                         'created_at': 0, 'updated_at': 0, 'text': 'x'} for i in range(100)]
        service = MemoryService(self.state, self.p1, memory_factory=lambda path: ManyFacts())
        page = service.handle({'op': 'list_facts', 'offset': 10000})
        self.assertTrue(page['pagination_limit_reached'])
        self.assertIsNone(page['next_offset'])

    def test_intent_durable_private_and_status_distinguishes_absence(self):
        operation = uid()
        request = {'operation_id': operation, 'text': 'TOP SECRET FACT', 'source': SOURCE}
        status = self.request('operation_status', operation_id=operation)
        self.assertEqual(status, {'status': 'finished', 'state': 'not_recorded', 'intent': 'absent'})
        created = self.request('create_fact', **request)
        status = self.request('operation_status', operation_id=operation)
        self.assertEqual(status['state'], 'recorded')
        self.assertEqual(status['intent'], 'present')
        self.assertEqual(status['result'], created['result'])
        intent_files = list((self.state / 'orchestrator-memory.noindex' / 'intents').glob('*.enc'))
        self.assertEqual(len(intent_files), 1)
        raw = intent_files[0].read_bytes()
        self.assertNotIn(b'TOP SECRET FACT', raw)
        self.assertNotIn(b'note-1', raw)
        listing = self.request('list_intents', offset=0)
        self.assertEqual(listing['items'][0]['operation_id'], operation)
        self.assertNotIn('TOP SECRET FACT', json.dumps(listing))
        self.assertEqual(intent_files[0].stat().st_mode & 0o777, 0o600)
        other = MemoryService(self.state, self.p2, memory_factory=lambda path: Memory(path, crypt=fake_crypt))
        self.assertEqual(other.handle({'op':'operation_status', 'operation_id':operation}),
                         {'status':'finished', 'state':'not_recorded', 'intent':'absent'})

    def test_reserved_intent_without_result_remains_not_recorded(self):
        operation = uid()
        request = {'op':'create_fact', 'operation_id':operation, 'text':'x', 'source':SOURCE}
        mem = Memory(self.state, crypt=fake_crypt)
        self.service._reserve_intent(mem, request)
        status = self.request('operation_status', operation_id=operation)
        self.assertEqual(status['state'], 'not_recorded')
        self.assertEqual(status['intent'], 'present')
        self.assertEqual(status['operation'], 'create_fact')
        self.assertEqual(self.request('create_fact', operation_id=operation, text='other', source=SOURCE)['status'], 'operation_conflict')
        self.assertEqual(self.request('create_fact', operation_id=operation, text='x', source=SOURCE)['status'], 'finished')

    def test_input_size_is_utf8_bounded(self):
        self.assertEqual(self.request('create_fact', operation_id=uid(), text='é' * 40000, source=SOURCE)['status'], 'invalid_request')
        self.assertEqual(self.request('list_facts', offset=-1)['status'], 'invalid_request')


if __name__ == '__main__': unittest.main()
