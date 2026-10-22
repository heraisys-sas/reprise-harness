"""Temporary state and simulated launchctl only; never loads a real service."""
from contextlib import closing
import json
import fcntl
import os
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import scheduler_service as service
import harness_integration as integration
import package_helpers
import state_install


class SchedulerServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / 'home'
        self.root = package_helpers.package(SOURCE, self.base / 'Reprise.app')
        runtime = self.root.parent / 'runtime'
        (runtime / 'bin').mkdir(parents=True)
        (runtime / 'bin/python3').write_text('#!/bin/sh\nexit 99\n')
        (runtime / 'bin/python3').chmod(0o700)
        (runtime / 'runtime-manifest.json').write_text(json.dumps({'files': integration.tree_files(runtime)}))
        payload = json.loads((self.root / 'payload-manifest.json').read_text())
        payload['runtime'] = {'fixture': True}
        (self.root / 'payload-manifest.json').write_text(json.dumps(payload))
        self.state = self.base / 'state'
        state_install.initialize(self.state)
        with closing(sqlite3.connect(self.state / 'tasks.sqlite')) as db:
            db.execute('CREATE TABLE scheduler_meta(key TEXT PRIMARY KEY, value TEXT)')
            db.executemany('INSERT INTO scheduler_meta VALUES (?,?)', [('auto_enabled','false'), ('last_tick','1234')])
            db.execute('CREATE TABLE tasks(id TEXT,status TEXT)')
            db.execute("INSERT INTO tasks VALUES ('preserved','waiting_quota')")
            db.commit()
        env = patch.dict(os.environ, {'AGENT_RESUME_STATE': str(self.state)})
        env.start(); self.addCleanup(env.stop)
        self.loaded = False
        self.calls = []
        self.fail_bootstrap = False
        self.support, self.plist, self.managed = service.paths(self.home)

    def runner(self, args):
        self.calls.append(args)
        if args[0] == 'print':
            output = ''
            if self.loaded and self.plist.exists():
                output = '\n'.join(plistlib.loads(self.plist.read_bytes())['ProgramArguments'])
            return subprocess.CompletedProcess(args, 0 if self.loaded else 113, output, '')
        if args[0] == 'bootstrap':
            if self.fail_bootstrap:
                return subprocess.CompletedProcess(args, 1, '', 'fixture failure')
            self.loaded = True
        elif args[0] == 'bootout':
            self.loaded = False
        else:
            self.fail('Unexpected launchctl command')
        return subprocess.CompletedProcess(args, 0, '', '')

    def plan(self, **kwargs):
        return service.plan(self.root, self.home, **kwargs)

    def apply(self, value):
        return service.apply(self.root, value, self.runner)

    def inspect(self):
        return service.inspect(self.root, self.home, self.runner)

    def test_read_only_and_prepared_pause_tick(self):
        before = integration.tree_files(self.base)
        observed = self.inspect()
        self.assertEqual(before, integration.tree_files(self.base))
        self.assertFalse(observed['file_present'])
        self.assertFalse(observed['loaded'])
        self.assertFalse(observed['auto_enabled'])
        self.assertEqual(observed['last_tick'], 1234)
        self.assertEqual(observed['pending'], 1)
        self.assertFalse(self.home.exists())

    def test_read_only_inspect_includes_wal_without_original_side_effects(self):
        with closing(sqlite3.connect(self.state / 'tasks.sqlite')) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("UPDATE scheduler_meta SET value='5678' WHERE key='last_tick'")
            db.commit()
            before = integration.tree_files(self.base)
            self.assertEqual(self.inspect()['last_tick'], 5678)
            self.assertEqual(before, integration.tree_files(self.base))

    def test_install_no_activation_then_activate_and_idempotence(self):
        original = (self.state / 'tasks.sqlite').read_bytes()
        self.apply(self.plan())
        observed = self.inspect()
        self.assertTrue(observed['file_present'])
        self.assertFalse(observed['loaded'])
        self.assertTrue(observed['runtime_integrity'])
        self.assertIsNone(observed['effective_release'])
        self.apply(self.plan(activate=True))
        self.assertTrue(self.inspect()['loaded'])
        self.assertIsNotNone(self.inspect()['effective_release'])
        self.assertFalse(self.inspect()['auto_enabled'])
        self.calls.clear()
        files_before = integration.tree_files(self.base)
        self.apply(self.plan(activate=True))
        self.assertEqual(files_before, integration.tree_files(self.base))
        self.assertFalse(any(c[0] in ('bootstrap','bootout') for c in self.calls))
        self.assertEqual(original, (self.state / 'tasks.sqlite').read_bytes())

    def test_conflict_does_not_overwrite_personal_plist(self):
        self.plist.parent.mkdir(parents=True)
        self.plist.write_bytes(plistlib.dumps({'Label': service.LABEL, 'ProgramArguments': ['/bin/false']}))
        original = self.plist.read_bytes()
        with self.assertRaisesRegex(ValueError, 'non géré'):
            self.plan()
        self.assertEqual(original, self.plist.read_bytes())

    def test_stale_plan_and_modified_managed_file_refused(self):
        value = self.plan()
        self.plist.parent.mkdir(parents=True)
        self.plist.write_text('personal')
        with self.assertRaises(Exception):
            self.apply(value)
        self.assertEqual(self.plist.read_text(), 'personal')
        self.plist.unlink()
        self.apply(self.plan())
        self.plist.write_text('personal')
        with self.assertRaisesRegex(ValueError, 'personnalisé'):
            self.plan()

    def test_partial_failure_resume_then_selective_removal(self):
        self.fail_bootstrap = True
        value = self.plan(activate=True)
        with self.assertRaisesRegex(ValueError, 'Activation'):
            self.apply(value)
        self.assertEqual(self.inspect()['pending_plan'], value)
        self.fail_bootstrap = False
        self.apply(value)
        self.assertIsNone(self.inspect()['pending_plan'])
        original = (self.state / 'tasks.sqlite').read_bytes()
        release = Path(value['release'])
        self.apply(self.plan(action='remove'))
        self.assertFalse(self.plist.exists())
        self.assertFalse(self.loaded)
        self.assertTrue(release.exists())
        self.assertEqual(original, (self.state / 'tasks.sqlite').read_bytes())
        self.assertTrue(list(self.managed.glob('backup-*.plist')))

    def test_update_uses_new_release_and_preserves_pause(self):
        first = self.plan(activate=True)
        self.apply(first)
        (self.root / 'VERSION').write_text('fixture-next')
        payload = json.loads((self.root / 'payload-manifest.json').read_text())
        payload['files']['VERSION'] = integration.file_hash(self.root / 'VERSION')
        (self.root / 'payload-manifest.json').write_text(json.dumps(payload))
        update = self.plan(activate=True)
        self.assertNotEqual(first['release'], update['release'])
        self.apply(update)
        observed = self.inspect()
        self.assertEqual(observed['effective_release'], update['release'])
        self.assertFalse(observed['auto_enabled'])
        self.assertTrue(Path(first['release']).exists())

    def test_missing_runtime_is_distinct_and_repair_does_not_run_it(self):
        value = self.plan()
        self.apply(value)
        Path(plistlib.loads(self.plist.read_bytes())['ProgramArguments'][0]).unlink()
        observed = self.inspect()
        self.assertTrue(observed['file_present'])
        self.assertFalse(observed['runtime_integrity'])
        repair = self.plan()
        self.assertIsNotNone(repair['release_backup'])
        self.apply(repair)
        self.assertTrue(self.inspect()['runtime_integrity'])
        self.assertTrue(Path(repair['release_backup']).is_dir())

    def test_legacy_release_adoption_is_explicit(self):
        value = self.plan()
        self.apply(value)
        (self.managed / 'managed.json').unlink()
        adopted = self.plan()
        self.assertTrue(adopted['adopt_legacy'])
        with self.assertRaises(ValueError):
            self.plan(action='remove')
        self.apply(adopted)
        self.assertFalse(self.plan()['adopt_legacy'])

    def test_official_paths_flags_and_busy_worker(self):
        value = self.plan(activate=True)
        settings = plistlib.loads(value['content'].encode())
        self.assertEqual(settings['ProgramArguments'][1:4], ['-E', '-s', '-B'])
        self.assertEqual(Path(value['release']).parent, self.state.parent / 'integration-releases')
        with (self.state / 'worker.lock').open('a') as worker:
            fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'worker est actif'):
                self.apply(value)
        self.assertEqual(self.calls, [])
        self.assertFalse(self.plist.exists())
        self.assertFalse(self.managed.exists())
        self.apply(value)
        self.assertTrue(self.inspect()['runtime_integrity'])

    def test_unprepared_state_refused_without_creating_register(self):
        with patch.dict(os.environ, {'AGENT_RESUME_STATE': str(self.base / 'absent')}):
            with self.assertRaisesRegex(ValueError, 'Préparer'):
                self.plan()
        self.assertFalse((self.base / 'absent').exists())


if __name__ == '__main__':
    unittest.main()
