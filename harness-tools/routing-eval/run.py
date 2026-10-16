#!/usr/bin/env python3
"""Plan routing trials or validate isolated starters; never calls a model."""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify import execute, validate

ROOT = Path(__file__).resolve().parent


def local_path(root, value):
    if not isinstance(value, str):
        raise ValueError('Manifest paths must be strings')
    raw = Path(value)
    if raw.is_absolute() or '..' in raw.parts:
        raise ValueError('Manifest paths must be relative without parent traversal')
    path = root / raw
    if any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(root)):
        raise ValueError('Manifest symlinks are not supported')
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('Manifest path escaped cases root')
    return resolved


def load_cases(root):
    records = json.loads((root/'cases.json').read_text())
    if isinstance(records, dict):
        records = records['cases']
    seen = set()
    for case in records:
        ident = case['id']
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError('Unique case IDs required')
        seen.add(ident)
        prompt = local_path(root, case['prompt_file'])
        fixture = local_path(root, case['fixture_dir'])
        if not prompt.is_file() or not fixture.is_dir():
            raise ValueError('Expected prompt file and fixture directory')
        if any(p.is_symlink() for p in fixture.rglob('*')):
            raise ValueError('Fixture symlinks are not supported')
        validate({'checks': case['checks']})
    return records


def check_starters(root, cases):
    rows = []
    for case in cases:
        with tempfile.TemporaryDirectory(prefix='routing-fixture-') as folder:
            base = Path(folder)
            work = base/'work'
            shutil.copytree(local_path(root, case['fixture_dir']), work)
            logs = base/'logs'
            logs.mkdir(mode=0o700)
            results = []
            for check in case['checks']:
                argv = [value.replace('{cases}', str(root)).replace('{work}', str(work)) for value in check['argv']]
                result = execute({**check, 'argv': argv}, work, logs)
                # Logs are temporary and may include fixture output, not model evidence.
                result.pop('log', None)
                if result.get('exit_code') == 2 and not result.get('timeout'):
                    result['status'] = 'unavailable'
                results.append(result)
            known_bug = all(r['status'] == 'failed' and r.get('exit_code') == 1 and not r.get('timeout') for r in results)
            rows.append({'case_id': case['id'], 'starter_rejected': known_bug, 'checks': results})
    return {'mode': 'starter-validation', 'cases': rows, 'all_starters_rejected': all(r['starter_rejected'] for r in rows),
            'model_calls': 0, 'note': 'Expected failing starters only; not a model routing score. Exit 2/toolchain absence is not a successful rejection.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-fixtures', action='store_true')
    parser.add_argument('--cases', type=Path, default=ROOT/'cases')
    args = parser.parse_args()
    root = args.cases.resolve(strict=True)
    cases = load_cases(root)
    if args.check_fixtures:
        result = check_starters(root, cases)
        code = 0 if result['all_starters_rejected'] else 1
    else:
        result = {'mode': 'plan', 'model_calls': 0, 'cases': cases,
                  'spending_cap_eur_authorized': 2, 'budget_controller': 'not-implemented; paid calls disabled',
                  'arms': [
                      {'id': 'A', 'provider': 'deepseek-official', 'model': 'deepseek-flash', 'effort': 'low'},
                      {'id': 'B', 'provider': 'deepseek-official', 'model': 'deepseek-flash', 'effort': 'high'},
                      {'id': 'C', 'provider': 'deepseek-official', 'model': 'deepseek-flash', 'effort': 'low',
                       'expert': {'provider': 'openai-codex', 'model': 'gpt-6-astra', 'access': None}}],
                  'stage': 'offline-ready; real routes require verified model IDs, access and spending cap',
                  'external_calls_enabled': False}
        code = 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
