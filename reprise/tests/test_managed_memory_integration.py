"""Managed configs and exact generated MCP commands in isolated local fixtures.

The runtime delegates to the test interpreter and Vault is a test executable,
not Keychain cryptography. No application client, model or live config is used.
"""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import select
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
import claude_integration as claude
import harness_integration as dsh
import memory_integration
import package_helpers
from project_registry import ProjectRegistry
import state_install
import test_harness_integration as harness_fixtures

VAULT = r'''
import base64, hashlib, json, sys
request=json.load(sys.stdin)
if request['purpose'] not in ('orchestrator-memory-v1','orchestrator-intent-v1') or request['interactive']:
 raise SystemExit(2)
data=base64.b64decode(request['payload'],validate=True)
if request['operation']=='seal':
 data=b'TEST:'+hashlib.sha256(data).digest()+data
elif request['operation']=='open':
 if not data.startswith(b'TEST:') or hashlib.sha256(data[37:]).digest()!=data[5:37]: raise SystemExit(3)
 data=data[37:]
else: raise SystemExit(4)
print(json.dumps(base64.b64encode(data).decode()))
'''


class ManagedMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = package_helpers.package(SOURCE, self.base / 'Reprise.app')
        runtime = self.root.parent / 'runtime'
        (runtime / 'bin').mkdir(parents=True)
        executable = runtime / 'bin/python3'
        executable.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        executable.chmod(0o700)
        (runtime / 'runtime-manifest.json').write_text(json.dumps({'files': dsh.tree_files(runtime)}))
        payload = json.loads((self.root / 'payload-manifest.json').read_text())
        payload['runtime'] = {'fixture': True}
        (self.root / 'payload-manifest.json').write_text(json.dumps(payload))
        native = self.root.parent.parent / 'MacOS'; native.mkdir()
        (native / 'Vault').write_text('#!' + sys.executable + '\n' + VAULT)
        (native / 'Vault').chmod(0o700)
        (self.root.parent / 'native-manifest.json').write_text(json.dumps({'binaries': {
            'Vault': {'sha256': hashlib.sha256((native / 'Vault').read_bytes()).hexdigest()}}}))
        self.state = self.base / 'state'; state_install.initialize(self.state)
        self.patch = patch.dict(os.environ, {'AGENT_RESUME_STATE': str(self.state), 'PYTHONDONTWRITEBYTECODE': '1'})
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.repo = self.base / 'repo'; self.repo.mkdir()
        self.git(self.repo, 'init', '-q')
        self.git(self.repo, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-q', '--allow-empty', '-m', 'fixture')
        self.claude_project = self.base / 'claude-worktree'
        self.git(self.repo, 'worktree', 'add', '-q', '--detach', str(self.claude_project))
        self.other = self.base / 'other'; self.other.mkdir(); self.git(self.other, 'init', '-q')
        with ProjectRegistry(self.state) as registry:
            self.project_id = registry.create('P', self.repo, self.repo)['id']
            registry.associate_workspace(self.project_id, 'harness', self.repo)
            registry.associate_workspace(self.project_id, 'claude', self.claude_project)
            self.other_id = registry.create('Q', self.other, self.other)['id']
            registry.associate_workspace(self.other_id, 'claude', self.other)
        self.dsh_home = self.base / 'dsh'
        folder = self.dsh_home / 'profiles/desktop'; folder.mkdir(parents=True)
        self.dsh_config = folder / 'cordis.patch.yml'; self.dsh_config.write_text('[]\n')
        self.claude_home = self.base / 'claude'; self.claude_home.mkdir()
        _, self.claude_config, self.claude_managed = claude.paths(self.claude_home)
        self.claude_config.parent.mkdir(parents=True)
        self.claude_config.write_text(json.dumps({'mcpServers': {'unrelated': {'command': 'preserve'}}}))
        self.children = []; self.sequence = 0
        self.addCleanup(self.close_children)

    def git(self, repo, *args):
        subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, timeout=10)

    def close_children(self):
        for process in self.children:
            process.stdin.close()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            process.stdout.close(); process.stderr.close()

    def dsh_plan(self, **kw):
        return dsh.plan(self.root, self.dsh_home, 'desktop', self.repo, **kw)

    def claude_plan(self, **kw):
        return claude.plan(self.root, self.claude_home, self.claude_project, **kw)

    def commands(self):
        _, block = dsh.split_block(self.dsh_config.read_text())
        entry = next(row['config'] for row in dsh.block_entries(block) if row['id'] == 'reprise-memory')
        return ({k: entry[k] for k in ('command', 'args')},
                json.loads(self.claude_config.read_text())['mcpServers']['reprise-memory'])

    def start(self, entry):
        process = subprocess.Popen([entry['command'], *entry['args']], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.children.append(process)
        return process

    def rpc(self, process, method, params=None):
        self.sequence += 1
        request = {'jsonrpc': '2.0', 'id': self.sequence, 'method': method}
        if params is not None: request['params'] = params
        process.stdin.write(json.dumps(request).encode() + b'\n'); process.stdin.flush()
        self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'MCP response timeout')
        response = json.loads(process.stdout.readline())
        self.assertEqual(response['id'], self.sequence)
        return response['result']

    def call(self, process, tool, **arguments):
        response = self.rpc(process, 'tools/call', {'name': 'reprise_memory_' + tool, 'arguments': arguments})
        return json.loads(response['content'][0]['text'])

    def install_both(self, access_a='write', access_b='read'):
        a = self.dsh_plan(memory_access=access_a, memory_project_id=self.project_id)
        b = self.claude_plan(memory_access=access_b, memory_project_id=self.project_id, enable_await=True)
        before = self.dsh_config.read_bytes(), self.claude_config.read_bytes()
        # Plan/registry validation must not touch either target config.
        self.assertEqual((self.dsh_config.read_bytes(), self.claude_config.read_bytes()), before)
        dsh.apply(self.root, a); claude.apply(self.root, b)

    def test_exact_generated_commands_exchange_and_project_isolation(self):
        self.install_both()
        for evidence in (dsh.verify(self.dsh_home, 'desktop', root=self.root, project=self.repo),
                         claude.verify(self.root, self.claude_home, project=self.claude_project)):
            self.assertEqual(evidence['memory']['evidence'], 'direct_probe_tools_schema')
            self.assertEqual(evidence['memory']['client_loaded'], 'unknown')
            self.assertEqual(evidence['memory']['exchange_confirmed'], 'unknown')
        command_a, command_b = self.commands()
        self.assertIn('--allow-write', command_a['args'])
        self.assertNotIn('--allow-write', command_b['args'])
        for command, access in ((command_a, 'write'), (command_b, 'read')):
            probe = memory_integration.probe(command, {'access': access, 'project_id': self.project_id})
            self.assertEqual(probe['client_loaded'], 'unknown')
            self.assertEqual(probe['exchange_confirmed'], 'unknown')
        a, b = self.start(command_a), self.start(command_b)
        source = {'kind': 'operator', 'reference': 'explicit-test', 'producer': 'test-user', 'observed_at': '2026-11-04'}
        operation = '1ca68a04-a77c-4db9-9dfa-dfb5017104e8'
        capture = dict(operation_id=operation, text='Partage explicite nonce-memoire-42', source=source)
        result = self.call(a, 'capture', **capture)
        self.assertEqual(result['effect'], 'recorded')
        ident = result['result']['id']
        found = self.call(b, 'search', query='nonce-memoire-42', offset=0)['items'][0]
        self.assertEqual(found['id'], ident)
        item = self.call(b, 'get', fact_id=ident)['item']
        self.assertEqual(item['source'], source)
        self.assertEqual(item['recorded_by']['harness_id'], 'dsh-desktop')
        self.assertEqual(self.call(b, 'capture', **capture)['status'], 'permission_denied')
        other_home = self.base / 'claude-q'; other_home.mkdir()
        plan = claude.plan(self.root, other_home, self.other, memory_access='read', memory_project_id=self.other_id)
        claude.apply(self.root, plan)
        _, path, _ = claude.paths(other_home)
        q = self.start(json.loads(path.read_text())['mcpServers']['reprise-memory'])
        self.assertEqual(self.call(q, 'get', fact_id=ident)['status'], 'not_found')
        self.assertEqual(self.call(q, 'search', query='nonce-memoire-42', offset=0)['items'], [])
        self.assertEqual(self.call(q, 'get', fact_id=ident, project_id=self.project_id)['status'], 'invalid_request')
        self.assertEqual(json.loads(self.claude_config.read_text())['mcpServers']['unrelated'], {'command': 'preserve'})
        stored = self.state / 'orchestrator-memory.noindex/memory.enc'
        memory_before = stored.read_bytes()
        dsh.apply(self.root, self.dsh_plan(memory_access='off'))
        claude.apply(self.root, self.claude_plan(action='remove'))
        self.assertEqual(stored.read_bytes(), memory_before)

    def test_upgrade_downgrade_disable_remove_and_preserve_selection(self):
        self.install_both()
        self.assertEqual(self.dsh_plan()['changes'], [])
        self.assertEqual(self.claude_plan()['changes'], [])
        # A new release carries identical selections without widening rights.
        version = self.root / 'VERSION'; version.write_text(version.read_text() + '\nfixture-update\n')
        path = self.root / 'payload-manifest.json'; payload = json.loads(path.read_text())
        payload['files']['VERSION'] = hashlib.sha256(version.read_bytes()).hexdigest(); path.write_text(json.dumps(payload))
        old_commands = self.commands()
        dsh.apply(self.root, self.dsh_plan()); claude.apply(self.root, self.claude_plan())
        commands = self.commands()
        self.assertNotEqual(commands[0]['command'], old_commands[0]['command'])
        self.assertNotEqual(commands[1]['command'], old_commands[1]['command'])
        self.assertIn('--allow-write', commands[0]['args'])
        self.assertNotIn('--allow-write', commands[1]['args'])
        self.assertTrue(json.loads((self.claude_managed / 'managed.json').read_text())['enable_await'])
        dsh.apply(self.root, self.dsh_plan(memory_access='read'))
        self.assertNotIn('--allow-write', self.commands()[0]['args'])
        dsh.apply(self.root, self.dsh_plan(memory_access='off'))
        claude.apply(self.root, self.claude_plan(memory_access='off'))
        self.assertNotIn('reprise-memory', self.dsh_config.read_text())
        self.assertNotIn('reprise-memory', json.loads(self.claude_config.read_text())['mcpServers'])
        claude.apply(self.root, self.claude_plan(action='remove'))
        dsh.apply(self.root, self.dsh_plan(action='remove'))
        self.assertEqual(self.dsh_plan()['memory']['access'], 'off')
        self.assertEqual(self.claude_plan()['memory']['access'], 'off')

    def test_conflicts_unknown_binding_and_stale_plan_do_not_overwrite(self):
        before = self.dsh_config.read_bytes(), self.claude_config.read_bytes()
        with self.assertRaises(ValueError): self.dsh_plan(memory_access='write', memory_project_id=self.other_id)
        with self.assertRaises(ValueError): self.claude_plan(memory_access='write', memory_project_id=self.other_id)
        self.assertEqual((self.dsh_config.read_bytes(), self.claude_config.read_bytes()), before)
        self.install_both()
        plan = self.claude_plan(memory_access='write')
        value = json.loads(self.claude_config.read_text())
        value['mcpServers']['reprise-memory']['args'].append('--foreign')
        self.claude_config.write_text(json.dumps(value))
        changed = self.claude_config.read_bytes()
        with self.assertRaises(ValueError): claude.apply(self.root, plan)
        with self.assertRaises(ValueError): self.claude_plan(action='remove')
        self.assertEqual(self.claude_config.read_bytes(), changed)
        text = self.dsh_config.read_text().replace('dsh-desktop', 'foreign-owner')
        self.dsh_config.write_text(text)
        with self.assertRaises(ValueError): self.dsh_plan()
        with self.assertRaises(ValueError): self.dsh_plan(action='remove')
        self.assertEqual(self.dsh_config.read_text(), text)

    def test_unmanaged_connector_collision_is_never_adopted(self):
        value = json.loads(self.claude_config.read_text())
        value['mcpServers']['reprise-memory'] = {'command': 'foreign'}
        self.claude_config.write_text(json.dumps(value))
        with self.assertRaises(ValueError): self.claude_plan(memory_access='read', memory_project_id=self.project_id)
        self.dsh_config.write_text('- insert: [{"id": "reprise-memory", "name": "foreign"}]\n')
        with self.assertRaises(ValueError): self.dsh_plan(memory_access='read', memory_project_id=self.project_id)

    def test_cli_plan_preserves_existing_dsh_permissions_and_exposes_memory(self):
        dsh.apply(self.root, self.dsh_plan(delegate=True, output_resume=True, agent_role='coordinator',
                                        codex_workspace_write=True))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = dsh.main(self.root, ['integration-plan', '--home', str(self.dsh_home), '--profile', 'desktop',
                '--project', str(self.repo), '--action', 'install', '--memory-access', 'read',
                '--memory-project-id', self.project_id])
        self.assertEqual(code, 0)
        plan = json.loads(output.getvalue())['plan']
        self.assertTrue(plan['delegate']); self.assertTrue(plan['output_resume'])
        # Workspace-write is a strict opt-in and is revoked when omitted from a new plan.
        self.assertFalse(plan.get('codex_workspace_write', False)); self.assertEqual(plan['agent_role'], 'coordinator')
        self.assertEqual(plan['memory']['access'], 'read')
        dsh.apply(self.root, plan)
        self.assertEqual(dsh.inspect(self.dsh_home, 'desktop')['memory']['access'], 'read')
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = claude.main(self.root, ['claude-integration-plan', '--home', str(self.claude_home),
                '--project', str(self.claude_project), '--memory-access', 'read', '--memory-project-id', self.project_id])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['plan']['memory']['access'], 'read')

    def test_cli_never_moves_implicit_permissions_to_another_project_and_can_revoke(self):
        self.dsh_config.write_text(harness_fixtures.HarnessIntegrationTests.asi1_profile(None))
        dsh.apply(self.root, self.dsh_plan(delegate=True, codex_workspace_write=True,
            output_resume=True, asi1_effort=True, bash_sandbox_disabled=True, agent_role='coordinator'))
        before = self.dsh_config.read_bytes()
        output = io.StringIO()
        arguments = ['integration-plan', '--home', str(self.dsh_home), '--profile', 'desktop',
                     '--project', str(self.other), '--action', 'install']
        with contextlib.redirect_stdout(output):
            code = dsh.main(self.root, arguments)
        self.assertEqual(code, 1)
        self.assertEqual(self.dsh_config.read_bytes(), before)
        with self.assertRaises(ValueError):
            dsh.plan(self.root, self.dsh_home, 'desktop', self.other)
        # Explicit inverse flags revoke in-place without removing the integration.
        for project in (self.repo, self.other):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = dsh.main(self.root, ['integration-plan', '--home', str(self.dsh_home),
                    '--profile', 'desktop', '--project', str(project), '--action', 'install',
                    '--no-delegate', '--no-codex-workspace-write', '--no-output-resume',
                    '--no-asi1-effort', '--enable-bash-sandbox', '--agent-role', 'standard'])
            self.assertEqual(code, 0)
            plan = json.loads(output.getvalue())['plan']
            self.assertFalse(plan['delegate'])
            self.assertFalse(plan.get('codex_workspace_write', False))
            self.assertFalse(plan.get('output_resume', False))
            self.assertFalse(plan.get('asi1_effort', False))
            self.assertFalse(plan.get('bash_sandbox_disabled', False))
            dsh.apply(self.root, plan)
            self.assertNotIn('--codex-workspace-write', self.dsh_config.read_text())
            self.assertNotIn('"disabled": true', self.dsh_config.read_text())
