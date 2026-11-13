import base64
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import orchestrator_memory as memory
from project_registry import ProjectRegistry


def uid(): return str(uuid.uuid4())


def fake_crypt(data, purpose, operation):
    if purpose not in (memory.PURPOSE, 'orchestrator-intent-v1'): raise ValueError('wrong purpose')
    if operation == 'seal':
        # Test double only, not cryptography; avoids plaintext in the test file.
        encoded = base64.b64encode(data)
        return b'TEST:' + hashlib.sha256(encoded).digest() + encoded
    if operation == 'open':
        if not data.startswith(b'TEST:') or hashlib.sha256(data[37:]).digest() != data[5:37]:
            raise ValueError('bad ciphertext')
        return base64.b64decode(data[37:], validate=True)
    raise ValueError('unexpected operation')


def create_in_process(state, project, operation, queue):
    try:
        result = memory.Memory(state, crypt=fake_crypt).create_fact(project, operation, 'concurrent', SOURCE)
        queue.put(('ok', result))
    except Exception as exc:
        import traceback
        queue.put(('error', traceback.format_exc()))


def crash_in_process(state, project, operation, when):
    store = memory.Memory(state, crypt=fake_crypt)
    original = memory.os.replace
    def crash(source, destination, **kwargs):
        if when == 'before': os._exit(70)
        original(source, destination, **kwargs)
        os._exit(71)
    memory.os.replace = crash
    store.create_fact(project, operation, 'recoverable', SOURCE)


SOURCE = {'kind': 'operator', 'reference': 'note-1', 'producer': 'user', 'observed_at': '2026-11-02'}


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.state = self.base / 'state'; self.state.mkdir(mode=0o700)
        self.repo1 = self.base / 'repo1'; self.repo1.mkdir()
        self.repo2 = self.base / 'repo2'; self.repo2.mkdir()
        for repo in (self.repo1, self.repo2):
            subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
        with ProjectRegistry(self.state) as registry:
            self.p1 = registry.create('Atlas', self.repo1, self.repo1)['id']
            self.p2 = registry.create('Boreal', self.repo2, self.repo2)['id']
        self.store = memory.Memory(self.state, crypt=fake_crypt)

    def tearDown(self): self.temp.cleanup()

    def test_recall_scope_resolution_preserves_legacy_and_does_not_create_scopes(self):
        fact = self.store.create_fact(self.p1, uid(), 'Historical project fact', SOURCE)
        historical = self.store.get_fact(self.p1, fact['id'])
        work = uid()
        path = self.store.directory / 'memory.enc'
        before = path.read_bytes()
        result = self.store.recall_scopes(self.p1, work)
        self.assertEqual(result['work_scope_status'], 'legacy_store')
        self.assertIsNone(result['work_scope'])
        self.assertEqual(path.read_bytes(), before)
        self.store.migrate_scopes_v2()
        before = path.read_bytes()
        result = self.store.recall_scopes(self.p1, work)
        self.assertEqual(result['work_scope_status'], 'not_registered')
        self.assertEqual(self.store.get_fact(self.p1, fact['id']), historical)
        self.assertEqual(path.read_bytes(), before)

    def test_recall_scope_resolution_only_returns_current_work(self):
        self.store.migrate_scopes_v2()
        work = uid()
        current = self.store.register_scope('work', project_id=self.p1, work_id=work)
        self.store.register_scope('work', project_id=self.p1, work_id=uid())
        self.store.register_scope('user', subject_id=uid())
        path = self.store.directory / 'memory.enc'
        before = path.read_bytes()
        result = self.store.recall_scopes(self.p1, work)
        self.assertEqual(result['work_scope_status'], 'available')
        self.assertEqual(result['work_scope']['id'], current)
        self.assertEqual(path.read_bytes(), before)

    def test_recall_scope_resolution_refuses_conflicting_associations(self):
        self.store.migrate_scopes_v2()
        work = uid()
        self.store.register_scope('work', project_id=self.p2, work_id=work)
        with self.assertRaisesRegex(ValueError, 'association conflict'):
            self.store.recall_scopes(self.p1, work)

    def test_recall_scope_resolution_refuses_project_namespace_collision(self):
        self.store.migrate_scopes_v2()
        work = uid()
        # p2 is registered but has no facts and therefore no migrated scope.
        with self.store._store(write=True) as db:
            db.execute('INSERT INTO scopes VALUES (?, ?, NULL, ?, ?)',
                       (self.p2, 'work', self.p1, work))
        with self.assertRaisesRegex(ValueError, 'namespace collision'):
            self.store.recall_scopes(self.p1, work)

    def test_recall_scope_resolution_refuses_duplicate_associations(self):
        self.store.migrate_scopes_v2()
        work = uid()
        self.store.register_scope('work', project_id=self.p1, work_id=work)
        # Synthetic corruption/duplicate: nullable UNIQUE columns do not
        # establish uniqueness of a host work association in SQLite.
        with self.store._store(write=True) as db:
            db.execute('INSERT INTO scopes VALUES (?, ?, NULL, ?, ?)',
                       (uid(), 'work', self.p1, work))
        with self.assertRaisesRegex(ValueError, 'association conflict'):
            self.store.recall_scopes(self.p1, work)

    def test_recall_scope_resolution_registry_failure_is_not_unknown_project(self):
        self.store.migrate_scopes_v2()
        work = uid()
        namespace = self.store.register_scope('work', project_id=self.p1, work_id=work)
        original = memory.Memory._project
        def fail_scope(store, project):
            if project == namespace:
                raise ValueError('Malformed project registry schema')
            return original(store, project)
        with patch.object(memory.Memory, '_project', fail_scope):
            with self.assertRaisesRegex(ValueError, 'Malformed project registry schema'):
                self.store.recall_scopes(self.p1, work)

    def checkpoint(self, fact_ids, *, project=None, work=None, operation=None, version=0):
        return self.store.create_checkpoint(project or self.p1, work or uid(), operation or uid(), version,
            fact_ids=fact_ids, decisions=['décision'], constraints=['contrainte'], next_action='vérifier',
            uncertain_effects=['envoi inconnu'], source=SOURCE)

    def test_project_lookup_is_read_only_and_does_not_touch_registry(self):
        path = self.state / 'projects.sqlite'
        before = path.read_bytes(); metadata = path.stat()
        self.assertEqual(self.store.list_facts(self.p1), [])
        after = path.stat()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((after.st_mode, after.st_uid, after.st_mtime_ns, after.st_ctime_ns),
                         (metadata.st_mode, metadata.st_uid, metadata.st_mtime_ns, metadata.st_ctime_ns))
        self.assertEqual([p.name for p in self.state.glob('projects.sqlite*')], ['projects.sqlite'])

    def test_project_lookup_does_not_release_existing_sqlite_writer_lock(self):
        registry = self.state / 'projects.sqlite'
        owner = sqlite3.connect(registry, timeout=.1)
        owner.execute('BEGIN IMMEDIATE')
        contender_script = """import sqlite3,sys
c=sqlite3.connect(sys.argv[1],timeout=.15)
for line in sys.stdin:
 try:
  c.execute('BEGIN IMMEDIATE'); c.rollback(); print('acquired',flush=True)
 except sqlite3.OperationalError:
  print('blocked',flush=True)
"""
        contender = subprocess.Popen([sys.executable, '-u', '-c', contender_script, str(registry)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            contender.stdin.write('first\n'); contender.stdin.flush()
            self.assertEqual(contender.stdout.readline().strip(), 'blocked')
            self.store._project(self.p1)
            contender.stdin.write('second\n'); contender.stdin.flush()
            self.assertEqual(contender.stdout.readline().strip(), 'blocked')
        finally:
            owner.rollback(); owner.close()
            contender.stdin.close()
            contender.wait(timeout=5)
            contender.stdout.close()

    def test_malformed_registry_is_rejected_without_schema_or_permission_changes(self):
        path = self.state / 'projects.sqlite'
        path.unlink()
        connection = sqlite3.connect(path)
        connection.execute('CREATE TABLE projects(id TEXT PRIMARY KEY)')
        connection.execute('INSERT INTO projects VALUES (?)', (self.p1,))
        connection.commit(); connection.close(); path.chmod(0o400)
        before = path.read_bytes(); metadata = path.stat()
        with self.assertRaisesRegex(ValueError, 'Malformed project registry schema'):
            self.store.list_facts(self.p1)
        after = path.stat()
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual((after.st_mode, after.st_uid, after.st_mtime_ns, after.st_ctime_ns),
                         (metadata.st_mode, metadata.st_uid, metadata.st_mtime_ns, metadata.st_ctime_ns))
        self.assertEqual([p.name for p in self.state.glob('projects.sqlite*')], ['projects.sqlite'])

    def test_reading_absent_memory_never_calls_vault_or_creates_ciphertext(self):
        calls = []
        def forbidden(*args):
            calls.append(args)
            raise AssertionError('vault must not be called')
        store = memory.Memory(self.state, crypt=forbidden)
        self.assertEqual(store.list_facts(self.p1), [])
        self.assertEqual(calls, [])
        self.assertFalse((store.directory / 'memory.enc').exists())

    def test_sqlite_temporary_storage_is_memory_for_registry_and_store(self):
        observed = []
        class RecordingConnection(sqlite3.Connection):
            def execute(self, sql, *args):
                result = super().execute(sql, *args)
                if sql == 'PRAGMA temp_store=MEMORY':
                    observed.append(super().execute('PRAGMA temp_store').fetchone()[0])
                return result
        original = sqlite3.connect
        def connect(*args, **kwargs):
            kwargs['factory'] = RecordingConnection
            return original(*args, **kwargs)
        with patch.object(memory.sqlite3, 'connect', side_effect=connect):
            self.assertEqual(self.store.list_facts(self.p1), [])
        self.assertEqual(observed, [2])
        probe = subprocess.run([sys.executable, '-I', '-c', memory._REGISTRY_PROBE],
                               input=json.dumps({'state': str(self.state), 'project_id': self.p1}),
                               text=True, capture_output=True, check=True, timeout=5)
        self.assertEqual(json.loads(probe.stdout), {'status': 'ok', 'temp_store': 2})

    def test_missing_ciphertext_with_marker_refuses_all_operations(self):
        fact = self.store.create_fact(self.p1, uid(), 'durable fact', SOURCE)
        folder = self.store.directory
        self.assertEqual((folder / 'initialized').stat().st_size, 0)
        (folder / 'memory.enc').unlink()
        with self.assertRaises(memory.registry_marker.RegistryMissing):
            self.store.get_fact(self.p1, fact['id'])
        with self.assertRaises(memory.registry_marker.RegistryMissing):
            self.store.create_fact(self.p1, uid(), 'not recreated', SOURCE)
        self.assertFalse((folder / 'memory.enc').exists())

    def test_legacy_memory_requires_explicit_adoption_before_mutation(self):
        fact = self.store.create_fact(self.p1, uid(), 'legacy fact', SOURCE)
        folder = self.store.directory
        before = (folder / 'memory.enc').read_bytes()
        (folder / 'initialized').unlink()  # Simulate an old, unmarked snapshot.
        self.assertEqual(self.store.get_fact(self.p1, fact['id'])['text'], 'legacy fact')
        with self.assertRaises(memory.registry_marker.LegacyRegistry):
            self.store.create_fact(self.p1, uid(), 'new fact', SOURCE)
        self.assertEqual((folder / 'memory.enc').read_bytes(), before)
        self.assertEqual(self.store.adopt_legacy_registry(), {'status': 'adopted'})
        self.assertEqual(self.store.adopt_legacy_registry(), {'status': 'adopted'})
        self.assertEqual((folder / 'memory.enc').read_bytes(), before)
        self.store.create_fact(self.p1, uid(), 'new fact', SOURCE)
        self.assertEqual(len(self.store.list_facts(self.p1)), 2)

    def test_first_write_marker_stranded_before_replace_requires_recovery(self):
        with patch.object(memory.os, 'replace', side_effect=OSError('synthetic replace failure')):
            with self.assertRaises(OSError):
                self.store.create_fact(self.p1, uid(), 'first', SOURCE)
        self.assertTrue((self.store.directory / 'initialized').exists())
        self.assertFalse((self.store.directory / 'memory.enc').exists())
        with self.assertRaises(memory.registry_marker.RegistryMissing):
            self.store.list_facts(self.p1)

    def test_crud_idempotence_conflict_and_reopen(self):
        operation = uid()
        created = self.store.create_fact(self.p1, operation, 'fait secret', SOURCE)
        self.assertEqual(created, self.store.create_fact(self.p1, operation, 'fait secret', SOURCE))
        with self.assertRaisesRegex(ValueError, 'different input'):
            self.store.create_fact(self.p1, operation, 'autre', SOURCE)
        result = self.store.get_fact(self.p1, created['id'])
        self.assertEqual(result['status'], 'asserted')
        self.assertEqual(result['source'], SOURCE)
        self.assertEqual(len(self.store.list_facts(self.p1)), 1)
        update_operation = uid()
        updated = self.store.update_fact(self.p1, created['id'], update_operation, 1, 'autre', SOURCE)
        self.assertEqual(updated['revision'], 2)
        self.assertEqual(updated, self.store.update_fact(self.p1, created['id'], update_operation, 1, 'autre', SOURCE))
        with self.assertRaisesRegex(ValueError, 'revision conflict'):
            self.store.delete_fact(self.p1, created['id'], uid(), 1)
        reopened = memory.Memory(self.state, crypt=fake_crypt)
        self.assertEqual(reopened.get_fact(self.p1, created['id'])['text'], 'autre')
        delete_operation = uid()
        deleted = reopened.delete_fact(self.p1, created['id'], delete_operation, 2)
        self.assertEqual(deleted, reopened.delete_fact(self.p1, created['id'], delete_operation, 2))
        self.assertIsNone(reopened.get_fact(self.p1, created['id']))

    def test_project_isolation_and_checkpoint_snapshots(self):
        fact = self.store.create_fact(self.p1, uid(), 'fait projet', SOURCE)
        work, op = uid(), uid()
        checkpoint = self.checkpoint([fact['id']], work=work, operation=op)
        self.assertEqual(checkpoint, self.checkpoint([fact['id']], work=work, operation=op))
        self.assertIsNone(self.store.get_fact(self.p2, fact['id']))
        self.assertIsNone(self.store.get_checkpoint(self.p2, checkpoint['id']))
        self.assertEqual(self.store.list_checkpoints(self.p2, work), [])
        with self.assertRaisesRegex(ValueError, 'Fact not found'):
            self.checkpoint([fact['id']], project=self.p2)
        with self.assertRaisesRegex(ValueError, 'version conflict'):
            self.checkpoint([fact['id']], work=work, version=0)
        self.store.delete_fact(self.p1, fact['id'], uid(), 1)
        saved = self.store.get_checkpoint(self.p1, checkpoint['id'])
        self.assertEqual(saved['payload']['facts'][0]['text'], 'fait projet')
        self.assertEqual(saved['payload']['uncertain_effects'], ['envoi inconnu'])
        self.assertEqual(self.checkpoint([], work=work, version=1)['version'], 2)

    def test_invalid_project_provenance_and_sizes_refused(self):
        for project in (uid(), 'bad'):
            with self.assertRaises(ValueError): self.store.create_fact(project, uid(), 'x', SOURCE)
        for source in ({}, dict(SOURCE, status='verified'), dict(SOURCE, sha256='not-a-hash')):
            with self.assertRaises(ValueError): self.store.create_fact(self.p1, uid(), 'x', source)
        with self.assertRaises(ValueError): self.store.create_fact(self.p1, uid(), 'x' * 20000, SOURCE)
        with self.assertRaises(ValueError): self.store.list_facts(self.p1, limit=101)
        with self.assertRaises(ValueError): self.checkpoint([{}])

    def test_no_plaintext_and_denied_vault_preserves_data(self):
        fact = self.store.create_fact(self.p1, uid(), 'secret-fact-123', SOURCE)
        path = self.store.directory / 'memory.enc'
        before = path.read_bytes()
        self.assertNotIn(b'secret-fact-123', before)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        def failing(data, purpose, operation):
            if operation == 'seal': raise ValueError('vault unavailable')
            return fake_crypt(data, purpose, operation)
        denied = memory.Memory(self.state, crypt=failing)
        with self.assertRaises(ValueError): denied.create_fact(self.p1, uid(), 'another-secret', SOURCE)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.store.get_fact(self.p1, fact['id'])['text'], 'secret-fact-123')
        self.assertFalse(any(p.name.startswith('.memory-') for p in self.store.directory.iterdir()))

    def test_direct_write_requires_state_directory_sync_even_on_retry(self):
        operation = uid()
        state = self.state.stat()
        original = os.fsync
        calls = []
        def fail_state(fd):
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) == (state.st_dev, state.st_ino):
                calls.append(True)
                raise OSError('state directory not durable')
            return original(fd)
        for _ in range(2):
            with patch.object(memory.os, 'fsync', side_effect=fail_state):
                with self.assertRaises(OSError):
                    self.store.create_fact(self.p1, operation, 'not admitted', SOURCE)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.store.list_facts(self.p1), [])
        self.assertEqual(self.store.get_operation(self.p1, operation)['state'], 'not_recorded')
        self.store.create_fact(self.p1, operation, 'not admitted', SOURCE)
        self.assertEqual(len(self.store.list_facts(self.p1)), 1)

    def test_replace_failure_preserves_and_post_replace_fsync_can_reconcile(self):
        fact = self.store.create_fact(self.p1, uid(), 'before', SOURCE)
        path = self.store.directory / 'memory.enc'; before = path.read_bytes()
        with patch.object(memory.os, 'replace', side_effect=OSError('simulated')):
            with self.assertRaises(OSError): self.store.create_fact(self.p1, uid(), 'not saved', SOURCE)
        self.assertEqual(before, path.read_bytes())
        op = uid(); original_fsync = os.fsync
        def fail_directory(fd):
            info = os.fstat(fd)
            folder = self.store.directory.stat()
            if (info.st_dev, info.st_ino) == (folder.st_dev, folder.st_ino):
                raise OSError('after replace')
            return original_fsync(fd)
        with patch.object(memory.os, 'fsync', side_effect=fail_directory):
            with self.assertRaises(OSError): self.store.create_fact(self.p1, op, 'after', SOURCE)
            # Both replay and receipt lookup must fail while directory sync fails.
            with self.assertRaises(OSError): self.store.create_fact(self.p1, op, 'after', SOURCE)
            with self.assertRaises(OSError): self.store.get_operation(self.p1, op)
        result = self.store.create_fact(self.p1, op, 'after', SOURCE)
        self.assertEqual(self.store.get_operation(self.p1, op)['state'], 'recorded')
        self.assertEqual(self.store.get_fact(self.p1, result['id'])['text'], 'after')
        self.assertEqual(len(self.store.list_facts(self.p1)), 2)
        self.assertEqual(self.store.get_fact(self.p1, fact['id'])['text'], 'before')

    def test_corruption_and_future_schema_do_not_overwrite(self):
        self.store.create_fact(self.p1, uid(), 'x', SOURCE)
        path = self.store.directory / 'memory.enc'
        path.write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.store.create_fact(self.p1, uid(), 'y', SOURCE)
        self.assertEqual(path.read_bytes(), b'corrupt')
        db = sqlite3.connect(':memory:'); db.execute('PRAGMA user_version=999')
        data = fake_crypt(db.serialize(), memory.PURPOSE, 'seal'); db.close(); path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'schema version'):
            self.store.create_fact(self.p1, uid(), 'z', SOURCE)
        self.assertEqual(path.read_bytes(), data)

    def test_directory_and_file_symlinks_refused(self):
        outside = self.base / 'outside'; outside.mkdir(mode=0o700)
        otherstate = self.base / 'otherstate'; otherstate.mkdir(mode=0o700)
        (otherstate / 'orchestrator-memory.noindex').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError): memory.Memory(otherstate, crypt=fake_crypt)
        (self.store.directory / 'memory.enc').symlink_to(outside / 'target')
        with self.assertRaises(OSError): self.store.list_facts(self.p1)
        self.assertFalse((outside / 'target').exists())

    def test_process_death_reconciles_around_replace(self):
        self.store.create_fact(self.p1, uid(), 'before', SOURCE)
        for when, exitcode in (('before', 70), ('after', 71)):
            operation = uid()
            process = multiprocessing.Process(target=crash_in_process, args=(self.state, self.p1, operation, when))
            process.start(); process.join(15)
            self.assertEqual(process.exitcode, exitcode)
            result = self.store.create_fact(self.p1, operation, 'recoverable', SOURCE)
            self.assertEqual(result, self.store.create_fact(self.p1, operation, 'recoverable', SOURCE))
        self.assertEqual(len(self.store.list_facts(self.p1)), 3)
        for path in self.store.directory.iterdir():
            if path.is_file(): self.assertNotIn(b'recoverable', path.read_bytes())

    def test_concurrent_duplicate_request_single_fact(self):
        operation = uid(); queue = multiprocessing.Queue()
        processes = [multiprocessing.Process(target=create_in_process, args=(self.state, self.p1, operation, queue)) for _ in range(2)]
        for process in processes: process.start()
        for process in processes:
            process.join(15); self.assertEqual(process.exitcode, 0)
        first, second = queue.get(timeout=2), queue.get(timeout=2)
        self.maxDiff = None
        self.assertEqual(first[0], 'ok', (first, second)); self.assertEqual(first, second)
        self.assertEqual(len(self.store.list_facts(self.p1)), 1)


if __name__ == '__main__': unittest.main()
