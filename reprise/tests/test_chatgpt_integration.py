from contextlib import closing
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import chatgpt_integration as integration
import harness_integration
import package_helpers
import state_install


class ChatGPTIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = package_helpers.package(SOURCE, self.base / 'Reprise.app')
        runtime = self.root.parent / 'runtime'
        (runtime / 'bin').mkdir(parents=True)
        (runtime / 'bin/python3').write_text('#!/bin/sh\nexit 99\n')
        (runtime / 'bin/python3').chmod(0o700)
        (runtime / 'runtime-manifest.json').write_text(json.dumps({'files': harness_integration.tree_files(runtime)}))
        payload = json.loads((self.root / 'payload-manifest.json').read_text())
        payload['runtime'] = {'fixture': True}
        if 'chatgpt_mcp.py' not in payload['files']:
            (self.root / 'chatgpt_mcp.py').write_text('# fixture facade\n')
            payload['files']['chatgpt_mcp.py'] = integration.file_hash(self.root / 'chatgpt_mcp.py')
        (self.root / 'payload-manifest.json').write_text(json.dumps(payload))
        self.state = self.base / 'state'
        state_install.initialize(self.state)
        env = patch.dict(os.environ, {'AGENT_RESUME_STATE': str(self.state)})
        env.start()
        self.addCleanup(env.stop)
        self.home = self.base / 'home'
        self.home.mkdir()
        self.project = self.base / 'project'
        self.project.mkdir()
        self.home, self.config, self.managed = integration.paths(self.home)

    def plan(self, project=None, **kwargs):
        return integration.plan(self.root, self.home, project or self.project, **kwargs)

    def install(self):
        return integration.apply(self.root, self.plan())

    def entry(self):
        doc = tomllib.loads(self.config.read_text())
        return doc['mcp_servers']['reprise']

    def bridge_response(self):
        names = ['get_task', 'get_result', 'submit_result', 'reprise_backends', 'reprise_delegate', 'reprise_followup']
        return subprocess.CompletedProcess([], 0, stdout='\n'.join(json.dumps(m) for m in [
            {'id': 1, 'result': {'serverInfo': {'version': 'fixture'}}},
            {'id': 2, 'result': {'tools': [{'name': name} for name in names]}}]), stderr='')

    def resync_release_evidence(self):
        release = self.state.parent / 'integration-releases'
        release = next(release.iterdir())
        evidence = json.loads((release / 'release.json').read_text())
        actual = harness_integration.tree_files(release)
        actual.pop('release.json', None)
        evidence['files'] = actual
        (release / 'release.json').write_text(json.dumps(evidence))
        return release

    def fixture_config(self):
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            '# préférences Codex\nmodel = "gpt-5.1"\n\n[mcp_servers.other]\ncommand = "/usr/bin/true"\nargs = []\n'
            'env = {TOKEN = "secret"}\n\n[profiles.work]\nmodel = "gpt-5.1-mini"\n')

    def test_inspect_empty_is_read_only(self):
        before = harness_integration.tree_files(self.home)
        inspected = integration.inspect(self.root, self.home)
        self.assertFalse(inspected['config_exists'])
        self.assertFalse(inspected['configuration_written'])
        self.assertFalse(inspected['managed'])
        self.assertIsNone(inspected['project'])
        self.assertEqual(harness_integration.tree_files(self.home), before)

    def test_plan_is_read_only_and_secret_free(self):
        before = harness_integration.tree_files(self.home)
        preview = self.plan()
        self.assertEqual(preview['action'], 'install')
        self.assertEqual(harness_integration.tree_files(self.home), before)
        self.assertNotIn(str(self.base.parent), json.dumps(preview).replace(str(self.base), ''))

    def test_install_writes_config_toml_reprise_table(self):
        result = self.install()
        self.assertTrue(result['changed'])
        entry = self.entry()
        self.assertEqual(set(entry), {'command', 'args', 'enabled'})
        self.assertTrue(entry['enabled'])
        self.assertIn('/integration-releases/', entry['command'])
        self.assertEqual(entry['args'][:3], ['-E', '-s', '-B'])
        self.assertIn('chatgpt_mcp.py', entry['args'][3])
        self.assertIn('--project-root', entry['args'])
        self.assertEqual(entry['args'][entry['args'].index('--state') + 1], str(self.state / 'chatgpt-state'))
        self.assertEqual(entry['args'][entry['args'].index('--expert-state') + 1], str(self.state))
        self.assertIn(str(self.project), entry['args'])
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertEqual(manifest['format'], 1)
        self.assertEqual(manifest['status'], 'installed')
        self.assertEqual(manifest['entry_sha256'], integration.entry_hash(entry))
        self.assertTrue(integration.inspect(self.root, self.home)['managed'])
        self.assertTrue(self.config.parent.stat().st_mode & 0o777 <= 0o755)
        for item in (self.managed / 'backups').rglob('*'):
            if item.is_file():
                self.assertEqual(item.stat().st_mode & 0o777, 0o600)

    def test_apply_preserves_unmanaged_fixture(self):
        self.fixture_config()
        self.install()
        doc = tomllib.loads(self.config.read_text())
        self.assertEqual(doc['model'], 'gpt-5.1')
        self.assertEqual(doc['mcp_servers']['other'],
                         {'command': '/usr/bin/true', 'args': [], 'env': {'TOKEN': 'secret'}})
        self.assertEqual(doc['profiles']['work'], {'model': 'gpt-5.1-mini'})
        self.assertIn('# préférences Codex', self.config.read_text())
        self.assertEqual(integration.inspect(self.root, self.home)['managed'], True)

    def test_install_refuses_unmanaged_reprise_entry(self):
        self.fixture_config()
        self.config.write_text(self.config.read_text()
                               + '\n[mcp_servers.reprise]\ncommand = "/usr/bin/custom"\nargs = []\n')
        with self.assertRaisesRegex(ValueError, 'non gérée'):
            self.plan()
        self.assertNotIn('[mcp_servers.reprise]', self.config.read_text().split('[profiles')[0])

    def test_idempotent_replan_yields_changed_false(self):
        self.install()
        again = self.install()
        self.assertFalse(again['changed'])
        config = self.config.read_text()
        self.assertEqual(integration.apply(self.root, self.plan())['changed'], False)
        self.assertEqual(self.config.read_text(), config)

    def test_tampered_plan_refused(self):
        proposed = self.plan()
        tampered = dict(proposed, project=str(self.home))
        with self.assertRaisesRegex(ValueError, 'Plan modifié'):
            integration.apply(self.root, tampered)
        self.assertFalse(self.config.exists())

    def test_install_refuses_unknown_facade(self):
        (self.root / 'chatgpt_mcp.py').unlink()
        payload = json.loads((self.root / 'payload-manifest.json').read_text())
        payload['files'].pop('chatgpt_mcp.py', None)
        (self.root / 'payload-manifest.json').write_text(json.dumps(payload))
        with self.assertRaises(ValueError):
            self.plan()

    def test_remove_removes_table_keeps_manifest_and_rest(self):
        self.fixture_config()
        self.install()
        published = self.config.read_bytes()
        result = integration.apply(self.root, self.plan(action='remove'))
        self.assertTrue(result['changed'])
        self.assertNotIn('[mcp_servers.reprise]', self.config.read_text())
        doc = tomllib.loads(self.config.read_text())
        self.assertEqual(doc['mcp_servers']['other'], {'command': '/usr/bin/true', 'args': [], 'env': {'TOKEN': 'secret'}})
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertEqual(manifest['status'], 'removed')
        self.assertIsNone(manifest['entry_sha256'])
        self.assertFalse(integration.inspect(self.root, self.home)['managed'])
        self.assertFalse(integration.apply(self.root, self.plan(action='remove'))['changed'])
        # The published blob stays recoverable in the timestamped backup.
        self.assertIn(published, [item.read_bytes() for item in (self.managed / 'backups').rglob('config.toml')])

    def test_remove_without_install_refused(self):
        with self.assertRaises(ValueError):
            self.plan(action='remove')

    def test_remove_with_other_project_refused(self):
        self.install()
        other = self.base / 'other'
        other.mkdir()
        with self.assertRaises(ValueError):
            self.plan(action='remove', project=other)

    def test_symlink_refused(self):
        self.config.parent.mkdir(parents=True)
        self.config.symlink_to(self.base / 'absent')
        with self.assertRaises(ValueError):
            self.plan()

    def test_concurrent_edit_refused(self):
        proposed = self.plan()
        self.install()
        self.config.write_text(self.config.read_text().replace('enabled = true', 'enabled = false'))
        with self.assertRaises(ValueError):
            integration.apply(self.root, proposed)

    def test_manifest_failure_rolls_back_own_publication(self):
        self.fixture_config()
        before = integration.file_hash(self.config)
        proposed = self.plan()
        original = integration.atomic
        def fail(target, text):
            if target == self.managed / 'managed.json':
                raise OSError('fixture')
            return original(target, text)
        with patch.object(integration, 'atomic', side_effect=fail), self.assertRaises(OSError):
            integration.apply(self.root, proposed)
        self.assertEqual(integration.file_hash(self.config), before)
        self.assertNotIn('[mcp_servers.reprise]', self.config.read_text())
        self.assertEqual(integration.file_hash(self.managed / 'managed.json'), None)

    def test_verify_with_subprocess_patch(self):
        self.install()
        with patch.object(integration.subprocess, 'run', return_value=self.bridge_response()) as run:
            verified = integration.verify(self.root, self.home)
        self.assertEqual(verified['status'], 'verified')
        self.assertTrue(verified['config_written'])
        self.assertFalse(verified['inference_executed'])
        self.assertEqual(verified['client_loaded'], 'unknown')
        self.assertEqual(verified['bridge_version'], 'fixture')
        self.assertIn('MCP servers → Restart', verified['message'])
        self.assertIn('MCP servers → Restart', verified['next_step'])
        command = run.call_args.args[0]
        self.assertIn('--state', command)
        self.assertNotEqual(command[command.index('--state') + 1], str(self.state / 'chatgpt-state'))
        self.assertNotEqual(command[command.index('--expert-state') + 1], str(self.state))
        self.assertNotIn('tools/call', run.call_args.kwargs.get('input', ''))

    def test_verify_refuses_incomplete_tools(self):
        self.install()
        partial = subprocess.CompletedProcess([], 0, stdout='\n'.join(json.dumps(m) for m in [
            {'id': 1, 'result': {'serverInfo': {'version': 'fixture'}}},
            {'id': 2, 'result': {'tools': [{'name': 'get_task'}]}}]), stderr='')
        with patch.object(integration.subprocess, 'run', return_value=partial), self.assertRaises(ValueError):
            integration.verify(self.root, self.home)

    def test_verify_refuses_missing_table(self):
        self.fixture_config()
        self.install()
        text = self.config.read_text()
        self.config.write_text(text[:text.index('[mcp_servers.reprise]')])
        with patch.object(integration.subprocess, 'run', return_value=self.bridge_response()), \
                self.assertRaisesRegex(ValueError, 'absente'):
            integration.verify(self.root, self.home)

    def test_verify_refuses_divergent_table(self):
        self.install()
        text = self.config.read_text()
        self.config.write_text(text.replace('enabled = true', 'enabled = false'))
        with patch.object(integration.subprocess, 'run', return_value=self.bridge_response()), \
                self.assertRaisesRegex(ValueError, 'divergente'):
            integration.verify(self.root, self.home)

    def test_verify_real_handshake_with_fake_runtime(self):
        self.install()
        release = self.state.parent / 'integration-releases'
        release = next(release.iterdir())
        python3 = release / 'Contents/Resources/runtime/bin/python3'
        responses = self.bridge_response().stdout
        script = '#!/bin/sh\ncat > /dev/null\ncat <<\'FIXTURE\'\n%s\nFIXTURE\n' % responses
        python3.write_text(script)
        python3.chmod(0o700)
        evidence = json.loads((release / 'release.json').read_text())
        actual = harness_integration.tree_files(release)
        actual.pop('release.json', None)
        evidence['files'] = actual
        (release / 'release.json').write_text(json.dumps(evidence))
        verified = integration.verify(self.root, self.home)
        self.assertEqual(verified['status'], 'verified')
        self.assertEqual(verified['bridge_version'], 'fixture')
        self.assertEqual(verified['bridge_tools'], sorted(['get_task', 'get_result', 'submit_result',
                                                           'reprise_backends', 'reprise_delegate', 'reprise_followup']))

    def test_verify_requires_installed_manifest(self):
        with self.assertRaises(ValueError):
            integration.verify(self.root, self.home)

    def test_verify_refuses_release_outside_selected_installation(self):
        self.install()
        manifest_path = self.managed / 'managed.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['release'] = str(self.base / 'integration-releases' / ('f' * 64))
        manifest_path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'installation sélectionnée'):
            integration.verify(self.root, self.home)

    def test_cli_plan_and_apply_roundtrip(self):
        output = io.StringIO()
        with patch('sys.stdout', output):
            result = integration.main(self.root, ['chatgpt-integration-plan', '--home', str(self.home),
                                                  '--project', str(self.project)])
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'planned')
        integration.apply(self.root, payload['plan'])
        output = io.StringIO()
        with patch('sys.stdout', output), patch.object(integration.subprocess, 'run', return_value=self.bridge_response()):
            result = integration.main(self.root, ['chatgpt-integration-verify', '--home', str(self.home)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'verified')

    def test_cli_refusal_is_generic(self):
        output = io.StringIO()
        with patch('sys.stdout', output), patch.object(integration, 'plan', side_effect=ValueError('secret-token')):
            result = integration.main(self.root, ['chatgpt-integration-plan', '--home', str(self.home),
                                                  '--project', str(self.project)])
        self.assertEqual(result, 1)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload['status'], 'refused')
        self.assertNotIn('secret-token', output.getvalue())

    def test_config_toml_matches_manifest_and_keeps_other_content(self):
        self.fixture_config()
        self.install()
        entry = self.entry()
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertEqual(integration.entry_hash(entry), manifest['entry_sha256'])
        lines = self.config.read_text().splitlines()
        self.assertEqual(lines[lines.index('[mcp_servers.reprise]') + 1],
                         'command = ' + integration.toml_str(entry['command']))
        self.assertNotIn(str(self.base.parent), self.config.read_text().replace(str(self.base), ''))


if __name__ == '__main__': unittest.main()
