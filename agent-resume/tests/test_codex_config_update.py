"""Temporary-only tests for the explicit native Codex config update adapter."""
import json
import io
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import tomllib
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_config_update as update
import managed_update
from contextlib import contextmanager


class Client:
    def __init__(self, config):
        self.config = config
        self.version = 'v1'
        self.calls = []

    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'config/read':
            return {'layers': [{'name': {'type': 'user', 'profile': None, 'file': str(self.config)},
                                'version': self.version, 'config': tomllib.loads(self.config.read_text())}]}
        if method == 'config/batchWrite':
            if params['expectedVersion'] != self.version:
                raise ValueError('configVersionConflict')
            data = tomllib.loads(self.config.read_text())
            for edit in params['edits']:
                _, name, key = edit['keyPath'].split('.')
                data['mcp_servers'][name][key] = edit['value']
            # Fixture writer only; production writes through app-server.
            lines = ['[mcp_servers.' + name + ']\ncommand = ' + json.dumps(entry['command']) +
                     '\nargs = ' + json.dumps(entry['args']) + '\n' for name, entry in data['mcp_servers'].items()]
            self.config.write_text('\n'.join(lines))
            self.version = 'v2'
            return {'status': 'ok', 'version': self.version, 'filePath': str(self.config)}
        raise AssertionError(method)


class CodexConfigUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.base = self.root / 'integration-releases'
        self.old = self.base / ('a' * 64)
        self.target = self.base / ('b' * 64)
        for path in (self.old, self.target):
            (path / 'Contents/Resources/helpers').mkdir(parents=True)
            (path / 'Contents/Resources/runtime/bin').mkdir(parents=True)
        self.config = self.root / 'config.toml'
        entries = []
        for name, script in update.ENTRIES.items():
            command = str(self.old / 'Contents/Resources/runtime/bin/python3')
            args = ['-E', '-s', '-B', str(self.old / 'Contents/Resources/helpers' / script),
                    '--project-root', '/tmp/project', '--state', '/tmp/state', '--socket', '/tmp/socket']
            if script == 'claude_mcp.py':
                args += ['--expert-state', '/tmp/expert-state']
            entries.append(f'[mcp_servers.{name}]\ncommand = {json.dumps(command)}\nargs = {json.dumps(args)}\n')
        self.config.write_text('\n'.join(entries))
        self.client = Client(self.config)
        self.release = patch.object(update, '_release', side_effect=lambda path, base: Path(path))
        self.release.start()
        self.addCleanup(self.release.stop)

    def test_apply_preserves_scope_and_two_entries(self):
        old = tomllib.loads(self.config.read_text())['mcp_servers']
        value = update.plan(self.client, self.config, self.base, self.target, list(update.ENTRIES))
        result = update.apply(self.client, value, self.root / 'operations')
        self.assertEqual(result['client_loaded'], 'unknown')
        self.assertTrue(update.verify(self.client, value)['ok'])
        new = tomllib.loads(self.config.read_text())['mcp_servers']
        for name in update.ENTRIES:
            self.assertEqual(old[name]['args'][4:], new[name]['args'][4:])
        request = next(p for m, p in self.client.calls if m == 'config/batchWrite')
        self.assertFalse(request['reloadUserConfig'])
        self.assertEqual(request['expectedVersion'], 'v1')
        backup = Path(result['operation']) / 'config-before.toml'
        self.assertEqual(update._digest(backup.read_bytes()), value['before_sha256'])
        with self.assertRaises(ValueError):
            update.apply(self.client, value, self.root / 'operations')

    def test_concurrent_config_change_refuses_before_write(self):
        value = update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'])
        self.config.write_text(self.config.read_text() + '\n# changed\n')
        with self.assertRaisesRegex(ValueError, 'modifiée'):
            update.apply(self.client, value, self.root / 'operations')
        self.assertFalse(any(m == 'config/batchWrite' for m, _ in self.client.calls))

    def test_foreign_command_refused(self):
        text = self.config.read_text().replace(str(self.old / 'Contents/Resources/runtime/bin/python3'), '/usr/bin/python3')
        self.config.write_text(text)
        with self.assertRaisesRegex(ValueError, 'étranger'):
            update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'])

    def test_timeout_does_not_replay(self):
        value = update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'])
        original = self.client.call
        def uncertain(method, params):
            if method == 'config/batchWrite':
                raise TimeoutError('unknown')
            return original(method, params)
        self.client.call = uncertain
        with self.assertRaises(TimeoutError):
            update.apply(self.client, value, self.root / 'operations')
        folder = self.root / 'operations' / value['id']
        self.assertEqual(json.loads((folder / 'dispatch-intent.json').read_text())['status'], 'indeterminate')
        with self.assertRaises(FileExistsError):
            update.apply(self.client, value, self.root / 'operations')

    def test_client_cleanup_failure_after_receipt_is_not_refused(self):
        state = self.root / 'support/state'
        state.mkdir(parents=True)
        operations = state.parent / 'maintenance/codex-config'
        value = update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'])
        update.apply(self.client, value, operations)
        with patch.object(Path, 'home', return_value=self.root), \
             patch('app_paths.state_directory', return_value=state), \
             patch.object(managed_update, 'codex_apply', side_effect=OSError('close failed')), \
             patch.object(sys, 'stdin', io.StringIO(json.dumps({'plan': value}))), \
             patch.object(sys, 'stdout', new_callable=io.StringIO) as output:
            code = managed_update.codex_cli(self.root, ['codex-integration-apply'])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'indeterminate')
        parent_plan = self.root / 'parent-plan.json'
        parent_plan.write_text(json.dumps({'plan': {'id': value['id'], 'state': str(state)}}))
        with patch.object(managed_update, 'apply', side_effect=OSError('close failed')), \
             patch.object(sys, 'stdout', new_callable=io.StringIO) as output:
            code = managed_update.main(['apply', '--plan', str(parent_plan)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'indeterminate')

    def test_corrupt_receipt_keeps_valid_intent_indeterminate(self):
        state = self.root / 'support/state'
        state.mkdir(parents=True)
        operations = state.parent / 'maintenance/codex-config'
        value = update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'])
        result = update.apply(self.client, value, operations)
        (Path(result['operation']) / 'receipt.json').write_text('{')
        _, status = managed_update.codex_operation_read(state, value['id'])
        self.assertEqual(status, 'indeterminate')
        self.assertEqual(managed_update.codex_operations(state)[0]['status'], 'indeterminate')
        for malformed in ('[]', 'null', '"text"', '1'):
            (Path(result['operation']) / 'receipt.json').write_text(malformed)
            _, status = managed_update.codex_operation_read(state, value['id'])
            self.assertEqual(status, 'indeterminate')
        folder = Path(result['operation'])
        for name in ('plan.json', 'dispatch-intent.json'):
            original = (folder / name).read_text()
            (folder / name).write_text('[]')
            with self.assertRaises(ValueError):
                managed_update.codex_operation_read(state, value['id'])
            (folder / name).write_text(original)

    def test_visible_receipt_is_indeterminate_until_resynced(self):
        state = self.root / 'support/state'
        state.mkdir(parents=True)
        operations = state.parent / 'maintenance/codex-config'
        value = update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'])
        update.apply(self.client, value, operations)
        original = update._sync_dir
        def fail_folder(path):
            if path == operations / value['id']:
                raise OSError('receipt directory not durable')
            original(path)
        with patch.object(update, '_sync_dir', side_effect=fail_folder):
            _, status = managed_update.codex_operation_read(state, value['id'])
            self.assertEqual(status, 'indeterminate')
        _, status = managed_update.codex_operation_read(state, value['id'])
        self.assertEqual(status, 'recorded')

    def test_unavailable_release_keeps_intent_status_visible(self):
        state = self.root / 'support/state'
        state.mkdir(parents=True)
        operations = state.parent / 'maintenance/codex-config'
        value = update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'])
        def fail(method, params):
            if method == 'config/batchWrite':
                raise TimeoutError('unknown')
            return Client.call(self.client, method, params)
        self.client.call = fail
        with self.assertRaises(TimeoutError):
            update.apply(self.client, value, operations)
        with patch.object(managed_update, 'codex_verify', side_effect=ValueError('release absent')):
            result = managed_update.codex_verify_operation(self.root, self.root, state, value['id'])
        self.assertEqual(result['operation_status'], 'indeterminate')
        self.assertIsNone(result['configuration_written'])
        self.assertFalse(result['ok'])

    def test_malformed_batch_result_remains_indeterminate(self):
        value = update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'])
        original = self.client.call
        def malformed(method, params):
            if method == 'config/batchWrite':
                return []
            return original(method, params)
        self.client.call = malformed
        with self.assertRaises(ValueError):
            update.apply(self.client, value, self.root / 'operations')
        folder = self.root / 'operations' / value['id']
        self.assertTrue((folder / 'dispatch-intent.json').is_file())
        self.assertFalse((folder / 'receipt.json').exists())

    def test_native_client_rejects_malformed_envelopes(self):
        executable = self.root / 'fake-codex'
        for response in ([], {'id': 2, 'error': []}, {'id': 2, 'error': {'data': []}}, {'id': 2, 'result': []}):
            executable.write_text('#!/usr/bin/python3\nimport json,sys\n' +
                'for line in sys.stdin:\n' +
                ' m=json.loads(line)\n' +
                ' if m.get("id")==1: print(json.dumps({"id":1,"result":{}}),flush=True)\n' +
                ' elif m.get("id")==2: print(' + repr(json.dumps(response)) + ',flush=True)\n')
            executable.chmod(0o700)
            client = update.NativeClient(executable, self.root)
            try:
                with self.assertRaises(ValueError):
                    client.call('config/batchWrite', {})
            finally:
                client.close()

    def test_parent_fsync_failure_is_retried_before_native_write(self):
        operations = self.root / 'new-parent' / 'operations'
        value = update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'])
        original = update._sync_dir
        failed = []
        def fail_once(path):
            if path == operations.parent and not failed:
                failed.append(True)
                raise OSError('simulated fsync failure')
            original(path)
        with patch.object(update, '_sync_dir', side_effect=fail_once):
            with self.assertRaises(OSError):
                update.apply(self.client, value, operations)
        self.assertFalse(any(method == 'config/batchWrite' for method, _ in self.client.calls))
        synced = []
        def record(path):
            synced.append(path)
            original(path)
        with patch.object(update, '_sync_dir', side_effect=record):
            update.apply(self.client, value, operations)
        self.assertIn(operations.parent, synced)
        self.assertIn(operations.parent.parent, synced)
        self.assertTrue(any(method == 'config/batchWrite' for method, _ in self.client.calls))

    def test_durable_intent_and_release_sync_precede_native_write(self):
        events = []
        source = self.root / 'source'
        source.mkdir()
        runtime = self.root / 'runtime'
        runtime.mkdir()
        operations = self.root / 'new-parent' / 'operations'
        bundle = (source, runtime, self.target.name)
        original_sync = update._sync_dir
        original_call = self.client.call
        def sync(path):
            events.append(('fsync_dir', str(path)))
            original_sync(path)
        def stage(helpers, run, target):
            self.assertTrue((operations / value['id'] / 'dispatch-intent.json').is_file())
            events.append(('stage', str(target)))
            target.mkdir(exist_ok=True)
        def sync_release(target):
            events.append(('release_synced', str(target)))
        def call(method, params):
            if method == 'config/batchWrite':
                events.append(('write', ''))
            return original_call(method, params)
        self.client.call = call
        with patch.object(update.integration, 'bundle_info', return_value=bundle), \
             patch.object(update.integration, 'stage_release', side_effect=stage), \
             patch.object(update, '_sync_dir', side_effect=sync), \
             patch.object(update, '_sync_release', side_effect=sync_release):
            value = update.plan(self.client, self.config, self.base, self.target, ['reprise_codex'], source=source)
            update.apply(self.client, value, operations)
        self.assertLess(events.index(('stage', str(self.target))), events.index(('release_synced', str(self.target))))
        self.assertLess(events.index(('release_synced', str(self.target))), events.index(('write', '')))
        self.assertIn(('fsync_dir', str(operations.parent)), events)
        self.assertIn(('fsync_dir', str(operations)), events)
        self.assertLess(events.index(('fsync_dir', str(operations / value['id']))), events.index(('stage', str(self.target))))

    def test_lost_reply_recovers_inspection_without_claiming_causality(self):
        state = self.root / 'support/state'
        state.mkdir(parents=True)
        home = self.root / 'home'
        (home / '.codex').mkdir(parents=True)
        self.config.rename(home / '.codex/config.toml')
        self.config = home / '.codex/config.toml'
        self.client.config = self.config
        self.base = state.parent / 'integration-releases'
        self.target = self.base / ('b' * 64)
        source = self.root / 'source'
        source.mkdir()
        runtime = self.root / 'runtime'
        runtime.mkdir()
        @contextmanager
        def client_factory(_home):
            yield self.client
        original = self.client.call
        def lost(method, params):
            result = original(method, params)
            if method == 'config/batchWrite':
                raise TimeoutError('response lost after native write')
            return result
        self.client.call = lost
        with patch.object(managed_update, 'codex_client', side_effect=client_factory), \
             patch.object(update.integration, 'bundle_info', return_value=(source, runtime, self.target.name)), \
             patch.object(update.integration, 'stage_release', side_effect=lambda a, b, c: c.mkdir()), \
             patch.object(update, '_sync_release'):
            value = managed_update.codex_plan(source, home, state, ['reprise_codex'])
            output = io.StringIO()
            with patch.object(Path, 'home', return_value=home), \
                 patch('app_paths.state_directory', return_value=state), \
                 patch.object(sys, 'stdin', io.StringIO(json.dumps({'plan': value}))), \
                 patch.object(sys, 'stdout', output):
                code = managed_update.codex_cli(source, ['codex-integration-apply'])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.getvalue())['status'], 'indeterminate')
            self.assertEqual(managed_update.codex_operations(state), [{'id': value['id'], 'status': 'indeterminate'}])
            outcome = managed_update.codex_verify_operation(source, home, state, value['id'])
            self.assertEqual(outcome['operation_status'], 'indeterminate')
            self.assertTrue(outcome['configuration_written'])
            self.assertEqual(outcome['evidence'], 'current_configuration_only')
            before = len([m for m, _ in self.client.calls if m == 'config/batchWrite'])
            with self.assertRaises((ValueError, FileExistsError)):
                managed_update.codex_apply(value, source, home, state)
            self.assertEqual(before, len([m for m, _ in self.client.calls if m == 'config/batchWrite']))

    def test_real_staged_release_is_authenticated(self):
        import harness_integration
        import package_helpers
        self.release.stop()
        source = Path(__file__).resolve().parents[1]
        helpers = package_helpers.package(source, self.root / 'source.app')
        runtime = helpers.parent / 'runtime'
        (runtime / 'bin').mkdir(parents=True)
        program = runtime / 'bin/python3'
        program.write_text('#!/bin/sh\nexit 0\n')
        program.chmod(0o700)
        (runtime / 'runtime-manifest.json').write_text(json.dumps({'files': harness_integration.tree_files(runtime)}))
        manifest = helpers / 'payload-manifest.json'
        payload = json.loads(manifest.read_text())
        payload['runtime'] = {'fixture': True}
        manifest.write_text(json.dumps(payload))
        _, _, identity = harness_integration.bundle_info(helpers)
        staged = self.base / identity
        harness_integration.stage_release(helpers, runtime, staged)
        self.assertEqual(update._release(staged, self.base), staged)
        (staged / 'Contents/Resources/helpers/apple_expert.py').write_text('tampered')
        with self.assertRaises(ValueError):
            update._release(staged, self.base)

    def test_bundle_to_staged_release_and_native_config_in_temporary_home(self):
        import harness_integration
        import package_helpers
        program = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
        if not program.is_file():
            self.skipTest('Codex CLI absente')
        self.release.stop()
        source = Path(__file__).resolve().parents[1]
        helpers = package_helpers.package(source, self.root / 'source.app')
        runtime = helpers.parent / 'runtime'
        (runtime / 'bin').mkdir(parents=True)
        python = runtime / 'bin/python3'
        python.write_text('#!/bin/sh\nexit 0\n')
        python.chmod(0o700)
        (runtime / 'runtime-manifest.json').write_text(json.dumps({'files': harness_integration.tree_files(runtime)}))
        manifest = helpers / 'payload-manifest.json'
        payload = json.loads(manifest.read_text())
        payload['runtime'] = {'fixture': True}
        manifest.write_text(json.dumps(payload))
        _, _, old_id = harness_integration.bundle_info(helpers)
        old = self.base / old_id
        harness_integration.stage_release(helpers, runtime, old)
        entries = []
        for name, script in update.ENTRIES.items():
            args = ['-E', '-s', '-B', str(old / 'Contents/Resources/helpers' / script),
                    '--project-root', '/tmp/project', '--state', '/tmp/state']
            if script == 'claude_mcp.py':
                args += ['--expert-state', '/tmp/expert-state']
            entries.append(f'[mcp_servers.{name}]\ncommand = {json.dumps(str(old / "Contents/Resources/runtime/bin/python3"))}\nargs = {json.dumps(args)}\n')
        self.config.write_text('\n'.join(entries))
        apple = helpers / 'apple_expert.py'
        apple.write_text(apple.read_text() + '\n# changed source fixture\n')
        payload['files']['apple_expert.py'] = update._digest(apple.read_bytes())
        manifest.write_text(json.dumps(payload))
        _, _, new_id = harness_integration.bundle_info(helpers)
        target = self.base / new_id
        self.assertNotEqual(old, target)
        client = update.NativeClient(program, self.root)
        self.addCleanup(client.close)
        value = update.plan(client, self.config, self.base, target, list(update.ENTRIES), source=helpers)
        self.assertFalse(target.exists())
        result = update.apply(client, value, self.root / 'operations')
        self.assertEqual(result['status'], 'written')
        self.assertTrue(update.verify(client, value)['ok'])
        self.assertTrue((target / 'Contents/Resources/helpers/apple_expert.py').is_file())
        self.assertTrue((old / 'Contents/Resources/helpers/apple_expert.py').is_file())
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'reprise_backends', 'arguments': {}}},
        ]
        for name, script in update.ENTRIES.items():
            project = self.root / ('project-' + name)
            project.mkdir()
            args = [sys.executable, str(target / 'Contents/Resources/helpers' / script),
                    '--project-root', str(project), '--state', str(self.root / ('state-' + name))]
            if script == 'claude_mcp.py':
                args += ['--expert-state', str(self.root / 'expert-state')]
            env = dict(os.environ, HOME=str(self.root))
            env.pop('PYTHONPATH', None)
            env.pop('AGENT_RESUME_STATE', None)
            proc = subprocess.run(args, input='\n'.join(map(json.dumps, messages)) + '\n',
                                  env=env, text=True, capture_output=True, timeout=15)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            replies = [json.loads(line) for line in proc.stdout.splitlines()]
            reply = next(x for x in replies if x.get('id') == 2)
            contents = json.loads(reply['result']['content'][0]['text'])
            self.assertIn('foundation-models', [x['id'] for x in contents['backends']])

    def test_real_native_server_batch_and_conflict_in_temporary_home(self):
        program = Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
        if not program.is_file():
            self.skipTest('Codex CLI absente')
        client = update.NativeClient(program, self.root)
        self.addCleanup(client.close)
        value = update.plan(client, self.config, self.base, self.target, list(update.ENTRIES))
        result = update.apply(client, value, self.root / 'operations-real')
        self.assertEqual(result['status'], 'written')
        self.assertTrue(update.verify(client, value)['ok'])
        self.assertEqual(result['client_loaded'], 'unknown')
        current = tomllib.loads(self.config.read_text())['mcp_servers']
        self.assertIn(str(self.target), current['reprise_codex']['command'])
        self.assertIn(str(self.target), current['reprise-harness']['command'])
        version = update._layer(client, self.config)['version']
        self.config.write_text(self.config.read_text() + '\n[concurrent]\nvalue = 1\n')
        with self.assertRaisesRegex(ValueError, 'configVersionConflict'):
            client.call('config/batchWrite', {'filePath': str(self.config), 'expectedVersion': version,
                        'reloadUserConfig': False, 'edits': [{'keyPath': 'mcp_servers.reprise_codex.command',
                        'value': 'must-not-write', 'mergeStrategy': 'replace'}]})
        self.assertNotIn('must-not-write', self.config.read_text())


if __name__ == '__main__':
    unittest.main()
