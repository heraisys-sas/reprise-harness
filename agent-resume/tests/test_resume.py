import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import scheduler

loader = importlib.machinery.SourceFileLoader('resume', str(Path(__file__).parents[1] / 'resume'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

class SchedulerTests(unittest.TestCase):
    def test_due_once_and_failure_does_not_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            module.STATE = Path(folder)
            db = module.database()
            fake = Path(folder) / 'codex'
            fake.write_text('#!/bin/sh\ncat >/dev/null\nexit 1\n')
            fake.chmod(0o700)
            ident = '12345678-1234-1234-1234-123456789abc'
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,0)', (ident, ident, folder, 'continue', 0, 'ready'))
            db.commit()
            module.run_due(db, str(fake))
            module.run_due(db, str(fake))
            row = db.execute('SELECT * FROM tasks').fetchone()
            self.assertEqual(row['status'], 'needs_review')
            self.assertEqual(row['attempts'], 1)
            db.close()

    def test_backup_restore_pauses_and_share_excludes_state(self):
        with tempfile.TemporaryDirectory() as folder:
            import os
            import json
            import zipfile
            env = dict(os.environ, AGENT_RESUME_STATE=folder + '/first')
            script = str(Path(__file__).parents[1] / 'resume')
            prompt = Path(folder) / 'prompt'
            prompt.write_text('private prompt')
            def run(*args):
                return subprocess.check_output([script, *args], env=env, cwd=folder, text=True)
            out = run('add', '--session', '12345678-1234-1234-1234-123456789abc', '--cwd', folder, '--at', '2030-01-01T00:00:00Z', '--prompt-file', str(prompt))
            run('enable', out.split()[0])
            archive = folder + '/backup.zip'
            run('backup', archive)
            env['AGENT_RESUME_STATE'] = folder + '/second'
            run('restore', archive)
            self.assertEqual(json.loads(run('status'))['status'], 'paused')
            self.assertFalse(json.loads(run('scheduler-status'))['auto_enabled'])
            share = run('share').strip()
            with zipfile.ZipFile(share) as z:
                self.assertFalse(any('state/' in n for n in z.namelist()))
                self.assertNotIn('private prompt', z.read('agent-resume/prompt.example.txt').decode())


class AutomaticSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.previous_state = module.STATE
        module.STATE = Path(self.folder.name)
        self.addCleanup(setattr, module, 'STATE', self.previous_state)
        self.db = module.database()
        self.addCleanup(self.db.close)
        self.now = time.time()
        self.row = {'id': '12345678-1234-1234-1234-123456789abc', 'cwd': self.folder.name,
                    'updated': self.now - 20, 'interrupted_at': self.now - 20,
                    'state': 'quota_confirmed', 'retry_at': self.now - 1, 'automatic_resume_supported': True}
        self.fake = Path(self.folder.name) / 'codex'
        self.fake.write_text('#!/bin/sh\ncat >/dev/null\nexit 0\n')
        self.fake.chmod(0o700)

    def data(self, row=None):
        return {'sessions': [self.row if row is None else row], 'error': None}

    def task(self):
        return self.db.execute('SELECT * FROM tasks ORDER BY due LIMIT 1').fetchone()

    def enqueue(self, row=None):
        scheduler.reconcile(self.db, self.data(row), self.now)

    def test_quota_event_loads_owner_session_and_starts_only_once(self):
        import codex_rpc
        from collections import deque
        task = self.row
        class Owner:
            starts = 0
            def __init__(self, *args):
                self.status = 'notLoaded'
                self.notifications = deque([{'method':'turn/completed','params':{
                    'threadId':task['id'],'turn':{'id':'resumed-turn','status':'completed'}}}])
            def send(self, value): pass
            def close(self): pass
            def call(self, method, params=None):
                if method == 'thread/resume': self.status = 'idle'
                if method in ('thread/read','thread/resume'):
                    return {'thread':{'id':task['id'],'cwd':task['cwd'],
                        'status':{'type':self.status},'modelProvider':'openai'}}
                if method == 'account/read': return {'account':{'type':'chatgpt'}}
                if method == 'account/rateLimits/read': return {'ordinaryUsageAllowed':True}
                if method == 'turn/start':
                    Owner.starts += 1
                    return {'turn':{'id':'resumed-turn'}}
                return {}
        self.enqueue()
        with patch.object(scheduler,'snapshot',return_value=self.data()), \
             patch('quota.read_limits',return_value={'ordinaryUsageAllowed':True}), \
             patch('engines.select',return_value='codex-server'), patch.object(codex_rpc,'Client',Owner):
            module.run_due(self.db,str(self.fake))
            module.run_due(self.db,str(self.fake))
        self.assertEqual(Owner.starts,1)
        self.assertEqual(self.task()['status'],'finished')
        self.assertEqual(self.task()['attempts'],1)
        self.assertEqual(self.db.execute('SELECT consumed FROM automation_events').fetchone()[0],1)

    def test_child_session_is_not_enqueued_independently(self):
        self.enqueue(self.row | {'is_subagent': True, 'automatic_resume_supported': False})
        self.assertIsNone(self.task())

    def test_preexisting_child_attempt_is_cancelled_before_quota_probe(self):
        self.enqueue()
        child = self.row | {'is_subagent': True, 'automatic_resume_supported': False}
        with patch.object(scheduler, 'snapshot', return_value=self.data(child)), patch('quota.read_limits') as probe:
            module.run_due(self.db, str(self.fake))
        self.assertEqual(self.task()['status'], 'cancelled')
        self.assertEqual(self.task()['attempts'], 0)
        probe.assert_not_called()

    def test_only_recent_explicit_quota_is_admitted_once(self):
        for changes in ({'state': 'quota_candidate'}, {'state': 'stopped'}, {'active': True},
                        {'interrupted_at': self.now - 86401}, {'interrupted_at': self.now + 2},
                        {'retry_at': float('nan')}, {'id': 'not-a-uuid'}):
            self.enqueue(self.row | changes)
            self.assertIsNone(self.task())
        self.enqueue()
        self.enqueue()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM automation_events').fetchone()[0], 1)

    def test_unknown_deadline_waits_for_explicit_ordinary_permission(self):
        self.enqueue(self.row | {'retry_at': None})
        self.assertEqual(self.task()['status'], 'waiting_quota')
        for value in (None, False):
            with patch('quota.read_limits', return_value={'ordinaryUsageAllowed': value}):
                scheduler.refresh_waiting(self.db, str(self.fake), self.now)
            self.assertEqual(self.task()['status'], 'waiting_quota')
            with self.db:
                scheduler.put(self.db, 'quota_checked_at', None)
        with patch('quota.read_limits', return_value={'ordinaryUsageAllowed': True}):
            scheduler.refresh_waiting(self.db, str(self.fake), self.now)
        self.assertEqual(self.task()['status'], 'ready')
        self.assertEqual(self.task()['due'], self.now + 120)

    def test_quota_probe_is_cached_but_new_event_invalidates_it(self):
        self.enqueue()
        with patch('quota.read_limits', return_value={'ordinaryUsageAllowed': True}) as probe:
            scheduler.refresh_waiting(self.db, str(self.fake), self.now)
            scheduler.refresh_waiting(self.db, str(self.fake), self.now + 60)
            self.assertEqual(probe.call_count, 1)
            self.enqueue(self.row | {'interrupted_at': self.now - 5, 'updated': self.now - 5})
            scheduler.refresh_waiting(self.db, str(self.fake), self.now + 61)
            self.assertEqual(probe.call_count, 2)

    def test_auto_attempt_is_once_even_after_failure_or_reenable(self):
        self.enqueue()
        self.fake.write_text('#!/bin/sh\ncat >/dev/null\nexit 1\n')
        with patch.object(scheduler, 'snapshot', return_value=self.data()), \
                patch('quota.read_limits', return_value={'ordinaryUsageAllowed': True}):
            module.run_due(self.db, str(self.fake))
            self.assertEqual(self.task()['status'], 'needs_review')
            self.assertEqual(self.task()['attempts'], 1)
            with self.db:
                self.db.execute("UPDATE tasks SET status='ready'")
            module.run_due(self.db, str(self.fake))
            self.enqueue()
        self.assertEqual(self.task()['attempts'], 1)
        self.assertEqual(self.db.execute('SELECT consumed FROM automation_events').fetchone()[0], 1)

    def test_changed_or_resumed_session_cancels_without_starting(self):
        self.enqueue()
        with patch.object(scheduler, 'snapshot', return_value={'sessions': [], 'error': None}), \
                patch.object(module.subprocess, 'run') as run:
            module.run_due(self.db, str(self.fake))
        run.assert_not_called()
        self.assertEqual(self.task()['status'], 'cancelled')
        self.assertEqual(self.task()['attempts'], 0)

    def test_session_is_reread_after_quota_probe(self):
        self.enqueue()
        with patch('quota.read_limits', return_value={'ordinaryUsageAllowed': True}), \
                patch.object(scheduler, 'snapshot', side_effect=[self.data(), {'sessions': [], 'error': None}]), \
                patch.object(module.subprocess, 'run') as run:
            module.run_due(self.db, str(self.fake))
        run.assert_not_called()
        self.assertEqual(self.task()['status'], 'cancelled')

    def test_fresh_quota_permission_required_at_execution(self):
        self.enqueue()
        with self.db:
            scheduler.put(self.db, 'quota_checked_at', self.now)
            scheduler.put(self.db, 'quota_allowed', True)
        with patch.object(scheduler, 'snapshot', return_value=self.data()), \
                patch('quota.read_limits', return_value={'ordinaryUsageAllowed': False}) as probe, \
                patch.object(module.subprocess, 'run') as run:
            module.run_due(self.db, str(self.fake))
        probe.assert_called_once()
        run.assert_not_called()
        self.assertEqual(self.task()['status'], 'waiting_quota')

    def test_disabled_mode_neither_enqueues_nor_executes(self):
        with self.db:
            scheduler.put(self.db, 'auto_enabled', False)
        self.enqueue()
        self.assertIsNone(self.task())
        with self.db:
            scheduler.put(self.db, 'auto_enabled', True)
        self.enqueue()
        with self.db:
            scheduler.put(self.db, 'auto_enabled', False)
        with patch.object(module.subprocess, 'run') as run:
            module.run_due(self.db, str(self.fake))
        run.assert_not_called()
        self.assertEqual(self.task()['attempts'], 0)

    def test_manual_task_prevents_auto_duplicate(self):
        with self.db:
            self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,0)',
                            ('manual', self.row['id'], self.folder.name, 'manual', self.now, 'paused'))
        self.enqueue()
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM automation_events').fetchone()[0], 0)

    def test_registered_weekly_wait_survives_admission_age_limit(self):
        self.enqueue()
        scheduler.reconcile(self.db, self.data(), self.now + 7 * 86400)
        self.assertEqual(self.task()['status'], 'ready')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 1)

    def test_crashed_worker_is_reviewed_not_retried(self):
        self.enqueue()
        with self.db:
            self.db.execute("UPDATE tasks SET status='running',attempts=1")
            self.db.execute('UPDATE automation_events SET consumed=1')
        with patch.object(module.subprocess, 'run') as run:
            module.run_due(self.db, str(self.fake))
        run.assert_not_called()
        self.assertEqual(self.task()['status'], 'needs_review')

    def test_auto_command_pins_chatgpt_provider_and_preserves_sandbox(self):
        self.enqueue()
        args = module.command(self.task(), str(self.fake), automatic=True)
        self.assertIn('forced_login_method="chatgpt"', args)
        self.assertIn('model_provider="openai"', args)
        self.assertIn('workspace-write', args)

if __name__ == '__main__':
    unittest.main()
