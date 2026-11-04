"""Evidence-based execution status; no model or secret-bearing log excerpts in UI."""
import fcntl
import os
from pathlib import Path
import selectors
import subprocess
import time
import tempfile
from runtime import JsonLines, terminate
from engine_events import ExecutionEvidence, failure

LABELS = {
    'ready': 'Reprise programmée', 'waiting_quota': 'Disponibilité du quota à vérifier',
    'paused': 'En pause', 'launching': 'Lancement du moteur', 'running': 'Tour du moteur démarré',
    'finished': 'Tour terminé · résultat métier non vérifié', 'needs_review': 'Résultat de reprise à vérifier',
    'needs_path': 'Dossier indisponible', 'cancelled': 'Reprise annulée · session modifiée',
    'blocked_trust': 'Lancement refusé · dossier non reconnu par le CLI',
    'blocked_auth': 'Connexion du fournisseur requise', 'quota_blocked': 'Quota refusé au lancement',
    'blocked_owner': 'Session détenue par un autre moteur',
    'blocked_parent': 'Sous-agent · reprise à confier au parent',
    'blocked_session': 'Session ou dossier non confirmé',
    'blocked_permission': 'Approbation humaine nécessaire',
    'needs_input': 'Réponse humaine nécessaire',
    'stopped': 'Tour interrompu',
    'engine_unavailable': 'Moteur indisponible ou non configuré',
    'queued_parent': 'Demande transmise au parent · reprise enfant non confirmée',
    'delegated_parent': 'Parent sollicité · reprise enfant non confirmée',
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
    state, reason = failure(text)
    return (state, reason) if state != 'failed' else ('needs_review', 'Le moteur a échoué ; consulter le journal local pour le diagnostic.')

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
        if row['status'] in ('needs_review', 'failed'):
            try:
                with (Path(state_dir) / (row['id'] + '.log')).open('rb') as log:
                    text = log.read(65536).decode('utf-8', errors='replace')
                state, reason = classify(text)
                if state != 'needs_review' or not row.get('reason'):
                    row['status'], row['reason'] = state, reason
            except OSError:
                row['reason'] = 'Aucun diagnostic local disponible.'
        elif row['status'] == 'running' and not state:
            row['status'] = 'launching'
            row['reason'] = 'Ancienne exécution sans confirmation de démarrage du tour.'
        row['label'] = LABELS.get(row['status'], 'État inconnu')
        if row['status'] == 'ready' and row['due'] <= time.time():
            row['label'] = 'Échéance atteinte · lancement en attente'
        result.append(row)
    return result

def execute(db, task, argv, env, log, timeout=7200, engine='codex-cli'):
    ident = task['id']
    save(db, ident, 'launching', started_at=time.time())
    decoder = JsonLines()
    diagnostic = bytearray()
    evidence = ExecutionEvidence(engine, task['session'])
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
                        state = evidence.feed(event)
                        if state:
                            save(db, ident, state, last_event_at=time.time())
    finally:
        selector.close()
        terminate(process)
    state, reason = evidence.finish(process.returncode, timed_out)
    if state in ('failed', 'needs_review') and not timed_out:
        classified, diagnostic_reason = classify(diagnostic.decode('utf-8', errors='replace'))
        if classified != 'needs_review':
            state, reason = classified, diagnostic_reason
        elif process.returncode == 0:
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
