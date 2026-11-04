"""Opt-in memory configuration shared by managed local harness installers.

No model/client activation. Registry observation runs read-only in a separate
process so closing SQLite cannot release the installer's POSIX registry locks.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

from orchestrator_memory import _uuid
from project_registry import _git_info

CONNECTOR = 'reprise-memory'
SERVER_NAME = 'reprise_memory'

_REGISTRY_BINDING = r'''
import json, os, pathlib, sqlite3, stat, sys
state = pathlib.Path(sys.argv[1])
project, harness = sys.argv[2:4]
fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
 info = os.fstat(fd)
 if info.st_uid != os.getuid() or info.st_mode & 0o077:
  raise ValueError('Private state required')
 for suffix in ('-journal', '-wal', '-shm'):
  try: os.stat('projects.sqlite'+suffix, dir_fd=fd, follow_symlinks=False)
  except FileNotFoundError: pass
  else: raise ValueError('Registry transaction active')
 filefd = os.open('projects.sqlite', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
 try:
  before = os.fstat(filefd)
  if not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid() or before.st_nlink != 1 or before.st_mode & 0o077:
   raise ValueError('Private registry required')
  header=os.pread(filefd,100,0)
  if len(header)<100 or header[:16]!=b'SQLite format 3\x00' or header[18:20]!=b'\x01\x01':
   raise ValueError('Rollback journal registry required before SQLite open')
  db=sqlite3.connect((state/'projects.sqlite').as_uri()+'?mode=ro', uri=True, timeout=2)
  try:
   db.execute('PRAGMA query_only=ON')
   db.execute('PRAGMA temp_store=MEMORY')
   db.execute('BEGIN')
   if db.execute('PRAGMA journal_mode').fetchone()[0].lower()=='wal':
    raise ValueError('WAL registry refused')
   rows=db.execute('SELECT p.source_common_dir,w.path,w.common_dir FROM projects p '
    'JOIN project_workspaces w ON w.project_id=p.id WHERE p.id=? AND w.harness=?',(project,harness)).fetchall()
   after=os.stat('projects.sqlite', dir_fd=fd, follow_symlinks=False)
   if len(rows)!=1 or (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):
    raise ValueError('Registry binding unavailable')
   print(json.dumps(rows[0]))
  finally: db.close()
 finally: os.close(filefd)
finally: os.close(fd)
'''


def selected(old, access=None, project_id=None, *, action='install'):
    """Omission preserves installed grants; removed installs never re-enable write."""
    previous = (old or {}).get('memory', {'access': 'off', 'project_id': None})
    if (not isinstance(previous, dict) or set(previous) != {'access', 'project_id'}
            or previous.get('access') not in ('off', 'read', 'write')):
        raise ValueError('Sélection mémoire gérée invalide.')
    if access is not None and access not in ('off', 'read', 'write'):
        raise ValueError('Droit mémoire invalide.')
    installed = (old or {}).get('status') == 'installed'
    effective = access if access is not None else previous['access'] if installed else 'off'
    ident = project_id if project_id is not None else previous['project_id']
    if ident is not None:
        _uuid(ident)
    if action == 'remove':
        return {'access': 'off', 'project_id': ident}
    if effective != 'off' and ident is None:
        raise ValueError('UUID de projet mémoire explicitement enregistré requis.')
    if effective == 'write' and access is None and project_id is not None and ident != previous['project_id']:
        raise ValueError('Un nouveau projet exige une autorisation mémoire write explicite.')
    return {'access': effective, 'project_id': ident}


def validate_binding(state, memory, workspace, harness):
    if memory['access'] == 'off':
        return
    if harness not in ('harness', 'claude'):
        raise ValueError('Harness mémoire non pris en charge.')
    state = Path(state).expanduser().absolute()
    if state.is_symlink():
        raise ValueError('État mémoire symbolique refusé.')
    try:
        result = subprocess.run([sys.executable, '-E', '-s', '-B', '-c', _REGISTRY_BINDING,
            str(state), _uuid(memory['project_id']), harness], capture_output=True, text=True,
            timeout=5, check=True)
        common, registered, registered_common = json.loads(result.stdout)
        selected_workspace = Path(workspace).expanduser().resolve(strict=True)
        if selected_workspace != Path(registered).resolve(strict=True):
            raise ValueError('Workspace différent du projet mémoire enregistré.')
        top, actual_common = _git_info(str(selected_workspace), 'Memory workspace')
        if top != str(selected_workspace) or actual_common != common or registered_common != common:
            raise ValueError('Identité Git du workspace mémoire modifiée.')
    except (OSError, ValueError, TypeError, subprocess.SubprocessError):
        raise ValueError('Projet mémoire indisponible ou association du workspace invalide ; aucune création implicite.') from None


def entry(release, state, memory, harness_id):
    if memory['access'] == 'off':
        return None
    release = Path(release)
    arguments = ['-E', '-s', '-B', str(release / 'Contents/Resources/helpers/reprise_memory_mcp.py'),
                 '--state', str(state), '--project-id', memory['project_id'], '--harness-id', harness_id]
    if memory['access'] == 'write':
        arguments.append('--allow-write')
    return {'command': str(release / 'Contents/Resources/runtime/bin/python3'), 'args': arguments}


def probe(configured, memory):
    """Direct schema probe only: no memory reads/writes, no client loaded claim."""
    if configured is None:
        return {'configured': False, 'access': 'off', 'client_loaded': 'unknown', 'exchange_confirmed': 'unknown'}
    from reprise_memory_adapter import OPERATIONS
    from orchestrator_memory_service import MUTATIONS
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}]
    env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'DYLD_')) and k != 'AGENT_RESUME_STATE'}
    response = subprocess.run([configured['command'], *configured['args']],
        input=''.join(json.dumps(r) + '\n' for r in requests), capture_output=True, text=True,
        timeout=15, check=True, env=env)
    messages = [json.loads(line) for line in response.stdout.splitlines()]
    info = next(m['result']['serverInfo'] for m in messages if m.get('id') == 1)
    names = sorted(tool['name'] for m in messages if m.get('id') == 2 for tool in m['result']['tools'])
    expected = sorted('reprise_memory_' + name for name, op in OPERATIONS.items()
                      if memory['access'] == 'write' or op not in MUTATIONS)
    if info.get('name') != 'reprise-project-memory' or names != expected:
        raise ValueError('Catalogue mémoire différent des droits sélectionnés.')
    return {'configured': True, **memory, 'tools': names, 'evidence': 'direct_probe_tools_schema',
            'client_loaded': 'unknown', 'exchange_confirmed': 'unknown', 'inference_executed': False}
