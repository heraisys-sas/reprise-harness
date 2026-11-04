#!/usr/bin/env python3
"""Connection CLI for the Claude Desktop Quick Entry backend, modelled on resume.

Exposes status / probe / configure over claude_desktop without any model call or
daemon startup. Each subcommand prints one JSON object on stdout and exits 0 on
success, 1 otherwise; failures are structured, never raw tracebacks.
"""
import argparse
import json
from pathlib import Path
import sys

import claude_desktop
import engines


def _fail(reason, **extra):
    return 1, {'status': 'failed', 'reason': reason, **extra}


def run_status(state):
    db = claude_desktop.open_state(state)
    try:
        return 0, claude_desktop.status(db, state)
    finally:
        db.close()


def run_probe(program, path, session=None):
    result = claude_desktop.probe(program, path, session)
    code = 0 if result.get('connected') else 1
    return code, result


def run_configure(state, path, program):
    db = claude_desktop.open_state(state)
    try:
        result = claude_desktop.configure(db, program, path)
        code = 0 if result.get('saved') else 1
        return code, result
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    status = sub.add_parser('status', help='Discover candidate sockets without connecting')
    status.add_argument('--state', required=True, help='Private state directory')

    probe = sub.add_parser('probe', help='Probe an endpoint without any model call')
    probe.add_argument('--socket', required=True, help='Absolute socket path to test')
    probe.add_argument('--session', help='Optional session UUID to check load state')

    configure = sub.add_parser('configure', help='Save a probed socket or restore auto detection')
    configure.add_argument('--state', required=True, help='Private state directory')
    configure.add_argument('--socket', help='Absolute socket path to verify and save; omit to restore auto')

    args = parser.parse_args(argv)
    program = engines.executable('claude-desktop')
    try:
        if args.command == 'status':
            code, result = run_status(args.state)
        elif args.command == 'probe':
            code, result = run_probe(program, args.socket, args.session)
        else:
            code, result = run_configure(args.state, args.socket, program)
    except engines.Unavailable as error:
        code, result = _fail(error.reason, state=error.state)
    except (ValueError, OSError) as error:
        code, result = _fail(str(error) or 'Opération refusée.')
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
