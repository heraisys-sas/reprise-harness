"""Profile integration uses only temporary profiles, state and packaged helpers."""
from contextlib import closing
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re
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
from yaml_contract import yaml_hashes


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

    def test_yaml_integrity_and_identity(self):
        helpers, runtime, before = integration.bundle_info(self.root)
        yaml_file = helpers / 'vendor/yaml/reader.py'
        yaml_file.write_text('altered')
        with self.assertRaisesRegex(ValueError, 'YAML'):
            integration.bundle_info(self.root)
        payload_path = helpers / 'payload-manifest.json'
        payload = json.loads(payload_path.read_text())
        payload['vendor_yaml'] = yaml_hashes(helpers, exact=True)
        payload_path.write_text(json.dumps(payload))
        self.assertNotEqual(before, integration.bundle_info(self.root)[2])
        (helpers / 'vendor/yaml/token.pem').write_text('unexpected')
        with self.assertRaisesRegex(ValueError, 'YAML'):
            integration.bundle_info(self.root)

    def test_legacy_yaml_manifest_refused_without_touching_existing_release(self):
        helpers, runtime, identity = integration.bundle_info(self.root)
        release = self.base / 'releases' / identity
        integration.stage_release(helpers, runtime, release)
        before = integration.tree_files(release)
        payload_path = helpers / 'payload-manifest.json'
        payload = json.loads(payload_path.read_text())
        del payload['vendor_yaml']
        payload_path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'non authentifié'):
            integration.stage_release(helpers, runtime, release)
        self.assertEqual(before, integration.tree_files(release))
        shutil.rmtree(helpers / 'vendor/yaml')
        integration.bundle_info(self.root)  # legacy payload without YAML remains valid

    def test_yaml_symlink_is_refused(self):
        yaml_file = self.root / 'vendor/yaml/reader.py'
        yaml_file.unlink()
        yaml_file.symlink_to(self.root / 'vendor/yaml/parser.py')
        with self.assertRaisesRegex(ValueError, 'YAML'):
            integration.bundle_info(self.root)

    def test_yaml_changed_during_stage_is_not_published(self):
        helpers, runtime, identity = integration.bundle_info(self.root)
        release = self.base / 'releases' / identity
        copyfile = shutil.copyfile
        def mutate(source, target, *args, **kwargs):
            if str(source).endswith('vendor/yaml/reader.py'):
                Path(source).write_text('changed while copying')
            return copyfile(source, target, *args, **kwargs)
        with patch.object(integration.shutil, 'copyfile', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'YAML'):
                integration.stage_release(helpers, runtime, release)
        self.assertFalse(release.exists())

    def test_coherent_yaml_change_after_validation_cannot_publish_old_identity(self):
        proposed = self.plan('install', True)
        release = Path(proposed['release'])
        stage_release = integration.stage_release
        def mutate_before_stage(helpers, runtime, target):
            (helpers / 'vendor/yaml/reader.py').write_text('changed')
            manifest = helpers / 'payload-manifest.json'
            payload = json.loads(manifest.read_text())
            payload['vendor_yaml'] = yaml_hashes(helpers, exact=True)
            manifest.write_text(json.dumps(payload))
            return stage_release(helpers, runtime, target)
        with patch.object(integration, 'stage_release', side_effect=mutate_before_stage):
            with self.assertRaisesRegex(ValueError, 'Identité'):
                integration.apply(self.root, proposed)
        self.assertFalse(release.exists())

    def test_new_empty_yaml_manifest_is_refused_but_legacy_without_yaml_is_accepted(self):
        payload_path = self.root / 'payload-manifest.json'
        payload = json.loads(payload_path.read_text())
        shutil.rmtree(self.root / 'vendor/yaml')
        payload['vendor_yaml'] = {}
        payload_path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'YAML'):
            integration.bundle_info(self.root)
        del payload['vendor_yaml']
        payload_path.write_text(json.dumps(payload))
        integration.bundle_info(self.root)

    def test_real_yaml_import_does_not_write_cache_or_change_identity(self):
        helpers, runtime, before = integration.bundle_info(self.root)
        env = dict(os.environ)
        env.pop('PYTHONDONTWRITEBYTECODE', None)
        env.pop('PYTHONPYCACHEPREFIX', None)
        result = subprocess.run([sys.executable, '-c', 'import harness_integration; harness_integration._yaml_module()'],
                                cwd=helpers, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        cache = helpers / 'vendor/yaml/__pycache__'
        self.assertFalse(cache.exists())
        self.assertEqual(before, integration.bundle_info(self.root)[2])
        release = self.base / 'releases' / before
        integration.stage_release(helpers, runtime, release)
        self.assertFalse((release / 'Contents/Resources/helpers/vendor/yaml/__pycache__').exists())
        integration.bundle_info(self.root)

    def test_forged_timestamp_matching_pyc_is_refused_before_import(self):
        import py_compile
        import importlib.util
        helpers, _, _ = integration.bundle_info(self.root)
        source = helpers / 'vendor/yaml/reader.py'
        original = source.read_bytes()
        stat = source.stat()
        malicious = b'raise RuntimeError("FORGED_PYC_EXECUTED")\n'
        source.write_bytes(malicious + b' ' * (len(original) - len(malicious)))
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir()
        py_compile.compile(str(source), cfile=str(cache), doraise=True)
        source.write_bytes(original)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        env = dict(os.environ)
        env.pop('PYTHONDONTWRITEBYTECODE', None)
        result = subprocess.run([sys.executable, '-c', 'import harness_integration; harness_integration._yaml_module()'],
                                cwd=helpers, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('FORGED_PYC_EXECUTED', result.stderr)
        with self.assertRaisesRegex(ValueError, 'YAML'):
            integration.bundle_info(self.root)

    def install(self, delegate=False, **options):
        proposed = integration.plan(self.root, self.home, 'desktop', self.project, 'install', delegate, **options)
        integration.apply(self.root, proposed)
        return proposed

    def snapshot(self, root=None):
        return integration.tree_files(root or self.base)

    def test_workspace_write_grant_is_explicit_persisted_and_reversible(self):
        with self.assertRaises(ValueError):
            integration.plan(self.root, self.home, 'desktop', self.project, codex_workspace_write=True)
        granted = integration.plan(self.root, self.home, 'desktop', self.project,
                                   delegate=True, codex_workspace_write=True)
        self.assertTrue(granted['codex_workspace_write'])
        integration.apply(self.root, granted)
        self.assertIn('--codex-workspace-write', self.config.read_text())
        self.assertTrue(integration.inspect(self.home, 'desktop')['codex_workspace_write'])
        self.assertFalse(integration.plan(self.root, self.home, 'desktop', self.project,
                                        delegate=True, codex_workspace_write=True)['changes'])
        integration.apply(self.root, self.plan(delegate=True))
        self.assertNotIn('--codex-workspace-write', self.config.read_text())
        self.assertFalse(integration.inspect(self.home, 'desktop')['codex_workspace_write'])

    def test_cli_reinstallation_without_workspace_write_flag_revokes_grant(self):
        self.install(delegate=True, codex_workspace_write=True)
        output = io.StringIO()
        with redirect_stdout(output):
            status = integration.main(self.root, [
                'integration-plan', '--home', str(self.home), '--profile', 'desktop',
                '--project', str(self.project), '--action', 'install', '--delegate'])
        self.assertEqual(status, 0, output.getvalue())
        result = json.loads(output.getvalue())
        proposed = result['plan']
        self.assertFalse(proposed.get('codex_workspace_write', False))
        self.assertIn('--codex-workspace-write', self.config.read_text())
        integration.apply(self.root, proposed)
        self.assertNotIn('--codex-workspace-write', self.config.read_text())
        self.assertFalse(integration.inspect(self.home, 'desktop')['codex_workspace_write'])

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
        self.config.write_text(self.config.read_text().replace('630000', '630001'))
        with self.assertRaisesRegex(ValueError, 'personnalisé'):
            self.plan()

    def reformatted_config(self, transform=lambda payload: payload):
        text = self.config.read_text()
        match = re.search(r'^- insert: (.+)$', text, re.M)
        if match is None:
            match = re.search(r'^- insert:\n    (.+)$', text, re.M)
        payload = transform(match.group(1))
        base = text[:text.index(integration.START)]
        return base + integration.START + '\n- insert:\n    ' + payload + '\n' + integration.END + '\n'

    def test_equivalent_reformatted_block_is_reconciled(self):
        self.install()
        reformatted = self.reformatted_config()
        old_block = reformatted[reformatted.index(integration.START):]
        self.assertNotEqual(reformatted, self.config.read_text())
        outside = '# personal settings\n' + reformatted
        self.config.write_text(outside)
        proposed = self.plan()
        self.assertEqual(proposed['block_reconciled'],
                         {'from_sha256': integration.digest(old_block.encode()),
                          'to_sha256': proposed['block_reconciled']['to_sha256']})
        self.assertNotEqual(proposed['block_reconciled']['from_sha256'],
                            proposed['block_reconciled']['to_sha256'])
        integration.apply(self.root, proposed)
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertEqual(manifest['reconciled_from_sha256'], proposed['block_reconciled']['from_sha256'])
        self.assertEqual(self.plan()['changes'], [])
        self.assertTrue(self.config.read_text().startswith('# personal settings\n'))
        self.assertNotIn('- insert:\n', self.config.read_text())

    def test_reformatted_managed_block_allows_explicit_workspace_write_update(self):
        self.install(delegate=True)
        self.config.write_text(self.reformatted_config())
        proposed = integration.plan(self.root, self.home, 'desktop', self.project,
                                   delegate=True, codex_workspace_write=True)
        self.assertTrue(proposed['codex_workspace_write'])
        self.assertTrue(proposed['block_reconciled'])
        integration.apply(self.root, proposed)
        self.assertIn('--codex-workspace-write', self.config.read_text())
        self.assertTrue(integration.inspect(self.home, 'desktop')['codex_workspace_write'])

    def test_real_block_change_is_refused_not_reconciled(self):
        self.install()
        self.config.write_text(self.reformatted_config(lambda payload: payload.replace('630000', '630001')))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'personnalisé'):
            self.plan()
        self.assertEqual(self.snapshot(), before)

    def test_unreadable_block_is_refused_not_adopted(self):
        self.install()
        self.config.write_text(self.reformatted_config(lambda payload: payload[:len(payload) // 2]))
        before = self.snapshot()
        for action in ('install', 'remove'):
            with self.assertRaisesRegex(ValueError, 'personnalisé'):
                self.plan(action)
        self.assertEqual(self.snapshot(), before)

    def test_verify_accepts_equivalent_reformatted_block_and_refuses_real_change(self):
        probe = self.bridge_probe()
        self.config.write_text(self.reformatted_config())
        with patch.object(integration.subprocess, 'run', return_value=probe):
            result = integration.verify(self.home, 'desktop', self.root)
        self.assertEqual(result['status'], 'verified')
        self.assertIn('block_reconciled', result)
        self.config.write_text(self.reformatted_config(lambda payload: payload.replace('630000', '630001')))
        with patch.object(integration.subprocess, 'run', return_value=probe):
            with self.assertRaisesRegex(ValueError, 'absente ou modifiée'):
                integration.verify(self.home, 'desktop', self.root)

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

    def test_coordinator_role_is_explicit_persisted_and_does_not_enable_tools(self):
        proposed = integration.plan(self.root, self.home, 'desktop', self.project, agent_role='coordinator')
        self.assertEqual(proposed['agent_role'], 'coordinator')
        integration.apply(self.root, proposed)
        saved = json.loads((self.managed / 'managed.json').read_text())
        self.assertEqual(saved['agent_role'], 'coordinator')
        self.assertEqual(saved['tools'], list(integration.OBSERVE))
        skill = (self.managed / 'skills/reprise-delegation/SKILL.md').read_text()
        self.assertIn('Active operator-selected role: coordinator', skill)
        self.assertEqual(integration.plan(self.root, self.home, 'desktop', self.project,
                                         agent_role='coordinator')['changes'], [])
        normal = integration.plan(self.root, self.home, 'desktop', self.project)
        integration.apply(self.root, normal)
        self.assertNotIn('Active operator-selected role: coordinator',
                         (self.managed / 'skills/reprise-delegation/SKILL.md').read_text())
        self.assertNotIn('agent_role', json.loads((self.managed / 'managed.json').read_text()))

    def test_output_continuation_is_explicit_scoped_and_removable(self):
        proposed = integration.plan(self.root, self.home, 'desktop', self.project,
                                    output_resume=True)
        self.assertTrue(proposed['output_resume'])
        integration.apply(self.root, proposed)
        saved = json.loads((self.managed / 'managed.json').read_text())
        self.assertTrue(saved['output_resume'])
        block = integration.split_block(self.config.read_text())[1]
        entries = json.loads(block.split('- insert: ', 1)[1].split(integration.END)[0])
        plugin = next(e for e in entries if e['id'] == 'reprise-output-resume')
        self.assertTrue(plugin['config']['enabled'])
        self.assertEqual(plugin['config']['projectRoot'], str(self.project))
        self.assertEqual(plugin['config']['maxConsecutive'], 2)
        self.assertTrue(Path(plugin['name']).is_file())
        self.assertTrue(integration.inspect(self.home, 'desktop')['output_resume'])
        self.assertEqual(integration.plan(self.root, self.home, 'desktop', self.project,
                                         output_resume=True)['changes'], [])
        integration.apply(self.root, self.plan())
        self.assertNotIn('reprise-output-resume', self.config.read_text())
        self.assertFalse(integration.inspect(self.home, 'desktop')['output_resume'])

    def test_output_continuation_project_is_independent_of_bridge(self):
        other = self.base / 'other-project'
        other.mkdir()
        proposed = integration.plan(self.root, self.home, 'desktop', self.project,
                                    output_resume=True, output_resume_project=other)
        self.assertEqual(proposed['output_resume_project'], str(other))
        integration.apply(self.root, proposed)
        saved = json.loads((self.managed / 'managed.json').read_text())
        self.assertEqual(saved['project'], str(self.project))
        self.assertEqual(saved['output_resume_project'], str(other))
        self.assertEqual(integration.inspect(self.home, 'desktop')['output_resume_project'], str(other))

    def test_unknown_role_is_rejected_before_any_profile_write(self):
        before = self.snapshot()
        with self.assertRaises(ValueError):
            integration.plan(self.root, self.home, 'desktop', self.project, agent_role='asi1')
        self.assertEqual(self.snapshot(), before)

    def test_invalid_profile_refused(self):
        for name in ('../other', '', 'desktop/child', 'name with spaces'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                integration.plan(self.root, self.home, name, self.project)

    def test_delegation_explicitly_selected_without_model_call(self):
        with patch.object(integration.subprocess, 'run', side_effect=AssertionError('no inference')):
            self.install(delegate=True)
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertEqual(manifest['tools'], list(integration.OBSERVE + integration.DELEGATE))
        self.assertNotIn('approval_respond', manifest['tools'])  # #117 T2a : exposition automatique retirée
        self.assertIn('reprise_followup', (self.managed / 'skills/reprise-delegation/SKILL.md').read_text())
        deployed = self.managed / 'skills/reprise-delegation/REPRISE-AGENTS.template.md'
        self.assertEqual(deployed.read_bytes(), (SOURCE.parent / 'docs/templates/REPRISE-AGENTS.template.md').read_bytes())
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
        response = subprocess.run([sys.executable, str(SOURCE / 'reprise'), 'integration-apply'],
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
        command = [sys.executable, str(SOURCE / 'reprise'), 'integration-inspect', '--home', str(self.home)]
        result = subprocess.run(command, capture_output=True, text=True, env=environment, check=True)
        self.assertEqual(json.loads(result.stdout)['profiles'], ['desktop', 'other'])
        self.assertFalse(absent.exists())

    def bridge_probe(self, delegate=False, legacy=False):
        self.install(delegate=delegate)
        tools = []
        for name in sorted(integration.OBSERVE + (integration.DELEGATE if delegate else ())):
            properties = {}
            if not legacy:
                if name == 'reprise_requests':
                    properties = {'view': {'enum': ['receipt', 'page']}}
                elif name in integration.DELEGATE:
                    properties = {'return_mode': {'enum': ['full', 'receipt']}}
            tools.append({'name': name, 'inputSchema': {'properties': properties}})
        stdout = '\n'.join(json.dumps(message) for message in [
            {'id': 1, 'result': {'serverInfo': {'version': 'fixture-version'}}},
            {'id': 2, 'result': {'tools': tools}},
        ])
        return subprocess.CompletedProcess([], 0, stdout=stdout, stderr='')

    def test_capabilities_match_actual_bridge_schemas_with_oneof_const(self):
        from codex_expert_mcp import TOOLS
        result = integration.bridge_capabilities(TOOLS)
        self.assertEqual(result['evidence'], 'direct_probe_tools_schema')
        self.assertEqual(result['result_transport'], {
            'receipt': True, 'pagination': True, 'delegate_receipt': True,
            'followup_receipt': True,
        })

    def test_verify_reports_probed_capabilities_and_current_selected_bundle(self):
        probe = self.bridge_probe(delegate=True)
        before = self.snapshot(self.state)
        with patch.object(integration.subprocess, 'run', return_value=probe) as run:
            result = integration.verify(self.home, 'desktop', self.root)
        self.assertEqual(result['bridge_version'], 'fixture-version')
        self.assertEqual(result['selected_release']['status'], 'current')
        self.assertFalse(result['update_required'])
        self.assertTrue(all(result['bridge_capabilities']['result_transport'].values()))
        self.assertEqual(result['levels']['integration_loaded'], 'unknown')
        self.assertEqual(result['bridge_capabilities']['client_loaded'], 'unknown')
        requests = [json.loads(line) for line in run.call_args.kwargs['input'].splitlines()]
        self.assertEqual([item['method'] for item in requests],
                         ['initialize', 'notifications/initialized', 'tools/list'])
        self.assertEqual(self.snapshot(self.state), before)

    def test_verify_detects_legacy_tools_even_with_matching_release_identity(self):
        probe = self.bridge_probe(delegate=True, legacy=True)
        with patch.object(integration.subprocess, 'run', return_value=probe):
            result = integration.verify(self.home, 'desktop', self.root)
        self.assertTrue(result['update_required'])
        self.assertEqual(result['selected_release']['status'], 'current')
        self.assertEqual(set(result['missing_result_capabilities']),
                         {'receipt', 'pagination', 'delegate_receipt', 'followup_receipt'})

    def test_verify_detects_different_selected_bundle_without_claiming_loaded_client(self):
        probe = self.bridge_probe()
        helpers, runtime, _ = integration.bundle_info(self.root)
        with patch.object(integration.subprocess, 'run', return_value=probe), \
                patch.object(integration, 'bundle_info', return_value=(helpers, runtime, 'f' * 64)):
            result = integration.verify(self.home, 'desktop', self.root)
        self.assertTrue(result['update_required'])
        self.assertEqual(result['selected_release']['status'], 'outdated')
        self.assertEqual(result['levels']['client_tools_discovered'], 'unknown')
        self.assertIsNone(result['bridge_capabilities']['result_transport']['followup_receipt'])

    def test_verify_without_selected_bundle_preserves_unknown_comparison(self):
        probe = self.bridge_probe()
        with patch.object(integration.subprocess, 'run', return_value=probe), \
                patch.object(integration, 'bundle_info', side_effect=FileNotFoundError('fixture')):
            result = integration.verify(self.home, 'desktop', self.root)
        self.assertEqual(result['selected_release']['status'], 'unknown')
        self.assertFalse(result['update_required'])
        self.assertEqual(result['levels']['integration_loaded'], 'unknown')

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

    def asi1_profile(self):
        return (
            '- id: llm-pi-ai\n'
            "  name: '@deepseek-ai/dsh-llm-pi-ai'\n"
            '  config:\n'
            '    providers:\n'
            '      asi-cloud:\n'
            '        api: openai-completions\n'
            '        models:\n'
            '          - id: asi1\n'
            '            contextWindow: 128000\n'
            '            maxTokens: 4096\n'
            '        compat:\n'
            '          supportsUsageInStreaming: true\n'
            '          supportsStore: false\n'
            '- insert:\n'
            '    - id: preset-asi1-orchestrator\n'
            "      name: '@deepseek-ai/dsh-agent-preset'\n"
            '      config:\n'
            '        id: asi1-orchestrator\n'
            '        plugins:\n'
            '          - id: persona\n'
            "            name: '@deepseek-ai/dsh-persona'\n"
        )

    def test_asi1_effort_is_explicit_managed_and_reversible(self):
        original = self.asi1_profile()
        self.config.write_text(original)
        proposed = integration.plan(self.root, self.home, 'desktop', self.project,
                                    output_resume=True, asi1_effort=True)
        self.assertTrue(proposed['asi1_effort'])
        integration.apply(self.root, proposed)
        text = self.config.read_text()
        self.assertEqual(text.count(integration.ASI1_START + '\n'), 2)
        self.assertEqual(text.count(integration.ASI1_PLUGIN_START + '\n'), 1)
        self.assertEqual(text.count('reasoningEfforts:'), 1)
        self.assertIn('supportsReasoningEffort: true', text)
        self.assertIn('supportsDeveloperRole: false', text)
        self.assertIn('- id: asi1-orchestrator-effort', text)
        plugin_path = self.managed / 'plugins/asi1-orchestrator-effort.cjs'
        self.assertTrue(plugin_path.is_file())
        self.assertIn(str(plugin_path), text)
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertTrue(manifest['asi1_effort'])
        self.assertEqual(len(manifest['asi1_effort_blocks']), 2)
        self.assertEqual(len(manifest['asi1_preset_blocks']), 1)
        self.assertTrue(integration.inspect(self.home, 'desktop')['asi1_effort'])
        self.assertEqual(integration.plan(self.root, self.home, 'desktop', self.project,
                                         output_resume=True, asi1_effort=True)['changes'], [])
        integration.apply(self.root, integration.plan(self.root, self.home, 'desktop', self.project, 'remove'))
        self.assertEqual(self.config.read_text(), original)
        self.assertFalse(plugin_path.exists())
        self.assertFalse(integration.inspect(self.home, 'desktop')['asi1_effort'])

    def test_asi1_effort_requires_the_route_and_model(self):
        with self.assertRaisesRegex(ValueError, 'Configuration ASI1 absente'):
            integration.plan(self.root, self.home, 'desktop', self.project, asi1_effort=True)

    def test_asi1_effort_requires_the_preset(self):
        self.config.write_text(self.asi1_profile().split('- insert:')[0])
        with self.assertRaisesRegex(ValueError, 'Preset asi1-orchestrator absent'):
            integration.plan(self.root, self.home, 'desktop', self.project, asi1_effort=True)

    def test_asi1_effort_does_not_adopt_preexisting_declarations(self):
        original = self.asi1_profile().replace(
            '            maxTokens: 4096\n',
            '            maxTokens: 4096\n'
            '            reasoningEfforts:\n'
            '              low: low\n'
            '              medium: medium\n'
            '              high: high\n', 1).replace(
            '          supportsStore: false\n',
            '          supportsStore: false\n'
            '          supportsReasoningEffort: true\n'
            '          supportsDeveloperRole: false\n', 1)
        self.config.write_text(original)
        integration.apply(self.root, integration.plan(self.root, self.home, 'desktop', self.project,
                                                      asi1_effort=True))
        self.assertEqual(self.config.read_text().count(integration.ASI1_START + '\n'), 0)
        integration.apply(self.root, integration.plan(self.root, self.home, 'desktop', self.project, 'remove'))
        self.assertIn('reasoningEfforts:', self.config.read_text())

    def test_asi1_effort_keeps_preexisting_preset_row(self):
        original = self.asi1_profile().replace(
            '        plugins:\n          - id: persona\n',
            '        plugins:\n'
            '          - id: asi1-orchestrator-effort\n'
            '            name: /manual/asi1.cjs\n'
            '          - id: persona\n')
        self.config.write_text(original)
        integration.apply(self.root, integration.plan(self.root, self.home, 'desktop', self.project,
                                                      asi1_effort=True))
        self.assertNotIn(integration.ASI1_PLUGIN_START, self.config.read_text())
        self.assertIn('/manual/asi1.cjs', self.config.read_text())
        plugin_path = self.managed / 'plugins/asi1-orchestrator-effort.cjs'
        self.assertTrue(plugin_path.is_file())
        integration.apply(self.root, integration.plan(self.root, self.home, 'desktop', self.project, 'remove'))
        self.assertIn('/manual/asi1.cjs', self.config.read_text())
        self.assertFalse(plugin_path.exists())

    def test_asi1_effort_block_is_not_silently_rewritten(self):
        self.config.write_text(self.asi1_profile())
        integration.apply(self.root, integration.plan(self.root, self.home, 'desktop', self.project,
                                                      asi1_effort=True))
        text = self.config.read_text().replace('low: low', 'low: high', 1)
        self.config.write_text(text)
        for action in ('install', 'remove'):
            with self.assertRaisesRegex(ValueError, 'Bloc ASI1 personnalisé'):
                integration.plan(self.root, self.home, 'desktop', self.project, action,
                                 asi1_effort=action == 'install')

    def test_chatgpt_socket_entries_are_optional_without_workspace_write(self):
        for socket in (None, '/tmp/future-chatgpt.sock'):
            with self.subTest(socket=socket):
                entries = integration._bridge_entries(
                    Path('/release'), '/state', ('reprise_backends',), '/project',
                    'desktop', self.managed, chatgpt_socket=socket)
                args = entries[0]['config']['args']
                self.assertEqual('--chatgpt-socket' in args, socket is not None)
                if socket is not None:
                    self.assertEqual(args[args.index('--chatgpt-socket') + 1], socket)
                self.assertNotIn('--chatgpt-workspace-write', args)
                self.assertNotIn('--codex-workspace-write', args)

    def test_chatgpt_socket_lifecycle_preserves_removed_manifest_and_state(self):
        probe = self.bridge_probe()
        socket = str(self.base / 'future-chatgpt.sock')
        self.assertFalse(Path(socket).exists())
        proposed = self.install(chatgpt_socket=socket)
        self.assertEqual(proposed['chatgpt_socket'], socket)
        self.assertEqual(integration.inspect(self.home, 'desktop')['chatgpt_socket'], socket)
        manifest_path = self.managed / 'managed.json'
        installed = json.loads(manifest_path.read_text())
        self.assertEqual(installed['chatgpt_socket'], socket)
        self.assertEqual(integration.plan(self.root, self.home, 'desktop', self.project,
                                         chatgpt_socket=socket)['changes'], [])
        with patch.object(integration.subprocess, 'run', return_value=probe):
            integration.verify(self.home, 'desktop', root=self.root, project=self.project)
        state_before = integration.tree_files(self.state)
        integration.apply(self.root, self.plan('remove'))
        removed = json.loads(manifest_path.read_text())
        self.assertEqual(removed['chatgpt_socket'], socket)
        self.assertEqual(removed['status'], 'removed')
        self.assertEqual(removed['state'], installed['state'])
        self.assertEqual(removed['release'], installed['release'])
        self.assertTrue(Path(removed['release']).is_dir())
        self.assertEqual(integration.tree_files(self.state), state_before)
        self.assertNotIn('--chatgpt-socket', self.config.read_text())
        self.assertNotIn('chatgpt_socket', self.plan())

    def test_chatgpt_socket_defaults_to_disabled_and_omission_revokes(self):
        self.install()
        self.assertIsNone(integration.inspect(self.home, 'desktop')['chatgpt_socket'])
        self.assertNotIn('chatgpt_socket', json.loads((self.managed / 'managed.json').read_text()))
        self.install(chatgpt_socket='/tmp/chatgpt.sock')
        proposed = self.plan()
        self.assertNotIn('chatgpt_socket', proposed)
        integration.apply(self.root, proposed)
        self.assertNotIn('--chatgpt-socket', self.config.read_text())
        self.assertIsNone(integration.inspect(self.home, 'desktop')['chatgpt_socket'])

    def test_chatgpt_socket_rejects_relative_paths(self):
        for socket in ('chatgpt.sock', '~/chatgpt.sock', ''):
            with self.subTest(socket=socket):
                with self.assertRaisesRegex(ValueError, 'Socket ChatGPT'):
                    integration.plan(self.root, self.home, 'desktop', self.project,
                                     chatgpt_socket=socket)
                with self.assertRaisesRegex(ValueError, 'Socket ChatGPT'):
                    integration.desired(self.root, self.home, 'desktop', self.project,
                                        'install', chatgpt_socket=socket)
                with self.assertRaisesRegex(ValueError, 'Socket ChatGPT'):
                    integration._bridge_entries(Path('/release'), '/state', (), '/project',
                                                'desktop', self.managed, chatgpt_socket=socket)

    def test_chatgpt_socket_cli_toggles_and_omission(self):
        base_args = ['integration-plan', '--home', str(self.home), '--profile', 'desktop',
                     '--project', str(self.project), '--action', 'install']
        self.install(chatgpt_socket='/tmp/previous-chatgpt.sock')
        for options, expected in ((['--chatgpt-socket', '/tmp/new-chatgpt.sock'], '/tmp/new-chatgpt.sock'),
                                  (['--no-chatgpt-socket'], None), ([], None)):
            with self.subTest(options=options):
                output = io.StringIO()
                with redirect_stdout(output):
                    status = integration.main(self.root, base_args + options)
                self.assertEqual(status, 0, output.getvalue())
                proposed = json.loads(output.getvalue())['plan']
                self.assertEqual(proposed.get('chatgpt_socket'), expected)
                integration.apply(self.root, proposed)
                self.assertEqual(integration.inspect(self.home, 'desktop')['chatgpt_socket'], expected)
        output = io.StringIO()
        with redirect_stdout(output):
            status = integration.main(self.root, base_args + ['--chatgpt-socket', 'relative.sock'])
        self.assertEqual(status, 1)
        self.assertIn('Socket ChatGPT', json.loads(output.getvalue())['message'])

    def test_chatgpt_socket_verify_rejects_manifest_divergence_with_matching_hash(self):
        self.install(chatgpt_socket='/tmp/chatgpt.sock')
        manifest_path = self.managed / 'managed.json'
        old = json.loads(manifest_path.read_text())
        _, block = integration.split_block(self.config.read_text())
        self.assertEqual(old['block_sha256'], integration.digest(block.encode()))
        old['chatgpt_socket'] = '/tmp/other-chatgpt.sock'
        manifest_path.write_text(json.dumps(old))
        with self.assertRaisesRegex(ValueError, 'Configuration Reprise'):
            integration.verify(self.home, 'desktop', root=self.root, project=self.project)

    def test_chatgpt_socket_reformatted_block_can_be_updated(self):
        self.install(chatgpt_socket='/tmp/chatgpt.sock')
        self.config.write_text(self.reformatted_config())
        proposed = integration.plan(self.root, self.home, 'desktop', self.project,
                                    chatgpt_socket='/tmp/new-chatgpt.sock')
        self.assertIn('block_reconciled', proposed)
        integration.apply(self.root, proposed)
        self.assertEqual(integration.inspect(self.home, 'desktop')['chatgpt_socket'],
                         '/tmp/new-chatgpt.sock')

    def test_desktop_socket_is_generated_when_present_and_persisted(self):
        socket = self.base / 'quick-entry.sock'
        socket.write_text('')
        with patch.object(integration, 'DESKTOP_SOCKET', socket):
            proposed = self.install()
        text = self.config.read_text()
        self.assertIn('--desktop-socket', text)
        self.assertIn(str(socket), text)
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertEqual(manifest['desktop_socket'], str(socket))
        self.assertTrue(integration.inspect(self.home, 'desktop')['desktop_socket'])
        with patch.object(integration.subprocess, 'run', return_value=self.bridge_probe()):
            integration.verify(self.home, 'desktop', root=self.root, project=self.project)
        integration.apply(self.root, integration.plan(self.root, self.home, 'desktop', self.project, 'remove'))
        self.assertNotIn('--desktop-socket', self.config.read_text())

    def test_desktop_socket_is_absent_by_default_and_plan_tracks_the_filesystem(self):
        with patch.object(integration, 'DESKTOP_SOCKET', self.base / 'absent.sock'):
            proposed = self.install()
        self.assertNotIn('--desktop-socket', self.config.read_text())
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertNotIn('desktop_socket', manifest)
        self.assertIsNone(integration.inspect(self.home, 'desktop')['desktop_socket'])
        with patch.object(integration.subprocess, 'run', return_value=self.bridge_probe()):
            integration.verify(self.home, 'desktop', root=self.root, project=self.project)
        socket = self.base / 'quick-entry.sock'
        socket.write_text('')
        with patch.object(integration, 'DESKTOP_SOCKET', socket):
            with self.assertRaisesRegex(ValueError, 'Refaire le plan'):
                integration.apply(self.root, proposed)

    def test_bash_sandbox_disabled_is_explicit_persisted_and_carried_over(self):
        proposed = self.install(bash_sandbox_disabled=True)
        text = self.config.read_text()
        self.assertIn('bash-sandbox', text)
        self.assertIn('"disabled": true', text)
        self.assertIn('"timeoutMs": 90000', text)
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertTrue(manifest['bash_sandbox_disabled'])
        self.assertTrue(integration.inspect(self.home, 'desktop')['bash_sandbox_disabled'])
        # Re-plan sans le drapeau : l'état persisté est conservé (plan sans changement).
        self.assertEqual(integration.plan(self.root, self.home, 'desktop', self.project)['changes'], [])
        with patch.object(integration.subprocess, 'run', return_value=self.bridge_probe()):
            integration.verify(self.home, 'desktop', root=self.root, project=self.project)
        integration.apply(self.root, integration.plan(self.root, self.home, 'desktop', self.project, 'remove'))
        self.assertNotIn('bash-sandbox', self.config.read_text())

    def test_bash_sandbox_plugin_is_not_generated_by_default(self):
        self.install()
        self.assertNotIn('bash-sandbox', self.config.read_text())
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertNotIn('bash_sandbox_disabled', manifest)
        self.assertFalse(integration.inspect(self.home, 'desktop')['bash_sandbox_disabled'])
        entries = integration._bridge_entries(Path('/release'), '/state', ('reprise_backends',), '/project', 'desktop', self.managed)
        self.assertEqual([entry['id'] for entry in entries],
                         ['reprise-codex-expert', 'reprise-skill-filesystem'])
        self.assertNotIn('--desktop-socket', entries[0]['config']['args'])

    def test_bridge_entries_non_regression_with_new_options(self):
        entries = integration._bridge_entries(
            Path('/release'), '/state', ('reprise_backends', 'reprise_delegate'), '/project', 'desktop',
            self.managed, output_resume=True, output_resume_project='/resume', codex_workspace_write=True,
            desktop_socket='/tmp/quick.sock', bash_sandbox_disabled=True)
        self.assertEqual([entry['id'] for entry in entries],
                         ['reprise-codex-expert', 'reprise-skill-filesystem', 'reprise-output-resume', 'bash-sandbox'])
        args = entries[0]['config']['args']
        self.assertEqual(args[args.index('--desktop-socket') + 1], '/tmp/quick.sock')
        self.assertEqual(args[args.index('--project-root') + 1], '/project')
        self.assertEqual(args[args.index('--state') + 1], '/state')
        self.assertIn('--codex-workspace-write', args)
        disabled = entries[-1]
        self.assertTrue(disabled['disabled'])
        self.assertEqual(disabled['config'], {'timeoutMs': 90000, 'maxOutputBytes': 32000})
        with self.assertRaisesRegex(ValueError, 'Action ou capacité invalide'):
            integration.plan(self.root, self.home, 'desktop', self.project, bash_sandbox_disabled='yes')

    # Bloc tel que reformaté par Harness (YAML multi-lignes, folding `\`, style bloc),
    # copié du profil ~/.dsh avec des chemins génériques substitués.
    HARNESS_REFORMATTED_BLOCK = '''- insert:
    [
      {
          "id": "reprise-codex-expert",
          "name": "@deepseek-ai/dsh-mcp-client",
          "config":
            {
              "transport": "stdio",
              "serverName": "reprise_codex",
              "command": "<REPRISE>\\
                /integration-releases/f7d6baabc9484640c88018433ac92ed49b9884d5f122759820d4923a4beaa015/Contents/Resources/runti\\
                me/bin/python3",
              "args":
                [
                  "-E",
                  "-s",
                  "-B",
                  "<REPRISE>\\
                /integration-releases/f7d6baabc9484640c88018433ac92ed49b9884d5f122759820d4923a4beaa015/Contents/Resourc\\
                es/helpers/codex_expert_mcp.py",
                  "--project-root",
                  "<PROJECT>",
                  "--desktop-socket",
                  "<DESKTOP>/quick-entry.sock",
                  "--state",
                  "<REPRISE>/state",
                  "--tools",
                  "reprise_backends,reprise_requests,reprise_quotas,reprise_d\\
                    elegate,reprise_followup,reprise_reconcile"
                ],
              "toolCallTimeoutMs": 330000,
              "failOnStartupError": false,
              "reconnect": { "enabled": false }
            }
        },
      {
          "id": "reprise-skill-filesystem",
          "name": "@deepseek-ai/dsh-skill-filesystem",
          "config":
            {
              "providerName": "reprise-desktop",
              "includeDefaultRoots": false,
              "customSkillDirs": [ "<PROFILE>/.reprise/skills" ],
              "watch": false
            }
        },
      {
          "id": "reprise-output-resume",
          "name": "<REPRISE>\\
                /integration-releases/f7d6baabc9484640c88018433ac92ed49b9884d5f122759820d4923a4beaa015/Contents/Resources/helpers/harne\\
                ss-output-resume.cjs",
          "config":
            {
              "enabled": true,
              "projectRoot": "<RESUME>",
              "stateDirectory": "<REPRISE>/state/harness-output-resume",
              "maxConsecutive": 2
            }
        }
    ]
- id: bash-sandbox
  name: "@deepseek-ai/dsh-bash-sandbox"
  disabled: true
  config:
    timeoutMs: 90000
    maxOutputBytes: 32000
'''

    RELEASE_NAME = 'f7d6baabc9484640c88018433ac92ed49b9884d5f122759820d4923a4beaa015'

    def _fixture_block(self):
        return self.HARNESS_REFORMATTED_BLOCK.replace('<REPRISE>', str(self.base / 'Reprise')) \
            .replace('<PROJECT>', str(self.project)).replace('<PROFILE>', str(self.folder)) \
            .replace('<DESKTOP>', str(self.base / '.claude-desktop')) \
            .replace('<RESUME>', str(self.base / 'resume'))

    def _expected_fixture_entries(self):
        release = self.base / 'Reprise' / 'integration-releases' / self.RELEASE_NAME
        return integration._bridge_entries(
            release, str(self.base / 'Reprise' / 'state'),
            ('reprise_backends', 'reprise_requests', 'reprise_quotas',
             'reprise_delegate', 'reprise_followup', 'reprise_reconcile'),
            str(self.project), 'desktop', self.managed,
            output_resume=True, output_resume_project=str(self.base / 'resume'),
            desktop_socket=str(self.base / '.claude-desktop' / 'quick-entry.sock'),
            bash_sandbox_disabled=True)

    def test_block_entries_parses_harness_reformatted_yaml_fixture(self):
        block = integration.START + '\n' + self._fixture_block() + integration.END + '\n'
        parsed = integration.block_entries(block)
        expected = self._expected_fixture_entries()
        self.assertEqual([entry['id'] for entry in parsed], [entry['id'] for entry in expected])
        # Égalité sémantique stricte, ordre inclus (les Paths des entries générées sont comparés en str).
        self.assertEqual(parsed, expected)

    def test_block_entries_json_inline_non_regression(self):
        entries = [{'id': 'a', 'config': {'path': '/x', 'n': 1}}]
        for payload in ('- insert: ' + json.dumps(entries) + '\n',
                        '- insert:\n    ' + json.dumps(entries) + '\n'):
            self.assertEqual(integration.block_entries(integration.START + '\n' + payload + integration.END + '\n'),
                             entries)

    def test_block_entries_refuses_invalid_or_wrongly_shaped_yaml(self):
        for body in ('- insert: [\n  {"id": "a",\n  "broken":\n',
                     'just a scalar\n', '- insert:\n  - not a mapping: [\n'):
            with self.assertRaisesRegex(ValueError, 'illisible'):
                integration.block_entries(integration.START + '\n' + body + integration.END + '\n')

    def test_verify_accepts_harness_reformatted_block_without_rewrite(self):
        probe = self.bridge_probe(delegate=True)
        self.install(delegate=True, bash_sandbox_disabled=True)
        _, original_block = integration.split_block(self.config.read_text())
        entries = integration.block_entries(original_block)
        bridge = json.dumps([entry for entry in entries if entry['id'] != 'bash-sandbox'], indent=2)
        sandbox = next(entry for entry in entries if entry['id'] == 'bash-sandbox')
        body = '- insert:\n' + '\n'.join('    ' + line for line in bridge.splitlines()) + '\n'
        body += '- id: bash-sandbox\n  name: ' + json.dumps(sandbox['name']) + '\n  disabled: true\n  config:\n'
        body += '    timeoutMs: 90000\n    maxOutputBytes: 32000\n'
        base, _ = integration.split_block(self.config.read_text())
        self.config.write_text(base + integration.START + '\n' + body + integration.END + '\n')
        with patch.object(integration.subprocess, 'run', return_value=probe):
            result = integration.verify(self.home, 'desktop', root=self.root, project=self.project)
        self.assertEqual(result['status'], 'verified')
        self.assertIn('block_reconciled', result)
        # verify() ne réécrit pas : le bloc reformatté reste en place.
        self.assertEqual(integration.split_block(self.config.read_text())[1],
                         integration.START + '\n' + body + integration.END + '\n')

    def test_claude_code_options_roundtrip_and_disable(self):
        program = str(self.base / 'claude-fixture')
        (self.base / 'claude-fixture').write_text('#!/bin/sh\n')
        os.chmod(program, 0o755)
        with self.assertRaises(ValueError):
            integration.plan(self.root, self.home, 'desktop', self.project, claude_quota_confirmed=True)
        with self.assertRaises(ValueError):
            integration.plan(self.root, self.home, 'desktop', self.project, claude_program='claude')
        proposed = integration.plan(self.root, self.home, 'desktop', self.project,
                                    delegate=True, claude_program=program,
                                    claude_quota_confirmed=True)
        self.assertEqual(proposed['claude_program'], program)
        self.assertTrue(proposed['claude_quota_confirmed'])
        integration.apply(self.root, proposed)
        text = self.config.read_text()
        self.assertIn('--claude-program', text)
        self.assertIn('--claude-included-quota-confirmed', text)
        inspected = integration.inspect(self.home, 'desktop')
        self.assertEqual(inspected['claude_program'], program)
        self.assertTrue(inspected['claude_quota_confirmed'])
        # Update omitting the options keeps the installed values.
        carried = integration.plan(self.root, self.home, 'desktop', self.project, delegate=True)
        self.assertEqual(carried['claude_program'], program)
        self.assertTrue(carried['claude_quota_confirmed'])
        self.assertFalse(carried['changes'])
        # Reinstall after removal = disabled (old manifest gone).
        removed = integration.plan(self.root, self.home, 'desktop', self.project, 'remove')
        integration.apply(self.root, removed)
        self.assertNotIn('--claude-program', self.config.read_text())
        self.assertFalse(integration.inspect(self.home, 'desktop')['claude_quota_confirmed'])

    def test_claude_code_invalid_settings_are_refused(self):
        self.install(delegate=True)
        with self.assertRaisesRegex(ValueError, 'Programme Claude'):
            integration.plan(self.root, self.home, 'desktop', self.project, delegate=True,
                             claude_program='claude')
        missing = self.base / 'claude-absent'
        with self.assertRaisesRegex(ValueError, 'Programme Claude'):
            integration.plan(self.root, self.home, 'desktop', self.project, delegate=True,
                             claude_program=str(missing))
        not_executable = self.base / 'claude-noexec'
        not_executable.write_text('#!/bin/sh\n')
        os.chmod(not_executable, 0o644)
        with self.assertRaisesRegex(ValueError, 'Programme Claude'):
            integration.plan(self.root, self.home, 'desktop', self.project, delegate=True,
                             claude_program=str(not_executable))
        with self.assertRaisesRegex(ValueError, 'Confirmation du quota'):
            integration.plan(self.root, self.home, 'desktop', self.project, delegate=True,
                             claude_quota_confirmed=True)
        with self.assertRaisesRegex(ValueError, 'Action ou capacité invalide'):
            integration.plan(self.root, self.home, 'desktop', self.project, delegate=True,
                             claude_program='claude', claude_quota_confirmed='yes')

    def test_claude_code_plan_tamper_and_staleness_are_refused(self):
        program = str(self.base / 'claude-fixture')
        (self.base / 'claude-fixture').write_text('#!/bin/sh\n')
        os.chmod(program, 0o755)
        proposed = integration.plan(self.root, self.home, 'desktop', self.project,
                                    delegate=True, claude_program=program,
                                    claude_quota_confirmed=True)
        integration.apply(self.root, proposed)
        tampered = dict(proposed, claude_program=str(self.base / 'claude-other'))
        with self.assertRaisesRegex(ValueError, 'Plan altéré'):
            integration.apply(self.root, tampered)
        # Staleness: plan after install re-derives from the manifest, so a new
        # plan omitting the options must carry the installed values forward.
        current = integration.plan(self.root, self.home, 'desktop', self.project, delegate=True,
                                   claude_program=program, claude_quota_confirmed=True)
        self.assertEqual(current['claude_program'], program)
        self.assertTrue(current['claude_quota_confirmed'])
        integration.apply(self.root, current)
        with self.assertRaisesRegex(ValueError, 'Plan altéré'):
            integration.apply(self.root, dict(current, claude_quota_confirmed=False))

    def test_reformatted_block_preserves_claude_code_settings(self):
        program = str(self.base / 'claude-fixture')
        (self.base / 'claude-fixture').write_text('#!/bin/sh\n')
        os.chmod(program, 0o755)
        self.install(delegate=True, claude_program=program, claude_quota_confirmed=True)
        self.config.write_text(self.reformatted_config())
        old_block = self.config.read_text()[self.config.read_text().index(integration.START):]
        proposed = self.plan()
        self.assertEqual(proposed['block_reconciled']['from_sha256'],
                         integration.digest(old_block.encode()))
        integration.apply(self.root, proposed)
        inspected = integration.inspect(self.home, 'desktop')
        self.assertEqual(inspected['claude_program'], program)
        self.assertTrue(inspected['claude_quota_confirmed'])
        text = self.config.read_text()
        self.assertIn('--claude-program', text)
        self.assertIn('--claude-included-quota-confirmed', text)

    def test_claude_code_and_workspace_write_are_independent(self):
        program = str(self.base / 'claude-fixture')
        (self.base / 'claude-fixture').write_text('#!/bin/sh\n')
        os.chmod(program, 0o755)
        self.install(delegate=True, claude_program=program, claude_quota_confirmed=True)
        self.assertIn('--claude-program', self.config.read_text())
        self.assertNotIn('--codex-workspace-write', self.config.read_text())
        self.install(delegate=True, claude_program=program, claude_quota_confirmed=True,
                     codex_workspace_write=True)
        text = self.config.read_text()
        self.assertIn('--claude-program', text)
        self.assertIn('--codex-workspace-write', text)
        # Revoking workspace writes alone must not touch the claude settings.
        revoked = integration.plan(self.root, self.home, 'desktop', self.project, delegate=True,
                                   codex_workspace_write=False)
        integration.apply(self.root, revoked)
        text = self.config.read_text()
        self.assertIn('--claude-program', text)
        self.assertNotIn('--codex-workspace-write', text)
        inspected = integration.inspect(self.home, 'desktop')
        self.assertEqual(inspected['claude_program'], program)
        self.assertTrue(inspected['claude_quota_confirmed'])
        self.assertFalse(inspected['codex_workspace_write'])


if __name__ == '__main__':
    unittest.main()
