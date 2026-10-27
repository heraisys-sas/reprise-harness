import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from app_paths import native_binary, state_directory
from package_helpers import ASSETS, package

SOURCE = Path(__file__).parents[1]


class PackageHelpersTests(unittest.TestCase):
    def test_desktop_apply_assets_preserve_current_packaging(self):
        for name in ('CLAUDE-DESKTOP-APPLY.md', 'claude_desktop_apply.py',
                     'claude_quota_log.py', 'managed_update.py', 'proxy_service.py',
                     'delegation_reconcile.py'):
            self.assertIn(name, ASSETS)

    def test_moved_payload_runs_without_source_and_keeps_state_outside_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary).resolve()
            app = folder / 'build/Reprise.app'
            package(SOURCE, app)
            moved = folder / 'distributed/Reprise.app'
            moved.parent.mkdir()
            shutil.move(app, moved)
            root = moved / 'Contents/Resources/helpers'
            manifest = json.loads((root / 'payload-manifest.json').read_text())
            self.assertEqual(manifest['runtime'], 'external-python-required')
            imported = subprocess.run([sys.executable, '-c', 'import codex_expert_mcp'],
                                      cwd=root, capture_output=True, text=True)
            self.assertEqual(imported.returncode, 0, imported.stderr)
            self.assertTrue((root / 'install-ux-review.py').is_file())
            self.assertFalse((root / 'state').exists())
            self.assertFalse((root / 'tests').exists())
            self.assertFalse((root / 'quick-entry-com.example.reprise.plist').exists())
            self.assertFalse((root / 'ARCHITECTURE-CRITIQUE-ASTRA.md').exists())
            environment = dict(os.environ, HOME=str(folder / 'home'), REPRISE_ALLOW_NEW_STATE='1')
            environment.pop('AGENT_RESUME_STATE', None)
            environment.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, str(root / 'resume'), 'scheduler-status'],
                                    cwd=folder, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            json.loads(result.stdout)
            self.assertTrue((folder / 'home/Library/Application Support/Reprise/state/tasks.sqlite').exists())
            self.assertFalse((root / 'state').exists())
            self.assertEqual(native_binary(root, 'Vault'), moved / 'Contents/MacOS/Vault')
            # Repackaging must remove stale resources without touching user state.
            (root / 'stale.py').write_text('')
            package(SOURCE, moved)
            self.assertFalse((root / 'stale.py').exists())

    def test_bundled_explicit_initialization_without_environment_bypass(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary).resolve()
            root = package(SOURCE, folder / 'Reprise.app')
            environment = dict(os.environ, HOME=str(folder / 'home'))
            for key in ('AGENT_RESUME_STATE', 'REPRISE_ALLOW_NEW_STATE', 'PYTHONPATH'):
                environment.pop(key, None)
            target = folder / 'home/Library/Application Support/Reprise/state'
            result = subprocess.run([sys.executable, str(root / 'resume'), 'state-init', '--target', str(target)],
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, str(root / 'resume'), 'scheduler-status'],
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((target / 'tasks.sqlite').exists())
            self.assertFalse((root / 'state').exists())

    def test_packaged_setup_preview_and_apply_without_initial_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary).resolve()
            root = package(SOURCE, folder / 'Reprise.app')
            environment = dict(os.environ, HOME=str(folder / 'home'))
            for key in ('AGENT_RESUME_STATE', 'REPRISE_ALLOW_NEW_STATE', 'PYTHONPATH'):
                environment.pop(key, None)
            def command(*args, input=None):
                result = subprocess.run([sys.executable, str(root / 'resume'), *args], env=environment,
                                        input=input, capture_output=True, text=True)
                return result, json.loads(result.stdout)
            result, state = command('state-status')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(state['status'], 'needs_setup')
            self.assertFalse((folder / 'home').exists())
            result, planned = command('state-plan', '--operation', 'initialize', '--target', state['target'],
                                      '--quick-target', state['quick_target'])
            self.assertEqual(planned['status'], 'planned')
            self.assertFalse((folder / 'home').exists())
            result, refused = command('state-apply', input=json.dumps({'plan': planned['plan']}))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(refused['status'], 'refused')
            self.assertFalse((folder / 'home').exists())
            result, done = command('state-apply', input=json.dumps({'plan': planned['plan'], 'confirm_new': True}))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(done['status'], 'completed')
            result, state = command('state-status')
            self.assertEqual(state['status'], 'ready')
            self.assertFalse((Path(state['target']) / 'tasks.sqlite').exists())
            self.assertFalse((root / 'state').exists())

    def test_legacy_state_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            folder = Path(temporary).resolve()
            root = folder / 'Reprise.app/Contents/Resources/helpers'
            root.mkdir(parents=True)
            (folder / 'state').mkdir()
            (folder / 'state/tasks.sqlite').touch()
            with patch('pathlib.Path.home', return_value=folder / 'home'):
                with self.assertRaisesRegex(RuntimeError, 'import sauvegardé'):
                    state_directory(root)
                os.environ['AGENT_RESUME_STATE'] = str(folder / 'state')
                self.assertEqual(state_directory(root), folder / 'state')
            (folder / 'state/tasks.sqlite').unlink()
            os.environ.pop('AGENT_RESUME_STATE', None)
            with patch('pathlib.Path.home', return_value=folder / 'home'):
                with self.assertRaisesRegex(RuntimeError, 'Initialisation du prototype bloquée'):
                    state_directory(root)
            self.assertEqual(native_binary(folder, 'Vault'), folder / 'Reprise.app/Contents/MacOS/Vault')
