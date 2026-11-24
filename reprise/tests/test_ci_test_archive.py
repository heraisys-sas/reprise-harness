"""Tests for ci_test explicit archive errors (issue 199)."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CI_TEST = Path(__file__).resolve().parents[1] / 'ci_test.py'
VAR = 'REPRISE_TEST_RUNTIME_ARCHIVE'


def run_ci_test(args, archive):
    env = dict(os.environ)
    env.pop('REPRISE_TEST_PYTHON', None)
    env[VAR] = archive
    return subprocess.run(
        [sys.executable, str(CI_TEST), *args],
        env=env, capture_output=True, text=True, timeout=120)


class CiTestArchiveTests(unittest.TestCase):
    def test_missing_explicit_archive_exits_2_without_traceback(self):
        missing = str(Path(tempfile.gettempdir()) / 'reprise-inexistant-archive.tar.gz')
        for args in (['--parallel', '2'], ['--shard-index', '0', '--shard-count', '2']):
            with self.subTest(args=args):
                proc = run_ci_test(args, missing)
                self.assertEqual(proc.returncode, 2)
                output = proc.stdout + proc.stderr
                self.assertIn(VAR, output)
                self.assertIn(missing, output)
                self.assertNotIn('Traceback', output)

    def test_invalid_sha_explicit_archive_exits_2_without_traceback(self):
        with tempfile.NamedTemporaryFile(suffix='.tar.gz', delete=False) as tmp:
            tmp.write(b'not-a-valid-runtime-archive')
            path = tmp.name
        try:
            proc = run_ci_test(['--parallel', '2'], path)
            self.assertEqual(proc.returncode, 2)
            output = proc.stdout + proc.stderr
            self.assertIn(VAR, output)
            self.assertIn(path, output)
            self.assertNotIn('Traceback', output)
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
