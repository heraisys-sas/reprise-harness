"""Command-line access to the local project registry."""
import argparse
import json
import os
from pathlib import Path
import sqlite3

from app_paths import state_directory
from project_registry import ProjectRegistry


def main(root, argv):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('project-list')
    create = commands.add_parser('project-create')
    create.add_argument('--name', required=True)
    create.add_argument('--root', required=True)
    create.add_argument('--source', required=True)
    rename = commands.add_parser('project-rename')
    rename.add_argument('--project-id', required=True)
    rename.add_argument('--name', required=True)
    associate = commands.add_parser('project-associate')
    associate.add_argument('--project-id', required=True)
    associate.add_argument('--harness', choices=('codex', 'harness', 'claude'), required=True)
    associate.add_argument('--workspace', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        state = state_directory(Path(root))
        with ProjectRegistry(state) as registry:
            if args.command == 'project-list':
                result = {'status': 'ok', 'projects': registry.list()}
            elif args.command == 'project-create':
                result = {'status': 'ok', 'project': registry.create(args.name, args.root, args.source)}
            elif args.command == 'project-rename':
                result = {'status': 'ok', 'project': registry.rename(args.project_id, args.name)}
            else:
                result = {'status': 'ok', 'project': registry.associate_workspace(args.project_id, args.harness, args.workspace)}
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print(json.dumps({'status': 'refused', 'message': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
