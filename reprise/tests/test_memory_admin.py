"""Administrative plans use temporary state and protocol-only test crypto."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_orchestrator_memory as fixtures
import memory_admin as admin


def crypt(data, purpose, operation):
    if operation == 'check':
        return None
    return fixtures.fake_crypt(data, purpose, operation)


class AdminTests(unittest.TestCase):
    def setUp(self):
        fixtures.MemoryTests.setUp(self)
        self.state = self.state.resolve()
    tearDown = fixtures.MemoryTests.tearDown

    def tree(self):
        return {str(p.relative_to(self.state)): (p.stat().st_ino, p.stat().st_mode,
                p.read_bytes() if p.is_file() else None) for p in self.state.rglob('*')}

    def test_fresh_inspect_and_plans_create_nothing(self):
        # The fixture's Memory constructor created a directory: remove it to
        # cover an installation that has never opened memory.
        self.store.directory.rmdir()
        before = self.tree()
        result = admin.inspect(self.state, crypt=crypt)
        self.assertEqual(result['schema'], 1)
        self.assertFalse(result['store_present'])
        plan = admin.plan(self.state, 'migration', crypt=crypt)
        self.assertEqual(self.tree(), before)
        applied = admin.apply(plan, crypt=crypt)
        self.assertEqual(applied['result']['version'], 2)
        self.assertIsNone(applied['result']['backup'])

    def test_migration_backup_and_scope_plan_are_explicit(self):
        fact = self.store.create_fact(self.p1, fixtures.uid(), 'legacy', fixtures.SOURCE)
        before = self.tree()
        planned = admin.plan(self.state, 'migration', crypt=crypt)
        self.assertEqual(self.tree(), before)
        admin.apply(planned, crypt=crypt)
        scopeplan = admin.plan(self.state, 'scope', request={'kind':'project', 'project_id':self.p2}, crypt=crypt)
        before = self.tree()
        self.assertEqual(admin.list_scopes(self.state, crypt=crypt)['items'][0]['id'], self.p1)
        self.assertEqual(self.tree(), before)
        result = admin.apply(scopeplan, crypt=crypt)
        self.assertEqual(result['result']['scope_id'], self.p2)
        self.assertEqual(self.store.get_fact(self.p1, fact['id'])['text'], 'legacy')

    def test_changed_store_or_registry_rejects_plan(self):
        self.store.create_fact(self.p1, fixtures.uid(), 'legacy', fixtures.SOURCE)
        planned = admin.plan(self.state, 'migration', crypt=crypt)
        self.store.create_fact(self.p1, fixtures.uid(), 'concurrent', fixtures.SOURCE)
        with self.assertRaises(ValueError):admin.apply(planned, crypt=crypt)
        self.assertFalse((self.store.directory/'memory.v1-backup.enc').exists())
        planned = admin.plan(self.state, 'migration', crypt=crypt)
        registry = self.state/'projects.sqlite'
        original = registry.read_bytes()
        registry.write_bytes(original+b'x')
        with self.assertRaises(ValueError):admin.apply(planned, crypt=crypt)

    def test_revalidates_after_native_lock_before_mutation(self):
        self.store.create_fact(self.p1, fixtures.uid(), 'legacy', fixtures.SOURCE)
        planned = admin.plan(self.state, 'migration', crypt=crypt)
        original = admin.Memory._store
        import contextlib
        @contextlib.contextmanager
        def concurrent_change(store, **kw):
            with original(store, **kw) as db:
                # Change identity while the apply's native lock is held.
                registry = self.state/'projects.sqlite'
                registry.write_bytes(registry.read_bytes()+b'x')
                yield db
        with patch.object(admin.Memory, '_store', concurrent_change):
            with self.assertRaises(ValueError):admin.apply(planned, crypt=crypt)
        self.assertFalse((self.store.directory/'memory.v1-backup.enc').exists())

    def test_refuses_unknown_scope_inputs_and_v1_scope_registration(self):
        for request in ({'kind':'user','subject_id':fixtures.uid(),'project_id':self.p1},
                        {'kind':'work','project_id':fixtures.uid(),'work_id':fixtures.uid()},
                        {'kind':'project','project_id':self.p1}):
            with self.assertRaises(ValueError):admin.plan(self.state,'scope',request=request,crypt=crypt)

    def test_plan_rejects_insecure_or_symbolic_state(self):
        self.state.chmod(0o755)
        with self.assertRaises((ValueError,OSError)):admin.inspect(self.state,crypt=crypt)
        self.state.chmod(0o700)
        alias=self.base/'alias';alias.symlink_to(self.state)
        with self.assertRaises((ValueError,OSError)):admin.inspect(alias,crypt=crypt)

    def test_no_key_creation_when_check_unavailable(self):
        planned = admin.plan(self.state,'migration',crypt=crypt)
        calls=[]
        def unavailable(data,purpose,operation):
            calls.append(operation)
            raise ValueError('key unavailable')
        with self.assertRaises(ValueError):admin.apply(planned,crypt=unavailable)
        self.assertEqual(calls,['check'])
        self.assertFalse((self.store.directory/'memory.enc').exists())

    def test_cli_roundtrip_on_fresh_state_without_vault_access(self):
        import subprocess
        executable = Path(admin.__file__).with_name('reprise')
        before = self.tree()
        for command in ('memory-inspect', 'memory-migration-plan', 'memory-scope-list'):
            result = subprocess.run([sys.executable, '-B', str(executable), command,
                '--state', str(self.state)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsInstance(json.loads(result.stdout), dict)
        self.assertEqual(self.tree(), before)

    def test_rejects_changed_plan_action_or_payload(self):
        selected = admin.plan(self.state, 'migration', crypt=crypt)
        selected['request'] = {'unexpected': True}
        with self.assertRaises(ValueError):admin.apply(selected, crypt=crypt)
        selected = admin.plan(self.state, 'migration', crypt=crypt)
        selected['fingerprint'] = '0'*64
        with self.assertRaises(ValueError):admin.apply(selected, crypt=crypt)
        self.assertFalse((self.store.directory/'memory.enc').exists())

    def test_scope_user_work_roundtrip_and_idempotent_registration(self):
        admin.apply(admin.plan(self.state,'migration',crypt=crypt),crypt=crypt)
        requests=[{'kind':'user','subject_id':fixtures.uid()},
                  {'kind':'work','project_id':self.p1,'work_id':fixtures.uid()}]
        for request in requests:
            result=admin.apply(admin.plan(self.state,'scope',request=request,crypt=crypt),crypt=crypt)
            repeated=admin.apply(admin.plan(self.state,'scope',request=request,crypt=crypt),crypt=crypt)
            self.assertEqual(result['result'],repeated['result'])
        self.assertEqual(len(admin.list_scopes(self.state,crypt=crypt)['items']),2)
