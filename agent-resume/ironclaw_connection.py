"""Explicit local endpoint configuration; credentials remain in their original files."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from app_paths import state_location
from state_install import initialized
from harness_integration import atomic, file_hash
from ironclaw_ssh import Endpoint, ProbeError, probe


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
        elif argv[0] == 'ironclaw-probe':
            result = probe(endpoint(root))
            result.update(status='verified' if result['authenticated'] else 'unavailable',
                          message='Accès SSH et API authentifiée vérifiés sans inférence.' if result['authenticated'] else 'API non authentifiée ; aucune tâche soumise.')
        else:
            raise ValueError('Commande IronClaw inconnue.')
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, ProbeError):
        print(json.dumps({'status': 'error', 'message': 'Connexion IronClaw non confirmée. Vérifiez les paramètres, les permissions des fichiers et l’hôte SSH approuvé. Aucune tâche soumise.'}, ensure_ascii=False))
        return 1
