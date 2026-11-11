"""Run deterministic unittest fixtures with a disposable HOME, never user state."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = Path(__file__).parent / 'tests'
PROJECT_ROOT = Path(__file__).parent.parent
TOP_DURATIONS = 25


def discover_module_weights():
    """Importable test modules with their number of test cases (discovery imports, runs nothing)."""
    suite = unittest.defaultTestLoader.discover(str(TESTS_DIR))
    weights = {}

    def walk(node):
        if isinstance(node, unittest.TestSuite):
            for child in node:
                walk(child)
        else:
            module = node.__class__.__module__
            weights[module] = weights.get(module, 0) + 1
    walk(suite)
    return weights


def partition_modules(count):
    """Deterministic assignment: sort by weight desc then name, greedy bin-packing to the lightest shard."""
    weights = discover_module_weights()
    ordered = sorted(weights, key=lambda name: (-weights[name], name))
    shards = [[] for _ in range(count)]
    loads = [0] * count
    for name in ordered:
        target = min(range(count), key=lambda i: (loads[i], i))
        shards[target].append(name)
        loads[target] += weights[name]
    return shards


def parse_shard_args(parser, args):
    sharded = (args.shard_index is not None) or (args.shard_count is not None)
    if not sharded:
        return None
    if args.shard_index is None or args.shard_count is None:
        parser.error('--shard-index et --shard-count doivent être fournis ensemble.')
    if args.shard_count < 1:
        parser.error('--shard-count doit être >= 1.')
    if not 0 <= args.shard_index < args.shard_count:
        parser.error('--shard-index doit vérifier 0 <= index < count.')
    return args.shard_index, args.shard_count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard-index', type=int, default=None,
                        help='index du shard (0-based), avec --shard-count')
    parser.add_argument('--shard-count', type=int, default=None,
                        help='nombre total de shards')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        print('Python >= 3.11 requis (le runtime épinglé est 3.13) : les tests sur interpréteurs anciens crashent (sqlite3/mémoire).')
        return 2
    shard = parse_shard_args(parser, args)
    with tempfile.TemporaryDirectory(prefix='reprise-test-home-') as home:
        env = dict(os.environ, HOME=home, PYTHONDONTWRITEBYTECODE='1')
        env.pop('AGENT_RESUME_STATE', None)
        if shard is None:
            command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s',
                       str(TESTS_DIR), '-q']
        else:
            index, count = shard
            modules = partition_modules(count)[index]
            if not modules:
                print(f'Shard {index}/{count} vide : aucun module de test affecté.')
                return 2
            print(f'Shard {index}/{count} : {len(modules)} modules')
            command = [sys.executable, '-B', '-m', 'unittest', *modules, '-q',
                       '--durations', str(TOP_DURATIONS)]
        # module names are discovery-style top-level names ('test_x'): run from the tests dir
        return subprocess.run(command, env=env, cwd=str(TESTS_DIR)).returncode


if __name__ == '__main__':
    sys.exit(main())
