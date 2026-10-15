#!/usr/bin/env python3
"""Read Harness 0.2 SessionEvent JSONL; emit metadata only, no transcript."""
import argparse
import json
from usage_report import report


def observations(events, task_id):
    route, start, rows = {}, {}, []
    previous = None
    for event in events:
        seq = event['seq']
        if previous is not None and seq != previous+1:
            raise ValueError('Session events must be contiguous, ordered and from one session')
        previous = seq
        kind, data = event['type'], event['data']
        key = (data.get('turn'), data.get('step'))
        if kind == 'request/header':
            route = data.get('header', {}).get('config', {})
        elif kind == 'step/start':
            start[key] = event['time']
        elif kind in ('assistant/message', 'assistant/attempt'):
            usage = data.get('usage')
            if usage is None:
                usage = next((r['chunk']['usage'] for r in reversed(data.get('stream', []))
                              if r.get('type') == 'chunk' and r.get('chunk', {}).get('type') == 'usage'), {})
            mapped = {target: usage.get(source) for target, source in (
                ('prompt_cache_hit_tokens', 'cacheReadTokens'), ('prompt_cache_miss_tokens', 'inputTokens'),
                ('completion_tokens', 'outputTokens'), ('reasoning_tokens', 'reasoningTokens'))}
            # Harness input/cache-write/cache-read counters are disjoint. Unknown cache writes prevent a total.
            counts = [usage.get(k) for k in ('inputTokens', 'cacheReadTokens', 'cacheWriteTokens')]
            mapped['prompt_cache_miss_tokens'] = counts[0]+counts[2] if counts[0] is not None and counts[2] is not None else None
            mapped['prompt_tokens'] = sum(counts) if all(v is not None for v in counts) else None
            mapped['latency_ms'] = event['time']-start[key] if key in start else None
            rows.append({'request_id': f'{task_id}:{seq}', 'task_id': task_id,
                         'provider': route.get('provider'), 'model': route.get('model'),
                         'status': 'attempt' if kind.endswith('attempt') else 'interrupted' if data.get('interrupted') else 'response',
                         'usage': mapped})
    return rows


def cli_observations(events, task_id):
    rows = []
    for event in events:
        if event.get('type') != 'status' or event.get('phase') != 'step_end':
            continue
        u = event.get('usage') or {}
        rows.append({'request_id': f"{task_id}:{event['turn']}:{event['step']}",
                     'task_id': task_id, 'status': 'step_end',
                     'usage': {target: u.get(source) for target, source in (
                         ('prompt_cache_hit_tokens', 'cacheReadTokens'),
                         ('prompt_cache_miss_tokens', 'inputTokens'),
                         ('completion_tokens', 'outputTokens'), ('reasoning_tokens', 'reasoningTokens'))}})
    return rows


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('jsonl')
    p.add_argument('--task-id', required=True)
    p.add_argument('--cli', action='store_true', help='Read --json status events; root steps only, route/latency unknown')
    args = p.parse_args()
    with open(args.jsonl) as stream:
        reader = cli_observations if args.cli else observations
        result = report(reader((json.loads(line) for line in stream if line.strip()), args.task_id))
    print(json.dumps(result, ensure_ascii=False))
