from contextlib import closing
import hashlib
import io
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
        self.assertNotIn('--enable-await', entry['args'])
        self.assertIn(str(self.state / 'quick-state'), entry['args'])
        self.assertEqual(entry['args'][:3], ['-E', '-s', '-B'])
        self.assertTrue(integration.inspect(self.root, self.home)['managed'])
        self.assertFalse(self.install()['changed'])
        for item in (self.managed / 'backups').rglob('*.json'):
            self.assertEqual(item.stat().st_mode & 0o777, 0o600)

    def test_enable_await_is_explicit_idempotent_and_can_be_removed_managed(self):
        preview = self.plan(enable_await=True)
        self.assertTrue(preview['enable_await'])
        self.assertNotEqual(preview['fingerprint'], self.plan()['fingerprint'])
        self.assertTrue(integration.apply(self.root, preview)['changed'])
        value = json.loads(self.config.read_text())
        self.assertIn('--enable-await', value['mcpServers']['reprise-harness']['args'])
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertIs(manifest['enable_await'], True)
        self.assertFalse(integration.apply(self.root, self.plan(enable_await=True))['changed'])

        update = self.plan()  # an ordinary managed update preserves the installed setting
        self.assertTrue(update['enable_await'])
        self.assertFalse(integration.apply(self.root, update)['changed'])

        disable = self.plan(enable_await=False)
        self.assertFalse(disable['enable_await'])
        self.assertTrue(integration.apply(self.root, disable)['changed'])
        value = json.loads(self.config.read_text())
        self.assertNotIn('--enable-await', value['mcpServers']['reprise-harness']['args'])
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertIs(manifest['enable_await'], False)
        self.assertFalse(self.install()['changed'])

    def test_repeated_remove_preserves_setting_but_new_install_defaults_disabled(self):
        integration.apply(self.root, self.plan(enable_await=True))
        remove = self.plan(action='remove')
        self.assertTrue(remove['enable_await'])
        self.assertTrue(integration.apply(self.root, remove)['changed'])
        removed_manifest = (self.managed / 'managed.json').read_bytes()

        repeated = self.plan(action='remove')
        self.assertTrue(repeated['enable_await'])
        self.assertFalse(repeated['changes'])
        self.assertFalse(integration.apply(self.root, repeated)['changed'])
        self.assertEqual((self.managed / 'managed.json').read_bytes(), removed_manifest)
        self.assertFalse(integration.inspect(self.root, self.home)['enable_await'])

        self.assertTrue(self.install()['changed'])
        manifest = json.loads((self.managed / 'managed.json').read_text())
        self.assertIs(manifest['enable_await'], False)
        self.assertNotIn('--enable-await', json.loads(self.config.read_text())['mcpServers']['reprise-harness']['args'])

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

    def legacy_entry(self, modified=False):
        self.install()
        value = json.loads(self.config.read_text())
        entry = value['mcpServers'].pop('reprise-harness')
        if modified:
            entry['args'].append('--custom')
        value['mcpServers']['reprise'] = entry
        self.config_write(value)

    def test_manifest_name_drift_migrates_only_hash_matched_legacy(self):
        self.legacy_entry()
        inspected = integration.inspect(self.root, self.home)
        self.assertTrue(inspected['managed'])
        self.assertEqual(inspected['connector_name'], 'reprise')
        self.assertEqual(inspected['manifest_connector_name'], 'reprise-harness')
        self.assertFalse(inspected['connector_name_matches'])
        self.assertTrue(self.install()['changed'])
        servers = json.loads(self.config.read_text())['mcpServers']
        self.assertEqual(set(servers), {'reprise-harness'})
        self.assertTrue(integration.inspect(self.root, self.home)['connector_name_matches'])
        self.assertFalse(self.install()['changed'])

    def test_manifest_name_drift_modified_legacy_refuses_install_and_remove(self):
        self.legacy_entry(modified=True)
        before = harness_integration.tree_files(self.home)
        inspected = integration.inspect(self.root, self.home)
        self.assertFalse(inspected['managed'])
        self.assertFalse(inspected['connector_name_matches'])
        for action in ('install', 'remove'):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'réconciliation explicite'):
                self.plan(action=action)
        self.assertEqual(harness_integration.tree_files(self.home), before)

    def test_manifest_name_drift_remove_only_hash_matched_legacy(self):
        self.legacy_entry()
        integration.apply(self.root, self.plan(action='remove'))
        self.assertEqual(json.loads(self.config.read_text())['mcpServers'], {})
        self.assertFalse(integration.apply(self.root, self.plan(action='remove'))['changed'])

    def test_manifest_name_drift_verify_requires_migration_before_probe(self):
        self.legacy_entry()
        with patch.object(integration.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'migration gérée'):
                integration.verify(self.root, self.home)
        run.assert_not_called()

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
        with patch.object(integration.subprocess, 'run', return_value=self.bridge_response()) as run:
            verified = integration.verify(self.root, self.home)
        self.assertFalse(verified['inference_executed'])
        self.assertEqual(verified['client_loaded'], 'unknown')
        command = run.call_args.args[0]
        self.assertNotEqual(command[command.index('--expert-state')+1], str(self.state))
        self.assertNotIn('tools/call', run.call_args.kwargs['input'])

    def bridge_response(self, enable_await=False):
        names = ['get_task', 'get_result', 'submit_result', 'reprise_backends', 'reprise_requests', 'reprise_delegate', 'reprise_followup']
        if enable_await:
            names.append('await_task')
        return subprocess.CompletedProcess([], 0, stdout='\n'.join(json.dumps(m) for m in [
            {'id': 1, 'result': {'serverInfo': {'version': 'fixture'}}},
            {'id': 2, 'result': {'tools': [{'name': name} for name in names]}}]), stderr='')

    def test_verify_probes_await_only_when_enabled(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.install()
                if enabled:
                    integration.apply(self.root, self.plan(enable_await=True))
                with patch.object(integration.subprocess, 'run', return_value=self.bridge_response(enabled)) as run:
                    verified = integration.verify(self.root, self.home)
                command = run.call_args.args[0]
                self.assertEqual('--enable-await' in command, enabled)
                self.assertNotEqual(command[command.index('--state')+1], str(self.state / 'quick-state'))
                self.assertNotIn('tools/call', run.call_args.kwargs['input'])
                self.assertEqual(verified['enable_await'], enabled)
                self.assertEqual(verified['await_tool_exposed'], enabled)
                self.assertEqual(('await_task' in verified['bridge_tools']), enabled)

    def test_verify_refuses_manifest_config_option_divergence(self):
        self.install()
        value = json.loads(self.config.read_text())
        value['mcpServers']['reprise-harness']['args'].append('--enable-await')
        self.config_write(value)
        with patch.object(integration.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'divergente'):
                integration.verify(self.root, self.home)
        run.assert_not_called()

    def test_old_manifest_without_enable_await_means_disabled(self):
        self.install()
        path = self.managed / 'managed.json'
        manifest = json.loads(path.read_text())
        manifest.pop('enable_await')
        path.write_text(json.dumps(manifest))
        with patch.object(integration.subprocess, 'run', return_value=self.bridge_response(False)) as run:
            verified = integration.verify(self.root, self.home)
        self.assertFalse(verified['enable_await'])
        self.assertNotIn('--enable-await', run.call_args.args[0])

    def test_enable_await_plan_tamper_and_staleness_are_refused(self):
        proposed = self.plan(enable_await=True)
        tampered = dict(proposed, enable_await=False)
        with self.assertRaisesRegex(ValueError, 'Plan modifié'):
            integration.apply(self.root, tampered)
        self.config_write({'concurrent': True})
        with self.assertRaises(ValueError):
            integration.apply(self.root, proposed)
        self.assertEqual(json.loads(self.config.read_text()), {'concurrent': True})

    def test_cli_accepts_explicit_enable_and_disable_options(self):
        output = io.StringIO()
        with patch('sys.stdout', output):
            result = integration.main(self.root, ['claude-integration-plan', '--home', str(self.home),
                                                  '--project', str(self.project), '--enable-await'])
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertTrue(payload['plan']['enable_await'])
        integration.apply(self.root, payload['plan'])

        output = io.StringIO()
        with patch('sys.stdout', output):
            result = integration.main(self.root, ['claude-integration-plan', '--home', str(self.home),
                                                  '--project', str(self.project)])
        self.assertEqual(result, 0)
        self.assertTrue(json.loads(output.getvalue())['plan']['enable_await'])

        output = io.StringIO()
        with patch('sys.stdout', output):
            result = integration.main(self.root, ['claude-integration-plan', '--home', str(self.home),
                                                  '--project', str(self.project), '--disable-await'])
        self.assertEqual(result, 0)
        self.assertFalse(json.loads(output.getvalue())['plan']['enable_await'])

    def test_cli_rejects_await_options_for_remove(self):
        self.install()
        before = harness_integration.tree_files(self.home)
        for option in ('--enable-await', '--disable-await'):
            with self.subTest(option=option):
                output = io.StringIO()
                with patch('sys.stdout', output):
                    result = integration.main(self.root, ['claude-integration-plan', '--home', str(self.home),
                                                          '--project', str(self.project), '--action', 'remove', option])
                self.assertEqual(result, 1)
                self.assertEqual(json.loads(output.getvalue())['status'], 'refused')
                self.assertEqual(harness_integration.tree_files(self.home), before)

    def test_verify_keeps_daemon_version_when_capabilities_fail(self):
        from engines import Unavailable
        self.install()
        with patch.object(integration.subprocess, 'run', return_value=self.bridge_response()), \
                patch('claude_desktop_rpc.Client') as client:
            client.return_value.call.side_effect = [
                {'serverInfo': {'version': '0.1.0'}},
                Unavailable('blocked_permission', 'Unsupported method')]
            verified = integration.verify(self.root, self.home)
        self.assertEqual(verified['daemon'], {'version': '0.1.0', 'binding': 'unknown',
                         'dispatch_supported': 'unknown', 'capabilities_observed': False})
        self.assertFalse(verified['inference_executed'])
        client.return_value.close.assert_called_once_with()
        self.assertEqual([call.args[0] for call in client.return_value.call.call_args_list],
                         ['initialize', 'quick_entry/capabilities'])

    def test_verify_reports_observed_daemon_binding(self):
        self.install()
        for matched in (True, False):
            with self.subTest(matched=matched), \
                    patch.object(integration.subprocess, 'run', return_value=self.bridge_response()), \
                    patch('claude_desktop_rpc.Client') as client:
                client.return_value.call.side_effect = [
                    {'serverInfo': {'version': 'fixture-daemon'}},
                    {'project_root': str(self.project), 'dispatch_supported': False,
                     'state_sha256': hashlib.sha256(str(self.state / 'quick-state').encode()).hexdigest()
                     if matched else 'wrong-state'}]
                verified = integration.verify(self.root, self.home)
            self.assertEqual(verified['daemon'], {'version': 'fixture-daemon',
                             'binding': 'matched' if matched else 'mismatch',
                             'dispatch_supported': False, 'capabilities_observed': True})
            client.return_value.close.assert_called_once_with()


if __name__ == '__main__': unittest.main()
