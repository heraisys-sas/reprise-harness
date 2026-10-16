"""Portable task routes, restored paused; no credentials or scheduler ledger."""
import json
import math
from pathlib import Path
import uuid
import zipfile
import engines
import scheduler


def backup(db, path):
    routes = {r[0]: {'engine': r[1], 'options': json.loads(r[2])}
              for r in db.execute('SELECT * FROM task_engines')}
    tasks = []
    for row in db.execute('SELECT * FROM tasks'):
        task = dict(row)
        engine, values = engines.route(db, task['id'])
        task['route'] = routes.get(task['id'], {}) | {'engine': engine, 'options': values}
        tasks.append(task)
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps({'version': 2, 'tasks': tasks}))


def restore(db, path):
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo('manifest.json')
        if info.file_size > 10_000_000:
            raise ValueError('Sauvegarde trop volumineuse')
        data = json.loads(archive.read(info))
    if data.get('version') not in (1, 2):
        raise ValueError('Version incompatible')
    validated = []
    for task in data['tasks']:
        ident = str(uuid.UUID(task['id']))
        route = task.get('route', {}) if data['version'] == 2 else {}
        engine = route.get('engine', 'codex')
        values = engines.options(engine, route.get('options', {}))
        session = engines.validate_session(engine, task['session'])
        due, attempts = float(task['due']), int(task['attempts'])
        if not math.isfinite(due) or attempts < 0 or not isinstance(task['prompt'], str) or not Path(task['cwd']).is_absolute():
            raise ValueError('Tâche invalide')
        validated.append(((ident, session, task['cwd'], task['prompt'], due, 'paused', attempts), engine, values))
    with db:
        for row, engine, values in validated:
            if db.execute('INSERT OR IGNORE INTO tasks VALUES (?,?,?,?,?,?,?)', row).rowcount:
                engines.assign(db, row[0], engine, values)
        scheduler.put(db, 'auto_enabled', False)
