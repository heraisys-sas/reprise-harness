"""Deterministic shard partition of the test modules: exhaustive, no duplicate."""
import sys
import unittest
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
