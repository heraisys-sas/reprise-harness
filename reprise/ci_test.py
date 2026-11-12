"""Run deterministic unittest fixtures with a disposable HOME, never user state."""
import argparse
import concurrent.futures
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).parent / 'tests'
PROJECT_ROOT = Path(__file__).parent.parent
TOP_DURATIONS = 25
LOCK = Path(__file__).parent / 'python-runtime.lock.json'
# Interpréteur épinglé extrait localement (hors archive .app) : cache stable
# par empreinte, ignoré par Git, recréé depuis l'archive en cas de doute.
INTERP_CACHE = Path(__file__).parent / '.runtime-py'


def discover_module_weights(tests_dir=TESTS_DIR):
    """Importable test modules with their number of test cases (discovery imports, runs nothing).

    Discovery failures must abort the run: a module that fails to import yields a
    `_FailedTest` whose class module is `unittest.loader`, which would silently
    replace the broken test file in the partition (false green CI).
    """
    loader = unittest.TestLoader()
    suite = loader.discover(str(tests_dir))
    if loader.errors:
        details = '\n'.join(loader.errors)
        raise SystemExit(
            f'Découverte des tests en échec ({len(loader.errors)} importation(s) impossible(s)) :\n{details}')
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


def partition_modules(count, tests_dir=TESTS_DIR):
    """Deterministic assignment: sort by weight desc then name, greedy bin-packing to the lightest shard."""
    weights = discover_module_weights(tests_dir)
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


def pinned_interpreter():
    """Interpréteur du runtime épinglé pour les tests locaux.

    Sources, par priorité : $REPRISE_TEST_PYTHON explicite, puis l'archive du
    runtime déjà en cache (`~/Library/Caches/Reprise/python/…`) — vérifiée par
    sha256 et extraite une fois dans .runtime-py/. Le binaire épinglé sert
    alors à exécuter les tests ET est exposé via REPRISE_TEST_RUNTIME_ARCHIVE
    (les tests marqués skip si l'archive manque tournent aussi en local).
    Retour : (chemin python, dict d'environnement) — (sys.executable, {}) en
    repli silencieux.
    """
    override = os.environ.get('REPRISE_TEST_PYTHON')
    if override:
        return override, {}
    archive = Path.home() / 'Library/Caches/Reprise/python'
    if not archive.is_dir():
        return sys.executable, {}
    lock = json.loads(LOCK.read_text())
    machine = platform.machine()
    target = lock['targets'].get(machine)
    if target is None:
        return sys.executable, {}
    archive_path = archive / f"cpython-{lock['python_version']}-{lock['release']}-{machine}.tar.gz"
    if not archive_path.is_file():
        return sys.executable, {}
    interp_root = INTERP_CACHE / f"interp-{target['sha256'][:12]}"
    binary = next(iter(interp_root.glob('*/bin/python3')), None)
    if binary is None:
        import package_runtime
        package_runtime.verify_archive(archive_path, target['sha256'])
        if interp_root.exists():
            import shutil
            shutil.rmtree(interp_root)
        package_runtime.safe_extract(archive_path, interp_root)
        # L'archive install_only contient un dossier racine 'python/'.
        binary = next(p for p in interp_root.glob('*/bin/python3'))
        stamp = interp_root / 'archive.txt'
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(archive_path.name + '\n')
    return str(binary), {'REPRISE_TEST_RUNTIME_ARCHIVE': str(archive_path)}


def run_shard(index, count, python, extra_env, home):
    modules = partition_modules(count)[index]
    if not modules:
        return index, 2, ''
    env = dict(os.environ, HOME=home, PYTHONDONTWRITEBYTECODE='1', **extra_env)
    env.pop('AGENT_RESUME_STATE', None)
    command = [python, '-B', '-m', 'unittest', *modules, '-q',
               '--durations', str(TOP_DURATIONS)]
    proc = subprocess.run(command, env=env, cwd=str(TESTS_DIR),
                          capture_output=True, text=True)
    return index, proc.returncode, proc.stdout + proc.stderr


def run_parallel(count, python, extra_env):
    """Même partition et mêmes tests que les shards CI, exécutés en parallèle.

    Sortie complète écrasée à chaque run dans .runtime-py/test-parallel.log ;
    la console ne montre que le résumé par shard.
    """
    log_path = INTERP_CACHE / 'test-parallel.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    failures = []
    with tempfile.TemporaryDirectory(prefix='reprise-test-home-') as home:
        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            futures = [pool.submit(run_shard, i, count, python, extra_env, home)
                       for i in range(count)]
            with open(log_path, 'w') as log:
                for index, code, output in (f.result() for f in futures):
                    tail = output.strip().splitlines()[-1] if output.strip() else '(aucune sortie)'
                    print(f'Shard {index}/{count}: exit={code} — {tail}')
                    if code != 0:
                        failures.append(index)
                    log.write(f'===== Shard {index}/{count} exit={code} =====\n{output}\n')
    if failures:
        print(f'Échec des shards {failures} — analyser le log avant toute relance : {log_path}')
        return 1
    print(f'OK — {count} shards en parallèle (log : {log_path}).')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shard-index', type=int, default=None,
                        help='index du shard (0-based), avec --shard-count')
    parser.add_argument('--shard-count', type=int, default=None,
                        help='nombre total de shards')
    parser.add_argument('--parallel', type=int, default=8,
                        help='exécute les N shards en parallèle (défaut 8) ; 0 pour la route séquentielle, --shard-* explicite pour un shard CI')
    parser.add_argument('--reuse-log', nargs='?', const=10, type=int, default=10, metavar='MINUTES',
                        help="avec --parallel : réutilise le log de moins de N minutes (défaut 10) au lieu de relancer")
    parser.add_argument('--force', action='store_true',
                        help='avec --parallel : relance même si un log frais existe')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        print('Python >= 3.11 requis (le runtime épinglé est 3.13) : les tests sur interpréteurs anciens crashent (sqlite3/mémoire).')
        return 2
    shard = parse_shard_args(parser, args)
    # --shard-index/--shard-count explicites conservent la route shards CI (parallel ignoré)
    if args.parallel and shard is None:
        log_path = INTERP_CACHE / 'test-parallel.log'
        if args.reuse_log is not None and not args.force:
            fresh = log_path.is_file() and (
                time.time() - log_path.stat().st_mtime) < args.reuse_log * 60
            if fresh:
                print(f'Log réutilisé ({log_path}, moins de {args.reuse_log} min) : pas de relance.')
                print('Passer --force pour relancer quand même.')
                tail = log_path.read_text().strip().splitlines()[-1] if log_path.stat().st_size else '(log vide)'
                print(tail)
                return 0
            print(f'Log absent ou de plus de {args.reuse_log} min : relance nécessaire ({log_path}).')
            return 1
        python, extra_env = pinned_interpreter()
        if python != sys.executable:
            print(f'Interpréteur épinglé : {python}')
        else:
            print('Interpréteur épinglé absent : repli sur', sys.executable)
        return run_parallel(args.parallel, python, extra_env)
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
