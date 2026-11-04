import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'verify.py'
spec = importlib.util.spec_from_file_location('verify', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class VerifyTests(unittest.TestCase):
    def run_check(self, checks, execute=True):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'checks.json'
            config.write_text(json.dumps({'checks': checks}))
            result = subprocess.run([sys.executable, str(SCRIPT), '--config', str(config), '--root', directory] + (['--run'] if execute else []), capture_output=True, text=True, timeout=10)
            return result.returncode, json.loads(result.stdout)

    def test_plan_never_executes(self):
        code, result = self.run_check([{'name':'check','argv':['nonexistent-command']}], False)
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'not-run')

    def test_pass_and_missing_are_distinct(self):
        code, result = self.run_check([{'name':'ok','argv':[sys.executable,'-c','print("OK")']}, {'name':'missing','argv':['nonexistent-harness-command']}])
        self.assertEqual(code, 1)
        self.assertEqual([x['status'] for x in result['checks']], ['passed','unavailable'])

    def test_timeout_and_failure(self):
        code, result = self.run_check([{'name':'slow','argv':[sys.executable,'-c','import time; time.sleep(10)'],'timeout':1}, {'name':'bad','argv':[sys.executable,'-c','raise SystemExit(4)']}])
        self.assertEqual(code, 1)
        self.assertTrue(result['checks'][0]['timeout'])
        self.assertEqual(result['checks'][1]['exit_code'], 4)

    def test_shell_metacharacters_are_literal(self):
        code, result = self.run_check([{'name':'literal','argv':[sys.executable,'-c','import sys; print(sys.argv[1])','$(echo unwanted)']}])
        self.assertEqual(code, 0)
        self.assertIn('$(echo unwanted)', result['checks'][0]['diagnostic'])

    def test_redaction_and_bounded_output(self):
        value = 'test-' + 'a'*30
        code, result = self.run_check([{'name':'bounded','argv':[sys.executable,'-c',f'print("API_KEY={value}"); print("x"*2200000)']}])
        entry = result['checks'][0]
        self.assertEqual(code, 0)
        self.assertGreater(entry['discarded_bytes'], 0)
        self.assertNotIn(value, Path(entry['log']).read_text())
        self.assertLessEqual(len(entry['diagnostic']),1200)

    def test_duplicate_names_are_rejected(self):
        with self.assertRaises(ValueError): module.validate({'checks':[{'name':'same','argv':['x']},{'name':'same','argv':['x']}]})

if __name__ == '__main__': unittest.main()
