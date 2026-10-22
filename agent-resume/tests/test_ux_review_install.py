import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
import os
import subprocess
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

_loader = importlib.machinery.SourceFileLoader(
    'install_ux_review', str(Path(__file__).parents[1] / 'install-ux-review.py'))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
installer = importlib.util.module_from_spec(_spec)
_loader.exec_module(installer)


class InstallUxReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.state = self.home / 'ux-state'
        self.config = self.home / 'Library/Application Support/Claude/claude_desktop_config.json'

    def install(self, **kw):
        kw.setdefault('home', self.home)
        kw.setdefault('state', self.state)
        kw.setdefault('app_running', False)
        return installer.install(**kw)

    def read_config(self):
        return json.loads(self.config.read_text(encoding='utf-8'))

    def test_writes_block_and_preserves_other_keys(self):
        self.config.parent.mkdir(parents=True)
        original = {'mcpServers': {'other': {'command': 'x', 'args': []}},
                    'coworkUserFilesPath': '/tmp/x',
                    'preferences': {'quickEntryShortcut': 'double-tap-option'}}
        self.config.write_text(json.dumps(original, indent=2, ensure_ascii=False), encoding='utf-8')
        result = self.install()
        self.assertTrue(result['changed'])
        data = self.read_config()
        self.assertIn('other', data['mcpServers'])
        self.assertEqual(data['coworkUserFilesPath'], '/tmp/x')
        self.assertEqual(data['preferences']['quickEntryShortcut'], 'double-tap-option')
        entry = data['mcpServers']['reprise_ux_review']
        self.assertEqual(entry['command'], installer.sys.executable)
        self.assertIn(str(installer.SERVER), entry['args'])
        self.assertIn('--state', entry['args'])

    def test_format_matches_claude_exact_dump(self):
        self.install()
        # Round-trip the written file and compare to our canonical serialization.
        data = self.read_config()
        canonical = json.dumps(data, indent=2, ensure_ascii=False)
        self.assertEqual(self.config.read_text(encoding='utf-8'), canonical)
        self.assertFalse(self.config.read_text(encoding='utf-8').endswith('\n'))

    def test_idempotent_second_call(self):
        self.install()
        second = self.install()
        self.assertFalse(second['changed'])

    def test_refuses_different_existing_block_without_force(self):
        self.install()
        with self.assertRaises(FileExistsError):
            self.install(state=self.home / 'other-state')
        # Force replaces and keeps a backup.
        before = self.config.read_bytes()
        result = self.install(state=self.home / 'other-state', force=True)
        self.assertTrue(result['changed'])
        self.assertIsNotNone(result['backup'])
        self.assertEqual(Path(result['backup']).read_bytes(), before)

    def test_creates_config_when_absent(self):
        result = self.install()
        self.assertTrue(result['changed'])
        self.assertTrue(self.config.exists())
        data = self.read_config()
        self.assertIn('reprise_ux_review', data['mcpServers'])

    def test_attachment_and_task_in_args(self):
        attach = self.home / 'code.swift'
        attach.write_text('struct V { }')
        result = self.install(attachment=attach, task='Analyse la clarté')
        entry = self.read_config()['mcpServers']['reprise_ux_review']
        self.assertIn('--attachment', entry['args'])
        self.assertIn(str(attach.resolve()), entry['args'])
        self.assertIn('--task', entry['args'])
        self.assertIn('Analyse la clarté', entry['args'])

    def test_rejects_paths_outside_home(self):
        with self.assertRaises(ValueError):
            self.install(state=Path('/tmp/outside-state'))
        self.assertFalse(self.config.exists())

    def test_rejects_missing_attachment(self):
        with self.assertRaises(ValueError):
            self.install(attachment=self.home / 'missing.swift')

    def test_reports_needs_relaunch_only_when_running(self):
        self.install(app_running=False)
        result = self.install(app_running=True)
        self.assertTrue(result['needs_relaunch'])
        result = self.install(app_running=False)
        self.assertFalse(result['needs_relaunch'])

    def test_invalid_json_refused_without_clobber(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{pas du json', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(self.config.read_text(encoding='utf-8'), '{pas du json')

    def test_backup_preserves_existing_config_when_overwritten(self):
        self.config.parent.mkdir(parents=True)
        prior = {'mcpServers': {'legacy': {'command': 'a'}}}
        self.config.write_text(json.dumps(prior), encoding='utf-8')
        result = self.install()
        self.assertIsNotNone(result['backup'])
        self.assertEqual(json.loads(Path(result['backup']).read_text())['mcpServers']['legacy']['command'], 'a')

    def test_concurrent_change_during_staging_is_preserved(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{}')
        concurrent = '{"preferences": {"changed": true}}'
        with patch.object(installer.os, 'fsync', side_effect=lambda fd: self.config.write_text(concurrent)):
            with self.assertRaisesRegex(ValueError, 'modifiée'):
                self.install()
        self.assertEqual(self.config.read_text(), concurrent)
        self.assertEqual(list(self.config.parent.glob('*.reprise-*')), [])
        self.assertEqual(list(self.config.parent.glob('*.bak-*')), [])

    def test_backup_and_published_config_are_private_and_temporary_unique(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text('{}')
        fixed = self.config.with_name(self.config.name + '.reprise-tmp')
        fixed.write_text('unrelated')
        result = self.install()
        self.assertEqual(Path(result['backup']).stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(fixed.read_text(), 'unrelated')

    def test_old_log_does_not_prove_loaded_tools(self):
        log = installer.verification_log(self.home)
        log.parent.mkdir(parents=True)
        log.write_text('old launch failed')
        for result in (self.install(), self.install()):
            self.assertFalse(result['loaded'])
            self.assertFalse(result['launch_observed'])

    def test_default_state_is_stable_outside_source_and_respects_explicit_state(self):
        with patch.dict(os.environ, {}, clear=True):
            result = installer.install(home=self.home, app_running=False)
            self.assertEqual(result['state'],
                             str((self.home / 'Library/Application Support/Reprise/ux-state').resolve()))
        with patch.dict(os.environ, {'AGENT_RESUME_STATE': str(self.home / 'selected')}):
            self.assertEqual(installer.default_state(self.home), self.home / 'selected/ux-state')

    def test_failed_quit_or_open_is_reported(self):
        for running in (True, False):
            with self.subTest(running=running), patch.object(installer, 'claude_running', return_value=running), \
                    patch.object(installer.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'command')) as run:
                result = installer.relaunch(home=self.home, wait=False)
                self.assertFalse(result['relaunch_performed'])
                self.assertTrue(run.call_args.kwargs['check'])

    def test_relaunch_log_change_is_only_launch_observation(self):
        log = installer.verification_log(self.home)
        log.parent.mkdir(parents=True)
        def opened(*args, **kwargs):
            log.write_text('server process failed to import')
        with patch.object(installer, 'claude_running', return_value=False), \
                patch.object(installer.subprocess, 'run', side_effect=opened):
            result = installer.relaunch(home=self.home)
        self.assertTrue(result['relaunch_performed'])
        self.assertTrue(result['launch_observed'])
        self.assertFalse(result['loaded'])
    def test_relative_paths_resolve_against_current_directory(self):
        work = (self.home / 'work').resolve()
        work.mkdir()
        attach = work / 'code.swift'
        attach.write_text('struct V {}', encoding='utf-8')
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(work)
        self.install(state='relative-state', attachment='code.swift')
        args = self.read_config()['mcpServers'][installer.NAME]['args']
        self.assertIn(str(work / 'relative-state'), args)
        self.assertIn(str(attach), args)
        # The written entry must be recognized unchanged from another cwd.
        elsewhere = self.home / 'elsewhere'
        elsewhere.mkdir()
        os.chdir(elsewhere)
        second = self.install(state=work / 'relative-state', attachment=attach)
        self.assertFalse(second['changed'])

    def test_null_existing_entry_requires_force(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(json.dumps({'mcpServers': {installer.NAME: None}}), encoding='utf-8')
        before = self.config.read_bytes()
        with self.assertRaises(FileExistsError):
            self.install()
        self.assertEqual(self.config.read_bytes(), before)
        result = self.install(force=True)
        self.assertTrue(result['changed'])
        self.assertIsInstance(self.read_config()['mcpServers'][installer.NAME], dict)

    def test_non_object_mcp_servers_refused_without_write(self):
        for broken in (None, [], 'x'):
            with self.subTest(broken=broken):
                self.config.parent.mkdir(parents=True, exist_ok=True)
                self.config.write_text(json.dumps({'mcpServers': broken}), encoding='utf-8')
                before = self.config.read_bytes()
                with self.assertRaises(ValueError):
                    self.install()
                self.assertEqual(self.config.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
