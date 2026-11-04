#!/usr/bin/env python3
"""Aggregate explicit per-request observations. Missing values remain unknown."""
import argparse
import json
import math

FIELDS = ('prompt_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens', 'completion_tokens', 'reasoning_tokens', 'latency_ms', 'billed_cost')


def report(events):
    seen, requests = set(), []
    for event in events:
        ident = event['request_id']
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError('Unique nonempty request_id required; do not sum stream snapshots')
        seen.add(ident)
        usage = event.get('usage') or {}
        row = {k: usage.get(k) for k in FIELDS}
        for value in row.values():
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError('Metrics must be finite nonnegative numbers or null')
        total, hit, miss = (row[k] for k in FIELDS[:3])
        if all(v is not None for v in (hit, miss, total)) and hit+miss != total:
            raise ValueError('Inconsistent input token counts')
        requests.append({'request_id': ident, 'provider': event.get('provider'), 'model': event.get('model'),
                         'task_id': event.get('task_id'), 'status': event.get('status', 'unknown'), **row})
    totals = {k: sum(r[k] for r in requests) if requests and all(r[k] is not None for r in requests) else None for k in FIELDS}
    currencies = {e.get('currency') for e in events}
    if len(currencies) != 1 or None in currencies:
        totals['billed_cost'] = None
    return {'requests': requests, 'totals': totals, 'currency': next(iter(currencies)) if len(currencies) == 1 else None,
            'task_outcomes': [e.get('validation') for e in events if e.get('validation') is not None],
            'note': 'Only provider observations; reasoning is a reported subset, never added to completion. Validation must include explicit tool evidence; no automatic success inference.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('jsonl')
    args = p.parse_args()
    with open(args.jsonl) as stream:
        print(json.dumps(report([json.loads(line) for line in stream if line.strip()]), ensure_ascii=False))
