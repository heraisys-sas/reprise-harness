import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_expert as claude

ID = '12345678-1234-4234-8234-123456789abc'
AUTH = (0, json.dumps({'loggedIn': True, 'authMethod': 'claude.ai', 'apiProvider': 'firstParty'}), '')
HELP = (0, ' '.join(claude.SAFE) + ' --session-id --effort', '')


def terminal(**changes):
    return (0, json.dumps(dict(type='result', subtype='success', is_error=False,
        session_id=ID, result='Reviewed', modelUsage={'claude-sonnet-test': {}}, **changes)), '')


class ClaudeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def review(self, **kwargs):
        return claude.review('/claude', self.root, 'Review this', model='sonnet',
                             thread_id=ID, subscription_only=True, **kwargs)

    def test_success_flags_and_pinned_identity(self):
        with patch.object(claude, 'run', side_effect=[HELP, AUTH, terminal()]) as run:
            result = self.review()
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['model'], 'claude-sonnet-test')
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--resume')+1], ID)
        self.assertEqual(command[command.index('--tools')+1], '')
        self.assertNotIn('--bare', command)
        self.assertNotIn('--fallback-model', command)
        self.assertIsNone(result['quota_remaining'])

    def test_no_billing_confirmation_no_process(self):
        with patch.object(claude, 'run') as run:
            result = claude.review('/claude', self.root, 'Review', model='sonnet')
        run.assert_not_called()
        self.assertEqual(result['status'], 'billing_unverified')

    def test_padded_oversized_question_rejected_without_process(self):
        with patch.object(claude, 'run') as run:
            for question in (' '*12000+'x', 'x'+' '*12000, ' '*12000):
                with self.assertRaises(ValueError):
                    claude.review('/claude', self.root, question, model='sonnet',
                                  thread_id=ID, subscription_only=True)
            run.assert_not_called()

    def test_auth_failures_never_infer(self):
        for method in ('none', 'api_key', 'api_key_helper', 'third_party', 'oauth_token'):
            auth = (0, json.dumps({'loggedIn': True, 'authMethod': method, 'apiProvider': 'firstParty'}), '')
            with patch.object(claude, 'run', side_effect=[HELP, auth]) as run:
                self.assertEqual(self.review()['status'], 'blocked_auth')
                self.assertEqual(run.call_count, 2)

    def test_environment_drops_credentials_provider_and_runtime_overrides(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY':'secret', 'CLAUDE_CODE_EFFORT_LEVEL':'max',
                                    'NODE_OPTIONS':'--require evil', 'CLAUDE_CONFIG_DIR':'/elsewhere'}):
            env = claude.child_environment()
        for key in ('ANTHROPIC_API_KEY','CLAUDE_CODE_EFFORT_LEVEL','NODE_OPTIONS','CLAUDE_CONFIG_DIR'):
            self.assertNotIn(key, env)
        self.assertEqual(env['ENABLE_CLAUDEAI_MCP_SERVERS'], 'false')

    def test_invalid_terminal_never_persists(self):
        good = json.loads(terminal()[1])
        for changes in ({'session_id':'other'}, {'subtype':'error_max_turns'}, {'is_error':True},
                        {'modelUsage':{}}, {'modelUsage':{'claude-opus-test':{}}},
                        {'result':''}, {'permission_denials':[{'tool_name':'Bash'}]}):
            with patch.object(claude, 'run', side_effect=[HELP, AUTH, (0,json.dumps(good | changes),'')]):
                result = self.review()
            self.assertNotEqual(result['status'], 'finished')
            self.assertFalse(result['resumable'])

    def test_timeout_no_retry(self):
        with patch.object(claude, 'run', side_effect=[HELP, AUTH, TimeoutError]) as run:
            self.assertEqual(self.review()['status'], 'timed_out')
            self.assertEqual(run.call_count, 3)

    def test_explicit_context_only_and_no_symlinks_traversal_or_large_files(self):
        (self.root/'file.py').write_text('print(1)')
        self.assertIn('print(1)', claude.context(self.root, ['file.py']))
        (self.root/'alias').symlink_to(self.root/'file.py')
        (self.root/'dir').symlink_to(self.root, target_is_directory=True)
        (self.root/'large').write_bytes(b'x'*96001)
        for name in ('alias','dir/file.py','../outside','/etc/passwd','large'):
            with self.assertRaises((OSError,ValueError)):
                claude.context(self.root,[name])

    def test_process_deadline_and_output_limit(self):
        with self.assertRaises(TimeoutError):
            claude.run([sys.executable,'-c','import time;time.sleep(2)'],self.root,os.environ,timeout=.05)
        with patch.object(claude,'MAX_OUTPUT',100):
            with self.assertRaises(OverflowError):
                claude.run([sys.executable,'-c','print("x"*1000)'],self.root,os.environ)

    def test_pin_refuses_different_concrete_model(self):
        with patch.object(claude, 'run', side_effect=[HELP, AUTH, terminal()]):
            result = claude.review('/claude', self.root, 'Review', model='claude-sonnet-other',
                                   thread_id=ID, subscription_only=True)
        self.assertEqual(result['status'],'needs_review')

    def test_timeout_kills_descendant_after_parent_exits(self):
        marker = self.root/'descendant-survived'
        child = 'import time,pathlib;time.sleep(.3);pathlib.Path(' + repr(str(marker)) + ').touch()'
        parent = 'import subprocess,sys;subprocess.Popen([sys.executable,"-c",' + repr(child) + '])'
        with self.assertRaises(TimeoutError):
            claude.run([sys.executable,'-c',parent], self.root, os.environ, timeout=.1)
        time.sleep(.35)
        self.assertFalse(marker.exists())
