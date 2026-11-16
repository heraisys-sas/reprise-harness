#!/usr/bin/env python3
"""Applique les intégrations Reprise via l'API module (jamais d'édition directe)."""
import argparse
import json
import sys

HELPERS = None


def load(name):
    sys.path.insert(0, HELPERS)
    return __import__(name)


def main():
    global HELPERS
    parser = argparse.ArgumentParser()
    parser.add_argument('--helpers', required=True)
    parser.add_argument('--integrations', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--harness-project', required=True)
    parser.add_argument('--harness-profile', default='desktop')
    args = parser.parse_args()
    HELPERS = args.helpers

    results = {}
    wanted = args.integrations.split(',')

    if 'claude' in wanted:
        from claude_integration import plan, apply, verify
        p = plan(HELPERS, __import__('pathlib').Path.home(), args.project, 'install')
        r = apply(HELPERS, p)
        v = verify(HELPERS, __import__('pathlib').Path.home(), project=args.project)
        results['claude'] = {'apply': r.get('status'), 'verify': v['status'],
                             'daemon': v.get('daemon', {})}

    if 'chatgpt' in wanted:
        from chatgpt_integration import plan, apply, verify
        p = plan(HELPERS, __import__('pathlib').Path.home(), args.project, 'install')
        r = apply(HELPERS, p)
        v = verify(HELPERS, __import__('pathlib').Path.home(), project=args.project)
        results['chatgpt'] = {'apply': r.get('status'), 'verify': v['status']}

    if 'harness' in wanted:
        from harness_integration import plan, apply, verify
        p = plan(HELPERS, __import__('pathlib').Path.home() / '.dsh',
                 args.harness_profile, args.harness_project, 'install',
                 True, 'standard', False, None, False, False, True, None, None, None)
        r = apply(HELPERS, p)
        v = verify(__import__('pathlib').Path.home() / '.dsh', args.harness_profile,
                   root=HELPERS, project=args.harness_project)
        results['harness'] = {'apply': r.get('status'), 'verify': v['status']}

    for name, res in results.items():
        status = res.get('verify', 'unknown')
        print(f"{name}: {'CHANGED' if res.get('apply') == 'completed' else 'OK'} verify={status}")
    print(json.dumps(results))
    failed = [n for n, r in results.items() if r.get('verify') not in ('verified', 'ok')]
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
