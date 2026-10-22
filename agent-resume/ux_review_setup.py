"""Bridge for `resume ux-review-config`, dispatched by the resume header
before the heavy imports: a --check must never initialize the SQLite state
(database() mkdir + connect used to run before the installer). Mirrors the
state_setup refusal contract: expected installer refusals print a JSON
{'status': 'refused', 'message', 'recovery'} and exit 1, never a traceback.
"""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path


def _installer(root):
    loader = importlib.machinery.SourceFileLoader(
        'install_ux_review', str(Path(root) / 'install-ux-review.py'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def main(root, argv=None):
    parser = argparse.ArgumentParser(prog='resume ux-review-config', description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    ux = commands.add_parser('ux-review-config', help='pousser le serveur MCP de revue UX dans la config Claude Desktop')
    ux.add_argument('--state', help='dossier d’état durable (défaut : Application Support/Reprise/ux-state sous le domicile)')
    ux.add_argument('--attachment', help='fichier Swift à joindre à la tâche')
    ux.add_argument('--task', help='texte de la tâche de revue')
    ux.add_argument('--force', action='store_true', help='remplacer une entrée existante différente')
    ux.add_argument('--relaunch', action='store_true', help='quitter puis rouvrir Claude Desktop (jamais automatique)')
    ux.add_argument('--check', action='store_true', help='n’écrire rien, afficher seulement l’état attendu')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        installer = _installer(root)
        result = installer.install(state=args.state, attachment=args.attachment,
                                   task=args.task, force=args.force, dry_run=args.check)
        if args.relaunch and not args.check and result.get('needs_relaunch'):
            result['relaunch'] = installer.relaunch()
    except (OSError, ValueError, KeyError, TypeError) as error:
        result = {'status': 'refused', 'message': str(error),
                  'recovery': 'Aucune écriture effectuée. Relire la config Claude Desktop ; corriger la cause (JSON invalide, « mcpServers » non-objet, chemin hors du domicile, entrée différente à remplacer avec --force) puis relancer.'}
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0
