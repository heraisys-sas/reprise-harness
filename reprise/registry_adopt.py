"""Explicit operator adoption of legacy encrypted registries; no service startup."""
import argparse
import json
import sys

from coordinator_jobs import Jobs
from orchestrator_memory import Memory


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True)
    parser.add_argument('--registry', required=True, choices=('memory', 'jobs'))
    parser.add_argument('--confirm-existing-registry', action='store_true')
    args = parser.parse_args(argv)
    if not args.confirm_existing_registry:
        parser.error('Explicit --confirm-existing-registry required')
    try:
        target = Memory(args.state) if args.registry == 'memory' else Jobs(args.state)
        result = target.adopt_legacy_registry()
    except (OSError, ValueError):
        print(json.dumps({'status': 'refused'}))
        return 1
    print(json.dumps({'registry': args.registry, **result}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
