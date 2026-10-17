from pathlib import Path
from types import SimpleNamespace
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
import engines
import scheduler
import shared_mode as shared

class SharedModeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=sqlite3.connect(':memory:');self.addCleanup(self.db.close)
        scheduler.setup(self.db);engines.setup(self.db)
        for target,value in [('shared_mode.desktop_version','v1'),('shared_mode.desktop_running',False),('shared_mode.probe',{'connected':True}),('shared_mode.subprocess.run',SimpleNamespace(returncode=0))]:
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch.object(engines,'SOCKET',self.root/'server.sock');p.start();self.addCleanup(p.stop)
    def test_preparation_pauses_automation_without_model_or_new_server(self):
        with patch('shared_mode.subprocess.run') as run:
            self.assertTrue(shared.prepare(self.db,'codex',self.root)['ok'])
            run.assert_not_called()
        self.assertFalse(scheduler.enabled(self.db))
    def test_daemon_start_only_when_socket_absent(self):
        with patch('shared_mode.probe',side_effect=[{'connected':False},{'connected':True}]),patch('shared_mode.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(shared.prepare(self.db,'codex',self.root)['ok'])
        self.assertEqual(run.call_args.args[0][:4],['codex','app-server','daemon','start'])
    def test_existing_inaccessible_socket_not_replaced(self):
        engines.SOCKET.write_text('not a socket')
        with patch('shared_mode.probe',return_value={'connected':False}),patch('shared_mode.subprocess.run') as run:
            self.assertFalse(shared.prepare(self.db,'codex',self.root)['ok']);run.assert_not_called()
    def test_running_desktop_not_quit_or_relaunched(self):
        with patch('shared_mode.desktop_running',return_value=True),patch('shared_mode.prepare') as prepare:
            self.assertFalse(shared.launch(self.db,'codex',self.root)['ok']);prepare.assert_not_called()
    def test_launch_uses_per_process_environment_without_global_changes(self):
        with patch('engines.socket_path',return_value=self.root/'actual.sock'),patch('shared_mode.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
            self.assertTrue(shared.launch(self.db,'codex',self.root)['ok'])
        args=run.call_args.args[0]
        self.assertEqual(args[:2],['/usr/bin/open','-a'])
        self.assertTrue(any(x.startswith('CODEX_APP_SERVER_WS_URL=ws+unix:///') for x in args))
        self.assertNotIn('-n',args)
    def test_chat_must_be_loaded_before_startup_eligible(self):
        with self.db:scheduler.put(self.db,'shared_mode',{'launched':True,'launch_version':'v1'})
        with patch('shared_mode.desktop_running',return_value=True),patch('shared_mode.probe',return_value={'connected':True,'thread_loaded':False,'reason':'Not loaded'}):
            self.assertFalse(shared.check(self.db,'codex','session')['ok'])
        self.assertFalse(shared.state(self.db)['startup_eligible'])
        with patch('shared_mode.desktop_running',return_value=True),patch('shared_mode.probe',return_value={'connected':True,'thread_loaded':True}):
            self.assertTrue(shared.check(self.db,'codex','session')['ok'])
        self.assertTrue(shared.state(self.db)['startup_eligible'])
    def test_updated_desktop_requires_new_validation(self):
        with self.db:scheduler.put(self.db,'shared_mode',{'verified_version':'old'})
        self.assertFalse(shared.state(self.db)['startup_eligible'])
        self.assertFalse(shared.prepare(self.db,'codex',self.root,startup=True)['ok'])
    def test_validated_startup_waits_for_chat_before_restoring_automation(self):
        with self.db:
            scheduler.put(self.db,'shared_mode',{'verified_version':'v1','verified_session':'session'})
            scheduler.put(self.db,'auto_enabled',True)
        self.assertTrue(shared.prepare(self.db,'codex',self.root,startup=True)['ok'])
        self.assertFalse(scheduler.enabled(self.db))
        self.assertTrue(scheduler.get(self.db,'shared_mode')['restore_auto_after_check'])
        with self.db:
            current=scheduler.get(self.db,'shared_mode')
            scheduler.put(self.db,'shared_mode',current | {'launched':True,'launch_version':'v1'})
        with patch('shared_mode.desktop_running',return_value=True),patch('shared_mode.probe',return_value={'connected':True,'thread_loaded':True}):
            self.assertTrue(shared.check(self.db,'codex','session')['ok'])
        self.assertTrue(scheduler.enabled(self.db))
    def test_explicit_pause_during_probe_prevents_restore(self):
        with self.db:
            scheduler.put(self.db,'shared_mode',{'launched':True,'launch_version':'v1','restore_auto_after_check':True})
            scheduler.put(self.db,'auto_enabled',False)
        def probe(*args):
            with self.db:
                current=scheduler.get(self.db,'shared_mode')
                scheduler.put(self.db,'shared_mode',current | {'restore_auto_after_check':False})
            return {'connected':True,'thread_loaded':True}
        with patch('shared_mode.desktop_running',return_value=True),patch('shared_mode.probe',side_effect=probe):
            self.assertTrue(shared.check(self.db,'codex','session')['ok'])
        self.assertFalse(scheduler.enabled(self.db))
    def test_stop_refuses_open_desktop(self):
        with patch('shared_mode.desktop_running',return_value=True),patch('codex_rpc.Client') as client:
            self.assertFalse(shared.stop(self.db,'codex',self.root)['ok'])
            client.assert_not_called()
    def test_stop_refuses_active_or_unknown_threads(self):
        for status in ('active','unknown'):
            with patch('codex_rpc.Client') as client,patch('engines.options',return_value={}),patch('shared_mode.subprocess.run') as run:
                client.return_value.call.side_effect=[{}, {'data':['thread']}, {'thread':{'id':'thread','status':{'type':status}}}]
                self.assertFalse(shared.stop(self.db,'codex',self.root)['ok'])
                run.assert_not_called()
                self.assertFalse(scheduler.enabled(self.db))
    def test_stop_confirms_shutdown_and_invalidates_validation(self):
        with self.db:scheduler.put(self.db,'shared_mode',{'verified_version':'v1','verified_session':'session'})
        with patch('codex_rpc.Client') as client,patch('engines.options',return_value={}),patch('shared_mode.probe',return_value={'connected':False}),patch('shared_mode.subprocess.run',return_value=SimpleNamespace(returncode=0)) as run:
            client.return_value.call.side_effect=[{}, {'data':['thread']}, {'thread':{'id':'thread','status':{'type':'idle'}}}]
            self.assertTrue(shared.stop(self.db,'codex',self.root)['ok'])
            self.assertEqual(run.call_args.args[0],['codex','app-server','daemon','stop'])
        self.assertFalse(shared.state(self.db)['startup_eligible'])
        self.assertFalse(scheduler.enabled(self.db))
    def test_stop_failure_preserves_validation_and_reports_uncertainty(self):
        with self.db:scheduler.put(self.db,'shared_mode',{'verified_version':'v1','verified_session':'session'})
        with patch('codex_rpc.Client') as client,patch('engines.options',return_value={}),patch('shared_mode.subprocess.run',return_value=SimpleNamespace(returncode=1)):
            client.return_value.call.side_effect=[{}, {'data':[]}]
            self.assertFalse(shared.stop(self.db,'codex',self.root)['ok'])
        self.assertTrue(shared.state(self.db)['startup_eligible'])
