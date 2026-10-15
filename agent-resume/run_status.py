"""Evidence-based execution status; no model or secret-bearing log excerpts in UI."""
import fcntl
import os
from pathlib import Path
import selectors
import subprocess
import time
import tempfile
from runtime import JsonLines, terminate

LABELS = {
    'ready': 'Reprise programmée', 'waiting_quota': 'Disponibilité du quota à vérifier',
    'paused': 'En pause', 'launching': 'Lancement du CLI', 'running': 'Tour Codex démarré',
    'finished': 'Tour terminé · résultat métier non vérifié', 'needs_review': 'Résultat de reprise à vérifier',
    'needs_path': 'Dossier indisponible', 'cancelled': 'Reprise annulée · session modifiée',
    'blocked_trust': 'Lancement refusé · dossier non reconnu par le CLI',
    'blocked_auth': 'Connexion Codex requise', 'quota_blocked': 'Quota refusé au lancement',
    'failed': 'Échec de la reprise', 'timed_out': 'Délai de reprise dépassé',
}

def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS run_details (task_id TEXT PRIMARY KEY, state TEXT NOT NULL, '
               'reason TEXT, started_at REAL, ended_at REAL, last_event_at REAL, exit_code INTEGER)')

def save(db, ident, state, reason=None, **fields):
    with db:
        db.execute('INSERT INTO run_details(task_id,state,reason) VALUES (?,?,?) '
                   'ON CONFLICT(task_id) DO UPDATE SET state=excluded.state,reason=excluded.reason', (ident, state, reason))
        for name, value in fields.items():
            if name not in ('started_at', 'ended_at', 'last_event_at', 'exit_code'):
                raise ValueError('Unknown run field')
            db.execute(f'UPDATE run_details SET {name}=? WHERE task_id=?', (value, ident))

def classify(text):
    text = text.lower()
    if 'not inside a trusted directory' in text:
        return 'blocked_trust', 'Le CLI refuse ce dossier. Reprendre depuis Codex ou configurer explicitement sa confiance ; aucune protection contournée.'
    if 'usage_limit_exceeded' in text or 'hit your usage limit' in text:
        return 'quota_blocked', 'Le fournisseur a refusé le quota malgré la vérification préalable.'
    if 'not logged in' in text or 'authentication required' in text or 'unauthorized' in text:
        return 'blocked_auth', 'La connexion au compte doit être rétablie dans Codex.'
    return 'needs_review', 'Le CLI a échoué ; consulter le journal local pour le diagnostic.'

def rows(db, state_dir):
    result = []
    query = """SELECT t.id,t.session,t.cwd,t.due,t.status,t.attempts,
               d.state,d.reason,d.started_at,d.ended_at,d.last_event_at,d.exit_code
               FROM tasks t LEFT JOIN run_details d ON d.task_id=t.id ORDER BY t.due DESC"""
    for task in db.execute(query).fetchall():
        row = dict(task)
        state = row.pop('state')
        if state:
            if row['status'] not in ('ready', 'paused', 'waiting_quota', 'cancelled', 'needs_path'):
                row['status'] = state
            else:
                row.pop('reason', None)
        elif row['status'] == 'needs_review':
            try:
                with (Path(state_dir) / (row['id'] + '.log')).open('rb') as log:
                    text = log.read(65536).decode('utf-8', errors='replace')
                row['status'], row['reason'] = classify(text)
            except OSError:
                row['reason'] = 'Aucun diagnostic local disponible.'
        elif row['status'] == 'running':
            row['status'] = 'launching'
            row['reason'] = 'Ancienne exécution sans confirmation de démarrage du tour.'
        row['label'] = LABELS.get(row['status'], 'État inconnu')
        if row['status'] == 'ready' and row['due'] <= time.time():
            row['label'] = 'Échéance atteinte · lancement en attente'
        result.append(row)
    return result

def execute(db, task, argv, env, log, timeout=7200):
    ident = task['id']
    save(db, ident, 'launching', started_at=time.time())
    decoder = JsonLines()
    diagnostic = bytearray()
    completed = False
    turn_error = False
    timed_out = False
    with tempfile.TemporaryFile() as prompt:
        prompt.write(task['prompt'].encode()); prompt.seek(0)
        process = subprocess.Popen(argv, stdin=prompt, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, cwd=task['cwd'], env=env, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    start = time.monotonic()
    captured = 0
    try:
        with Path(log).open('wb') as output:
            os.chmod(log, 0o600)
            while selector.get_map() or process.poll() is None:
                if time.monotonic() - start >= timeout:
                    timed_out = True
                    break
                for key, _ in selector.select(.2):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    keep = max(0, 2 * 1024 * 1024 - captured)
                    output.write(chunk[:keep]); captured += min(len(chunk), keep)
                    diagnostic.extend(chunk[:max(0, 65536-len(diagnostic))])
                    for event in decoder.feed(chunk):
                        kind = event.get('type')
                        if kind == 'turn.started':
                            save(db, ident, 'running', last_event_at=time.time())
                        elif kind in ('item.started', 'item.completed'):
                            with db:
                                db.execute('UPDATE run_details SET last_event_at=? WHERE task_id=?', (time.time(), ident))
                        elif kind == 'turn.completed':
                            completed = True
                        elif kind in ('turn.failed', 'error'):
                            turn_error = True
    finally:
        selector.close()
        terminate(process)
    if timed_out:
        state, reason = 'timed_out', 'Le délai maximal a été atteint ; aucune nouvelle tentative automatique.'
    elif process.returncode == 0 and completed and not turn_error:
        state, reason = 'finished', 'Le CLI confirme la fin du tour ; cela ne prouve pas que la tâche demandée est achevée.'
    else:
        state, reason = classify(diagnostic.decode('utf-8', errors='replace'))
        if process.returncode == 0 and not turn_error:
            reason = 'Le processus est sorti sans événement de fin de tour confirmé.'
    save(db, ident, state, reason, ended_at=time.time(), exit_code=process.returncode)
    return 'finished' if state == 'finished' else 'needs_review'


def worker_active(state_dir):
    try:
        with (Path(state_dir) / 'worker.lock').open('rb') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
    except OSError:
        return False
