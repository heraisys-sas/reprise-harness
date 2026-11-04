import json
import sqlite3
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_orchestrator_memory as fixtures
from memory_integration import selected, validate_binding, entry
from project_registry import ProjectRegistry


class SelectionTests(unittest.TestCase):
    def test_default_preserve_disable_and_new_scope_write(self):
        ident = fixtures.uid()
        self.assertEqual(selected(None), {'access': 'off', 'project_id': None})
        old = {'status': 'installed', 'memory': {'access': 'write', 'project_id': ident}}
        self.assertEqual(selected(old), old['memory'])
        self.assertEqual(selected(old, 'read')['access'], 'read')
        self.assertEqual(selected(old, action='remove')['access'], 'off')
        self.assertEqual(selected(dict(old, status='removed'))['access'], 'off')
        with self.assertRaises(ValueError): selected(old, project_id=fixtures.uid())
        with self.assertRaises(ValueError): selected(None, 'write')
        self.assertNotIn('--allow-write', entry('/release', '/state', selected(old, 'read'), 'reader')['args'])


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
