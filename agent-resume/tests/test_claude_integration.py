from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import claude_integration as integration
import harness_integration
import package_helpers
import state_install


class ClaudeIntegrationTests(unittest.TestCase):
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
        # The new facade may be outside package_helpers.ASSETS during parallel implementation.
        if 'claude_mcp.py' not in payload['files']:
            (self.root / 'claude_mcp.py').write_text('# fixture facade\n')
            payload['files']['claude_mcp.py'] = integration.file_hash(self.root / 'claude_mcp.py')
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
        _, self.config, self.managed = integration.paths(self.home)

    def plan(self, **kwargs):
        return integration.plan(self.root, self.home, self.project, **kwargs)

    def install(self):
        return integration.apply(self.root, self.plan())

    def config_write(self, value):
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps(value))

    def test_plan_read_only_and_no_secrets(self):
        self.config_write({'custom': 'fixture-secret', 'mcpServers': {'other': {'token': 'fixture-secret'}}})
        before = harness_integration.tree_files(self.home)
        plan = self.plan()
        self.assertNotIn('fixture-secret', json.dumps(plan))
        self.assertEqual(harness_integration.tree_files(self.home), before)
        self.install()
        current = json.loads(self.config.read_text())
        self.assertEqual(current['custom'], 'fixture-secret')
        self.assertEqual(current['mcpServers']['other']['token'], 'fixture-secret')

    def test_new_install_immutable_runtime_and_idempotence(self):
        self.assertFalse(self.config.exists())
        result = self.install()
        self.assertTrue(result['changed'])
        entry = json.loads(self.config.read_text())['mcpServers']['reprise-harness']
        self.assertIn('/integration-releases/', entry['command'])
        self.assertIn('--expert-state', entry['args'])
        self.assertIn(str(self.state / 'quick-state'), entry['args'])
        self.assertEqual(entry['args'][:3], ['-E', '-s', '-B'])
        self.assertTrue(integration.inspect(self.root, self.home)['managed'])
        self.assertFalse(self.install()['changed'])
        for item in (self.managed / 'backups').rglob('*.json'):
            self.assertEqual(item.stat().st_mode & 0o777, 0o600)

    def test_legacy_managed_name_migrates_and_other_reprise_is_preserved(self):
        self.install()
        value = json.loads(self.config.read_text())
        value['mcpServers']['reprise'] = value['mcpServers'].pop('reprise-harness')
        self.config_write(value)
        target = self.managed / 'managed.json'
        old = json.loads(target.read_text())
        old.pop('connector_name')
        target.write_text(json.dumps(old))
        self.install()
        self.assertNotIn('reprise', json.loads(self.config.read_text())['mcpServers'])
        value = json.loads(self.config.read_text())
        value['mcpServers']['reprise'] = {'command': 'unrelated-store-connector'}
        self.config_write(value)
        self.install()
        self.assertEqual(json.loads(self.config.read_text())['mcpServers']['reprise'],
                         {'command': 'unrelated-store-connector'})

    def test_custom_entry_and_managed_edit_refused(self):
        self.config_write({'mcpServers': {'reprise-harness': {'command': 'custom'}}})
        with self.assertRaises(ValueError): self.plan()
        self.config.unlink()
        self.install()
        value = json.loads(self.config.read_text())
        value['mcpServers']['reprise-harness']['args'].append('--custom')
        self.config_write(value)
        with self.assertRaises(ValueError): self.plan()
        with self.assertRaises(ValueError): self.plan(action='remove')

    def test_concurrent_configuration_and_tampered_plan_refused(self):
        proposed = self.plan()
        self.config_write({'custom': True})
        with self.assertRaises(ValueError): integration.apply(self.root, proposed)
        proposed = self.plan()
        proposed['project'] = str(self.home)
        with self.assertRaises(ValueError): integration.apply(self.root, proposed)
        self.assertEqual(json.loads(self.config.read_text()), {'custom': True})

    def test_probe_replacement_is_explicit_and_narrow(self):
        probe = {'command': '/usr/bin/python3', 'args': ['/old/root/desktop_job_probe.py', '--state', '/private/tmp/probe/state']}
        self.config_write({'mcpServers': {'reprise_quick_probe': probe}})
        integration.apply(self.root, self.plan())
        self.assertIn('reprise_quick_probe', json.loads(self.config.read_text())['mcpServers'])
        integration.apply(self.root, self.plan(replace_probe=True))
        self.assertNotIn('reprise_quick_probe', json.loads(self.config.read_text())['mcpServers'])
        value = json.loads(self.config.read_text())
        value['mcpServers']['reprise_quick_probe'] = {'args': ['custom.py', 'desktop_job_probe.py']}
        self.config_write(value)
        with self.assertRaises(ValueError): self.plan(replace_probe=True)

    def test_remove_only_owned_entry_preserves_others_and_state(self):
        self.config_write({'mcpServers': {'other': {'command': 'other'}}})
        self.install()
        marker = self.state / 'unclaimed-result'
        marker.write_text('preserve')
        integration.apply(self.root, self.plan(action='remove'))
        self.assertEqual(json.loads(self.config.read_text())['mcpServers'], {'other': {'command': 'other'}})
        self.assertEqual(marker.read_text(), 'preserve')
        self.assertFalse(integration.apply(self.root, self.plan(action='remove'))['changed'])

    def test_symlink_refused(self):
        self.config.parent.mkdir(parents=True)
        self.config.symlink_to(self.base / 'absent')
        with self.assertRaises(ValueError): self.plan()

    def test_manifest_failure_rolls_back_own_configuration(self):
        self.config_write({'custom': 'original'})
        before = self.config.read_bytes()
        proposed = self.plan()
        original = integration.atomic
        def fail(target, text):
            if target == self.managed / 'managed.json': raise OSError('fixture')
            return original(target, text)
        with patch.object(integration, 'atomic', side_effect=fail), self.assertRaises(OSError):
            integration.apply(self.root, proposed)
        self.assertEqual(self.config.read_bytes(), before)

    def test_config_failure_after_replace_rolls_back_exact_publication(self):
        self.config_write({'original': True})
        before = self.config.read_bytes()
        proposed = self.plan()
        original = integration.atomic
        failed = False
        def fail(target, text):
            nonlocal failed
            original(target, text)
            if target == self.config and not failed:
                failed = True
                raise OSError('fixture post-replace fsync failure')
        with patch.object(integration, 'atomic', side_effect=fail), self.assertRaises(OSError):
            integration.apply(self.root, proposed)
        self.assertEqual(self.config.read_bytes(), before)

    def test_rollback_does_not_overwrite_concurrent_edit(self):
        proposed = self.plan()
        original = integration.atomic
        def fail(target, text):
            if target == self.managed / 'managed.json':
                self.config.write_text('{"concurrent": true}')
                raise OSError('fixture')
            return original(target, text)
        with patch.object(integration, 'atomic', side_effect=fail), self.assertRaises(OSError):
            integration.apply(self.root, proposed)
        self.assertEqual(json.loads(self.config.read_text()), {'concurrent': True})

    def test_verify_is_no_inference_temporary_state_probe(self):
        self.install()
        names = ['get_task', 'get_result', 'submit_result', 'reprise_backends', 'reprise_requests', 'reprise_delegate', 'reprise_followup']
        result = subprocess.CompletedProcess([], 0, stdout='\n'.join(json.dumps(m) for m in [
            {'id': 1, 'result': {'serverInfo': {'version': 'fixture'}}},
            {'id': 2, 'result': {'tools': [{'name': name} for name in names]}}]), stderr='')
        with patch.object(integration.subprocess, 'run', return_value=result) as run:
            verified = integration.verify(self.root, self.home)
        self.assertFalse(verified['inference_executed'])
        self.assertEqual(verified['client_loaded'], 'unknown')
        command = run.call_args.args[0]
        self.assertNotEqual(command[command.index('--expert-state')+1], str(self.state))
        self.assertNotIn('tools/call', run.call_args.kwargs['input'])


if __name__ == '__main__': unittest.main()
