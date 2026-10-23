"""Profile integration uses only temporary profiles, state and packaged helpers."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import harness_integration as integration
import package_helpers
import package_runtime
import state_install


class HarnessIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.app = self.base / 'Movable Reprise.app'
        self.root = package_helpers.package(SOURCE, self.app)
        runtime = self.root.parent / 'runtime'
        (runtime / 'bin').mkdir(parents=True)
        (runtime / 'bin/python3').write_text('#!/bin/sh\nexit 99\n')
        (runtime / 'bin/python3').chmod(0o700)
        (runtime / 'runtime-manifest.json').write_text(json.dumps({'files': integration.tree_files(runtime)}))
        payload = json.loads((self.root / 'payload-manifest.json').read_text())
        payload['runtime'] = {'fixture': True}
        (self.root / 'payload-manifest.json').write_text(json.dumps(payload))
        self.state = self.base / 'sharedstate'
        state_install.initialize(self.state)
        with closing(sqlite3.connect(self.state / 'preserve.sqlite')) as db:
            db.execute('CREATE TABLE retained(identity TEXT, result TEXT, attempt TEXT)')
            db.execute('INSERT INTO retained VALUES (?, ?, ?)',
                       ('opaque-fixture-account', 'unclaimed result', 'indeterminate'))
            db.commit()
        env = patch.dict(os.environ, {'AGENT_RESUME_STATE': str(self.state)})
        env.start()
        self.addCleanup(env.stop)
        self.home = self.base / 'harness'
        for name in ('desktop', 'other'):
            folder = self.home / 'profiles' / name
            folder.mkdir(parents=True)
            (folder / 'cordis.patch.yml').write_text('[]\n')
        self.folder = self.home / 'profiles/desktop'
        self.config = self.folder / 'cordis.patch.yml'
        self.managed = self.folder / '.reprise'
        self.project = self.base / 'project'
        self.project.mkdir()

    def plan(self, action='install', delegate=False):
        return integration.plan(self.root, self.home, 'desktop', self.project, action, delegate)

    def install(self, delegate=False):
        proposed = self.plan(delegate=delegate)
        integration.apply(self.root, proposed)
        return proposed

    def snapshot(self, root=None):
        return integration.tree_files(root or self.base)

    def test_public_plan_does_not_expose_personal_configuration(self):
        self.config.write_text('- insert: [{"id":"personal","config":{"token":"fixture-secret"}}]\n')
        proposed = self.plan()
        self.assertNotIn('fixture-secret', json.dumps(proposed))
        self.install()
        self.assertIn('fixture-secret', self.config.read_text())

    def test_commented_empty_array_roundtrips_and_unknown_root_is_refused(self):
        original = '# original\n[] # keep this comment\n'
        self.config.write_text(original)
        self.install()
        integration.apply(self.root, self.plan('remove'))
        self.assertEqual(self.config.read_text(), original)
        self.config.write_text('unexpected: mapping\n')
        with self.assertRaises(ValueError):
            self.plan()
        self.assertEqual(self.config.read_text(), 'unexpected: mapping\n')

    def test_failure_after_publication_before_journal_close_resumes_without_rewrite(self):
        proposed = self.plan()
        original = integration.atomic
        def interrupt(path, text):
            if path.name == 'operation.json' and json.loads(text).get('status') == 'completed':
                raise OSError('fixture final journal failure')
            return original(path, text)
        with patch.object(integration, 'atomic', side_effect=interrupt):
            with self.assertRaises(OSError):
                integration.apply(self.root, proposed)
        before = self.snapshot(self.folder)
        self.assertEqual(integration.inspect(self.home, 'desktop')['pending_plan'], proposed)
        result = integration.apply(self.root, proposed)
        self.assertEqual(result['status'], 'completed')
        after = self.snapshot(self.folder)
        before.pop('.reprise/operation.json')
        after.pop('.reprise/operation.json')
        self.assertEqual(before, after)

    def test_inspection_and_plan_are_read_only(self):
        before = self.snapshot()
        self.assertEqual(integration.inspect(self.home)['profiles'], ['desktop', 'other'])
        proposed = self.plan()
        self.assertTrue(proposed['changes'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.managed.exists())

    def test_install_idempotent_and_preserves_unmanaged_configuration(self):
        custom = '# personal settings\n- remove: personal-plugin\n'
        self.config.write_text(custom)
        self.install()
        before = self.snapshot()
        result = integration.apply(self.root, self.plan())
        self.assertFalse(result['changed'])
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(self.config.read_text().startswith(custom))
        self.assertEqual(self.config.read_text().count(integration.START), 1)
        backups = list((self.managed / 'backups').glob('*/cordis.patch.yml'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), custom)

    def test_remove_selective_and_keeps_state_release_and_unmanaged_files(self):
        global_skill = self.home / 'skills/personal/SKILL.md'
        global_skill.parent.mkdir(parents=True)
        global_skill.write_text('global personal skill')
        other_before = self.snapshot(self.home / 'profiles/other')
        state_before = self.snapshot(self.state)
        proposed = self.install()
        release = Path(proposed['release'])
        release_before = self.snapshot(release)
        extra = self.managed / 'skills/reprise-delegation/personal.txt'
        extra.write_text('keep personal addition')
        self.config.write_text('# personal addition\n' + self.config.read_text())
        integration.apply(self.root, self.plan('remove'))
        self.assertNotIn(integration.START, self.config.read_text())
        self.assertIn('# personal addition', self.config.read_text())
        self.assertEqual(extra.read_text(), 'keep personal addition')
        self.assertEqual(global_skill.read_text(), 'global personal skill')
        self.assertEqual(self.snapshot(self.home / 'profiles/other'), other_before)
        self.assertEqual(self.snapshot(self.state), state_before)
        self.assertEqual(self.snapshot(release), release_before)
        self.assertFalse(integration.apply(self.root, self.plan('remove'))['changed'])

    def test_personalized_skill_refuses_update_and_remove(self):
        self.install()
        skill = self.managed / 'skills/reprise-delegation/SKILL.md'
        skill.write_text(skill.read_text() + '\npersonal change\n')
        before = self.snapshot()
        for action in ('install', 'remove'):
            with self.assertRaisesRegex(ValueError, 'personnalisé'):
                self.plan(action)
        self.assertEqual(self.snapshot(), before)

    def test_personalized_block_refuses_update(self):
        self.install()
        self.config.write_text(self.config.read_text().replace('330000', '330001'))
        with self.assertRaisesRegex(ValueError, 'personnalisé'):
            self.plan()

    def test_unmanaged_existing_integration_conflicts(self):
        self.config.write_text('- insert: [{"id":"reprise-codex-expert"}]\n')
        with self.assertRaisesRegex(ValueError, 'non gérée'):
            self.plan()

    def test_changed_entry_after_preview_refused(self):
        proposed = self.plan()
        self.config.write_text('# changed after preview\n[]\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'modifiées'):
            integration.apply(self.root, proposed)
        self.assertEqual(self.config.read_text(), '# changed after preview\n[]\n')
        self.assertFalse((self.managed / 'operation.json').exists())
        after = self.snapshot()
        after.pop('harness/profiles/desktop/.reprise/lock', None)
        self.assertEqual(after, before)

    def test_missing_managed_file_can_be_repaired(self):
        self.install()
        skill = self.managed / 'skills/reprise-delegation/SKILL.md'
        expected = skill.read_bytes()
        skill.unlink()
        proposed = self.plan()
        self.assertEqual([c['path'] for c in proposed['changes']], [str(skill)])
        integration.apply(self.root, proposed)
        self.assertEqual(skill.read_bytes(), expected)

    def interrupt_install(self):
        proposed = self.plan()
        real_atomic = integration.atomic
        def interrupted(path, text):
            if Path(path).name == 'SKILL.md':
                raise OSError('fixture write interruption')
            return real_atomic(path, text)
        with patch.object(integration, 'atomic', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'fixture write interruption'):
                integration.apply(self.root, proposed)
        return proposed

    def test_pending_operation_recovers_without_duplicate_block(self):
        self.config.write_text('# preserve me\n[]\n')
        proposed = self.interrupt_install()
        inspected = integration.inspect(self.home, 'desktop')
        self.assertEqual(inspected['pending_plan'], proposed)
        with self.assertRaisesRegex(ValueError, 'partielle'):
            self.plan()
        integration.apply(self.root, inspected['pending_plan'])
        self.assertIn('# preserve me', self.config.read_text())
        self.assertEqual(self.config.read_text().count(integration.START), 1)
        self.assertEqual(json.loads((self.managed / 'operation.json').read_text())['status'], 'completed')
        self.assertFalse(self.plan()['changes'])

    def test_partial_concurrent_edit_blocks_recovery_before_new_writes(self):
        proposed = self.interrupt_install()
        self.config.write_text(self.config.read_text() + '# concurrent edit\n')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'Conflit après'):
            integration.apply(self.root, proposed)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.managed / 'skills/reprise-delegation/SKILL.md').exists())

    def test_symlink_managed_parent_refused(self):
        self.managed.mkdir()
        outside = self.base / 'outside'
        outside.mkdir()
        (self.managed / 'skills').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symbolique'):
            self.plan()
        self.assertEqual(list(outside.iterdir()), [])

    def test_invalid_profile_refused(self):
        for name in ('../other', '', 'desktop/child', 'name with spaces'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                integration.plan(self.root, self.home, name, self.project)

    def test_delegation_explicitly_selected_without_model_call(self):
        with patch.object(integration.subprocess, 'run', side_effect=AssertionError('no inference')):
            self.install(delegate=True)
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertEqual(manifest['tools'], list(integration.OBSERVE + integration.DELEGATE))
        self.assertIn('reprise_followup', (self.managed / 'skills/reprise-delegation/SKILL.md').read_text())
        deployed = self.managed / 'skills/reprise-delegation/REPRISE-AGENTS.template.md'
        self.assertEqual(deployed.read_bytes(), (SOURCE / 'REPRISE-AGENTS.template.md').read_bytes())
        self.assertIn(str(deployed.relative_to(self.folder)), manifest['managed_files'])

    def test_existing_unmanaged_skill_is_not_adopted(self):
        skill = self.managed / 'skills/reprise-delegation/SKILL.md'
        skill.parent.mkdir(parents=True)
        skill.write_text('personal skill under matching name')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'préexistant non géré'):
            self.plan()
        self.assertEqual(self.snapshot(), before)

    def test_symlink_operation_journal_is_refused(self):
        self.managed.mkdir()
        outside = self.base / 'outside-journal.json'
        outside.write_text('{}')
        (self.managed / 'operation.json').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symbolique'):
            integration.inspect(self.home, 'desktop')
        self.assertEqual(outside.read_text(), '{}')

    def test_pending_journal_text_tampering_is_refused(self):
        proposed = self.interrupt_install()
        path = self.managed / 'operation.json'
        journal = json.loads(path.read_text())
        journal['operations'][0]['text'] += '# injected after preview\n'
        path.write_text(json.dumps(journal))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'journal modifié'):
            integration.apply(self.root, proposed)
        self.assertEqual(self.snapshot(), before)
        response = subprocess.run([sys.executable, str(SOURCE / 'resume'), 'integration-apply'],
                                  input=json.dumps({'plan': proposed}), text=True, capture_output=True,
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=30)
        self.assertNotEqual(response.returncode, 0)
        result = json.loads(response.stdout)
        self.assertEqual(result['pending_plan'], proposed)
        self.assertEqual(result['backup'], journal['backup'])
        self.assertEqual(self.snapshot(), before)

    def test_profiles_install_independently(self):
        self.install()
        desktop_before = self.snapshot(self.folder)
        other_plan = integration.plan(self.root, self.home, 'other', self.project)
        integration.apply(self.root, other_plan)
        self.assertEqual(self.snapshot(self.folder), desktop_before)
        other = self.home / 'profiles/other/.reprise/managed.json'
        self.assertEqual(json.loads(other.read_text())['profile'], 'other')
        integration.apply(self.root, integration.plan(self.root, self.home, 'other', self.project, 'remove'))
        self.assertEqual(self.snapshot(self.folder), desktop_before)

    def test_cli_inspect_does_not_initialize_state(self):
        absent = self.base / 'never-initialized-state'
        environment = dict(os.environ, AGENT_RESUME_STATE=str(absent), PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable, str(SOURCE / 'resume'), 'integration-inspect', '--home', str(self.home)]
        result = subprocess.run(command, capture_output=True, text=True, env=environment, check=True)
        self.assertEqual(json.loads(result.stdout)['profiles'], ['desktop', 'other'])
        self.assertFalse(absent.exists())

    @unittest.skipUnless(os.environ.get('REPRISE_TEST_RUNTIME_ARCHIVE'), 'Pinned local runtime archive required')
    def test_real_relocated_runtime_bridge_discovery_without_inference(self):
        shutil.rmtree(self.root.parent / 'runtime')
        package_runtime.install(Path(os.environ['REPRISE_TEST_RUNTIME_ARCHIVE']), self.app)
        self.root = package_helpers.package(SOURCE, self.app)
        moved = self.base / 'relocated.app'
        self.app.rename(moved)
        self.root = moved / 'Contents/Resources/helpers'
        state_before = self.snapshot(self.state)
        proposed = self.install()
        config = self.config.read_text()
        self.assertNotIn(str(SOURCE), config)
        self.assertNotIn(str(moved), config)
        self.assertNotIn(str(self.app), config)
        shutil.rmtree(moved)
        result = integration.verify(self.home, 'desktop')
        self.assertEqual(result['bridge_tools'], sorted(integration.OBSERVE))
        self.assertIs(result['levels']['configuration_written'], True)
        for level in ('integration_loaded', 'client_tools_discovered', 'exchange_confirmed'):
            self.assertEqual(result['levels'][level], 'unknown')
        self.assertEqual(self.snapshot(self.state), state_before)
        self.assertTrue(Path(proposed['release']).is_dir())


if __name__ == '__main__':
    unittest.main()
