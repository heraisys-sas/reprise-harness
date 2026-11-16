#!/usr/bin/env python3
"""Vérifie les intégrations via l'API module (pas de déploiement)."""
import argparse
import json
import pathlib
import sys

HELPERS = None


def main():
    global HELPERS
    parser = argparse.ArgumentParser()
    parser.add_argument('--helpers', required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--harness-project', required=True)
    parser.add_argument('--harness-profile', default='desktop')
    args = parser.parse_args()
    HELPERS = args.helpers
    home = pathlib.Path.home()

    sys.path.insert(0, HELPERS)
    out = {}
    try:
        from claude_integration import verify as cv
        out['claude'] = cv(HELPERS, home, project=args.project)['status']
    except Exception as e:
        out['claude'] = f'error: {e}'
    try:
        from chatgpt_integration import verify as gv
        out['chatgpt'] = gv(HELPERS, home, project=args.project)['status']
    except Exception as e:
        out['chatgpt'] = f'error: {e}'
    try:
        from harness_integration import verify as hv
        out['harness'] = hv(home / '.dsh', args.harness_profile,
                            root=HELPERS, project=args.harness_project)['status']
    except Exception as e:
        out['harness'] = f'error: {e}'

    print(json.dumps(out, ensure_ascii=False))
    bad = [k for k, v in out.items() if v != 'verified']
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
