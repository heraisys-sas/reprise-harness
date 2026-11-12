import json
import sqlite3
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_orchestrator_memory as fixtures
from memory_integration import selected, validate_binding, entry
from project_registry import ProjectRegistry


class SelectionTests(unittest.TestCase):
    def test_default_preserve_disable_and_new_scope_write(self):
        ident = fixtures.uid()
        self.assertEqual(selected(None), {'access': 'off', 'project_id': None, 'scope_id': None})
        old = {'status': 'installed', 'memory': {'access': 'write', 'project_id': ident}}
        self.assertEqual(selected(old), dict(old['memory'], scope_id=None))
        self.assertEqual(selected(old, 'read')['access'], 'read')
        self.assertEqual(selected(old)['scope_id'], None)
        self.assertEqual(selected(old, action='remove')['access'], 'off')
        self.assertEqual(selected(dict(old, status='removed'))['access'], 'off')
        with self.assertRaises(ValueError): selected(old, project_id=fixtures.uid())
        with self.assertRaises(ValueError): selected(dict(old, memory={'access': 'write', 'project_id': ident, 'scope_id': None}), scope_id=fixtures.uid())
        with self.assertRaises(ValueError): selected(None, 'off', fixtures.uid(), fixtures.uid())
        with self.assertRaises(ValueError): selected(None, 'write')
        self.assertNotIn('--allow-write', entry('/release', '/state', selected(old, 'read'), 'reader')['args'])

    def test_scope_entry_and_write_reauthorization(self):
        project, scope = fixtures.uid(), fixtures.uid()
        old = {'status': 'installed', 'memory': {'access': 'write', 'project_id': project, 'scope_id': None}}
        with self.assertRaisesRegex(ValueError, 'autorisation.*write'):
            selected(old, scope_id=scope)
        new = selected(old, 'write', scope_id=scope)
        self.assertEqual(new['scope_id'], scope)
        args = entry('/release', '/state', new, 'reader')['args']
        self.assertEqual(args[args.index('--scope-id'):args.index('--scope-id') + 2], ['--scope-id', scope])
        self.assertIn('--allow-write', entry('/release', '/state', new, 'reader')['args'])


class BindingTests(unittest.TestCase):
    def setUp(self):
        fixtures.MemoryTests.setUp(self)
        with ProjectRegistry(self.state) as registry:
            registry.associate_workspace(self.p1, 'harness', self.repo1)
            registry.associate_workspace(self.p2, 'claude', self.repo2)

    def tearDown(self): fixtures.MemoryTests.tearDown(self)

    def test_readonly_registered_scope_and_unknown_rejected(self):
        path = self.state / 'projects.sqlite'
        before = (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
        validate_binding(self.state, {'access': 'read', 'project_id': self.p1}, self.repo1, 'harness')
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode), before)

        for project, workspace in ((self.p2, self.repo1), (fixtures.uid(), self.repo1), (self.p1, self.repo2)):
            with self.assertRaises(ValueError):
                validate_binding(self.state, {'access': 'write', 'project_id': project}, workspace, 'harness')
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode), before)

    def test_wal_header_refused_before_sqlite_open_without_sidecars(self):
        path = self.state / 'projects.sqlite'
        db = sqlite3.connect(path)
        db.execute('PRAGMA journal_mode=WAL')
        db.close()
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                  for p in self.state.iterdir() if p.is_file()}
        with self.assertRaises(ValueError):
            validate_binding(self.state, {'access': 'read', 'project_id': self.p1}, self.repo1, 'harness')
        after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                 for p in self.state.iterdir() if p.is_file()}
        self.assertEqual(after, before)

    def test_scope_validation_requires_existing_v2_and_project_binding(self):
        import memory_admin
        project_scope = {'id': self.p1, 'kind': 'project', 'subject_id': None, 'project_id': self.p1, 'work_id': None}
        user_id = fixtures.uid()
        user_scope = {'id': user_id, 'kind': 'user', 'subject_id': fixtures.uid(), 'project_id': None, 'work_id': None}
        work_id = fixtures.uid()
        work_scope = {'id': work_id, 'kind': 'work', 'subject_id': None, 'project_id': self.p1, 'work_id': fixtures.uid()}
        v2 = {'schema': 2, 'scopes': [project_scope, user_scope, work_scope]}
        with patch.object(memory_admin, '_observe', return_value={'schema': 1, 'scopes': []}):
            with self.assertRaises(ValueError): validate_binding(self.state, {'access': 'read', 'project_id': self.p1, 'scope_id': user_id}, self.repo1, 'harness')
        with patch.object(memory_admin, '_observe', return_value=v2):
            for scope in (self.p1, user_id, work_id):
                validate_binding(self.state, {'access': 'read', 'project_id': self.p1, 'scope_id': scope}, self.repo1, 'harness')
            with self.assertRaises(ValueError):
                validate_binding(self.state, {'access': 'read', 'project_id': self.p2, 'scope_id': work_id}, self.repo1, 'harness')
            with self.assertRaises(ValueError):
                validate_binding(self.state, {'access': 'read', 'project_id': self.p1, 'scope_id': fixtures.uid()}, self.repo1, 'harness')
