import hashlib
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
import package_helpers

SOURCE = Path(__file__).parents[1]


class PackageHelpersTests(unittest.TestCase):
    def test_yaml_package_is_closed_and_refuses_missing_or_symlinked_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'source'
            yaml = source / 'vendor/yaml'
            yaml.mkdir(parents=True)
            (source / 'VERSION').write_text('1')
            for name in package_helpers.YAML_FILES:
                (yaml / name).write_text(name)
            (yaml / '.env').write_text('private')
            (yaml / 'token.pem').write_text('private')
            (yaml / '__pycache__').mkdir()
            (yaml / '__pycache__/cache.py').write_text('cache')
            with patch.object(package_helpers, 'ASSETS', ('VERSION',)), patch.object(package_helpers, 'DOCS', ()):
                target = package(source, root / 'app') / 'vendor/yaml'
                self.assertEqual({p.name for p in target.iterdir()}, set(package_helpers.YAML_FILES))
                (yaml / 'LICENSE').unlink()
                with self.assertRaisesRegex(ValueError, 'LICENSE'):
                    package(source, root / 'app')
                (yaml / 'LICENSE').symlink_to(yaml / '__init__.py')
                with self.assertRaisesRegex(ValueError, 'LICENSE'):
                    package(source, root / 'app')

    def test_desktop_apply_assets_preserve_current_packaging(self):
        for name in ('claude_desktop_apply.py',
                     'claude_quota_log.py', 'claude_cdp_pipe.py', 'claude_inbox.py', 'websocket_transport.py', 'claude_session_store.py', 'claude_session_observer.py', 'managed_update.py',
                     'delegation_reconcile.py'):
            self.assertIn(name, ASSETS)
        self.assertTrue(any(path.endswith('/CLAUDE-DESKTOP-APPLY.md') for path in package_helpers.DOCS))

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
            for name in ('apple_expert.py', 'apple_pilot.py', 'apple_pilot_service.py', 'apple_pilot_transport.py', 'orchestrator_memory.py', 'orchestrator_memory_service.py', 'reprise_memory_adapter.py', 'reprise_memory_mcp.py', 'memory_integration.py', 'memory_admin.py', 'coordinator_jobs.py', 'coordinator_local.py'):
                self.assertTrue((root / name).is_file())
                self.assertEqual(manifest['files'][name], hashlib.sha256((root / name).read_bytes()).hexdigest())
                self.assertEqual((root / name).read_bytes(), (SOURCE / name).read_bytes())
            doc = 'ORCHESTRATOR-MEMORY-CONTRACT.md'
            self.assertTrue((root / doc).is_file())
            self.assertEqual(manifest['files'][doc], hashlib.sha256((root / doc).read_bytes()).hexdigest())
            self.assertEqual((root / doc).read_bytes(), (SOURCE.parent / 'docs/contracts' / doc).read_bytes())
            # Memory-admin resources belong to the independent memory lot.
            admin_doc = 'MEMORY-ADMIN.md'
            self.assertEqual((root/admin_doc).read_bytes(),
                             (SOURCE.parent/'docs/runbooks'/admin_doc).read_bytes())
            self.assertEqual(manifest['files'][admin_doc], hashlib.sha256((root/admin_doc).read_bytes()).hexdigest())
            from project_registry import ProjectRegistry
            memory_state = folder/'memory-admin-state'; memory_state.mkdir(mode=0o700)
            fixture_repo = folder/'fixture-repo'; fixture_repo.mkdir()
            subprocess.run(['git','-C',str(fixture_repo),'init','-q'],check=True)
            with ProjectRegistry(memory_state) as registry:
                registry.create('Portable', fixture_repo, fixture_repo)
            before = {str(p.relative_to(memory_state)): p.read_bytes() if p.is_file() else None
                      for p in memory_state.rglob('*')}
            environment = dict(os.environ)
            environment.pop('PYTHONPATH', None)
            for command in ('memory-inspect', 'memory-migration-plan', 'memory-scope-list'):
                admin = subprocess.run([sys.executable, '-E', '-s', str(root/'reprise'), command,
                    '--state', str(memory_state)], cwd=folder, env=environment,
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(admin.returncode, 0, admin.stderr + admin.stdout)
                json.loads(admin.stdout)
            self.assertEqual(before, {str(p.relative_to(memory_state)): p.read_bytes() if p.is_file() else None
                                     for p in memory_state.rglob('*')})
            imported = subprocess.run([sys.executable, '-c', 'import codex_expert_mcp, claude_desktop_apply, claude_inbox, websocket_transport, claude_session_store, claude_session_observer, apple_pilot, apple_pilot_transport, apple_pilot_service, orchestrator_memory, orchestrator_memory_service'],
                                      cwd=root, capture_output=True, text=True)
            self.assertEqual(imported.returncode, 0, imported.stderr)
            memory = subprocess.run([sys.executable, '-B', str(root / 'reprise_memory_mcp.py'),
                '--state', str(folder), '--project-id', '67e17924-f8a5-4144-a239-71625f618c4d',
                '--harness-id', 'packaged-reader'],
                input=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}) + '\n',
                cwd=folder, capture_output=True, text=True, timeout=10)
            self.assertEqual(memory.returncode, 0, memory.stderr)
            self.assertEqual(memory.stderr, '')
            names = [tool['name'] for tool in json.loads(memory.stdout)['result']['tools']]
            self.assertIn('reprise_memory_search', names)
            self.assertNotIn('reprise_memory_capture', names)
            pilot = subprocess.run([sys.executable, str(root / 'apple_pilot_service.py'), '--state', str(folder)],
                                   input=json.dumps({'id': 'c03aa24b-bdb5-46e1-bca7-16544d55b258', 'op': 'catalog'}) + '\n',
                                   cwd=folder, capture_output=True, text=True, timeout=20)
            self.assertEqual(pilot.returncode, 0, pilot.stderr)
            self.assertEqual(json.loads(pilot.stdout)['status'], 'finished')
            self.assertTrue((root / 'install-ux-review.py').is_file())
            project = folder / 'project'
            project.mkdir()
            quick_state, expert_state = folder / 'temporary-state/quick', folder / 'temporary-state/expert'
            environment = dict(os.environ)
            for key in ('AGENT_RESUME_STATE', 'PYTHONPATH', 'REPRISE_ALLOW_NEW_STATE'):
                environment.pop(key, None)
            requests = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
            ]
            launched = subprocess.run([sys.executable, str(root / 'claude_mcp.py'),
                                        '--project-root', str(project), '--state', str(quick_state),
                                        '--expert-state', str(expert_state), '--enable-await'],
                                       cwd=folder, env=environment,
                                       input='\n'.join(json.dumps(message) for message in requests) + '\n',
                                       capture_output=True, text=True, timeout=20)
            self.assertEqual(launched.returncode, 0, launched.stderr)
            replies = [json.loads(line) for line in launched.stdout.splitlines()]
            initialized = next(message['result'] for message in replies if message.get('id') == 1)
            tools = next(message['result']['tools'] for message in replies if message.get('id') == 2)
            self.assertEqual(initialized['serverInfo']['name'], 'reprise-harness')
            self.assertIn('await_task', {tool['name'] for tool in tools})
            self.assertTrue((quick_state / 'quick.sqlite').is_file())
            self.assertTrue((expert_state / 'expert.sqlite').is_file())
            for module in ('routing_events.py', 'external_rankings.py', 'openrouter_adapter.py', 'artificial_analysis_adapter.py'):
                self.assertTrue((root / module).is_file())
            self.assertFalse((root / 'state').exists())
            self.assertFalse((root / 'tests').exists())
            self.assertFalse((root / 'quick-entry-com.example.reprise.plist').exists())
            self.assertFalse((root / 'ROADMAP.md').exists())
            environment = dict(os.environ, HOME=str(folder / 'home'), REPRISE_ALLOW_NEW_STATE='1')
            environment.pop('AGENT_RESUME_STATE', None)
            environment.pop('PYTHONPATH', None)
            result = subprocess.run([sys.executable, str(root / 'reprise'), 'scheduler-status'],
                                    cwd=folder, env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            json.loads(result.stdout)
            self.assertTrue((folder / 'home/Library/Application Support/Reprise/state/tasks.sqlite').exists())
            self.assertFalse((root / 'state').exists())
            self.assertEqual(native_binary(root, 'Vault'), moved / 'Contents/MacOS/Vault')
            # Repackaging must remove stale resources without touching user state.
            (root / 'stale.py').write_text('')
            for retired in ('proxy_service.py', 'asi1_planner_proxy.py', 'PROXY-SERVICE.md'):
                (root / retired).write_text('retired fixture')
            package(SOURCE, moved)
            self.assertFalse((root / 'stale.py').exists())
            for retired in ('proxy_service.py', 'asi1_planner_proxy.py', 'PROXY-SERVICE.md'):
                self.assertFalse((root / retired).exists())

    def test_bundled_explicit_initialization_without_environment_bypass(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary).resolve()
            root = package(SOURCE, folder / 'Reprise.app')
            environment = dict(os.environ, HOME=str(folder / 'home'))
            for key in ('AGENT_RESUME_STATE', 'REPRISE_ALLOW_NEW_STATE', 'PYTHONPATH'):
                environment.pop(key, None)
            target = folder / 'home/Library/Application Support/Reprise/state'
            result = subprocess.run([sys.executable, str(root / 'reprise'), 'state-init', '--target', str(target)],
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, str(root / 'reprise'), 'scheduler-status'],
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
                result = subprocess.run([sys.executable, str(root / 'reprise'), *args], env=environment,
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
