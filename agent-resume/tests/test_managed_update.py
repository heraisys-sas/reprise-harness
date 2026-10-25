"""Disposable app/profile fixtures; never starts a client, service or model."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import managed_update as update
import package_helpers
import harness_integration as integration
import release_build


class ManagedUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.home = self.base / 'home'
        self.home.mkdir()
        self.state = self.home / 'Library/Application Support/Reprise/state'
        self.state.mkdir(parents=True)
        (self.state / 'valuable-result').write_text('preserve result and uncertainty barrier')
        self.source = self.fixture(self.base / 'source/Reprise.app', 'new')
        self.target = self.fixture(self.home / 'Applications/Reprise.app', 'old')
        self.running = patch.object(update, 'app_running', return_value=False)
        self.running.start();self.addCleanup(self.running.stop)

    def fixture(self, app, marker):
        helpers = package_helpers.package(SOURCE, app)
        runtime = helpers.parent / 'runtime'
        (runtime / 'bin').mkdir(parents=True)
        python = runtime / 'bin/python3'
        python.write_text('#!/bin/sh\nexit 99\n');python.chmod(0o700)
        (runtime / 'runtime-manifest.json').write_text(json.dumps({'files': integration.tree_files(runtime)}))
        payload = json.loads((helpers / 'payload-manifest.json').read_text())
        payload['runtime'] = {'fixture': True}
        (helpers / 'payload-manifest.json').write_text(json.dumps(payload))
        (app / 'Contents/marker').write_text(marker)
        proof = {'format': 1, 'git_commit': 'fixture', 'bundle': release_build.bundle_files(app)}
        (app / 'Contents/Resources/build-evidence.json').write_text(json.dumps(proof))
        return app

    def plan(self, **kwargs):
        return update.plan(self.source, self.target, self.home, self.state, **kwargs)

    def receipt(self, value):
        return update.operation(value) / 'receipt.json'

    def test_plan_is_read_only_and_contains_no_user_content(self):
        before = integration.tree_files(self.home)
        value = self.plan()
        self.assertEqual(integration.tree_files(self.home), before)
        self.assertNotIn('preserve result', json.dumps(value))
        self.assertFalse(update.operation(value).exists())
        self.assertEqual(value['components'], ['app'])

    def test_invalid_or_tampered_bundle_rejected_before_replacement(self):
        before = integration.tree_files(self.target)
        (self.source / 'Contents/marker').write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'Bundle modifié'):
            self.plan()
        self.assertEqual(integration.tree_files(self.target), before)

    def test_destination_drift_refused_before_backup(self):
        value = self.plan()
        (self.target / 'Contents/unexpected').write_text('user change')
        with self.assertRaisesRegex(ValueError, 'périmé'):
            update.apply(value)
        self.assertTrue((self.target / 'Contents/unexpected').exists())
        self.assertFalse((update.operation(value) / 'app-before.app').exists())

    def test_fingerprint_tampering_refused(self):
        value = self.plan();value['destination'] = str(self.home)
        with self.assertRaisesRegex(ValueError, 'altéré'):
            update.apply(value)

    def test_running_app_is_not_stopped_or_replaced(self):
        value = self.plan()
        with patch.object(update,'app_running',return_value=True), patch.object(update.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError, 'Reprise est ouvert'):
                update.apply(value)
        run.assert_not_called()
        self.assertEqual((self.target/'Contents/marker').read_text(),'old')

    def test_apply_verify_repeat_and_cleanup_preserve_state_and_unrelated_backups(self):
        value = self.plan();receipt = self.receipt(value)
        unrelated = self.state.parent / 'maintenance/unrelated'
        unrelated.mkdir(parents=True);(unrelated/'keep').write_text('keep')
        with patch.object(update.subprocess,'run') as run:
            update.apply(value);update.apply(value)
        run.assert_not_called()
        self.assertEqual((self.target/'Contents/marker').read_text(),'new')
        self.assertEqual((self.state/'valuable-result').read_text(),'preserve result and uncertainty barrier')
        self.assertTrue(update.verify(receipt)['ok'])
        result=update.cleanup(receipt)
        self.assertEqual(len(result['removed']),1)
        self.assertTrue((unrelated/'keep').exists())
        self.assertEqual(update.cleanup(receipt)['removed'],[])
        self.assertEqual(update.verify(receipt)['client_loaded'],'unknown')

    def test_interruption_after_backup_recovers_without_overwriting_backup(self):
        value=self.plan();original=update.remember_backup
        def fail_after_rename(receipt,path):
            original(receipt,path)
            raise OSError('fixture crash after old app rename')
        with patch.object(update,'remember_backup',side_effect=fail_after_rename):
            with self.assertRaises(OSError):update.apply(value)
        self.assertFalse(self.target.exists())
        backup=update.operation(value)/'app-before.app'
        self.assertEqual((backup/'Contents/marker').read_text(),'old')
        update.apply(value)
        self.assertEqual((backup/'Contents/marker').read_text(),'old')
        self.assertEqual((self.target/'Contents/marker').read_text(),'new')

    def test_interruption_after_publication_recovers_exactly_once(self):
        value=self.plan();original=Path.rename
        def rename_then_fail(path,target):
            result=original(path,target)
            if target==self.target and path.name.startswith('.Reprise-'):
                raise OSError('fixture lost publication reply')
            return result
        with patch.object(Path,'rename',rename_then_fail):
            with self.assertRaises(OSError):update.apply(value)
        self.assertEqual((self.target/'Contents/marker').read_text(),'new')
        update.apply(value)
        self.assertEqual(len(json.loads(self.receipt(value).read_text())['backups']),1)
        self.assertTrue(update.verify(self.receipt(value))['ok'])

    def test_changed_backup_blocks_all_cleanup(self):
        value=self.plan();update.apply(value)
        backup=update.operation(value)/'app-before.app'
        (backup/'valuable').write_text('do not remove')
        with self.assertRaisesRegex(ValueError,'Sauvegarde modifiée'):
            update.cleanup(self.receipt(value))
        self.assertTrue((backup/'valuable').exists())

    def test_new_log_errors_keep_backups_but_old_errors_do_not_block(self):
        log=self.state/'service-error.log';log.write_text('old Traceback\n')
        value=self.plan();update.apply(value)
        self.assertTrue(update.verify(self.receipt(value))['ok'])
        with log.open('a') as out:out.write('new Traceback\n')
        self.assertFalse(update.verify(self.receipt(value))['ok'])
        with self.assertRaisesRegex(ValueError,'logs non concluants'):
            update.cleanup(self.receipt(value))
        self.assertTrue((update.operation(value)/'app-before.app').exists())

    def test_outside_backup_and_symlink_refused(self):
        value=self.plan();update.apply(value);file=self.receipt(value)
        receipt=json.loads(file.read_text())
        receipt['backups']=[{'path':str(self.state),'inventory':update.fingerprint(update.inventory(self.state))}]
        file.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError,'hors périmètre'):
            update.cleanup(file)
        self.assertTrue(self.state.is_dir())
        link=self.base/'link';link.symlink_to(self.home,target_is_directory=True)
        with self.assertRaisesRegex(ValueError,'symbolique'):
            update.safe(link/'any')

    def test_cleanup_does_not_depend_on_disposable_source(self):
        value=self.plan();update.apply(value);shutil.rmtree(self.source)
        self.assertTrue(update.verify(self.receipt(value))['ok'])
        self.assertEqual(update.cleanup(self.receipt(value))['status'],'cleaned')

    def test_explicit_relaunch_is_the_only_launch_action(self):
        value=self.plan(relaunch=True)
        with patch.object(update.subprocess,'run') as run:
            update.apply(value);update.apply(value)
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.args[0],['/usr/bin/open','-a',str(self.target)])

    def test_target_changed_during_copy_is_preserved(self):
        value=self.plan();original=shutil.copytree
        def copy_then_change(*args,**kwargs):
            result=original(*args,**kwargs)
            if Path(args[0])==self.source:
                (self.target/'Contents/user-edit').write_text('concurrent')
            return result
        with patch.object(update.shutil,'copytree',side_effect=copy_then_change):
            with self.assertRaisesRegex(ValueError,'pendant la préparation'):
                update.apply(value)
        self.assertEqual((self.target/'Contents/user-edit').read_text(),'concurrent')
        self.assertEqual((self.target/'Contents/marker').read_text(),'old')
        self.assertFalse((update.operation(value)/'app-before.app').exists())

    def test_completed_app_drift_is_not_ignored_on_resume(self):
        value=self.plan();update.apply(value)
        (self.target/'Contents/user-change').write_text('preserve')
        with self.assertRaisesRegex(ValueError,'déjà publiée modifiée'):
            update.apply(value)
        self.assertTrue((self.target/'Contents/user-change').exists())

    def test_existing_harness_capabilities_and_project_are_preserved(self):
        profile=self.home/'.dsh/profiles/desktop'
        profile.mkdir(parents=True);(profile/'cordis.patch.yml').write_text('[]\n')
        (self.state/'tasks.sqlite').touch()
        helpers=self.source/'Contents/Resources/helpers'
        with update.settings(self.state):
            original=integration.plan(helpers,self.home/'.dsh','desktop',self.base,
                delegate=True,agent_role='coordinator',output_resume=True,
                output_resume_project=self.home,codex_workspace_write=True)
            integration.apply(helpers,original)
        value=self.plan(components=('harness',),profiles=('desktop',))
        selected=value['nested']['harness'][0]
        self.assertTrue(selected['delegate'])
        self.assertEqual(selected['agent_role'],'coordinator')
        self.assertTrue(selected['output_resume'])
        self.assertEqual(selected['output_resume_project'],str(self.home))
        self.assertTrue(selected['codex_workspace_write'])
        self.assertEqual(selected['project'],str(self.base))
        self.assertEqual(selected['state'],str(self.state))

    def test_default_state_does_not_turn_quick_state_into_a_child(self):
        from app_paths import state_location
        with patch.object(Path,'home',return_value=self.home), \
             patch.dict(os.environ,{'AGENT_RESUME_STATE':str(self.state)}):
            with update.settings(self.state):
                self.assertNotIn('AGENT_RESUME_STATE',os.environ)
                self.assertEqual(state_location(self.source/'Contents/Resources/helpers','quick-state'),
                                 self.state.parent/'quick-state')
            self.assertEqual(os.environ['AGENT_RESUME_STATE'],str(self.state))

    def test_plan_refuses_any_implicit_claude_registry_change(self):
        old={'status':'installed','state':str(self.state),'quick_state':str(self.state.parent/'quick-state'),'project':str(self.base)}
        # This is a custom state under the actual HOME, so its implicit quick
        # layout would be a child. Never move an already registered sibling.
        with patch.object(update.claude,'metadata',return_value=old), \
             patch.object(update.claude,'plan') as native_plan:
            with self.assertRaisesRegex(ValueError,'changerait un registre'):
                self.plan(components=('claude',))
        native_plan.assert_not_called()

    def test_observed_daemon_binding_mismatch_blocks_success_and_cleanup(self):
        helpers=self.source/'Contents/Resources/helpers'
        with update.settings(self.state):
            initial=update.claude.plan(helpers,self.home,self.base)
            update.claude.apply(helpers,initial)
        value=self.plan(components=('app','claude'));update.apply(value)
        with patch.object(update.claude,'verify',return_value={'daemon':{'binding':'mismatch'}}):
            self.assertFalse(update.verify(self.receipt(value))['ok'])
            with self.assertRaisesRegex(ValueError,'Vérification ou logs'):
                update.cleanup(self.receipt(value))
        self.assertTrue((update.operation(value)/'app-before.app').exists())

    def test_scheduler_completion_requires_loaded_arguments_not_just_plist(self):
        nested={'home':str(self.home),'activate':True,'plist':str(self.home/'scheduler.plist'),'content':'<plist/>'}
        # Early journal mismatch cannot certify a completed native operation.
        with patch.object(update.scheduler,'read',return_value={'status':'pending'}), \
             patch.object(update.scheduler,'launch',return_value=type('Observed',(),{'returncode':0})()):
            self.assertFalse(update.component_complete('scheduler',nested))


if __name__ == '__main__':
    unittest.main()
