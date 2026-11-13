"""Deterministic shard partition of the test modules: exhaustive, no duplicate."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import ci_test


class ShardPartitionTests(unittest.TestCase):
    def test_partition_exhaustive_no_duplicate(self):
        shards = ci_test.partition_modules(4)
        modules = list(ci_test.discover_module_weights())
        self.assertEqual(len(shards), 4)
        seen = [module for shard in shards for module in shard]
        self.assertEqual(sorted(seen), sorted(modules))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertTrue(all(shard for shard in shards))

    def test_partition_deterministic(self):
        self.assertEqual(ci_test.partition_modules(4), ci_test.partition_modules(4))

    def test_explicit_ci_runtime_archive_is_verified_and_cached(self):
        import package_runtime
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'runtime.tar.gz'
            archive.write_bytes(b'synthetic archive')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            lock = root / 'lock.json'
            lock.write_text(json.dumps({'targets': {'arm64': {'sha256': digest}}}))
            calls = []

            def fake_extract(source, destination):
                calls.append(source)
                binary = destination / 'python/bin/python3'
                binary.parent.mkdir(parents=True)
                binary.write_text('#!/bin/sh\nexit 0\n')
                binary.chmod(0o700)

            with patch.dict(os.environ, {'REPRISE_TEST_RUNTIME_ARCHIVE': str(archive)}), \
                 patch.object(ci_test, 'LOCK', lock), \
                 patch.object(ci_test, 'INTERP_CACHE', root / 'cache'), \
                 patch.object(ci_test.platform, 'machine', return_value='arm64'), \
                 patch.object(package_runtime, 'safe_extract', side_effect=fake_extract):
                first = ci_test.pinned_interpreter()
                self.assertEqual(first[0], str(root / 'cache' / f'interp-{digest[:12]}' / 'python/bin/python3'))
                self.assertEqual(first[1], {'REPRISE_TEST_RUNTIME_ARCHIVE': str(archive.resolve())})
                self.assertEqual(ci_test.pinned_interpreter(), first)
                self.assertEqual(calls, [archive.resolve()])
                archive.write_bytes(b'tampered')
                with self.assertRaises(ValueError):
                    ci_test.pinned_interpreter()

    def test_shard_uses_selected_interpreter_not_runner_python(self):
        with patch.object(ci_test, 'partition_modules', return_value=[['test_fixture']]), \
             patch.object(ci_test, 'pinned_interpreter',
                          return_value=('/pinned/python3', {'REPRISE_TEST_RUNTIME_ARCHIVE': '/fixture.tar.gz'})), \
             patch.object(ci_test.subprocess, 'run',
                          return_value=subprocess.CompletedProcess([], 0)) as run, \
             patch.object(sys, 'argv', ['ci_test.py', '--shard-index', '0', '--shard-count', '1']):
            self.assertEqual(ci_test.main(), 0)
        self.assertEqual(run.call_args.args[0][:3], ['/pinned/python3', '-B', '-m'])
        self.assertEqual(run.call_args.kwargs['env']['REPRISE_TEST_RUNTIME_ARCHIVE'], '/fixture.tar.gz')

    def test_unimportable_module_aborts_discovery(self):
        # A module failing to import yields a _FailedTest whose class module is
        # 'unittest.loader': partitioning it would silently drop the broken file
        # and later shards could report green without ever running it.
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            broken = Path(temp) / 'test_broken_import.py'
            broken.write_text('import nonexistent_module_for_shard_guard\n')
            with self.assertRaises(SystemExit) as caught:
                ci_test.discover_module_weights(tests_dir=Path(temp))
            message = str(caught.exception)
            self.assertIn('test_broken_import', message)
            self.assertIn('nonexistent_module_for_shard_guard', message)


if __name__ == '__main__':
    unittest.main()
