"""Explicit local endpoint configuration; credentials remain in their original files."""
from contextlib import closing
from dataclasses import asdict
import json
import os
import sqlite3
from pathlib import Path
import sys
from app_paths import state_location
from state_install import initialized
from harness_integration import atomic, file_hash
from ironclaw_ssh import Endpoint, ProbeError, probe
from ironclaw_tasks import Tasks, TaskError


def config_path(root):
    state = state_location(root).absolute()
    path = state / 'ironclaw-endpoint.json'
    if any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('Chemin symbolique refusé.')
    return path


def endpoint(root):
    path = config_path(root)
    if not file_hash(path):
        raise ValueError('Configurer explicitement la connexion IronClaw.')
    result = Endpoint(**json.loads(path.read_text()))
    result.validate()
    return result


def configure(root, value):
    path = config_path(root)
    if not initialized(path.parent):
        raise ValueError('Préparer le registre avant de configurer IronClaw.')
    connection = Endpoint(**value)
    connection.validate()
    atomic(path, json.dumps(asdict(connection), ensure_ascii=False, indent=2))
    path.chmod(0o600)
    return {'status': 'configured', 'message': 'Paramètres enregistrés. Aucune clé ni aucun jeton copié ; connexion non encore vérifiée.'}


def task_operation(root, command, value):
    if not isinstance(value, dict) or value.keys() - {'request_key', 'parent_request_key', 'content', 'allow_submit', 'acknowledge', 'reason'}:
        raise ValueError('Champs de requête inconnus.')
    if command != 'ironclaw-requests' and (not isinstance(value.get('request_key'), str) or not value['request_key'].strip() or len(value['request_key']) > 128):
        raise ValueError('Clé de requête persistée requise.')
    if command == 'ironclaw-fail' and (not isinstance(value.get('reason'), str) or not value['reason'].strip() or len(value['reason']) > 512):
        raise ValueError('Motif d’échec explicite requis.')
    if command != 'ironclaw-followup' and 'parent_request_key' in value:
        raise ValueError('Parent réservé à une continuation explicite.')
    for key in ('allow_submit', 'acknowledge'):
        if key in value and type(value[key]) is not bool:
            raise ValueError('Autorisation booléenne explicite requise.')
    if command in ('ironclaw-submit', 'ironclaw-followup') and value.get('allow_submit') is not True:
        raise ValueError('La soumission exige une autorisation explicite et peut consommer du quota distant.')
    if command == 'ironclaw-requests' and value:
        raise ValueError('La liste locale ne prend pas de paramètres.')
    if command not in ('ironclaw-submit', 'ironclaw-followup') and ('content' in value or value.get('allow_submit')):
        raise ValueError('La lecture ne soumet aucune tâche.')
    path = config_path(root).parent
    if not initialized(path):
        raise ValueError('Registre préparé requis.')
    dbpath = path / 'tasks.sqlite'
    file_hash(dbpath)
    with closing(sqlite3.connect(dbpath)) as db:
        tasks = Tasks(db, None if command in ('ironclaw-result', 'ironclaw-requests', 'ironclaw-fail') else endpoint(root))
        if command == 'ironclaw-requests':
            return {'requests': tasks.list_requests(), 'limit': 100}
        if command in ('ironclaw-submit', 'ironclaw-followup'):
            if value.get('acknowledge'):
                raise ValueError('Réclamation distincte de la soumission.')
            if command == 'ironclaw-followup':
                return tasks.followup(value['request_key'], value.get('parent_request_key'), value.get('content'), allow_submit=value.get('allow_submit', False))
            return tasks.submit(value['request_key'], value.get('content'), allow_submit=value.get('allow_submit', False))
        if command in ('ironclaw-poll', 'ironclaw-reconcile'):
            if value.get('acknowledge'):
                raise ValueError('Réclamation distincte du suivi.')
            return tasks.reconcile(value['request_key']) if command == 'ironclaw-reconcile' else tasks.poll(value['request_key'])
        if command == 'ironclaw-fail':
            if value.get('acknowledge'):
                raise ValueError('Réclamation distincte de la décision opérateur.')
            return tasks.fail(value['request_key'], value['reason'])
        return tasks.get(value['request_key'], acknowledge=value.get('acknowledge', False))


def main(root, argv):
    os.umask(0o077)
    try:
        if len(argv) != 1:
            raise ValueError('Une commande IronClaw explicite est requise.')
        if argv[0] == 'ironclaw-configure':
            result = configure(root, json.load(sys.stdin))
        elif argv[0] == 'ironclaw-inspect':
            path = config_path(root)
            value = asdict(endpoint(root)) if file_hash(path) else None
            result = {'status': 'inspected', 'endpoint': value, 'message': 'Inspection locale ; aucune connexion ni inférence.'}
        elif argv[0] in ('ironclaw-submit', 'ironclaw-poll', 'ironclaw-result', 'ironclaw-requests', 'ironclaw-reconcile', 'ironclaw-followup', 'ironclaw-fail'):
            raw = sys.stdin.read(65537)
            if len(raw) > 65536:
                raise ValueError('Requête trop volumineuse.')
            result = task_operation(root, argv[0], {} if not raw.strip() and argv[0] == 'ironclaw-requests' else json.loads(raw))
        elif argv[0] == 'ironclaw-probe':
            result = probe(endpoint(root))
            result.update(status='verified' if result['authenticated'] else 'unavailable',
                          message='Accès SSH et API authentifiée vérifiés sans inférence.' if result['authenticated'] else 'API non authentifiée ; aucune tâche soumise.')
        else:
            raise ValueError('Commande IronClaw inconnue.')
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, sqlite3.Error, ProbeError, TaskError):
        message = 'Opération IronClaw non confirmée. Consultez le reçu durable avec la même request_key ; ne renvoyez pas une tâche incertaine.' if argv and argv[0] in ('ironclaw-submit', 'ironclaw-poll', 'ironclaw-result', 'ironclaw-requests', 'ironclaw-reconcile', 'ironclaw-followup', 'ironclaw-fail') else 'Connexion IronClaw non confirmée. Vérifiez les paramètres, les permissions des fichiers et l’hôte SSH approuvé. Aucune tâche soumise.'
        print(json.dumps({'status': 'error', 'message': message}, ensure_ascii=False))
        return 1
