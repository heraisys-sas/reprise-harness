"""Runner local : exécute ci_test.py en N shards parallèles et agrège.

Même partition déterministe que la CI (`partition_modules`), sans service ni
Chrome réel. La sandbox locale peut refuser `swiftc` (test_apple_pilot_ui) :
l'échec reste visible dans la sortie, à comparer au bruit baseline.

Usage : python3 reprise/ci_test_local.py [N]   (N shards, défaut = os.cpu_count())
"""
import concurrent.futures
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SANDVOX_SWIFT_NOISE = 'test_native_local_helper_contract_and_self_deadline_without_inference'


def run_shard(index, count):
    proc = subprocess.run(
        [sys.executable, str(HERE / 'ci_test.py'), '--shard-index', str(index), '--shard-count', str(count)],
        capture_output=True, text=True)
    return index, proc.returncode, proc.stdout + proc.stderr


def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else (os.cpu_count() or 4)
    print(f'{count} shards en parallèle (partition ci_test.py)')
    failed = []
    swift_noise = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        for index, code, output in pool.map(lambda pair: run_shard(*pair), [(i, count) for i in range(count)]):
            tail = output.strip().splitlines()[-1] if output.strip() else '(sortie vide)'
            print(f'Shard {index}: exit={code} — {tail}')
            if code != 0:
                failed.append(index)
                if SANDVOX_SWIFT_NOISE in output:
                    swift_noise = True
    if failed:
        print(f'ÉCHEC shards {failed}' + (' — uniquement le bruit baseline swiftc (sandbox) si confirmé ci-dessus.' if swift_noise else ''))
        return 1
    print('TOUS SHARDS OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
