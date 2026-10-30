import importlib.machinery
import importlib.util
import contextlib
import io
from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import scheduler
import claude_quota_log

loader = importlib.machinery.SourceFileLoader('resume', str(Path(__file__).parents[1] / 'resume'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        transport = patch('engines.select', return_value='codex-cli')
        transport.start()
        self.addCleanup(transport.stop)

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

    def test_add_accepts_z_suffix_and_offsets_but_rejects_missing_timezone(self):
        with tempfile.TemporaryDirectory() as folder:
            import os
            script = str(Path(__file__).parents[1] / 'resume')
            prompt = Path(folder) / 'prompt'
            prompt.write_text('continue')
            env = dict(os.environ, AGENT_RESUME_STATE=folder + '/state')
            common = ['add', '--session', '12345678-1234-1234-1234-123456789abc',
                      '--cwd', folder, '--prompt-file', str(prompt)]
            for instant in ('2030-01-01T00:00:00Z', '2030-01-01T00:00:00z',
                            '2030-01-01T00:00:00+02:00'):
                result = subprocess.run([script, *common, '--at', instant], env=env,
                                         cwd=folder, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([script, *common, '--at', '2030-01-01T00:00:00'],
                                    env=env, cwd=folder, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('--at exige un fuseau horaire', result.stderr)

    def test_claude_inbox_cli_dispatches_each_operation_without_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            script = str(Path(__file__).parents[1] / 'resume')
            for operation, args in (('list', []), ('read', ['--request-key', 'request-1']),
                                    ('ack', ['--request-key', 'request-1', '--result-sha256', '0' * 64,
                                             '--total-length', '10', '--confirm-viewed'])):
                with self.subTest(operation=operation):
                    result = subprocess.run([script, f'claude-inbox-{operation}', '--project', folder, *args],
                                            env=dict(os.environ, HOME=folder), cwd=folder,
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertEqual(json.loads(result.stdout)['status'], 'manifest_unavailable')

    def test_desktop_apply_cli_help_is_available_without_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            import os
            script = str(Path(__file__).parents[1] / 'resume')
            result = subprocess.run([script, 'desktop-apply', '--help'],
                                    env=dict(os.environ, HOME=folder), cwd=folder,
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--status', result.stdout)
            self.assertIn('--chat', result.stdout)


class ClaudeQuotaStatusCLITests(unittest.TestCase):
    def _run(self, args, source_reader):
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with patch.object(module, 'STATE', Path(folder)), \
                 patch.object(module, 'codex_executable', return_value=None), \
                 patch.object(sys, 'argv', ['resume', 'claude-quota-status', *args]), \
                 patch.object(claude_quota_log, 'source_observation', source_reader), \
                 contextlib.redirect_stdout(output):
                module.main()
            return json.loads(output.getvalue())

    def test_quota_status_without_flag_preserves_shape_and_never_reads_renderer_log(self):
        reader = unittest.mock.Mock(side_effect=AssertionError('renderer log must not be read'))
        result = self._run([], reader)
        self.assertEqual(set(result), {'enabled', 'events', 'transport', 'observation'})
        reader.assert_not_called()

    def test_quota_status_opt_in_returns_only_normalized_source_observation(self):
        fixture = {
            'schema_version': 1, 'engine': 'claude-desktop',
            'source': 'claude.renderer_log', 'source_status': 'readable',
            'event_status': 'recognized', 'observation_status': 'observed',
            'observed_at': 1793065700.0, 'source_observed_at': None,
            'source_timezone': None, 'source_timestamp_status': 'unknown_timezone',
            'event_type': 'exceeded_limit', 'event_age_seconds': None,
            'event_freshness': 'unknown', 'resets_at': 1793065800.0,
            'reset_relation': 'future_at_read', 'current_quota_status': 'unknown',
            'current_blocked': None, 'current_available': None,
            'account_id': None, 'chat_id': None, 'used_percent': None,
            'issues': ['account_unattributed', 'chat_unattributed', 'current_quota_unknown'],
        }
        reader = unittest.mock.Mock(return_value=fixture)
        result = self._run(['--observe-renderer-log'], reader)
        reader.assert_called_once_with()
        self.assertEqual(result['source_observation'], fixture)
        self.assertEqual(result['source_observation']['event_freshness'], 'unknown')
        self.assertNotIn('diagnostic text', json.dumps(result))
        self.assertNotIn('account name', json.dumps(result))


class AutomaticSchedulerTests(unittest.TestCase):
    def setUp(self):
        transport = patch('engines.select', return_value='codex-cli')
        transport.start()
        self.addCleanup(transport.stop)
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

    def degraded(self, sessions, evidence):
        return {'sessions': sessions, 'error': 'one rollout unavailable',
                'scan_complete': True, 'session_coverage': evidence,
                'coverage': {'total': len(evidence)}}

    def test_degraded_other_session_does_not_block_proven_recovery(self):
        data = self.degraded([self.row], {self.row['id']: 'recognized', 'other': 'unreadable'})
        scheduler.reconcile(self.db, data, self.now)
        self.assertEqual(self.task()['status'], 'ready')
        with patch.object(scheduler, 'quota_state', side_effect=AssertionError('no quota probe')):
            self.assertTrue(scheduler.preflight(self.db, self.task(), str(self.fake),
                                              now=self.now, read=lambda: data))

    def test_uncertain_or_unscanned_session_never_runs_or_cancels(self):
        self.enqueue()
        for evidence in ({self.row['id']: 'unknown_format'},
                         {self.row['id']: 'unreadable'}, {'other': 'recognized'}):
            data = self.degraded([], evidence)
            scheduler.reconcile(self.db, data, self.now)
            self.assertEqual(self.task()['status'], 'ready')
            self.assertFalse(scheduler.preflight(self.db, self.task(), str(self.fake),
                                                now=self.now, read=lambda: data))
            self.assertEqual(self.task()['status'], 'ready')
        # Even an apparent quota in an unknown journal cannot authorize recovery.
        data = self.degraded([self.row], {self.row['id']: 'unknown_format'})
        self.assertFalse(scheduler.preflight(self.db, self.task(), str(self.fake),
                                            now=self.now, read=lambda: data))

    def test_uncertainty_on_second_preflight_read_keeps_task_pending(self):
        self.enqueue()
        unknown = self.degraded([], {self.row['id']: 'unreadable'})
        reads = iter([self.data(), unknown])
        self.assertFalse(scheduler.preflight(self.db, self.task(), str(self.fake),
                                            now=self.now, read=lambda: next(reads)))
        self.assertEqual(self.task()['status'], 'ready')

    def test_unknown_journal_never_admits_a_new_event(self):
        data = self.degraded([self.row], {self.row['id']: 'unknown_format'})
        scheduler.reconcile(self.db, data, self.now)
        self.assertIsNone(self.task())

    def test_recognized_completed_session_can_cancel_during_degraded_scan(self):
        self.enqueue()
        data = self.degraded([], {self.row['id']: 'recognized', 'other': 'unknown_format'})
        scheduler.reconcile(self.db, data, self.now)
        self.assertEqual(self.task()['status'], 'cancelled')

    def test_global_database_or_legacy_error_stays_blocking(self):
        self.enqueue()
        for data in ({'sessions': [], 'error': 'database unavailable'},
                     {'sessions': [], 'error': 'database unavailable',
                      'session_coverage': {}, 'coverage': {'total': 0}}):
            with self.assertRaises(ValueError):
                scheduler.reconcile(self.db, data, self.now)
            self.assertFalse(scheduler.preflight(self.db, self.task(), str(self.fake),
                                                now=self.now, read=lambda: data))
            self.assertEqual(self.task()['status'], 'ready')

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
             patch('quota.read_limits', side_effect=RuntimeError('unavailable')) as probe, \
             patch('engines.select',return_value='codex-server'), patch.object(codex_rpc,'Client',Owner):
            module.run_due(self.db,str(self.fake))
            module.run_due(self.db,str(self.fake))
        probe.assert_not_called()
        self.assertEqual(Owner.starts,1)
        self.assertEqual(self.task()['status'],'finished')
        self.assertEqual(self.task()['attempts'],1)
        self.assertEqual(self.db.execute('SELECT consumed FROM automation_events').fetchone()[0],1)

    def test_predispatch_deferral_releases_reservation_and_waits(self):
        self.enqueue()
        with patch.object(scheduler, 'snapshot', return_value=self.data()), \
             patch('quota.read_limits', return_value={'ordinaryUsageAllowed': True}), \
             patch('engines.select', return_value='codex-server'), \
             patch('codex_rpc.execute', return_value='ready') as execute:
            module.run_due(self.db, str(self.fake))
            module.run_due(self.db, str(self.fake))
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(self.task()['status'], 'ready')
        self.assertEqual(self.task()['attempts'], 0)
        self.assertGreater(self.task()['due'], self.now + 200)
        self.assertEqual(self.db.execute('SELECT consumed FROM automation_events').fetchone()[0], 0)
        scheduler.reconcile(self.db, {'sessions': [], 'error': None})
        self.assertEqual(self.task()['status'], 'cancelled')

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
        self.enqueue(self.row | {'retry_at': None})
        with patch('quota.read_limits', return_value={'ordinaryUsageAllowed': True}) as probe:
            scheduler.refresh_waiting(self.db, str(self.fake), self.now)
            scheduler.refresh_waiting(self.db, str(self.fake), self.now + 60)
            self.assertEqual(probe.call_count, 1)
            self.enqueue(self.row | {'interrupted_at': self.now - 5, 'updated': self.now - 5, 'retry_at': None})
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

    def test_session_is_reread_before_dispatch(self):
        self.enqueue()
        with patch('quota.read_limits', return_value={'ordinaryUsageAllowed': True}), \
                patch.object(scheduler, 'snapshot', side_effect=[self.data(), {'sessions': [], 'error': None}]), \
                patch.object(module.subprocess, 'run') as run:
            module.run_due(self.db, str(self.fake))
        run.assert_not_called()
        self.assertEqual(self.task()['status'], 'cancelled')

    def test_announced_deadline_runs_once_without_quota_measurement(self):
        self.enqueue()
        with self.db:
            scheduler.put(self.db, 'quota_allowed', False)
        with patch.object(scheduler, 'snapshot', return_value=self.data()), \
                patch('quota.read_limits', side_effect=RuntimeError('unavailable')) as probe:
            scheduler.refresh_waiting(self.db, str(self.fake), self.now)
            module.run_due(self.db, str(self.fake))
            module.run_due(self.db, str(self.fake))
        probe.assert_not_called()
        # A zero CLI exit without native completion evidence stays under review.
        self.assertEqual(self.task()['status'], 'needs_review')
        self.assertEqual(self.task()['attempts'], 1)

    def test_future_announced_deadline_is_preserved_without_probe(self):
        self.enqueue(self.row | {'retry_at': self.now + 3600})
        with patch('quota.read_limits', return_value={'ordinaryUsageAllowed': True}) as probe:
            scheduler.refresh_waiting(self.db, str(self.fake), self.now)
            self.assertFalse(scheduler.preflight(self.db, self.task(), str(self.fake),
                                               self.now, read=lambda: self.data()))
        probe.assert_not_called()
        self.assertEqual(self.task()['due'], self.now + 3600)
        self.assertEqual(self.task()['attempts'], 0)

    def test_legacy_waiting_task_recovers_announced_deadline(self):
        self.enqueue()
        with self.db:
            self.db.execute("UPDATE tasks SET status='waiting_quota',due=?", (self.now + 3600,))
        with patch.object(scheduler, 'snapshot', return_value=self.data()), \
                patch('quota.read_limits') as probe:
            scheduler.reconcile(self.db, self.data(), self.now)
            scheduler.refresh_waiting(self.db, str(self.fake), self.now)
        probe.assert_not_called()
        self.assertEqual(self.task()['status'], 'ready')
        self.assertEqual(self.task()['due'], self.row['retry_at'])

    def test_new_quota_event_uses_new_deadline_without_replaying_old_attempt(self):
        self.enqueue()
        with self.db:
            self.db.execute("UPDATE tasks SET status='needs_review',attempts=1")
            self.db.execute('UPDATE automation_events SET consumed=1')
        next_row = self.row | {'interrupted_at': self.now - 2, 'updated': self.now - 2,
                               'retry_at': self.now + 7200}
        scheduler.reconcile(self.db, self.data(next_row), self.now)
        tasks = self.db.execute('SELECT status,due,attempts FROM tasks ORDER BY due').fetchall()
        self.assertEqual(len(tasks), 2)
        self.assertEqual((tasks[0]['status'], tasks[0]['attempts']), ('needs_review', 1))
        self.assertEqual((tasks[1]['status'], tasks[1]['due'], tasks[1]['attempts']),
                         ('ready', next_row['retry_at'], 0))

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


    def test_claude_quota_config_accepts_cdp_transport(self):
        """claude-quota-config --transport cdp est accepté."""
        with tempfile.TemporaryDirectory() as folder:
            db_path = Path(folder) / 'tasks.db'
            script = str(Path(__file__).parents[1] / 'resume')
            result = subprocess.run(
                [script, 'claude-quota-config', '--transport', 'cdp', '--enabled', 'true'],
                env=dict(os.environ, HOME=folder),
                cwd=folder, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertTrue(data['claude_desktop_auto_enabled'])

if __name__ == '__main__':
    unittest.main()
