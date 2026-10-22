#!/usr/bin/env python3
"""Push the Claude Desktop MCP server for the Reprise UX/UI review round trip.

Only the `reprise_ux_review` entry is added or refreshed in
`claude_desktop_config.json`; every other key (other mcpServers entries,
coworkUserFilesPath, preferences) is preserved through a merge re-serialized
in the exact format Claude Desktop writes (json.dumps(indent=2,
ensure_ascii=False), no trailing newline — verified against a live file). The
previous file is backed up next to it before an atomic replace, and an
existing `reprise_ux_review` entry that differs is refused unless `--force`.

Claude Desktop reads this file at launch and does not hot-reload it, so the
script never starts or quits Claude silently. It reports whether Claude is
running (`needs_relaunch`) and, with `--relaunch`, can apply the explicit
graceful quit + reopen and verify the effective load through Claude's
per-server log (`~/Library/Logs/Claude/mcp-server-reprise_ux_review.log`),
whose recent mtime proves the server was actually spawned.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

NAME = 'reprise_ux_review'
CONFIG_REL = Path('Library/Application Support/Claude/claude_desktop_config.json')
LOG_REL = Path('Library/Logs/Claude/mcp-server-reprise_ux_review.log')
SOURCE = Path(__file__).resolve().parent
SERVER = SOURCE / 'ux_review_probe.py'
DEFAULT_STATE = SOURCE / 'ux-state'
RELAUNCH_SIGNAL_TIMEOUT = 20
RELAUNCH_QUIT_DELAY = 5


def config_path(home):
    return Path(home) / CONFIG_REL


def verification_log(home):
    return Path(home) / LOG_REL


def _read_dict(path):
    """Return the parsed config, or {} when absent. Never clobber invalid JSON."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding='utf-8')
        data = json.loads(raw)
    except (OSError, ValueError) as error:
        raise ValueError(f'Config Claude Desktop illisible ou JSON invalide : {path} ({error})') from error
    if not isinstance(data, dict):
        raise ValueError(f'Config Claude Desktop non-objet : {path}')
    return data


def server_args(*, state, attachment=None, task=None):
    """The stdio argv Claude Desktop must run for the review server."""
    args = [str(SERVER), '--state', str(Path(state).expanduser())]
    if attachment:
        args += ['--attachment', str(Path(attachment).expanduser())]
    if task:
        args += ['--task', task]
    return args


def _validate(home, state, attachment):
    home = Path(home).resolve()
    for path in (Path(state), Path(attachment) if attachment else None):
        if path is None:
            continue
        if not path.expanduser().resolve().is_relative_to(home):
            raise ValueError('Les chemins du serveur MCP doivent rester sous le domicile courant')
    if not SERVER.is_file():
        raise ValueError(f'Serveur MCP absent : {SERVER}')
    if attachment and not Path(attachment).expanduser().is_file():
        raise ValueError(f'Pièce jointe absente : {attachment}')


def install(*, home=None, state=None, attachment=None, task=None, force=False,
            app_running=None, dry_run=False):
    """Merge `reprise_ux_review` into the Claude Desktop config.

    With `dry_run=True`, computes and reports the planned change without any
    write (no backup, no config edit). Returns a dict with `changed`, paths
    used, backup, and whether a relaunch of Claude Desktop is required for the
    entry to be loaded.
    """
    home = Path.home() if home is None else Path(home)
    state = DEFAULT_STATE if state is None else Path(state)
    _validate(home, state, attachment)
    target = config_path(home)
    data = _read_dict(target)
    requested = {'command': sys.executable, 'args': server_args(state=state, attachment=attachment, task=task)}
    entry = data.setdefault('mcpServers', {}).setdefault(NAME, None)
    if entry == requested:
        running = app_running if app_running is not None else claude_running()
        return {
            'changed': False, 'config': str(target), 'name': NAME, 'state': str(state),
            'app_running': running, 'needs_relaunch': bool(running),
            'dry_run': dry_run, 'backup': None,
            'loaded': verification_log(home).exists(),
            'verification_log': str(verification_log(home)),
            'next_step': 'Relancer Claude Desktop pour que le serveur soit chargé.' if running
                         else 'Aucune action : Claude Desktop chargera le serveur à la prochaine ouverture.',
        }
    if entry is not None and not force:
        raise FileExistsError(
            f'Une entrée {NAME} existe déjà avec une configuration différente ; '
            f'utiliser --force pour la remplacer (backup conservé).')
    backup = None
    if target.exists():
        stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = target.with_name(target.name + f'.bak-{stamp}')
    data['mcpServers'][NAME] = requested
    running = app_running if app_running is not None else claude_running()
    planned = {
        'changed': True, 'config': str(target), 'name': NAME, 'state': str(state),
        'backup': str(backup) if backup else None, 'dry_run': dry_run,
        'app_running': running, 'needs_relaunch': bool(running),
        'loaded': verification_log(home).exists(),
        'verification_log': str(verification_log(home)),
        'command': sys.executable, 'args': server_args(state=state, attachment=attachment, task=task),
        'next_step': 'Relancer Claude Desktop (ou exécuter --relaunch) pour que le serveur soit chargé.'
                     if running else
                     'Aucune action : Claude Desktop chargera le serveur à la prochaine ouverture.',
    }
    if dry_run:
        return planned
    if backup is not None:
        backup.write_text(target.read_text(encoding='utf-8'), encoding='utf-8')
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.reprise-tmp')
    temporary.write_text(payload, encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, target)
    return planned


def claude_running():
    """Detect a running Claude Desktop by process name (pgrep), like desktop_mode."""
    try:
        return subprocess.run(['/usr/bin/pgrep', '-x', 'Claude'], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=2).returncode != 1
    except (OSError, subprocess.TimeoutExpired):
        return False


def relaunch(home=None, wait=True):
    """Graceful quit + reopen of Claude Desktop, and verify the server log appears.

    Explicitly operator-invoked only (--relaunch); never automatic. Returns a
    dict describing what was done and whether the review server was seen
    loading afterwards.
    """
    home = Path.home() if home is None else Path(home)
    log = verification_log(home)
    before = log.stat().st_mtime if log.exists() else None
    actions = []
    if claude_running():
        try:
            subprocess.run(['/usr/bin/osascript', '-e', 'tell application "Claude" to quit'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
            actions.append('quit')
            if wait:
                time.sleep(RELAUNCH_QUIT_DELAY)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return {'relaunch_performed': False, 'actions': actions,
                    'error': 'quitter Claude Desktop a échoué ; relance manuelle requise'}
    try:
        subprocess.run(['/usr/bin/open', '-a', 'Claude'], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)
        actions.append('open')
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {'relaunch_performed': False, 'actions': actions,
                'error': 'réouverture de Claude Desktop a échoué'}
    loaded = False
    if wait:
        deadline = time.time() + RELAUNCH_SIGNAL_TIMEOUT
        while time.time() < deadline:
            if log.exists() and (before is None or log.stat().st_mtime > before):
                loaded = True
                break
            time.sleep(1)
    return {'relaunch_performed': True, 'actions': actions, 'loaded': loaded,
            'verification_log': str(log)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', default=str(DEFAULT_STATE),
                        help='dossier d’état durable du serveur (défaut : agent-resume/ux-state)')
    parser.add_argument('--attachment', help='fichier Swift à joindre à la tâche de revue')
    parser.add_argument('--task', help='texte de la tâche (défaut : celui du probe)')
    parser.add_argument('--force', action='store_true',
                        help='remplacer une entrée reprise_ux_review existante différente')
    parser.add_argument('--check', action='store_true',
                        help='ne rien écrire : afficher seulement l’état/la modification planifiée')
    parser.add_argument('--relaunch', action='store_true',
                        help='après écriture, quitter puis rouvrir Claude Desktop (explicite, jamais auto)')
    parser.add_argument('--home', default=str(Path.home()),
                        help='racine de la config (tests uniquement)')
    args = parser.parse_args()
    try:
        result = install(home=args.home, state=args.state, attachment=args.attachment,
                         task=args.task, force=args.force, dry_run=args.check)
        if args.relaunch and not args.check and result.get('needs_relaunch'):
            result['relaunch'] = relaunch(home=args.home)
    except (ValueError, FileExistsError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
