"""Host-bound scopes and explicit migration; fake crypto, no live state."""
import json
import unittest
from unittest.mock import patch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_orchestrator_memory as fixtures
import orchestrator_memory as memory
from reprise_memory_adapter import Adapter


class ScopeTests(unittest.TestCase):
    setUp = fixtures.MemoryTests.setUp
    tearDown = fixtures.MemoryTests.tearDown

    def test_explicit_migration_preserves_project_receipts_and_backup(self):
        op = fixtures.uid()
        fact = self.store.create_fact(self.p1, op, 'legacy', fixtures.SOURCE)
        before = (self.store.directory / 'memory.enc').read_bytes()
        result = self.store.migrate_scopes_v2()
        self.assertEqual(result['version'], 2)
        self.assertEqual((self.store.directory / 'memory.v1-backup.enc').read_bytes(), before)
        self.assertEqual(self.store.get_fact(self.p1, fact['id'])['text'], 'legacy')
        self.assertEqual(self.store.create_fact(self.p1, op, 'legacy', fixtures.SOURCE), fact)
        self.assertEqual(self.store.migrate_scopes_v2()['status'], 'already_current')

    def test_scopes_isolate_user_project_and_two_works(self):
        self.store.create_fact(self.p1, fixtures.uid(), 'project', fixtures.SOURCE)
        self.store.migrate_scopes_v2()
        user = self.store.register_scope('user', subject_id=fixtures.uid())
        w1 = self.store.register_scope('work', project_id=self.p1, work_id=fixtures.uid())
        w2 = self.store.register_scope('work', project_id=self.p1, work_id=fixtures.uid())
        for scope in (user, w1, w2):
            bound = memory.ScopedMemory(self.state, scope, crypt=fixtures.fake_crypt)
            fact = bound.create_fact(scope, fixtures.uid(), scope, fixtures.SOURCE)
            self.assertEqual(bound.search_facts(scope, scope)[0]['id'], fact['id'])
            self.assertIsNone(self.store.get_fact(self.p1, fact['id']))
            with self.assertRaises(ValueError):bound.get_fact(self.p1, fact['id'])
        self.assertEqual(memory.ScopedMemory(self.state, w2, crypt=fixtures.fake_crypt).search_facts(w2, w1), [])

    def test_host_adapter_binds_scope_without_tool_supplied_authority(self):
        self.store.create_fact(self.p1, fixtures.uid(), 'project', fixtures.SOURCE)
        self.store.migrate_scopes_v2()
        scope = self.store.register_scope('work', project_id=self.p1, work_id=fixtures.uid())
        factory = lambda state: memory.Memory(state, crypt=fixtures.fake_crypt)
        adapter = Adapter(self.state, self.p1, 'a', allow_write=True, memory_factory=factory, scope_id=scope)
        fields = dict(operation_id=fixtures.uid(), text='selected', source=fixtures.SOURCE)
        capture = adapter.call('reprise_memory_capture', fields)
        self.assertEqual(capture['effect'], 'recorded')
        read = adapter.call('reprise_memory_get', {'fact_id':capture['result']['id']})
        self.assertEqual(read['item']['scope']['kind'], 'work')
        self.assertEqual(read['item']['recorded_by']['harness_id'], 'a')
        self.assertEqual(adapter.call('reprise_memory_get', {'fact_id':capture['result']['id'], 'scope_id':self.p1})['status'], 'invalid_request')
        other = Adapter(self.state, self.p2, 'b', memory_factory=factory, scope_id=scope)
        self.assertEqual(other.call('reprise_memory_get', {'fact_id':capture['result']['id']})['status'], 'invalid_request')

    def test_registration_requires_explicit_migration_and_valid_shape(self):
        with self.assertRaises(ValueError):self.store.register_scope('user', subject_id=fixtures.uid())
        self.store.create_fact(self.p1, fixtures.uid(), 'existing', fixtures.SOURCE)
        self.store.migrate_scopes_v2()
        with self.assertRaises(ValueError):self.store.register_scope('work', project_id=fixtures.uid(), work_id=fixtures.uid())
        with self.assertRaises(ValueError):self.store.register_scope('user', subject_id=fixtures.uid(), project_id=self.p1)

    def test_failed_migration_retains_v1_ciphertext_and_retry(self):
        self.store.create_fact(self.p1, fixtures.uid(), 'legacy', fixtures.SOURCE)
        before = (self.store.directory / 'memory.enc').read_bytes()
        original = self.store.crypt
        def fail_seal(data, purpose, operation):
            if operation == 'seal':
                raise ValueError('test seal failure')
            return original(data, purpose, operation)
        with patch.object(self.store, 'crypt', fail_seal):
            with self.assertRaises(ValueError):self.store.migrate_scopes_v2()
        self.assertEqual((self.store.directory / 'memory.enc').read_bytes(), before)
        self.assertEqual(self.store.migrate_scopes_v2()['status'], 'migrated')

    def test_work_checkpoint_and_deletion_keep_historical_copy(self):
        self.store.create_fact(self.p1, fixtures.uid(), 'legacy', fixtures.SOURCE)
        self.store.migrate_scopes_v2()
        work = fixtures.uid()
        scope = self.store.register_scope('work', project_id=self.p1, work_id=work)
        self.assertEqual(scope, self.store.register_scope('work', project_id=self.p1, work_id=work))
        bound = memory.ScopedMemory(self.state, scope, crypt=fixtures.fake_crypt)
        op = fixtures.uid()
        fact = bound.create_fact(scope, op, 'selected', fixtures.SOURCE)
        args = dict(fact_ids=[fact['id']], decisions=[], constraints=[], next_action='review', uncertain_effects=[], source=fixtures.SOURCE)
        with self.assertRaises(ValueError):bound.create_checkpoint(scope, fixtures.uid(), fixtures.uid(), 0, **args)
        checkpoint = bound.create_checkpoint(scope, work, fixtures.uid(), 0, **args)
        bound.update_fact(scope, fact['id'], fixtures.uid(), 1, 'corrected', fixtures.SOURCE)
        bound.delete_fact(scope, fact['id'], fixtures.uid(), 2)
        self.assertIsNone(bound.get_fact(scope, fact['id']))
        self.assertEqual(bound.get_checkpoint(scope, checkpoint['id'])['payload']['facts'][0]['text'], 'selected')
        self.assertEqual(bound.get_operation(scope, op)['state'], 'recorded')

    def test_fresh_state_migration_persists_without_fake_backup(self):
        result = self.store.migrate_scopes_v2()
        self.assertIsNone(result['backup'])
        self.assertFalse((self.store.directory / 'memory.v1-backup.enc').exists())
        self.assertTrue((self.store.directory / 'memory.enc').exists())
        reopened = memory.Memory(self.state, crypt=fixtures.fake_crypt)
        scope = reopened.register_scope('project', project_id=self.p2)
        self.assertEqual(scope, self.p2)
        self.assertEqual(reopened.register_scope('project', project_id=self.p2), scope)
        bound = memory.ScopedMemory(self.state, scope, crypt=fixtures.fake_crypt)
        self.assertEqual(bound.list_facts(scope), [])

    def test_empty_v1_store_migration_preserves_real_backup(self):
        # Commit a v1 store, then remove all rows without changing its schema.
        fact = self.store.create_fact(self.p1, fixtures.uid(), 'temporary', fixtures.SOURCE)
        with self.store._store(write=True) as db:
            db.execute('DELETE FROM facts')
            db.execute('DELETE FROM operations')
        before = (self.store.directory / 'memory.enc').read_bytes()
        self.store.migrate_scopes_v2()
        self.assertEqual((self.store.directory / 'memory.v1-backup.enc').read_bytes(), before)
        reopened = memory.Memory(self.state, crypt=fixtures.fake_crypt)
        self.assertEqual(reopened.migrate_scopes_v2()['status'], 'already_current')
        self.assertEqual(reopened.register_scope('project', project_id=self.p1), self.p1)

    def test_checkpoint_scope_rejection_has_no_durable_intent(self):
        self.store.migrate_scopes_v2()
        scopes = [self.store.register_scope('user', subject_id=fixtures.uid()),
                  self.store.register_scope('work', project_id=self.p1, work_id=fixtures.uid())]
        factory = lambda state: memory.Memory(state, crypt=fixtures.fake_crypt)
        for scope in scopes:
            adapter = Adapter(self.state, self.p1, 'host', allow_write=True,
                              memory_factory=factory, scope_id=scope)
            operation = fixtures.uid()
            result = adapter.call('reprise_memory_checkpoint', {
                'operation_id': operation, 'work_id': fixtures.uid(), 'expected_version': 0,
                'fact_ids': [], 'decisions': [], 'constraints': [], 'next_action': 'none',
                'uncertain_effects': [], 'source': fixtures.SOURCE})
            self.assertEqual(result['status'], 'invalid_request')
            self.assertNotEqual(result.get('effect'), 'indeterminate')
            intents = self.store.directory / 'intents'
            self.assertFalse(intents.exists() and list(intents.glob('*.enc')))
            status = adapter.call('reprise_memory_operation_status', {'operation_id': operation})
            self.assertEqual(status['state'], 'not_recorded')
            self.assertEqual(status['intent'], 'absent')

    def test_existing_backup_must_sync_again_before_migration(self):
        import os
        import stat
        self.store.create_fact(self.p1, fixtures.uid(), 'legacy', fixtures.SOURCE)
        before = (self.store.directory / 'memory.enc').read_bytes()
        backup_path = self.store.directory / 'memory.v1-backup.enc'
        original = os.fsync
        def fail_backup_sync(fd):
            info = os.fstat(fd)
            if backup_path.exists() and stat.S_ISREG(info.st_mode) and info.st_ino == backup_path.stat().st_ino:
                raise OSError('injected backup fsync failure')
            return original(fd)
        with patch.object(memory.os, 'fsync', fail_backup_sync):
            with self.assertRaises(OSError):self.store.migrate_scopes_v2()
            self.assertEqual(backup_path.read_bytes(), before)
            # Existing visible bytes are not proof of durable backup storage.
            with self.assertRaises(OSError):self.store.migrate_scopes_v2()
        self.assertEqual((self.store.directory / 'memory.enc').read_bytes(), before)
        observed = []
        def record_sync(fd):
            if os.fstat(fd).st_ino == backup_path.stat().st_ino:
                observed.append('backup')
            return original(fd)
        with patch.object(memory.os, 'fsync', record_sync):
            self.assertEqual(self.store.migrate_scopes_v2()['status'], 'migrated')
        self.assertEqual(observed, ['backup'])
