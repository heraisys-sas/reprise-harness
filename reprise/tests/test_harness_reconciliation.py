"""Dedicated adoption on synthetic profiles only; no client, provider or live home."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_harness_integration as fixture

integration = fixture.integration


class HarnessReconciliationTests(unittest.TestCase):
    setUp = fixture.HarnessIntegrationTests.setUp
    install = fixture.HarnessIntegrationTests.install

    def apply_plan(self, proposed):
        return integration.apply(self.root, proposed)

    def divergent(self, crlf=False):
        obsolete = self.base / 'obsolete-resume'
        obsolete.mkdir()
        self.install(delegate=True, output_resume=True, output_resume_project=obsolete)
        text = self.config.read_bytes().decode()
        span = integration._reconcile_span(text)
        entries = integration.block_entries(span.group())
        entries[0]['config']['command'] = '/synthetic/broken-concatenated-release/runtime/bin/python3'
        entries[0]['config']['args'][3] = '/synthetic/old-release/helpers/codex_expert_mcp.py'
        entries[0]['config']['args'].append('--codex-workspace-write')
        self.prefix = '# SYNTHETIC PRIVATE SETTING — not a repository default\r\n- insert: [{"id":"unrelated"}]\r\n'
        self.suffix = '\r\n# SYNTHETIC trailing settings\r\n- insert: [{"id":"tail"}]\r\n'
        block = integration.START + '\n- insert: ' + json.dumps(entries) + '\n' + integration.END + '\n'
        if crlf:
            block = block.replace('\n', '\r\n')
        self.config.write_bytes((self.prefix + block + self.suffix).encode())
        obsolete.rmdir()
        return integration.digest(block.encode())

    def reconcile(self, accepted=None):
        if accepted is None:
            accepted = integration.digest(integration._reconcile_span(self.config.read_bytes().decode()).group().encode())
        return integration.reconcile_plan(self.root, self.home, 'desktop', self.project,
                                           self.project, True, accepted)

    def test_explicit_plan_read_only_and_apply_preserves_outer_bytes_without_backups(self):
        accepted = self.divergent(crlf=True)
        before = integration.tree_files(self.folder)
        with self.assertRaises(ValueError):
            integration.plan(self.root, self.home, 'desktop', self.project, delegate=True)
        proposed = self.reconcile(accepted)
        self.assertEqual(integration.tree_files(self.folder), before)
        self.assertNotIn('SYNTHETIC PRIVATE SETTING', json.dumps(proposed))
        old_journal = (self.managed / 'operation.json').read_bytes()
        backups = integration.tree_files(self.managed / 'backups')
        with patch.object(integration.subprocess, 'run', side_effect=AssertionError('No subprocess permitted')):
            self.assertTrue(self.apply_plan(proposed)['changed'])
        text = self.config.read_bytes().decode()
        self.assertTrue(text.startswith(self.prefix)); self.assertTrue(text.endswith(self.suffix))
        manifest = json.loads((self.managed / 'managed.json').read_bytes())
        self.assertTrue(manifest['codex_workspace_write'])
        self.assertEqual(manifest['output_resume_project'], str(self.project))
        self.assertEqual(manifest['project'], str(self.project))
        entries = integration.block_entries(integration._reconcile_span(text).group())
        self.assertTrue(Path(entries[0]['config']['command']).is_file())
        self.assertEqual(entries[2]['config']['projectRoot'], str(self.project))
        self.assertIn('--codex-workspace-write', entries[0]['config']['args'])
        self.assertEqual(integration.tree_files(self.managed / 'backups'), backups)
        self.assertEqual((self.managed / 'operation.json').read_bytes(), old_journal)
        journal = (self.managed / 'reconciliation.json').read_text()
        self.assertNotIn('SYNTHETIC PRIVATE SETTING', journal)
        self.assertNotIn('/synthetic/broken-concatenated-release', journal)
        self.assertNotIn('backup', json.loads(journal))
        self.assertNotIn('target_manifest', json.loads(journal))
        snapshot = integration.tree_files(self.folder)
        self.assertFalse(self.apply_plan(proposed)['changed'])
        self.assertEqual(integration.tree_files(self.folder), snapshot)
        # The ordinary route now recognizes ownership, with the write choice still explicit.
        integration.plan(self.root, self.home, 'desktop', self.project, delegate=True,
                         output_resume=True, output_resume_project=self.project, codex_workspace_write=True)

    def test_choices_and_accepted_block_are_required(self):
        accepted = self.divergent()
        for write, sha in ((None, accepted), (True, '0'*64)):
            with self.subTest(write=write):
                before = integration.tree_files(self.folder)
                with self.assertRaises(ValueError):
                    integration.reconcile_plan(self.root, self.home, 'desktop', self.project, self.project, write, sha)
                self.assertEqual(integration.tree_files(self.folder), before)
        with self.assertRaises((ValueError, OSError)):
            integration.reconcile_plan(self.root, self.home, 'desktop', self.project, self.base/'missing', True, accepted)

    def test_unknown_semantic_change_is_never_adopted(self):
        self.divergent()
        self.config.write_bytes(self.config.read_bytes().replace(b'630000', b'630001'))
        before = integration.tree_files(self.folder)
        with self.assertRaisesRegex(ValueError, 'hors chemins'):
            self.reconcile()
        self.assertEqual(integration.tree_files(self.folder), before)

    def test_config_manifest_and_both_journals_are_compared_before_write(self):
        self.divergent()
        proposed = self.reconcile()
        paths = [self.config, self.managed/'managed.json', self.managed/'operation.json', self.managed/'reconciliation.json']
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes() if path.exists() else None
                if path == self.config:
                    path.write_bytes(original + b'# concurrent\n')
                elif path.name == 'reconciliation.json':
                    path.write_text('{"format":1,"status":"completed"}')
                else:
                    value = json.loads(original); value['concurrent'] = True; path.write_text(json.dumps(value))
                before = integration.tree_files(self.folder)
                with self.assertRaises(ValueError):
                    self.apply_plan(proposed)
                self.assertEqual(integration.tree_files(self.folder), before)
                if original is None: path.unlink()
                else: path.write_bytes(original)

    def test_crash_after_config_recovery_and_other_plan_refusal(self):
        self.divergent(); proposed = self.reconcile()
        original = integration._reconcile_atomic
        def interrupt(path, text, metadata):
            if path == self.managed/'managed.json': raise OSError('synthetic crash')
            original(path, text, metadata)
        with patch.object(integration, '_reconcile_atomic', side_effect=interrupt):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        self.assertEqual(integration.inspect(self.home, 'desktop')['pending_plan'], proposed)
        with self.assertRaises(ValueError): self.reconcile()
        with self.assertRaises(ValueError): integration.plan(self.root, self.home, 'desktop', self.project)
        changed = dict(proposed, codex_workspace_write=False)
        changed['fingerprint'] = integration.digest(integration.encoded({k:v for k,v in changed.items() if k!='fingerprint'}).encode())
        with self.assertRaisesRegex(ValueError, 'Autre'): self.apply_plan(changed)
        published_config = self.config.read_bytes()
        writes = []
        def observe(path, text, metadata):
            writes.append(path); original(path, text, metadata)
        with patch.object(integration, '_reconcile_atomic', side_effect=observe):
            self.apply_plan(proposed)
        self.assertNotIn(self.config, writes)
        self.assertEqual(self.config.read_bytes(), published_config)

    def test_conflict_after_interruption_stays_pending_without_rollback(self):
        self.divergent(); proposed = self.reconcile(); original = integration._reconcile_atomic
        def interrupt(path, text, metadata):
            if path == self.managed/'managed.json': raise OSError('synthetic crash')
            original(path, text, metadata)
        with patch.object(integration, '_reconcile_atomic', side_effect=interrupt):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        self.config.write_bytes(self.config.read_bytes()+b'# newer setting\n')
        before = integration.tree_files(self.folder)
        with self.assertRaisesRegex(ValueError, 'Conflit'): self.apply_plan(proposed)
        self.assertEqual(integration.tree_files(self.folder), before)

    def test_completed_operation_is_not_replayed_after_restore(self):
        self.divergent(); proposed = self.reconcile(); before = self.config.read_bytes()
        self.apply_plan(proposed)
        self.config.write_bytes(before)
        state = integration.tree_files(self.folder)
        with self.assertRaisesRegex(ValueError, 'aucun rejeu'): self.apply_plan(proposed)
        self.assertEqual(integration.tree_files(self.folder), state)

    def test_journal_target_tampering_refused(self):
        self.divergent(); proposed = self.reconcile(); original = integration._reconcile_atomic
        def interrupt(path, text, metadata):
            if path == self.config: raise OSError('synthetic crash before config')
            original(path, text, metadata)
        with patch.object(integration, '_reconcile_atomic', side_effect=interrupt):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        path = self.managed/'reconciliation.json'; value=json.loads(path.read_text())
        value['target_block'] += '# altered'; path.write_text(json.dumps(value))
        before=integration.tree_files(self.folder)
        with self.assertRaisesRegex(ValueError, 'altérée'): self.apply_plan(proposed)
        self.assertEqual(integration.tree_files(self.folder), before)

    def test_symlink_config_and_ambiguous_markers_refused(self):
        self.divergent(); original=self.config.read_bytes()
        outside=self.base/'unmanaged.yml'; outside.write_bytes(original)
        self.config.unlink(); self.config.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symbolique'): self.reconcile()
        self.config.unlink(); self.config.write_bytes(original + ('\n'+integration.START+'\n').encode())
        with self.assertRaises(ValueError): self.reconcile()

    def test_ordinary_pending_journal_blocks_adoption(self):
        self.divergent(); path=self.managed/'operation.json'
        journal=json.loads(path.read_text()); journal['status']='pending'; path.write_text(json.dumps(journal))
        with self.assertRaisesRegex(ValueError, 'ordinaire partielle'): self.reconcile()

    def test_completion_crash_is_recovered_without_rewriting_published_files(self):
        self.divergent(); proposed = self.reconcile(); original = integration._reconcile_atomic
        def interrupt(path, text, metadata):
            if path.name == 'reconciliation.json' and json.loads(text)['status'] == 'completed':
                raise OSError('synthetic completion failure')
            original(path, text, metadata)
        with patch.object(integration, '_reconcile_atomic', side_effect=interrupt):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        writes = []
        def observe(path, text, metadata): writes.append(path); original(path, text, metadata)
        with patch.object(integration, '_reconcile_atomic', side_effect=observe):
            self.apply_plan(proposed)
        self.assertEqual(writes, [self.managed/'reconciliation.json'])

    def test_manifest_metadata_not_duplicated_in_minimal_journal(self):
        self.divergent()
        path = self.managed/'managed.json'; value=json.loads(path.read_text())
        value['empty_before'] = '# SYNTHETIC old metadata not to duplicate\n[]\n'
        path.write_text(json.dumps(value))
        proposed = self.reconcile()
        self.apply_plan(proposed)
        self.assertNotIn('SYNTHETIC old metadata', (self.managed/'reconciliation.json').read_text())
        self.assertIn('SYNTHETIC old metadata', (self.managed/'managed.json').read_text())

    def test_changes_during_release_staging_refuse_publication(self):
        self.divergent(); proposed=self.reconcile(); original=integration.stage_release
        before_manifest=(self.managed/'managed.json').read_bytes()
        def interleave(*args):
            original(*args); self.config.write_bytes(self.config.read_bytes()+b'# concurrent edit\n')
        with patch.object(integration, 'stage_release', side_effect=interleave):
            with self.assertRaisesRegex(ValueError, 'pr\u00e9paration du release'):
                self.apply_plan(proposed)
        self.assertEqual((self.managed/'managed.json').read_bytes(), before_manifest)
        self.assertFalse((self.managed/'reconciliation.json').exists())
        self.assertTrue(self.config.read_bytes().endswith(b'# concurrent edit\n'))

    def test_nonblocking_profile_lock_rejects_concurrent_apply(self):
        self.divergent(); proposed=self.reconcile(); before=integration.tree_files(self.folder)
        with integration.locked(self.managed):
            with self.assertRaisesRegex(ValueError, 'd\u00e9j\u00e0 active'): self.apply_plan(proposed)
        self.assertEqual(integration.tree_files(self.folder), before)

    def test_cli_requires_explicit_write_and_produces_only_a_plan(self):
        import contextlib
        import io
        accepted = self.divergent()
        args = ['integration-reconcile-plan', '--home', str(self.home), '--profile', 'desktop',
                '--project', str(self.project), '--output-resume-project', str(self.project),
                '--accept-block-sha256', accepted]
        before = integration.tree_files(self.folder)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            integration.main(self.root, args)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = integration.main(self.root, args + ['--codex-workspace-write'])
        self.assertEqual(code, 0)
        plan = json.loads(output.getvalue())['plan']
        self.assertTrue(plan['codex_workspace_write'])
        self.assertEqual(plan['output_resume_project'], str(self.project))
        self.assertEqual(integration.tree_files(self.folder), before)

    def test_missing_manifest_after_crash_blocks_instead_of_reconstructing_personal_state(self):
        self.divergent(); proposed=self.reconcile(); original=integration._reconcile_atomic
        def interrupt(path, text, metadata):
            if path == self.config: raise OSError('synthetic interruption')
            original(path, text, metadata)
        with patch.object(integration, '_reconcile_atomic', side_effect=interrupt):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        (self.managed/'managed.json').unlink()
        before=integration.tree_files(self.folder)
        with self.assertRaisesRegex(ValueError, 'Conflit manifeste'): self.apply_plan(proposed)
        self.assertEqual(integration.tree_files(self.folder), before)

    def test_initial_journal_failure_does_not_publish_config_or_manifest(self):
        self.divergent(); proposed=self.reconcile(); original=integration._reconcile_atomic
        before_config=self.config.read_bytes(); before_manifest=(self.managed/'managed.json').read_bytes()
        def fail(path, text, metadata):
            if path.name == 'reconciliation.json': raise OSError('synthetic journal fsync failure')
            original(path, text, metadata)
        with patch.object(integration, '_reconcile_atomic', side_effect=fail):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        self.assertEqual(self.config.read_bytes(), before_config)
        self.assertEqual((self.managed/'managed.json').read_bytes(), before_manifest)
        self.assertFalse((self.managed/'reconciliation.json').exists())

    def test_plan_states_exclusive_access_precondition_without_claiming_kernel_cas(self):
        self.divergent(); proposed=self.reconcile()
        self.assertEqual(proposed['concurrency'], 'operator-exclusive-access-required')

    def test_simple_posix_modes_survive_replacements(self):
        self.divergent()
        self.config.chmod(0o640); (self.managed/'managed.json').chmod(0o644)
        before=integration._simple_metadata(self.config)
        proposed=self.reconcile(); self.apply_plan(proposed)
        self.assertEqual(integration._simple_metadata(self.config), before)
        self.assertEqual(self.config.stat().st_mode & 0o7777, 0o640)
        self.assertEqual((self.managed/'managed.json').stat().st_mode & 0o7777, 0o644)

    def test_extended_metadata_refused_before_publication(self):
        import os
        self.divergent()
        name = 'com.reprise.synthetic' if os.uname().sysname == 'Darwin' else 'user.reprise.synthetic'
        if os.uname().sysname == 'Darwin':
            import ctypes
            library=ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
            library.setxattr.argtypes=[ctypes.c_char_p,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_int]
            self.assertEqual(library.setxattr(os.fsencode(self.config), name.encode(), b'synthetic', 9, 0, 0), 0)
        else:
            os.setxattr(self.config, name, b'synthetic')
        before=integration.tree_files(self.folder)
        with self.assertRaisesRegex(ValueError, 'Attributs étendus'): self.reconcile()
        with self.assertRaisesRegex(ValueError, 'Attributs étendus'):
            integration._provenance_fingerprint(self.config)
        self.assertEqual(integration.tree_files(self.folder), before)

    def test_extended_acl_refused_on_synthetic_file(self):
        import os
        import subprocess
        if os.uname().sysname != 'Darwin': self.skipTest('macOS ACL')
        self.divergent()
        subprocess.run(['chmod', '+a', 'everyone allow read', str(self.config)], check=True)
        with self.assertRaisesRegex(ValueError, 'ACL étendue'): self.reconcile()

    def test_directory_fsync_failure_after_config_rename_is_retried_on_recovery(self):
        self.divergent(); proposed=self.reconcile(); original_sync=integration._sync_directory
        fired=False
        def fail_once(path):
            nonlocal fired
            if path == self.config.parent and integration.file_hash(self.config) == proposed['after_config_sha256'] and not fired:
                fired=True; raise OSError('synthetic directory sync after config rename')
            original_sync(path)
        with patch.object(integration, '_sync_directory', side_effect=fail_once):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        self.assertTrue(fired)
        synced=[]
        def observe(path): synced.append(path); original_sync(path)
        with patch.object(integration, '_sync_directory', side_effect=observe):
            self.apply_plan(proposed)
        self.assertIn(self.config.parent, synced)

    def test_completed_journal_rename_fsync_failure_requires_resync_before_idempotent_success(self):
        self.divergent(); proposed=self.reconcile(); original_sync=integration._sync_directory
        def fail_completed(path):
            journal=self.managed/'reconciliation.json'
            if path == self.managed and journal.exists() and json.loads(journal.read_text())['status'] == 'completed':
                raise OSError('synthetic completion directory sync')
            original_sync(path)
        with patch.object(integration, '_sync_directory', side_effect=fail_completed):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        with patch.object(integration, '_sync_file', side_effect=OSError('synthetic resync fails')):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        self.assertFalse(self.apply_plan(proposed)['changed'])

    def test_release_tree_synced_before_publish_and_on_existing_release_retry(self):
        self.divergent(); proposed=self.reconcile()
        synced=[]; original=integration._sync_tree
        def observe(path): synced.append(path); original(path)
        with patch.object(integration, '_sync_tree', side_effect=observe): self.apply_plan(proposed)
        self.assertIn(Path(proposed['release']), synced)
        target=Path(proposed['release'])/'Contents/Resources/runtime/bin/python3'
        with patch.object(integration, '_sync_file', side_effect=lambda path: (_ for _ in ()).throw(OSError('runtime fsync')) if path == target else None):
            with self.assertRaises(OSError): self.apply_plan(proposed)

    def test_new_release_files_synced_before_rename_and_parent_failure_retried(self):
        helpers, runtime, identity=integration.bundle_info(self.root)
        release=self.base/'fresh-releases'/identity
        files=[]; original_file=integration._sync_file; original_rename=Path.rename
        original_directory=integration._sync_directory; original_tree=integration._sync_tree
        test=self
        def sync_file(path): files.append(path); original_file(path)
        def rename(path, target):
            if target == release:
                test.assertIn(path/'Contents/Resources/runtime/bin/python3', files)
                test.assertIn(path/'release.json', files)
            return original_rename(path, target)
        def fail_after_publish(path):
            if path == release.parent and release.exists(): raise OSError('synthetic release publication sync')
            original_directory(path)
        with patch.object(integration, '_sync_file', side_effect=sync_file), patch.object(Path, 'rename', new=rename), patch.object(integration, '_sync_directory', side_effect=fail_after_publish):
            with self.assertRaises(OSError): integration.stage_release(helpers, runtime, release)
        self.assertTrue(release.is_dir())
        trees=[]
        def sync_tree(path): trees.append(path); original_tree(path)
        with patch.object(integration, '_sync_tree', side_effect=sync_tree):
            integration.stage_release(helpers, runtime, release)
        self.assertIn(release, trees)

    def test_manifest_and_initial_journal_rename_sync_failures_are_not_acknowledged_early(self):
        self.divergent(); proposed=self.reconcile(); original=integration._sync_directory
        journal=self.managed/'reconciliation.json'
        def fail_initial_journal(path):
            if path == self.managed and journal.exists(): raise OSError('synthetic pending journal directory sync')
            original(path)
        with patch.object(integration, '_sync_directory', side_effect=fail_initial_journal):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        self.assertEqual(json.loads(journal.read_text())['status'], 'pending')
        manifest=self.managed/'managed.json'
        def fail_manifest(path):
            if path == self.managed and integration.file_hash(manifest) == proposed['after_manifest_sha256']:
                raise OSError('synthetic manifest directory sync')
            original(path)
        with patch.object(integration, '_sync_directory', side_effect=fail_manifest):
            with self.assertRaises(OSError): self.apply_plan(proposed)
        self.assertEqual(json.loads(journal.read_text())['status'], 'pending')
        self.apply_plan(proposed)
        self.assertEqual(json.loads(journal.read_text())['status'], 'completed')
