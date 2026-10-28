"""All transport interactions are mocks; SQLite and config files are fixtures."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_session_observer as observer
from claude_session_store import SessionStore, open_database, binding
from claude_mcp import ClaudeMCP
from desktop_task_store import TaskStore
import claude_integration

CHAT = '00000000-0000-0000-0000-000000000001'
OTHER = '00000000-0000-0000-0000-000000000002'
NOW = 1793142000
ISO = '2026-10-29T14:40:00+01:00'
RESET = 1793281200
URL = 'https://claude.ai/chat/' + CHAT


def target(chat=CHAT, ident='target-a', origin='https://claude.ai'):
    return dict(type='page', id=ident, url=origin + '/chat/' + chat,
                webSocketDebuggerUrl='ws://127.0.0.1:9333/devtools/page/' + ident)


def notice(**extra):
    return dict(url=URL, notice='Usage limit reached. Reset at 14:40.', reset_iso=ISO,
                timezone='Europe/Paris', send_disabled=True, **extra)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.project = self.base / 'project'; self.project.mkdir()
        self.other_project = self.base / 'other'; self.other_project.mkdir()
        self.state = self.base / 'quick'
        self.root = self.base / 'helpers'; self.root.mkdir()
        self.db = open_database(self.state, write=True)
        self.addCleanup(self.db.close)
        self.store = SessionStore(self.db, self.project)
        self.store.register(CHAT, source='operator_desktop_confirmed', now=NOW-10)
        self.store.configure(enabled=True, port=9333)

    def status(self, **kw):
        return observer.status(self.root, project_root=str(self.project), quick_state=str(self.state),
                               now=kw.pop('now', NOW), **kw)

    @contextlib.contextmanager
    def transport(self, pages=None, raw=None, error=None, evaluate=None):
        self.client = Mock()
        self.client.evaluate.return_value = notice() if raw is None else raw
        if evaluate:
            self.client.evaluate.side_effect = evaluate
        with patch.object(observer.cdp, 'read_targets', return_value=pages if pages is not None else [target()], side_effect=error) as listing, \
                patch.object(observer.cdp, 'local_json', return_value={'webSocketDebuggerUrl': 'ws://127.0.0.1:9333/devtools/browser/instance-a'}) as metadata, \
                patch.object(observer.cdp, 'CDP', return_value=self.client) as connect:
            self.listing, self.metadata, self.connect = listing, metadata, connect
            yield

    def test_cache_never_connects_or_writes(self):
        before = (self.state / 'quick.sqlite').stat().st_mtime_ns
        with self.transport():
            result = self.status()
            self.listing.assert_not_called(); self.metadata.assert_not_called(); self.connect.assert_not_called()
        self.assertEqual((self.state / 'quick.sqlite').stat().st_mtime_ns, before)
        self.assertEqual(result['route']['refresh_status'], 'cached')
        self.assertEqual(result['sessions'][0]['quota']['freshness'], 'unobserved')
        self.assertFalse((self.state / 'claude-observer.lock').exists())

    def test_passive_refresh_is_scoped_and_contains_no_action(self):
        with self.transport(pages=[target(), target(OTHER, 'unknown'), target(CHAT, 'evil', 'https://evil.test')]):
            result = self.status(refresh_now=True)
            self.connect.assert_called_once()
            self.assertEqual(self.connect.call_args.kwargs['target_id'], 'target-a')
            self.client.navigate.assert_not_called(); self.client.call.assert_not_called()
            self.client.close.assert_called_once()
        self.assertEqual(len(result['sessions']), 1)
        session = result['sessions'][0]
        self.assertEqual(session['observation']['presence'], 'open')
        self.assertEqual(session['registration']['identity_status'], 'declared')
        self.assertFalse(session['registration']['attested'])
        self.assertFalse(any(result['capabilities'].values()))
        self.assertFalse(session['action_eligible'])
        quota = session['quota']
        self.assertEqual(quota['reset_status'], 'known')
        self.assertEqual(quota['pools'][0]['windows'][0]['resets_at'], RESET)
        self.assertIsNone(quota['pools'][0]['windows'][0]['remaining_percent'])
        self.assertIsNone(quota['account_key'])
        self.assertEqual(quota['knowledge'], 'unknown')
        raw_db = str(self.db.execute('SELECT data FROM quota_observations').fetchall())
        self.assertNotIn('Usage limit', raw_db)
        self.assertNotIn('unknown', json.dumps(result['sessions'][0]['observation']))

    def test_closed_known_chat_remains_without_discovering_unknown_chats(self):
        with self.transport(pages=[target(OTHER)]):
            result = self.status(refresh_now=True)
            self.connect.assert_not_called()
        self.assertEqual([s['chat_id'] for s in result['sessions']], [CHAT])
        self.assertEqual(result['sessions'][0]['observation']['presence'], 'not_open')
        self.assertEqual(result['route']['coverage'], 'open_tabs_only')

    def test_ambiguous_target_has_no_page_read(self):
        with self.transport(pages=[target(), target(ident='duplicate')]):
            result = self.status(refresh_now=True)
            self.connect.assert_not_called()
        self.assertEqual(result['sessions'][0]['observation']['presence'], 'ambiguous')
        self.assertEqual(result['sessions'][0]['observation']['target_count'], 2)

    def test_refresh_if_due_coalesces_and_explicit_can_refresh(self):
        with self.transport():
            first = self.status(refresh_if_due=True)
            self.assertEqual(first['route']['next_refresh_at'], NOW+60)
            second = self.status(refresh_if_due=True, now=NOW+20)
            self.assertEqual(second['route']['refresh_status'], 'not_due')
            self.assertEqual(self.listing.call_count, 1)
            self.status(refresh_now=True, now=NOW+21)
            self.assertEqual(self.listing.call_count, 2)

    def test_disabled_observation_is_independent_of_global_actions(self):
        self.store.configure(enabled=False)
        with self.transport():
            result = self.status(refresh_if_due=True)
            self.listing.assert_not_called()
        self.assertEqual(result['route']['refresh_status'], 'disabled')
        tables = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('scheduler_meta', tables)
        self.assertNotIn('tasks', tables)

    def test_observation_lock_is_nonblocking(self):
        with (self.state / 'claude-observer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.transport():
                result = self.status(refresh_now=True)
                self.listing.assert_not_called()
        self.assertEqual(result['route']['refresh_status'], 'busy')

    def test_endpoint_failure_backs_off_and_retains_historical_reset(self):
        with self.transport():
            self.status(refresh_now=True)
        with self.transport(error=OSError('private user data')):
            result = self.status(refresh_now=True, now=NOW+1)
            second = self.status(refresh_if_due=True, now=NOW+61)
        quota = result['sessions'][0]['quota']
        self.assertIsNone(quota['blocked'])
        self.assertEqual(quota['observation_status'], 'unavailable')
        self.assertEqual(quota['historical_reset']['resets_at'], RESET)
        self.assertTrue(quota['historical_reset']['historical_only'])
        self.assertEqual(result['route']['next_refresh_at'], NOW+61)
        self.assertEqual(second['route']['next_refresh_at'], NOW+181)
        self.assertNotIn('private user data', json.dumps(result))

    def test_notice_disappearance_does_not_clear_quota_to_available(self):
        with self.transport():
            self.status(refresh_now=True)
        raw = dict(url=URL, notice=None, reset_iso=None, timezone='Europe/Paris', send_disabled=False)
        with self.transport(raw=raw):
            result = self.status(refresh_now=True, now=NOW+2)
        quota = result['sessions'][0]['quota']
        self.assertIsNone(quota['blocked'])
        self.assertEqual(quota['reset_status'], 'unknown')
        self.assertEqual(quota['pools'], [])
        self.assertEqual(quota['historical_reset']['resets_at'], RESET)

    def test_source_navigation_change_is_not_read_as_expected_chat(self):
        raw = notice(); raw['url'] = 'https://claude.ai/chat/' + OTHER
        with self.transport(raw=raw):
            result = self.status(refresh_now=True)
        self.assertEqual(result['sessions'][0]['quota']['observation_status'], 'unavailable')
        self.assertEqual(result['sessions'][0]['quota']['pools'], [])

    def test_removed_session_is_not_recreated_after_io(self):
        def remove(expression):
            self.store.remove(CHAT)
            return notice()
        with self.transport(evaluate=remove):
            result = self.status(refresh_now=True)
        self.assertEqual(result['sessions'], [])
        self.assertEqual(self.db.execute('SELECT count(*) FROM quota_observations').fetchone()[0], 0)

    def test_disable_during_io_is_not_overwritten(self):
        def disable(expression):
            self.store.configure(enabled=False)
            return notice()
        with self.transport(evaluate=disable):
            result = self.status(refresh_now=True)
        self.assertFalse(result['route']['enabled'])
        self.assertFalse(self.store.route()['enabled'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM quota_observations').fetchone()[0], 0)

    def test_port_change_cannot_reuse_current_quota(self):
        with self.transport():
            first = self.status(refresh_now=True)
        self.store.configure(enabled=True, port=9444)
        result = self.status()
        self.assertNotEqual(first['route']['route_key'], result['route']['route_key'])
        self.assertEqual(result['sessions'][0]['quota']['freshness'], 'unobserved')
        self.assertEqual(result['sessions'][0]['observation']['presence'], 'not_observed')

    def test_re_registration_during_io_cannot_adopt_old_observation(self):
        def replace(expression):
            self.store.remove(CHAT)
            self.store.register(CHAT, source='desktop_mcp_declared', now=NOW+1)
            return notice()
        with self.transport(evaluate=replace):
            result = self.status(refresh_now=True)
        self.assertEqual(result['sessions'][0]['registration']['registered_at'], NOW+1)
        self.assertEqual(result['sessions'][0]['observation']['presence'], 'not_observed')
        self.assertEqual(result['sessions'][0]['quota']['freshness'], 'unobserved')

    def test_failure_after_budget_keeps_current_unknown_and_old_reset_historical(self):
        with self.transport():
            self.status(refresh_now=True)
        ticks = iter([0, 1, 10])
        with self.transport(error=OSError('offline')), patch.object(observer.time, 'monotonic', side_effect=lambda: next(ticks)):
            result = self.status(refresh_now=True, now=NOW+2)
        quota = result['sessions'][0]['quota']
        self.assertIsNone(quota['blocked'])
        self.assertEqual(quota['pools'], [])
        self.assertEqual(quota['historical_reset']['resets_at'], RESET)

    def test_two_projects_share_database_not_sessions_or_quota(self):
        other = SessionStore(self.db, self.other_project)
        other.register(CHAT, source='desktop_mcp_declared', now=NOW-10)
        other.configure(enabled=True, port=9333)
        with self.transport():
            a = self.status(refresh_now=True)
        _, _, desc = binding(self.other_project, self.state, 'explicit')
        b = other.snapshot(desc, now=NOW)
        self.assertNotEqual(a['route']['route_key'], b['route']['route_key'])
        self.assertNotEqual(a['sessions'][0]['session_key'], b['sessions'][0]['session_key'])
        self.assertEqual(b['sessions'][0]['quota']['freshness'], 'unobserved')

    def test_registry_fields_are_allowlisted(self):
        session = self.store.sessions()[0]
        self.store.registry.record_claude(self.store.scope, 9333, session['session_key'],
            dict(observation_status='observed', confidence='heuristic', blocked=True,
                 accountId='private-account', cookie='private-token', notice='private body',
                 remaining_percent=100, resets_at=True, displayed_reset_time='29:99'), observed_at=NOW)
        result = self.status()['sessions'][0]['quota']
        self.assertIsNone(result['blocked'])
        self.assertIsNone(result['displayed_reset_time'])
        self.assertIsNone(result['pools'][0]['windows'][0]['resets_at'])
        rows = str(self.db.execute('SELECT data FROM quota_observations').fetchall())
        self.assertNotIn('private', rows)

    def test_expired_or_stale_observation_never_proves_recovery(self):
        with self.transport():
            self.status(refresh_now=True)
        stale = self.status(now=NOW+301)['sessions'][0]['quota']
        self.assertEqual(stale['freshness'], 'stale')
        self.assertIsNone(stale['blocked'])
        expired = self.status(now=RESET+1)['sessions'][0]['quota']
        self.assertEqual(expired['reset_status'], 'elapsed')
        self.assertIsNone(expired['blocked'])
        skew = self.status(now=NOW-1)['sessions'][0]['quota']
        self.assertEqual(skew['freshness'], 'clock_skew')
        self.assertIsNone(skew['blocked'])

    def test_bounded_pass_can_report_partial(self):
        ticks = iter([0, 1, 2, 10])
        with self.transport(), patch.object(observer.time, 'monotonic', side_effect=lambda: next(ticks)):
            result = self.status(refresh_now=True)
            self.connect.assert_not_called()
        self.assertEqual(result['route']['refresh_status'], 'partial')
        self.assertEqual(result['sessions'][0]['quota']['freshness'], 'unobserved')

    def test_mcp_registration_uses_same_database_and_does_not_admit_task(self):
        tasks = TaskStore(self.state, self.project)
        self.addCleanup(tasks.close)
        facade = ClaudeMCP(tasks, Mock())
        result = facade.dispatch({'method':'tools/call', 'params':{'name':'register_session', 'arguments':{'chat_id':OTHER}}})
        data = json.loads(result['content'][0]['text'])
        self.assertEqual(data['registration']['source'], 'desktop_mcp_declared')
        self.assertFalse(data['registration']['attested'])
        self.assertEqual(len(self.status()['sessions']), 2)
        self.assertEqual(tasks.db.execute('SELECT count(*) FROM desktop_tasks').fetchone()[0], 0)
        self.assertNotIn('await_task', {t['name'] for t in facade.dispatch({'method':'tools/list'})['tools']})

    def test_registration_is_idempotent_and_remove_preserves_tasks(self):
        first = self.store.sessions()[0]
        self.assertEqual(first, self.store.register(CHAT, source='desktop_mcp_declared', now=NOW+1))
        tasks = TaskStore(self.state, self.project)
        tasks.admit('unchanged', 'existing task')
        self.store.remove(CHAT)
        self.assertEqual(tasks.db.execute('SELECT count(*) FROM desktop_tasks').fetchone()[0], 1)
        tasks.close()
        with self.assertRaises(ValueError):
            self.store.register('not-uuid', source='operator_desktop_confirmed')
        with self.assertRaises(ValueError):
            self.store.register(CHAT, source='web_new_chat')

    def test_explicit_binding_requires_both_paths_without_creating_default_state(self):
        missing = self.base / 'missing-state'
        result = observer.status(self.root, project_root=str(self.project), quick_state=str(missing))
        self.assertEqual(result['status'], 'ok')
        self.assertFalse(missing.exists())
        result = observer.status(self.root, project_root=str(self.project))
        self.assertEqual(result['status'], 'binding_mismatch')
        self.assertFalse((self.root / 'state').exists())
        result = observer.status(self.root, project_root='.', quick_state=str(missing))
        self.assertEqual(result['status'], 'binding_mismatch')

    def test_managed_binding_checks_manifest_and_config_without_cwd_fallback(self):
        home = self.base / 'home'; home.mkdir()
        _, config, managed = claude_integration.paths(home)
        config.parent.mkdir(parents=True); managed.mkdir(parents=True)
        self.assertEqual(observer.status(self.root, home=home)['status'], 'not_configured')
        state = self.root / 'quick-state'
        entry = {'command':'never-executed', 'args':['--project-root', str(self.project), '--state', str(state)]}
        config.write_text(json.dumps({'mcpServers':{'reprise-harness':entry}, 'private':'do-not-copy'}))
        manifest = dict(format=1, status='installed', connector_name='reprise-harness',
                        project=str(self.project), quick_state=str(state), state=str(self.root / 'state'),
                        entry_sha256=claude_integration.entry_hash(entry))
        (managed / 'managed.json').write_text(json.dumps(manifest))
        result = observer.status(self.root, home=home)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['binding']['status'], 'managed')
        self.assertEqual(result['binding']['quick_state'], str(state))
        self.assertFalse(state.exists())
        self.assertNotIn('do-not-copy', json.dumps(result))
        manifest['entry_sha256'] = 'changed'
        (managed / 'managed.json').write_text(json.dumps(manifest))
        self.assertEqual(observer.status(self.root, home=home)['status'], 'binding_mismatch')

    def test_cli_works_before_main_database_initialization(self):
        executable = Path(__file__).parents[1] / 'resume'
        state = self.base / 'cli-quick'
        env = dict(os.environ, HOME=str(self.base / 'unused-home'))
        env.pop('AGENT_RESUME_STATE', None)
        def command(name, *args):
            process = subprocess.run([sys.executable, str(executable), name, '--project-root', str(self.project),
                                      '--quick-state', str(state), *args], capture_output=True, text=True, env=env, check=True)
            return json.loads(process.stdout)
        self.assertEqual(command('claude-sessions-status')['sessions'], [])
        self.assertFalse(state.exists())
        result = command('claude-session-register', '--chat', CHAT)
        self.assertEqual(result['sessions'][0]['registration']['source'], 'operator_desktop_confirmed')
        result = command('claude-observer-config', '--enabled', 'true', '--port', '9333', '--interval-seconds', '120')
        self.assertEqual(result['route']['interval_seconds'], 120)
        result = command('claude-session-remove', '--chat', CHAT)
        self.assertEqual(result['sessions'], [])
        self.assertEqual(list(state.glob('*.sqlite')), [state / 'quick.sqlite'])
        self.assertFalse((self.root / 'state').exists())


class NoticeParsingTests(unittest.TestCase):
    def test_hhmm_without_date_is_only_displayed(self):
        raw = notice(); raw['reset_iso'] = None
        result = observer.parse_observation(raw, NOW)
        self.assertIsNone(result['resets_at'])
        self.assertEqual(result['displayed_reset_time'], '14:40')
        self.assertTrue(result['blocked'])

    def test_marketing_and_negation_are_not_explicit_refusals(self):
        messages = ['Upgrade for higher usage limits. Reset at 14:40.',
                    'Your usage limit resets at 14:40.', 'No usage limit reached.',
                    'You have not reached your usage limit.', 'Limite non atteinte.',
                    'Vous avez une limite de messages.', 'Aucune limite atteinte.']
        for message in messages:
            raw = notice(); raw['notice'] = message
            result = observer.parse_observation(raw, NOW)
            with self.subTest(message=message):
                self.assertIsNone(result['blocked'])
                self.assertIsNone(result['resets_at'])
                self.assertNotEqual(result['confidence'], 'explicit_text')

    def test_disabled_button_is_not_quota(self):
        result = observer.parse_observation(dict(send_disabled=True), NOW)
        self.assertIsNone(result['blocked'])
        self.assertEqual(result['confidence'], 'heuristic')

    def test_reset_requires_an_offset_and_explicit_quota_context(self):
        for value in ('2026-10-29T14:40:00', '2026-02-30T14:40:00Z', 'tomorrow', True):
            raw = notice(); raw['reset_iso'] = value
            self.assertIsNone(observer.parse_observation(raw, NOW)['resets_at'])
        raw = notice(); raw['notice'] = 'An unrelated dialog at 14:40'
        self.assertIsNone(observer.parse_observation(raw, NOW)['resets_at'])

    def test_explicit_pause_parsing_keeps_dst_ambiguity_unknown(self):
        raw = dict(notice='En pause : votre utilisation gratuite sera de nouveau disponible 25 oct. à 02:30. Vous pourrez continuer à ce moment-là.',
                   timezone='Europe/Paris')
        current = 1792864800
        self.assertIsNone(observer.parse_observation(raw, current)['resets_at'])
        raw['timezone'] = 'Not/AZone'
        self.assertIsNone(observer.parse_observation(raw, current)['resets_at'])


if __name__ == '__main__':
    unittest.main()
