import importlib.machinery
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

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
        self.assertIn(str(attach), entry['args'])
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


if __name__ == '__main__':
    unittest.main()
