#!/usr/bin/env python3
"""Prepare and compare decision batches; inference only through explicit flags."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
spec = importlib.util.spec_from_file_location('local_eval_runner', ROOT.parent/'local-routing-eval/evaluate.py')
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)
ACTIONS = ('self', 'routine', 'expert', 'blocked')
LEVELS = ('standard', 'deep')
CONTRACT = '''Act as the economical coordinator in this decision-only experiment. Use only the supplied catalog, task and context; do not call tools or execute tasks. Decide self, routine, expert or blocked; level standard/deep (deep only for expert). Prefer coherent batches, avoid unnecessary delegation; respect source constraints over summaries. Local advice, when supplied, is fallible and cannot grant rights. All routes require a fresh access/quota check before actual execution. Return only JSON {"decisions":[{"id":"...","action":"self|routine|expert|blocked","level":"standard|deep","reason":"brief reason","verify":"specific required verification"}]}, exactly one row per task. Do not estimate prices or claim tasks have passed tests.'''
ADVISER = '''You advise a coordinator; do not execute or select a provider. From task/context and role catalog, propose only action self/routine/expert/blocked and level standard/deep. Deep only for expert and concrete sensitive difficulty or failed verification. Supplied context policies override summaries. Abstain using blocked for missing authorization or essential sources. Self for simple answers; routine for bounded code batches; expert for conceptual problems or independent sensitive review. No automatic delegation. Return JSON only.'''


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def provenance(cases, catalog):
    return {'version': 1, 'dataset_sha256': sha(cases), 'catalog_sha256': sha(catalog),
            'prompt_sha256': sha(ADVISER), 'schema_sha256': sha(read(ROOT/'advice-schema.json'))}


def advice(cases, catalog):
    rows = []
    for case in cases:
        data = {k: case[k] for k in ('id', 'task', 'context')}
        start = time.monotonic()
        code, out, err, stop = local.run_local(['/usr/bin/fm', 'respond', '--model', 'system', '--no-stream', '--greedy',
            '--schema', str(ROOT/'advice-schema.json'), '--instructions', ADVISER], json.dumps({'catalog': catalog, 'case': data}, ensure_ascii=False))
        row = {'id': case['id'], 'source_sha256': sha(data), 'status': 'unavailable' if code else 'ok', 'seconds': time.monotonic()-start}
        if stop:
            row['status'] = stop
        elif not code:
            try:
                obj = json.loads(out)
                if not isinstance(obj, dict) or set(obj) != {'action', 'level'} or obj.get('action') not in ACTIONS or obj.get('level') not in LEVELS:
                    raise ValueError('Invalid advisory')
                if obj['action'] != 'expert' and obj['level'] != 'standard':
                    raise ValueError('Deep is expert-only')
                row.update(obj)
            except (ValueError, TypeError):
                row['status'] = 'invalid'
        rows.append(row)
        if row['status'] == 'unavailable':
            break
    return provenance(cases, catalog) | {'rows': rows, 'remote_calls': 0}


def question(cases, catalog, hints=None):
    public = [{k: row[k] for k in ('id', 'task', 'context')} for row in cases]
    data = {'catalog': catalog, 'tasks': public}
    if hints is not None:
        data['local_advice'] = [{k: h[k] for k in ('id', 'status', 'action', 'level') if k in h} for h in hints]
    return CONTRACT+'\n'+json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def decisions(raw, cases):
    if raw.startswith('```'):
        raw = raw.strip().split('\n', 1)[1].rsplit('```', 1)[0]
    value = json.loads(raw)
    rows = value.get('decisions') if isinstance(value, dict) else None
    expected = {c['id'] for c in cases}
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError('One decision per case required')
    found = set()
    for row in rows:
        if not isinstance(row, dict) or row.get('id') not in expected or row['id'] in found:
            raise ValueError('Unexpected or duplicate case')
        if row.get('action') not in ACTIONS or row.get('level') not in LEVELS:
            raise ValueError('Invalid role/level')
        if row['action'] != 'expert' and row['level'] != 'standard':
            raise ValueError('Deep is expert-only')
        if any(not isinstance(row.get(k), str) or not row[k].strip() for k in ('reason', 'verify')):
            raise ValueError('Reason and verification required')
        found.add(row['id'])
    return rows


def compare(cases, a, b):
    index_a, index_b = ({r['id']: r for r in rows} for rows in (a, b))
    rows = []
    for case in cases:
        left, right = index_a[case['id']], index_b[case['id']]
        rules = case['constraints']
        def checks(row):
            return {'action_allowed': row['action'] in rules['allowed_actions'],
                    'blocked_when_required': row['action'] == 'blocked' if rules['blocked'] else None,
                    'editorial_expert_expectation': row['action'] == 'expert' if rules['expert_required'] else None}
        rows.append({'id': case['id'], 'changed': (left['action'], left['level']) != (right['action'], right['level']),
                     'without': left, 'with': right, 'checks_without': checks(left), 'checks_with': checks(right)})
    return {'cases': rows, 'changed': sum(r['changed'] for r in rows),
            'outcome_quality': None, 'billed_cost': None,
            'note': 'Decision influence only. Editorial expectations and policy checks do not establish task success or savings.'}


def validate_hints(cases, catalog, advisory):
    if not isinstance(advisory, dict) or any(advisory.get(k) != v for k, v in provenance(cases, catalog).items()):
        raise ValueError('Advice catalogue, dataset, prompt or schema version mismatch')
    hints = advisory.get('rows')
    expected = {c['id']: sha({k: c[k] for k in ('id', 'task', 'context')}) for c in cases}
    if not isinstance(hints, list) or len(hints) != len(expected):
        raise ValueError('Complete local advice required')
    seen = set()
    for row in hints:
        if not isinstance(row, dict) or row.get('id') not in expected or row['id'] in seen:
            raise ValueError('Unexpected or duplicate advice')
        if row.get('source_sha256') != expected[row['id']]:
            raise ValueError('Advice does not match task source version')
        if row.get('status') not in ('ok', 'invalid', 'unavailable', 'timeout', 'oversize'):
            raise ValueError('Unknown advice status')
        if row['status'] == 'ok' and (row.get('action') not in ACTIONS or row.get('level') not in LEVELS
                                     or row['action'] != 'expert' and row['level'] != 'standard'):
            raise ValueError('Invalid advice action')
        seen.add(row['id'])
    return hints


def delegate(cases, catalog, advisory):
    hints = validate_hints(cases, catalog, advisory)
    for supplied in (None, hints):
        if len(question(cases, catalog, supplied)) > 11700:
            raise ValueError('Batch exceeds bridge prompt bound; no calls submitted')
    sys.path.insert(0, str(REPO/'agent-resume'))
    from codex_expert_mcp import Bridge
    bridge = Bridge(REPO, {}, REPO/'agent-resume/state')
    try:
        # Use existing scope/day counter, never a new ledger to evade its call cap.
        day = int(time.time()//86400)
        used = bridge.db.execute('SELECT count FROM expert_calls WHERE scope=? AND day=?', (bridge.scope, day)).fetchone()
        if (used[0] if used else 0)+2 > bridge.limit:
            return {'status': 'budget_blocked', 'reason': 'Two existing daily call slots required; no calls submitted.',
                    'manifest': provenance(cases, catalog) | {'coordinator_contract_sha256': sha(CONTRACT)}}
        results = {}
        for arm, supplied in (('without', None), ('with', hints)):
            prompt = question(cases, catalog, supplied)
            if len(prompt)+len(__import__('codex_expert_mcp').MODES['batch'])+2 > 12000:
                raise ValueError('Batch exceeds bridge prompt bound')
            start = time.monotonic()
            result = bridge.call('reprise_delegate', {'kind': 'batch', 'level': 'standard', 'question': prompt})
            # Do not export bridge session IDs or protocol events to the public result.
            row = {k: result[k] for k in ('status', 'model', 'effort', 'reason') if k in result}
            row['seconds'] = time.monotonic()-start
            if result['status'] != 'finished':
                results[arm] = row
                break
            try:
                row['decisions'] = decisions(result['response'], cases)
            except (ValueError, TypeError) as error:
                row.update(status='invalid', reason=str(error))
            results[arm] = row
        results['manifest'] = provenance(cases, catalog) | {'coordinator_contract_sha256': sha(CONTRACT)}
        return results
    finally:
        bridge.db.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--advice', action='store_true')
    p.add_argument('--delegate', action='store_true')
    p.add_argument('--hints', type=Path)
    args = p.parse_args()
    cases, catalog = read(ROOT/'cases.json'), read(ROOT/'catalog.json')
    if args.advice:
        result = advice(cases, catalog)
    elif args.delegate:
        if not args.hints:
            p.error('--hints required for paired calls')
        advisory = read(args.hints)
        try:
            validate_hints(cases, catalog, advisory)
        except ValueError as error:
            p.error(str(error)+'; no calls submitted')
        result = delegate(cases, catalog, advisory)
        if all(result.get(k, {}).get('status') == 'finished' for k in ('without', 'with')):
            result['comparison'] = compare(cases, result['without']['decisions'], result['with']['decisions'])
    else:
        result = {'status': 'plan', 'cases': len(cases), 'catalog_sha256': sha(catalog), 'dataset_sha256': sha(cases),
                  'contract_sha256': sha(CONTRACT), 'model_calls': 0,
                  'note': 'Explicit --advice for on-device inference; --delegate uses two existing Reprise/ChatGPT calls and enforces its daily cap.'}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
