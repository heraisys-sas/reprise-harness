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
        self.loaded_settings = None
        self.calls = []
        self.fail_bootstrap = False
        self.support, self.plist, self.managed = service.paths(self.home)

    def runner(self, args):
        self.calls.append(args)
        if args[0] == 'print':
            output = ''
            if self.loaded and self.loaded_settings:
                output = '\targuments = {\n' + ''.join('\t\t' + arg + '\n' for arg in self.loaded_settings['ProgramArguments']) + '\t}\n'
                output += '\tenvironment = {\n' + ''.join('\t\t' + key + ' => ' + val + '\n' for key, val in self.loaded_settings.get('EnvironmentVariables', {}).items()) + '\t}\n'
            return subprocess.CompletedProcess(args, 0 if self.loaded else 113, output, '')
        if args[0] == 'bootstrap':
            if self.fail_bootstrap:
                return subprocess.CompletedProcess(args, 1, '', 'fixture failure')
            self.loaded = True
            self.loaded_settings = plistlib.loads(self.plist.read_bytes())
        elif args[0] == 'bootout':
            self.loaded = False
            self.loaded_settings = None
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

    def test_resume_refuses_restored_old_plist_after_activation_failure(self):
        self.apply(self.plan())
        original = self.plist.read_bytes()
        value = self.plan(activate=True, interval=90)
        self.fail_bootstrap = True
        with self.assertRaisesRegex(ValueError, 'Activation'):
            self.apply(value)
        manifest_before = (self.managed / 'managed.json').read_bytes()
        self.plist.write_bytes(original)
        self.fail_bootstrap = False
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'Plist modifié'):
            self.apply(value)
        self.assertEqual(self.plist.read_bytes(), original)
        self.assertEqual((self.managed / 'managed.json').read_bytes(), manifest_before)
        self.assertEqual(self.inspect()['pending_plan'], value)
        self.assertFalse(any(c[0] == 'bootstrap' for c in self.calls))

    def test_repair_reloads_old_effective_release_when_plist_is_current(self):
        self.apply(self.plan(activate=True))
        old_settings = dict(self.loaded_settings)
        current = self.plan()['release']
        previous = str(Path(current).with_name('a' * 64))
        old_settings['ProgramArguments'] = [arg.replace(current, previous) for arg in old_settings['ProgramArguments']]
        self.loaded_settings = old_settings
        self.assertFalse(self.inspect()['loaded_current'])
        self.calls.clear()
        result = self.apply(self.plan(activate=True))
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(self.inspect()['loaded_current'])
        self.assertEqual([c[0] for c in self.calls if c[0] in ('bootout', 'bootstrap')], ['bootout', 'bootstrap'])

    def test_repair_without_activation_unloads_stale_service(self):
        self.apply(self.plan(activate=True))
        self.loaded_settings = {'ProgramArguments': ['/bin/false'], 'EnvironmentVariables': {'AGENT_RESUME_STATE': str(self.state)}}
        self.calls.clear()
        self.apply(self.plan())
        self.assertFalse(self.loaded)
        self.assertEqual([c[0] for c in self.calls if c[0] in ('bootout', 'bootstrap')], ['bootout'])

    def test_activation_must_confirm_effective_configuration(self):
        value = self.plan(activate=True)
        def misleading_runner(args):
            result = self.runner(args)
            if args[0] == 'bootstrap':
                self.loaded_settings = {'ProgramArguments': ['/bin/false'], 'EnvironmentVariables': {'AGENT_RESUME_STATE': str(self.state)}}
            return result
        with self.assertRaisesRegex(ValueError, 'configuration chargée'):
            service.apply(self.root, value, misleading_runner)
        self.assertFalse((self.managed / 'managed.json').exists())
        self.assertEqual(self.inspect()['pending_plan'], value)
        self.calls.clear()
        self.apply(value)
        self.assertTrue(self.inspect()['loaded_current'])
        self.assertEqual([c[0] for c in self.calls if c[0] in ('bootout', 'bootstrap')], ['bootout', 'bootstrap'])

    def test_plist_changed_during_activation_cannot_publish_manifest(self):
        value = self.plan(activate=True)
        def concurrent_runner(args):
            result = self.runner(args)
            if args[0] == 'bootstrap':
                self.plist.write_bytes(plistlib.dumps({'Label': service.LABEL, 'ProgramArguments': ['/bin/false']}))
            return result
        with self.assertRaisesRegex(ValueError, 'Plist modifié avant clôture'):
            service.apply(self.root, value, concurrent_runner)
        self.assertFalse((self.managed / 'managed.json').exists())
        self.assertEqual(self.inspect()['pending_plan'], value)

    def test_loaded_argument_vector_must_match_exactly(self):
        self.apply(self.plan(activate=True))
        expected = list(self.loaded_settings['ProgramArguments'])
        variants = [expected + ['--unexpected'], expected[:1] + list(reversed(expected[1:])), ['/bin/false'] + expected[1:]]
        for args in variants:
            with self.subTest(args=args):
                self.loaded_settings = {'ProgramArguments': args, 'EnvironmentVariables': {'AGENT_RESUME_STATE': str(self.state)}}
                observed = self.inspect()
                self.assertFalse(observed['loaded_current'])
                self.calls.clear()
                self.apply(self.plan(activate=True))
                self.assertTrue(self.inspect()['loaded_current'])
                self.assertEqual([c[0] for c in self.calls if c[0] in ('bootout', 'bootstrap')], ['bootout', 'bootstrap'])

    def test_loaded_arguments_require_unambiguous_complete_block(self):
        settings = {'ProgramArguments': ['/path with spaces/python3', 'tick'], 'EnvironmentVariables': {'AGENT_RESUME_STATE': '/state with spaces'}}
        good = 'program = /path with spaces/python3\n\targuments = {\n\t\t/path with spaces/python3\n\t\ttick\n\t}\n'
        good += '\tenvironment = {\n\t\tAGENT_RESUME_STATE => /state with spaces\n\t}\n'
        observed = subprocess.CompletedProcess([], 0, good, '')
        self.assertTrue(service.loaded_matches(observed, settings))
        for output in [good.split('arguments')[0], good + good, good.rsplit('}', 1)[0]]:
            self.assertFalse(service.loaded_matches(subprocess.CompletedProcess([], 0, output, ''), settings))

    def test_other_or_unknown_loaded_registry_cannot_be_stopped(self):
        self.apply(self.plan(activate=True))
        for environment in ({'AGENT_RESUME_STATE': str(self.base / 'other registry')}, {}):
            with self.subTest(environment=environment):
                self.loaded_settings['EnvironmentVariables'] = environment
                observed = self.inspect()
                self.assertFalse(observed['loaded_current'])
                self.assertFalse(observed['loaded_state_matches'])
                self.assertTrue(observed['configured_state_matches'])
                self.calls.clear()
                with self.assertRaisesRegex(ValueError, 'Registre du service chargé'):
                    self.apply(self.plan(activate=True))
                self.assertFalse(any(c[0] in ('bootout', 'bootstrap') for c in self.calls))
                self.assertTrue(self.loaded)

    def test_loaded_and_configured_other_registry_are_not_current(self):
        self.apply(self.plan(activate=True))
        settings = plistlib.loads(self.plist.read_bytes())
        settings['EnvironmentVariables']['AGENT_RESUME_STATE'] = str(self.base / 'other registry')
        self.plist.write_bytes(plistlib.dumps(settings))
        self.loaded_settings = settings
        observed = self.inspect()
        self.assertFalse(observed['configured_current'])
        self.assertFalse(observed['loaded_current'])

    def test_remove_cannot_stop_job_using_another_registry(self):
        self.apply(self.plan(activate=True))
        self.loaded_settings['EnvironmentVariables']['AGENT_RESUME_STATE'] = str(self.base / 'other registry')
        self.calls.clear()
        with self.assertRaisesRegex(ValueError, 'Registre du service chargé'):
            self.apply(self.plan(action='remove'))
        self.assertTrue(self.plist.exists())
        self.assertFalse(any(c[0] in ('bootout', 'bootstrap') for c in self.calls))

    def test_registry_parser_ignores_default_and_inherited_environment(self):
        prefix = 'default environment = {\n AGENT_RESUME_STATE => /wrong default\n}\ninherited environment = {\n AGENT_RESUME_STATE => /wrong inherited\n}\n'
        explicit = 'environment = {\n AGENT_RESUME_STATE => /parent with spaces/state\n}\n'
        def parsed(text):
            return service.loaded_registry(subprocess.CompletedProcess([], 0, text, ''))
        self.assertIsNone(parsed(prefix))
        self.assertEqual(parsed(prefix + explicit), '/parent with spaces/state')
        self.assertIsNone(parsed(explicit + explicit))
        self.assertIsNone(parsed(explicit.rsplit('}', 1)[0]))
        self.assertIsNone(parsed('environment = {\n AGENT_RESUME_STATE => /one\n AGENT_RESUME_STATE => /two\n}'))

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
