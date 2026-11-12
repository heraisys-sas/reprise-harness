"""App-only destructive replacement; entirely synthetic bundles and processes."""
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import test_managed_update as fixture

update = fixture.update
integration = fixture.integration


class AppNoBackupTests(unittest.TestCase):
    setUp = fixture.ManagedUpdateTests.setUp
    receipt = fixture.ManagedUpdateTests.receipt
    fixture = fixture.ManagedUpdateTests.fixture

    def plan(self, **kwargs):
        return update.plan(self.source, self.target, self.home, self.state, no_app_backup=True, **kwargs)

    def stage(self, value):
        return self.target.parent / ('.Reprise-' + value['id'] + '.app')

    def test_opt_in_read_only_then_replace_without_old_content_or_any_backup(self):
        secret='SYNTHETIC OLD CONTENT MUST NOT BE COPIED'
        (self.target/'Contents/marker').write_text(secret)
        before=integration.tree_files(self.home);value=self.plan()
        self.assertEqual(integration.tree_files(self.home),before)
        self.assertEqual(value['app_backup_policy'],'none')
        self.assertIn('Aucun rollback',json.dumps(value,ensure_ascii=False))
        with patch.object(update.subprocess,'run') as run:
            result=update.apply(value);update.apply(value)
        run.assert_not_called()
        self.assertEqual((self.target/'Contents/marker').read_text(),'new')
        receipt=json.loads(Path(result['receipt']).read_text())
        self.assertEqual(receipt['backups'],[])
        self.assertNotIn(secret,json.dumps(receipt))
        self.assertEqual(receipt['app_replacement']['phase'],'published')
        self.assertFalse(list(self.base.rglob('app-before.app')))
        self.assertFalse(self.stage(value).exists())
        self.assertTrue(update.verify(self.receipt(value))['ok'])
        self.assertEqual(update.cleanup(self.receipt(value))['removed'],[])
        self.assertEqual((self.state/'valuable-result').read_text(),'preserve result and uncertainty barrier')

    def test_default_plan_and_historical_backup_behavior_are_unchanged(self):
        value=update.plan(self.source,self.target,self.home,self.state)
        self.assertNotIn('app_backup_policy',value)
        update.apply(value)
        self.assertTrue((update.operation(value)/'app-before.app').is_dir())

    def test_scope_refuses_all_other_components_and_activation(self):
        for options in ({'components':('app','harness')},{'components':('scheduler',)},
                        {'components':('codex',)},{'profiles':('desktop',)},
                        {'activate':True},{'codex_entries':('reprise_codex',)}):
            with self.subTest(options=options),self.assertRaisesRegex(ValueError,'seul le composant app'):
                self.plan(**options)
        value=self.plan();value['components'].append('claude')
        value['fingerprint']=update.fingerprint({k:v for k,v in value.items() if k!='fingerprint'})
        with self.assertRaises(ValueError):update.apply(value)
        self.assertEqual((self.target/'Contents/marker').read_text(),'old')

    def test_app_open_before_or_during_stage_never_stops_or_deletes_it(self):
        value=self.plan()
        with patch.object(update,'app_running',return_value=True),patch.object(update.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'Reprise est ouvert'):update.apply(value)
        run.assert_not_called();self.assertEqual((self.target/'Contents/marker').read_text(),'old')
        with patch.object(update,'app_running',side_effect=[False,True]):
            with self.assertRaisesRegex(ValueError,'Reprise est ouvert'):update.apply(value)
        self.assertEqual((self.target/'Contents/marker').read_text(),'old')
        self.assertTrue(self.stage(value).is_dir())
        update.apply(value)

    def test_verified_stage_durability_failure_precedes_any_old_unlink(self):
        value=self.plan();original=integration._sync_tree
        def fail_stage(path):
            if path==self.stage(value):raise OSError('synthetic stage fsync')
            original(path)
        with patch.object(integration,'_sync_tree',side_effect=fail_stage):
            with self.assertRaises(OSError):update.apply(value)
        self.assertEqual((self.target/'Contents/marker').read_text(),'old')
        self.assertNotIn('app_replacement',json.loads(self.receipt(value).read_text()))
        update.apply(value)

    def test_partial_old_unlink_recovers_only_unchanged_remaining_inventory(self):
        value=self.plan();original=os.unlink;fired=False
        def unlink_then_crash(name,*args,**kwargs):
            nonlocal fired
            result=original(name,*args,**kwargs)
            if kwargs.get('dir_fd') is not None and not fired:
                fired=True;raise OSError('synthetic partial deletion')
            return result
        with patch.object(update.os,'unlink',new=unlink_then_crash):
            with self.assertRaises(OSError):update.apply(value)
        self.assertTrue(fired);self.assertTrue(self.stage(value).is_dir())
        self.assertEqual(json.loads(self.receipt(value).read_text())['app_replacement']['phase'],'removing')
        update.apply(value)
        self.assertEqual((self.target/'Contents/marker').read_text(),'new')

    def test_partial_delete_then_new_content_blocks_without_further_removal(self):
        value=self.plan();original=update._remove_owned_app
        def partial(target,owned,directories):
            (target/'Contents/marker').unlink();raise OSError('synthetic partial')
        with patch.object(update,'_remove_owned_app',side_effect=partial):
            with self.assertRaises(OSError):update.apply(value)
        (self.target/'Contents/new-user-data').write_text('must stay')
        before=integration.tree_files(self.target)
        with self.assertRaisesRegex(ValueError,'divergent'):update.apply(value)
        self.assertEqual(integration.tree_files(self.target),before)

    def test_absent_target_after_delete_is_recovered_from_stage_same_receipt(self):
        value=self.plan();original=update._remove_owned_app
        def remove_then_crash(*args):
            original(*args);raise OSError('synthetic crash old target absent')
        with patch.object(update,'_remove_owned_app',side_effect=remove_then_crash):
            with self.assertRaises(OSError):update.apply(value)
        self.assertFalse(self.target.exists());self.assertTrue(self.stage(value).exists())
        self.assertFalse((update.operation(value)/'app-before.app').exists())
        update.apply(value)
        self.assertEqual((self.target/'Contents/marker').read_text(),'new')

    def test_missing_or_tampered_stage_after_delete_refuses_without_rollback(self):
        value=self.plan();original=update._remove_owned_app
        def remove_then_crash(*args):original(*args);raise OSError('synthetic crash')
        with patch.object(update,'_remove_owned_app',side_effect=remove_then_crash):
            with self.assertRaises(OSError):update.apply(value)
        (self.stage(value)/'Contents/marker').write_text('tamper')
        with self.assertRaisesRegex(ValueError,'Stage inconnu'):update.apply(value)
        self.assertFalse(self.target.exists())

    def test_absent_target_without_destructive_intent_is_not_adopted(self):
        import shutil
        value=self.plan()
        with patch.object(update,'app_running',return_value=True):
            with self.assertRaises(ValueError):update.apply(value)
        shutil.rmtree(self.target)
        with self.assertRaisesRegex(ValueError,'modifiée depuis'):update.apply(value)
        self.assertFalse(self.target.exists())

    def test_publication_lost_reply_and_parent_sync_failure_recover_without_redelete(self):
        value=self.plan();original=Path.rename
        def rename_then_crash(path,target):
            result=original(path,target)
            if path==self.stage(value):raise OSError('synthetic publication response lost')
            return result
        with patch.object(Path,'rename',new=rename_then_crash):
            with self.assertRaises(OSError):update.apply(value)
        self.assertTrue(self.target.is_dir());self.assertFalse(self.stage(value).exists())
        with patch.object(update,'_remove_owned_app',side_effect=AssertionError('Never delete published bundle')):
            update.apply(value)
        self.assertTrue(update.verify(self.receipt(value))['ok'])

    def test_uncertain_launch_is_recorded_before_open_and_never_replayed(self):
        value=self.plan(relaunch=True)
        with patch.object(update.subprocess,'run',side_effect=subprocess.TimeoutExpired('open',15)) as run:
            with self.assertRaises(subprocess.TimeoutExpired):update.apply(value)
        self.assertEqual(run.call_count,1)
        self.assertTrue(json.loads(self.receipt(value).read_text())['launch_requested'])
        with patch.object(update.subprocess,'run') as repeated:update.apply(value)
        repeated.assert_not_called()

    def test_launch_intent_sync_failure_never_sends_then_never_replays(self):
        value=self.plan(relaunch=True);original=update.sync_update_receipt
        def fail_after_intent(folder):
            if json.loads((folder/'receipt.json').read_text()).get('launch_requested'):
                raise OSError('synthetic launch receipt directory fsync')
            original(folder)
        with patch.object(update,'sync_update_receipt',side_effect=fail_after_intent),patch.object(update.subprocess,'run') as run:
            with self.assertRaises(OSError):update.apply(value)
        run.assert_not_called()
        with patch.object(update.subprocess,'run') as run:update.apply(value)
        run.assert_not_called()

    def test_completed_drift_is_not_replaced_and_forged_backup_is_never_cleaned(self):
        value=self.plan();update.apply(value)
        (self.target/'Contents/new-user-data').write_text('preserve')
        with self.assertRaisesRegex(ValueError,'déjà publiée modifiée'):update.apply(value)
        file=self.receipt(value);receipt=json.loads(file.read_text())
        receipt['backups']=[{'path':str(self.state),'inventory':update.fingerprint(update.inventory(self.state))}]
        file.write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError,'sans sauvegarde incohérent'):update.cleanup(file)
        self.assertTrue(self.state.is_dir())

    def test_special_nodes_and_nested_state_refused(self):
        os.mkfifo(self.target/'Contents/special-node')
        with self.assertRaisesRegex(ValueError,'Type spécial'):self.plan()
        (self.target/'Contents/special-node').unlink()
        with self.assertRaisesRegex(ValueError,'hors des bundles'):
            update.plan(self.source,self.target,self.home,self.target/'state',no_app_backup=True)

    def test_staged_recovery_survives_disposable_source_loss(self):
        import shutil
        value=self.plan();original=update._remove_owned_app
        def crash(*args):original(*args);raise OSError('synthetic missing target')
        with patch.object(update,'_remove_owned_app',side_effect=crash):
            with self.assertRaises(OSError):update.apply(value)
        shutil.rmtree(self.source)
        update.apply(value)
        self.assertEqual((self.target/'Contents/marker').read_text(),'new')
        self.assertTrue(update.verify(self.receipt(value))['ok'])

    def test_special_node_in_completed_bundle_refused_before_sync(self):
        value=self.plan();update.apply(value)
        os.mkfifo(self.target/'Contents/new-special-node')
        with self.assertRaisesRegex(ValueError,'Type spécial'):update.apply(value)

    def test_directory_sync_failure_after_publication_is_retried(self):
        value=self.plan();original=integration._sync_directory
        def fail_after_publish(path):
            if path==self.target.parent and (self.target/'Contents/marker').exists() and (self.target/'Contents/marker').read_text()=='new':
                raise OSError('synthetic target parent fsync')
            original(path)
        with patch.object(integration,'_sync_directory',side_effect=fail_after_publish):
            with self.assertRaises(OSError):update.apply(value)
        self.assertEqual((self.target/'Contents/marker').read_text(),'new')
        with patch.object(integration,'_sync_tree',side_effect=OSError('synthetic target resync failure')):
            with self.assertRaises(OSError):update.apply(value)
        update.apply(value)
        self.assertTrue(update.verify(self.receipt(value))['ok'])

    def test_runtime_inside_deleted_target_is_refused_before_receipt_or_unlink(self):
        value=self.plan()
        with patch.object(update.sys,'executable',str(self.target/'Contents/Resources/runtime/bin/python3')):
            with self.assertRaisesRegex(ValueError,'runtime indépendant'):update.apply(value)
        self.assertFalse(self.receipt(value).exists())
        self.assertEqual((self.target/'Contents/marker').read_text(),'old')

    def test_app_open_after_old_removal_blocks_publication_without_false_rollback(self):
        value=self.plan()
        with patch.object(update,'app_running',side_effect=[False,False,True]):
            with self.assertRaisesRegex(ValueError,'ancien bundle absent'):update.apply(value)
        self.assertFalse(self.target.exists());self.assertTrue(self.stage(value).is_dir())
        update.apply(value)
        self.assertEqual((self.target/'Contents/marker').read_text(),'new')

    def test_cli_reports_interrupted_absent_app_and_no_backup(self):
        import contextlib
        import io
        value=self.plan();file=self.base/'plan.json';file.write_text(json.dumps({'plan':value}))
        original=update._remove_owned_app
        def crash(*args):original(*args);raise OSError('synthetic deletion interruption')
        out=io.StringIO()
        with patch.object(update,'_remove_owned_app',side_effect=crash),contextlib.redirect_stdout(out):
            code=update.main(['apply','--plan',str(file)])
        result=json.loads(out.getvalue())
        self.assertEqual(code,1);self.assertEqual(result['status'],'interrupted')
        self.assertFalse(result['app_present']);self.assertFalse(result['rollback_available'])
        self.assertEqual(result['app_backup_policy'],'none')
        self.assertNotIn('sauvegardes conservées',result['recovery'])

    def test_substitution_after_inventory_check_is_compared_to_owned_digest(self):
        owned=update.inventory(self.target);directories=update.app_directories(self.target)
        original=update._remaining_owned_app;victim=self.target/'Contents/marker'
        def interleave(*args):
            original(*args);victim.write_text('NEW USER CONTENT AFTER INVENTORY')
        with patch.object(update,'_remaining_owned_app',side_effect=interleave):
            with self.assertRaisesRegex(ValueError,'modifié'):update._remove_owned_app(self.target,owned,directories)
        self.assertEqual(victim.read_text(),'NEW USER CONTENT AFTER INVENTORY')

    def test_added_file_after_inventory_is_not_adopted_as_expected(self):
        owned=update.inventory(self.target);directories=update.app_directories(self.target)
        original=update._remaining_owned_app;new=self.target/'Contents/zz-user-file'
        def interleave(*args):original(*args);new.write_text('MUST NOT DELETE')
        with patch.object(update,'_remaining_owned_app',side_effect=interleave):
            with self.assertRaises((ValueError,OSError)):update._remove_owned_app(self.target,owned,directories)
        self.assertEqual(new.read_text(),'MUST NOT DELETE')

    def test_parent_symlink_substitution_cannot_redirect_unlink_outside_bundle(self):
        owned=update.inventory(self.target);directories=update.app_directories(self.target)
        original=update._remaining_owned_app
        outside=self.base/'outside';outside.mkdir();(outside/'marker').write_text('old')
        def interleave(*args):
            original(*args)
            (self.target/'Contents').rename(self.target/'Contents-owned')
            (self.target/'Contents').symlink_to(outside,target_is_directory=True)
        with patch.object(update,'_remaining_owned_app',side_effect=interleave):
            with self.assertRaises((ValueError,OSError)):update._remove_owned_app(self.target,owned,directories)
        self.assertEqual((outside/'marker').read_text(),'old')
        self.assertEqual((self.target/'Contents-owned/marker').read_text(),'old')

    def test_unknown_empty_stage_directory_prevents_publication_and_is_not_cleaned(self):
        value=self.plan()
        with patch.object(update,'_remove_owned_app',side_effect=OSError('synthetic pause')):
            with self.assertRaises(OSError):update.apply(value)
        empty=self.stage(value)/'foreign-empty';empty.mkdir()
        with self.assertRaisesRegex(ValueError,'Stage inconnu'):update.apply(value)
        self.assertTrue(empty.is_dir());self.assertEqual((self.target/'Contents/marker').read_text(),'old')

    def test_matching_target_shortcut_retains_stage_and_validates_receipt_first(self):
        import shutil
        value=self.plan()
        with patch.object(update,'_remove_owned_app',side_effect=OSError('synthetic pause')):
            with self.assertRaises(OSError):update.apply(value)
        shutil.rmtree(self.target);shutil.copytree(self.stage(value),self.target,symlinks=True)
        empty=self.stage(value)/'foreign-empty';empty.mkdir()
        with self.assertRaisesRegex(ValueError,'Stage inconnu'):update.apply(value)
        self.assertTrue(empty.is_dir())
        empty.rmdir()
        receipt=json.loads(self.receipt(value).read_text());original=receipt['app_replacement']['old_inventory']
        receipt['app_replacement']['old_inventory']={'forged':'0'*64};self.receipt(value).write_text(json.dumps(receipt))
        with self.assertRaisesRegex(ValueError,'Reçu de remplacement altéré'):update.apply(value)
        receipt['app_replacement']['old_inventory']=original;self.receipt(value).write_text(json.dumps(receipt))
        update.apply(value)
        self.assertTrue(self.stage(value).is_dir())  # No recursive cleanup on the shortcut.
