"""`resume ux-review-config` is dispatched by the resume header, before the
heavy imports: a --check must never initialize the SQLite state, and every
installer refusal must reach the operator as the JSON refusal contract
({'status': 'refused', 'message', 'recovery'}, exit 1), never a traceback.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NAME = 'reprise_ux_review'


class UxReviewBridgeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.home = self.folder / 'home'
        self.config = self.home / 'Library/Application Support/Claude/claude_desktop_config.json'
        environment = dict(os.environ, HOME=str(self.home))
        for key in ('AGENT_RESUME_STATE', 'REPRISE_ALLOW_NEW_STATE', 'PYTHONPATH'):
            environment.pop(key, None)
        self.environment = environment

    def run_bridge(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(ROOT / 'resume'), 'ux-review-config', *args],
                              cwd=cwd if cwd is not None else self.folder, env=self.environment,
                              capture_output=True, text=True, timeout=30)

    def write_config(self, content):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        raw = content if isinstance(content, str) else json.dumps(content)
        self.config.write_text(raw, encoding='utf-8')
        return raw.encode('utf-8')

    def test_check_plans_without_creating_state_or_config(self):
        result = self.run_bridge('--check')
        self.assertEqual(result.returncode, 0, result.stderr)
        planned = json.loads(result.stdout)
        self.assertTrue(planned['dry_run'])
        self.assertTrue(planned['changed'])
        self.assertIsNone(planned['backup'])
        self.assertEqual(planned['state'],
                         str(self.home / 'Library/Application Support/Reprise/ux-state'))
        self.assertFalse(self.home.exists())
        self.assertEqual(list(self.folder.rglob('*.sqlite')), [])
        self.assertEqual(list(self.folder.rglob('*.sqlite-*')), [])

    def test_refusals_reach_the_contract_without_traceback_or_write(self):
        cases = [
            ('json-invalide', '{oops'),
            ('mcpServers-null', {'mcpServers': None}),
            ('mcpServers-liste', {'mcpServers': []}),
            ('mcpServers-chaine', {'mcpServers': 'x'}),
            ('entree-divergente', {'mcpServers': {NAME: {'command': 'x', 'args': []}}}),
        ]
        for label, content in cases:
            with self.subTest(case=label):
                before = self.write_config(content)
                result = self.run_bridge('--check')
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('Traceback', result.stderr)
                refused = json.loads(result.stdout)
                self.assertEqual(refused['status'], 'refused')
                self.assertTrue(refused['message'])
                self.assertIn('recovery', refused)
                self.assertEqual(self.config.read_bytes(), before)

    def test_check_with_force_plans_replacement_without_writing(self):
        before = self.write_config({'mcpServers': {NAME: None}})
        result = self.run_bridge('--check', '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        planned = json.loads(result.stdout)
        self.assertTrue(planned['dry_run'])
        self.assertTrue(planned['changed'])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(list(self.config.parent.glob('*.bak-*')), [])

    def test_relative_paths_resolve_against_the_caller_cwd(self):
        work = self.home / 'work'
        work.mkdir(parents=True)
        (work / 'code.swift').write_text('struct V {}', encoding='utf-8')
        result = self.run_bridge('--check', '--state', 'relative-state',
                                 '--attachment', 'code.swift', cwd=work)
        self.assertEqual(result.returncode, 0, result.stderr)
        planned = json.loads(result.stdout)
        self.assertEqual(planned['state'], str(work / 'relative-state'))
        self.assertIn(str(work / 'relative-state'), planned['args'])
        self.assertIn(str(work / 'code.swift'), planned['args'])
        # Same entry planned from another cwd: identical, not a divergence.
        elsewhere = self.home / 'elsewhere'
        elsewhere.mkdir()
        again = self.run_bridge('--check', '--state', str(work / 'relative-state'),
                                '--attachment', str(work / 'code.swift'), cwd=elsewhere)
        self.assertEqual(again.returncode, 0, again.stderr)
        replanned = json.loads(again.stdout)
        self.assertEqual(replanned['args'], planned['args'])


if __name__ == '__main__':
    unittest.main()
