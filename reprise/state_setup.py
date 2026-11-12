"""State-free onboarding API. Plans are previews, never reservations or permission to overwrite."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

from app_paths import state_location
import state_install as install


def _path(value):
    return Path(value).expanduser().absolute()


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _receipt(target):
    try:
        data = json.loads((target / install.MARKER).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _source_hash(source):
    return _hash(install._inventory(source))


def _target_check(target):
    if target.exists() or target.is_symlink():
        raise ValueError(f'Destination existante : {target}. Aucun remplacement ; choisir un dossier source ou réconcilier cet état avant de réessayer.')
    if any(parent.suffix == '.app' for parent in target.resolve().parents):
        raise ValueError('L’état doit être conservé hors du bundle .app.')


def _source_check(source, target):
    if source.is_symlink() or not source.is_dir():
        raise ValueError(f'Source non reconnue : {source}. Sélectionner un dossier d’état réel.')
    if (source / install.MARKER).exists() and not install.initialized(source):
        raise ValueError('Manifeste source incompatible ou incomplet ; conserver la source et vérifier sa version.')
    if source.resolve() == target.resolve() or source.resolve() in target.resolve().parents or target.resolve() in source.resolve().parents:
        raise ValueError('Source et destination doivent être disjointes.')
    return _source_hash(source)


def _companions(source):
    return [p for p in (source / 'quick-state', source.parent / 'quick-state')
            if p != source and p.exists()]


def _recognized(path, database):
    return not path.is_symlink() and (install.initialized(path) or
        (not (path / install.MARKER).exists() and (path / database).is_file()))


def _completed_before(target):
    for entry in target.parent.glob('.reprise-setup-*.json'):
        try:
            data = json.loads(entry.read_text())
            if _path(data['plan']['target']) == target and data.get('status') == 'completed':
                return True
        except (OSError, ValueError, KeyError, TypeError):
            raise ValueError('Journal existant illisible ; réconciliation requise.')
    return False


def plan(operation, target, quick_target, source=None):
    if operation not in ('initialize', 'import'):
        raise ValueError('Opération inconnue.')
    target, quick = _path(target), _path(quick_target)
    if target.resolve() == quick.resolve() or quick.resolve() in target.resolve().parents:
        raise ValueError('Les destinations principale et Quick Apply doivent être distinctes.')
    preserve_main = operation == 'initialize' and _recognized(target, 'tasks.sqlite')
    if preserve_main and _completed_before(target):
        raise ValueError('État déjà préparé par un plan antérieur ; ne pas remplacer un état Quick Apply manquant par un registre neuf. Réconciliation requise.')
    if not preserve_main:
        _target_check(target)
    steps = []
    if operation == 'import':
        if source is None:
            raise ValueError('Sélectionner le dossier d’état source.')
        source = _path(source)
        source_hash = _source_check(source, target)
        if not (source / 'tasks.sqlite').is_file():
            raise ValueError('Le dossier principal doit contenir tasks.sqlite. Les états UX isolés ne sont pas un état principal ; leur import CLI reste distinct.')
        steps.append({'role': 'main', 'operation': 'import', 'source': str(source),
                      'target': str(target), 'source_hash': source_hash})
        candidates = _companions(source)
        if len(candidates) > 1:
            raise ValueError('Deux états Quick Apply détectés : réconcilier leur provenance avant import ; aucun choix automatique.')
        if candidates:
            quick_source = candidates[0]
            quick_hash = _source_check(quick_source, quick)
            if not (quick_source / 'quick.sqlite').is_file():
                raise ValueError('État Quick Apply présent mais non reconnu (quick.sqlite absent) ; vérifier ce dossier avant import.')
            # A nested source remains nested when the selected state override uses that layout.
            if quick_source == source / 'quick-state' and quick == target / 'quick-state':
                pass
            else:
                _target_check(quick)
                steps.append({'role': 'quick', 'operation': 'import', 'source': str(quick_source),
                              'target': str(quick), 'source_hash': quick_hash})
        else:
            _target_check(quick)
            steps.append({'role': 'quick', 'operation': 'initialize', 'target': str(quick)})
    else:
        _target_check(quick)
        steps = [{'role': role, 'operation': 'initialize', 'target': str(path)}
                 for role, path in (('main', target), ('quick', quick)) if role != 'main' or not preserve_main]
    for step in steps:
        if step['operation'] == 'import':
            step['backup_pattern'] = step['target'] + '.backup-UUID'
    result = {'format': 1, 'operation': operation, 'target': str(target), 'quick_target': str(quick), 'steps': steps}
    if preserve_main:
        result['preserved_main_hash'] = _source_hash(target)
    if operation == 'import':
        result['source_layout'] = {str(p): _source_hash(p) for p in _companions(source)}
    result['fingerprint'] = _hash(result)
    return result


def _validate(plan_data):
    if not isinstance(plan_data, dict) or plan_data.get('format') != 1:
        raise ValueError('Plan incompatible ; refaire la prévisualisation.')
    unsigned = {key: value for key, value in plan_data.items() if key != 'fingerprint'}
    if _hash(unsigned) != plan_data.get('fingerprint'):
        raise ValueError('Plan modifié ; refaire la prévisualisation.')
    if plan_data.get('operation') not in ('initialize', 'import') or any(not isinstance(plan_data.get(key), str) or not plan_data[key] for key in ('target', 'quick_target')):
        raise ValueError('Chemins ou opération du plan invalides.')
    steps = plan_data.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= 2:
        raise ValueError('Étapes du plan invalides.')
    roles = set()
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError('Étape invalide.')
        if step.get('role') not in ('main', 'quick') or step.get('operation') not in ('initialize', 'import'):
            raise ValueError('Étape inconnue.')
        role = step['role']
        expected_target = plan_data['target'] if role == 'main' else plan_data['quick_target']
        if role in roles or step.get('target') != expected_target:
            raise ValueError('Destination ou rôle du plan incohérent.')
        roles.add(role)
        if step['operation'] == 'import' and (not isinstance(step.get('source'), str) or not isinstance(step.get('source_hash'), str)):
            raise ValueError('Source du plan invalide.')
    return plan_data['fingerprint']


def _journal_path(plan_data):
    return _path(plan_data['target']).parent / ('.reprise-setup-' + _validate(plan_data) + '.json')


def _journal_write(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.setup-', delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
        install._sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _backups(target, quick):
    return sorted(str(p) for path in (target, quick) for p in path.parent.glob(path.name + '.backup-*') if p.is_dir())


def status(root):
    target, quick = (state_location(root, name).absolute() for name in ('state', 'quick-state'))
    result = {'target': str(target), 'quick_target': str(quick), 'backups': _backups(target, quick)}
    # A partially published main state never unlocks supervision while another step is pending.
    if target.parent.exists():
        for path in sorted(target.parent.glob('.reprise-setup-*.json')):
            try:
                journal = json.loads(path.read_text())
                pending = journal['plan']
                if _path(pending['target']) != target or journal.get('status') == 'completed':
                    continue
                _validate(pending)
                return result | {'status': 'pending', 'message': 'Préparation interrompue ou incomplète. Reprendre le plan conservé sans écraser les étapes déjà publiées.', 'pending_plan': pending}
            except (OSError, ValueError, KeyError, TypeError):
                return result | {'status': 'conflict', 'message': f'Journal de préparation illisible : {path}. Conserver les états et sauvegardes ; réconciliation requise.'}
    if target.is_symlink():
        return result | {'status': 'conflict', 'message': 'La destination est un lien symbolique ; aucune modification automatique.'}
    if _recognized(target, 'tasks.sqlite'):
        if not _recognized(quick, 'quick.sqlite'):
            if quick.exists() or quick.is_symlink() or _completed_before(target):
                return result | {'status': 'conflict', 'message': 'État Quick Apply absent après préparation, incompatible ou symbolique. Conserver les données et sauvegardes ; réconciliation requise, aucun registre neuf proposé.'}
            return result | {'status': 'needs_setup', 'preserve_main': True, 'message': 'État principal conservé. Choisir Créer un nouvel état pour préparer uniquement le dossier Quick Apply manquant ; aucun registre principal ne sera recréé.'}
        return result | {'status': 'ready', 'message': 'États reconnus. Aucun service activé par cette préparation.'}
    if (target / install.MARKER).exists():
        return result | {'status': 'conflict', 'message': 'Manifeste incompatible ou incomplet. Conserver le dossier et vérifier sa version.'}
    if target.exists():
        return result | {'status': 'conflict', 'message': 'Destination existante non reconnue. Aucun remplacement ; déplacer volontairement ce dossier hors de la destination après vérification, puis réessayer.'}
    return result | {'status': 'needs_setup', 'message': 'Choisir explicitement un état neuf ou importer un état historique.'}


def apply(plan_data, confirm_new=False, source_stopped=False):
    fingerprint = _validate(plan_data)
    if plan_data['operation'] == 'initialize' and confirm_new is not True:
        raise ValueError('Confirmer explicitement la création d’un état neuf.')
    if any(step['operation'] == 'import' for step in plan_data['steps']) and source_stopped is not True:
        raise ValueError('Confirmer l’arrêt de tous les écrivains des états sources ; Reprise ne les arrête pas.')
    journal_path = _journal_path(plan_data)
    # Cooperative lock belongs to setup, never to a running harness or inference.
    journal_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (journal_path.parent / '.reprise-setup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Une préparation est déjà en cours. Attendre puis relire son état.')
        pending = []
        for step in plan_data['steps']:
            destination = _path(step['target'])
            identity = fingerprint + ':' + step['role']
            if install.initialized(destination) and _receipt(destination).get('setup_id') == identity:
                continue
            _target_check(destination)
            if step['operation'] == 'import' and _source_check(_path(step['source']), destination) != step['source_hash']:
                raise ValueError('Source modifiée depuis la prévisualisation. Aucun écrasement ; refaire la prévisualisation si aucune étape n’a été publiée.')
            pending.append((step, identity))
        if pending and 'preserved_main_hash' in plan_data and _source_hash(_path(plan_data['target'])) != plan_data['preserved_main_hash']:
            raise ValueError('L’état principal conservé a changé depuis la prévisualisation ; refaire le plan avant écriture.')
        if pending and plan_data['operation'] == 'import':
            main_source = _path(plan_data['steps'][0]['source'])
            current_layout = {str(p): _source_hash(p) for p in _companions(main_source)}
            if current_layout != plan_data.get('source_layout'):
                raise ValueError('Les états Quick Apply associés ont changé depuis la prévisualisation ; réconciliation requise avant reprise.')
        journal = {'format': 1, 'status': 'pending', 'plan': plan_data}
        _journal_write(journal_path, journal)
        receipts = []
        for step, identity in pending:
            if step['operation'] == 'initialize':
                receipt = install.initialize(step['target'], setup_id=identity)
            else:
                receipt = install.import_state(step['source'], step['target'], source_stopped=True, setup_id=identity)
            receipts.append(receipt)
        journal['status'] = 'completed'
        _journal_write(journal_path, journal)
    return {'status': 'completed', 'message': 'Préparation terminée. Aucun service démarré par cette opération ; Continuer ouvre la supervision.',
            'receipts': receipts, 'target': plan_data['target'],
            'backups': _backups(_path(plan_data['target']), _path(plan_data['quick_target']))}


def main(root, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('state-status')
    preview = commands.add_parser('state-plan')
    preview.add_argument('--operation', choices=('initialize', 'import'), required=True)
    preview.add_argument('--target', required=True)
    preview.add_argument('--quick-target', required=True)
    preview.add_argument('--source')
    commands.add_parser('state-apply')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == 'state-status':
            result = status(root)
        elif args.action == 'state-plan':
            result = {'status': 'planned', 'message': 'Prévisualisation sans écriture ; compatibilité SQLite vérifiée pendant la copie.',
                      'plan': plan(args.operation, args.target, args.quick_target, args.source)}
        else:
            payload = json.loads(sys.stdin.read(1024 * 1024))
            result = apply(payload['plan'], payload.get('confirm_new', False), payload.get('source_stopped', False))
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as error:
        result = {'status': 'refused', 'message': str(error), 'recovery': 'Aucune suppression automatique. Relire l’état ; reprendre le plan conservé ou vérifier les chemins et sauvegardes.'}
        try:
            result['backups'] = status(root)['backups']
        except OSError:
            result['backups'] = []
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
