"""Onboarding previews and interrupted imports operate only on temporary states."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import state_setup as setup
from claude_desktop_quick import Endpoint


class StateSetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'legacy' / 'state'
        self.target = self.root / 'installed' / 'state'
        self.quick = self.target.parent / 'quick-state'

    def fixture(self, directory=None, name='tasks.sqlite', future=False):
        directory = directory or self.source
        directory.mkdir(parents=True)
        with closing(sqlite3.connect(directory / name)) as db:
            db.execute('CREATE TABLE results(id TEXT, response TEXT, claimed INTEGER)')
            db.execute("INSERT INTO results VALUES ('saved','unclaimed result',0)")
            db.execute('CREATE TABLE identities(salt BLOB)')
            db.execute('INSERT INTO identities VALUES (?)', (b'preserved-salt',))
            db.execute('CREATE TABLE attempts(id TEXT, status TEXT)')
            db.execute("INSERT INTO attempts VALUES ('uncertain','indeterminate')")
            if future:
                db.execute('PRAGMA user_version=9999')
            db.commit()

    def read(self, directory, table='results', name='tasks.sqlite'):
        with closing(sqlite3.connect(directory / name)) as db:
            return db.execute('SELECT * FROM ' + table).fetchall()

    def status(self):
        with patch.object(setup, 'state_location', side_effect=lambda root, name: self.target if name == 'state' else self.quick):
            return setup.status(self.root)

    def preview(self, operation='import'):
        return setup.plan(operation, self.target, self.quick, self.source if operation == 'import' else None)

    def test_status_and_initialize_preview_do_not_write(self):
        before = list(self.root.rglob('*'))
        self.assertEqual(self.status()['status'], 'needs_setup')
        self.preview('initialize')
        self.assertEqual(list(self.root.rglob('*')), before)

    def test_import_preview_does_not_write_sources_or_destinations(self):
        self.fixture()
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.preview()
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
        self.assertFalse(self.target.parent.exists())

    def test_new_state_requires_boolean_confirmation(self):
        plan = self.preview('initialize')
        for value in (False, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                setup.apply(plan, confirm_new=value)
        self.assertFalse(self.target.parent.exists())
        setup.apply(plan, confirm_new=True)
        self.assertEqual(self.status()['status'], 'ready')
        self.assertTrue(setup.install.initialized(self.quick))

    def test_import_requires_stopped_attestation(self):
        self.fixture()
        for value in (False, 1, 'true'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                setup.apply(self.preview(), source_stopped=value)
        self.assertFalse(self.target.parent.exists())

    def test_main_and_quick_results_identities_and_uncertainty_survive(self):
        self.fixture()
        quick_source = self.source.parent / 'quick-state'
        endpoint = Endpoint(quick_source)
        scope = endpoint.scope
        saved = endpoint.submit({'prompt': 'Retained local fixture', 'request_key': 'saved'})
        endpoint.requests.begin('uncertain', {'prompt': 'Never repeat this fixture'})
        uncertain = endpoint.requests.lookup({'request_key': 'uncertain'})
        endpoint.close()
        result = setup.apply(self.preview(), source_stopped=True)
        self.assertEqual(result['status'], 'completed')
        for table in ('results', 'identities', 'attempts'):
            self.assertEqual(self.read(self.target, table), self.read(self.source, table))
        imported = Endpoint(self.quick)
        self.addCleanup(imported.close)
        self.assertEqual(imported.scope, scope)
        self.assertEqual(imported.submit({'prompt': 'Retained local fixture', 'request_key': 'saved'}), saved)
        self.assertEqual(imported.requests.lookup({'request_key': 'uncertain'}), uncertain)
        self.assertEqual(uncertain['state'], 'indeterminate')
        self.assertEqual(len(result['backups']), 2)

    def test_import_includes_committed_wal_and_retains_source_files(self):
        self.fixture()
        connection = sqlite3.connect(self.source / 'tasks.sqlite')
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute("INSERT INTO results VALUES ('wal-only','committed WAL result',0)")
        connection.commit()
        self.assertTrue((self.source / 'tasks.sqlite-wal').exists())
        before = {p.name: p.read_bytes() for p in self.source.iterdir() if p.is_file()}
        result = setup.apply(self.preview(), source_stopped=True)
        expected = [('saved', 'unclaimed result', 0), ('wal-only', 'committed WAL result', 0)]
        self.assertEqual(self.read(self.target), expected)
        main_backup = next(Path(p) for p in result['backups'] if Path(p).name.startswith('state.backup-'))
        self.assertEqual(self.read(main_backup), expected)
        for name, content in before.items():
            self.assertEqual((self.source / name).read_bytes(), content)

    def test_partial_publication_stays_pending_and_resumes_without_replacing_main(self):
        self.fixture()
        plan = self.preview()
        with patch.object(setup.install, 'initialize', side_effect=OSError('Quick publication interrupted')):
            with self.assertRaises(OSError):
                setup.apply(plan, source_stopped=True)
        self.assertTrue(setup.install.initialized(self.target))
        self.assertEqual(self.status()['status'], 'pending')
        sentinel = self.target / 'after-publication'
        sentinel.write_text('keep this later data')
        result = setup.apply(self.status()['pending_plan'], source_stopped=True)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(sentinel.read_text(), 'keep this later data')
        self.assertEqual(self.status()['status'], 'ready')

    def test_completed_publications_resume_after_final_journal_failure_without_sources(self):
        self.fixture()
        plan = self.preview()
        original = setup._journal_write
        def fail_completed(path, value):
            if value['status'] == 'completed':
                raise OSError('Final journal interrupted')
            return original(path, value)
        with patch.object(setup, '_journal_write', side_effect=fail_completed):
            with self.assertRaises(OSError):
                setup.apply(plan, source_stopped=True)
        self.assertEqual(self.status()['status'], 'pending')
        shutil.rmtree(self.source)
        with patch.object(setup.install, 'import_state', side_effect=AssertionError('Must not import again')):
            setup.apply(plan, source_stopped=True)
        self.assertEqual(self.status()['status'], 'ready')

    def test_source_change_since_preview_is_refused_before_copy(self):
        self.fixture()
        plan = self.preview()
        (self.source / 'new-result.json').write_text('new information')
        with self.assertRaises(ValueError):
            setup.apply(plan, source_stopped=True)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.quick.exists())

    def test_new_quick_companion_since_preview_is_refused(self):
        self.fixture()
        plan = self.preview()
        self.fixture(self.source.parent / 'quick-state', 'quick.sqlite')
        with self.assertRaises(ValueError):
            setup.apply(plan, source_stopped=True)
        self.assertFalse(self.target.exists())

    def test_target_conflict_detected_before_main_copy(self):
        self.fixture()
        plan = self.preview()
        self.quick.mkdir(parents=True)
        sentinel = self.quick / 'valuable'
        sentinel.write_text('existing data')
        with self.assertRaises(ValueError):
            setup.apply(plan, source_stopped=True)
        self.assertFalse(self.target.exists())
        self.assertEqual(sentinel.read_text(), 'existing data')

    def test_future_database_version_never_published(self):
        self.fixture(future=True)
        with self.assertRaises(ValueError):
            setup.apply(self.preview(), source_stopped=True)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.read(self.source), [('saved', 'unclaimed result', 0)])
        self.assertEqual(self.status()['status'], 'pending')

    def test_ambiguous_quick_sources_require_resolution(self):
        self.fixture()
        self.fixture(self.source / 'quick-state', 'quick.sqlite')
        self.fixture(self.source.parent / 'quick-state', 'quick.sqlite')
        with self.assertRaises(ValueError):
            self.preview()
        self.assertFalse(self.target.parent.exists())

    def test_modified_plan_is_refused_without_publication(self):
        plan = self.preview('initialize')
        plan['quick_target'] = str(self.root / 'unexpected')
        with self.assertRaises(ValueError):
            setup.apply(plan, confirm_new=True)
        self.assertFalse(self.target.parent.exists())

    def test_existing_main_without_quick_is_preserved_during_explicit_setup(self):
        for marked in (False, True):
            with self.subTest(marked=marked):
                if self.target.parent.exists():
                    shutil.rmtree(self.target.parent)
                if marked:
                    setup.install.initialize(self.target)
                    sentinel = self.target / 'retained-result.json'
                    sentinel.write_text('existing principal data')
                else:
                    self.fixture(self.target)
                    sentinel = self.target / 'tasks.sqlite'
                before = sentinel.read_bytes()
                state = self.status()
                self.assertEqual(state['status'], 'needs_setup')
                self.assertTrue(state['preserve_main'])
                plan = self.preview('initialize')
                self.assertEqual([step['role'] for step in plan['steps']], ['quick'])
                setup.apply(plan, confirm_new=True)
                self.assertEqual(sentinel.read_bytes(), before)
                self.assertTrue(setup.install.initialized(self.quick))
                self.assertEqual(self.status()['status'], 'ready')

    def test_completed_quick_removal_cannot_silently_create_empty_registry(self):
        setup.apply(self.preview('initialize'), confirm_new=True)
        shutil.rmtree(self.quick)
        self.assertEqual(self.status()['status'], 'conflict')
        with self.assertRaises(ValueError):
            self.preview('initialize')
        self.assertFalse(self.quick.exists())
        self.assertTrue(setup.install.initialized(self.target))

    def test_quick_symlink_or_incompatible_manifest_is_conflict(self):
        setup.apply(self.preview('initialize'), confirm_new=True)
        shutil.rmtree(self.quick)
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'valuable').write_text('preserve linked state')
        self.quick.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.status()['status'], 'conflict')
        self.quick.unlink()
        self.quick.mkdir()
        (self.quick / setup.install.MARKER).write_text('{"format":999,"status":"ready"}')
        self.assertEqual(self.status()['status'], 'conflict')
        self.assertEqual((outside / 'valuable').read_text(), 'preserve linked state')

    def test_retained_main_change_after_preview_refuses_quick_creation(self):
        self.fixture(self.target)
        plan = self.preview('initialize')
        (self.target / 'new-result.json').write_text('new result since preview')
        with self.assertRaises(ValueError):
            setup.apply(plan, confirm_new=True)
        self.assertFalse(self.quick.exists())
        self.assertEqual((self.target / 'new-result.json').read_text(), 'new result since preview')
        self.assertEqual(self.read(self.target), [('saved', 'unclaimed result', 0)])

    def test_cli_status_produces_json_without_creating_state(self):
        home = self.root / 'home'
        home.mkdir()
        env = dict(os.environ, HOME=str(home), AGENT_RESUME_STATE=str(self.target))
        result = subprocess.run([sys.executable, str(ROOT / 'resume'), 'state-status'],
                                cwd=self.root, env=env, text=True, capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'needs_setup')
        self.assertFalse(self.target.parent.exists())
        self.assertFalse(list(home.rglob('*.sqlite')))


if __name__ == '__main__':
    unittest.main()
