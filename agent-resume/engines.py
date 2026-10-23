"""Explicit resume adapters; no cross-provider fallback or daemon startup."""
import json
import os
from pathlib import Path
import re
import shutil
import stat
import uuid

DSH = '/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh'
SOCKET = Path.home() / '.codex/app-server-control/app-server-control.sock'
ENGINES = {
    'codex': ('Codex · sélection du transport', 'Codex'),
    'codex-cli': ('Codex CLI', 'Codex'),
    'codex-server': ('Codex · serveur partagé', 'Codex'),
    'codex-parent': ('Codex · délégation au parent', 'Codex'),
    'harness-cli': ('DeepSeek Harness CLI', 'Harness'),
    'claude-code': ('Claude Code CLI', 'Claude Code'),
    'claude-desktop': ('Claude Desktop · Quick Entry', 'Claude Desktop'),
}

class Unavailable(ValueError):
    def __init__(self, state, reason):
        super().__init__(reason)
        self.state, self.reason = state, reason


def setup(db):
    db.execute('CREATE TABLE IF NOT EXISTS task_engines (task_id TEXT PRIMARY KEY, engine TEXT NOT NULL, options TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS engine_settings (engine TEXT PRIMARY KEY, options TEXT NOT NULL)')


def validate_session(engine, session):
    if engine not in ENGINES:
        raise ValueError('Moteur inconnu')
    if engine == 'harness-cli':
        if not isinstance(session, str) or not re.fullmatch(r'session-[A-Za-z0-9_-]{1,120}', session):
            raise ValueError('Identifiant Harness session-… requis')
        return session
    return str(uuid.UUID(session))


def options(engine, values):
    if engine not in ENGINES or not isinstance(values, dict):
        raise ValueError('Configuration de moteur invalide')
    permitted = {'socket', 'parent'} if engine == 'codex-parent' else {'socket'} if engine in ('codex', 'codex-server', 'claude-desktop') else {'profile'} if engine == 'harness-cli' else set()
    if values.keys() - permitted:
        raise ValueError('Option de moteur non prise en charge')
    if 'profile' in values and (not isinstance(values['profile'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', values['profile'])):
        raise ValueError('Nom de profil invalide')
    if 'socket' in values and (not isinstance(values['socket'], str) or not Path(values['socket']).is_absolute()):
        raise ValueError('Chemin de socket absolu requis')
    if 'parent' in values:
        values = values | {'parent': str(uuid.UUID(values['parent']))}
    return values


def assign(db, ident, engine, values=None):
    values = options(engine, values or {})
    db.execute('INSERT OR REPLACE INTO task_engines VALUES (?,?,?)', (ident, engine, json.dumps(values)))


def route(db, ident):
    row = db.execute('SELECT engine,options FROM task_engines WHERE task_id=?', (ident,)).fetchone()
    engine, values = (row[0], json.loads(row[1])) if row else ('codex', {})
    configured = db.execute('SELECT options FROM engine_settings WHERE engine=?', (engine,)).fetchone()
    return engine, options(engine, (json.loads(configured[0]) if configured else {}) | values)


def executable(engine):
    if engine.startswith('codex'):
        candidates = [shutil.which('codex'), '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
                      '/Applications/Codex.app/Contents/Resources/codex', '/Applications/ChatGPT.app/Contents/Resources/codex']
    elif engine == 'harness-cli':
        candidates = [shutil.which('dsh'), DSH]
    else:
        candidates = [shutil.which('claude'), str(Path.home()/'.local/bin/claude')]
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file() and os.access(p, os.X_OK)), None)


CODEX_APPS = (Path('/Applications/ChatGPT.app'), Path('/Applications/Codex.app'))


def bundle_version(app):
    """Distingue bundle absent et métadonnées illisibles."""
    import plistlib
    app = Path(app)
    result = {'path': str(app), 'plist_path': str(app / 'Contents/Info.plist'),
              'desktop_version': None, 'desktop_build': None, 'status': 'absent'}
    try:
        app.stat()
    except FileNotFoundError:
        return result
    except OSError:
        return dict(result, status='unreadable')
    try:
        data = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        version, build = data.get('CFBundleShortVersionString'), data.get('CFBundleVersion')
        result.update(desktop_version=version if isinstance(version, str) else None,
                      desktop_build=str(build) if type(build) in (str, int) else None)
        result['status'] = 'available' if result['desktop_version'] or result['desktop_build'] else 'unreadable'
    except (OSError, ValueError, AttributeError):
        result['status'] = 'unreadable'
    return result


def codex_versions():
    """Publie les versions et les chemins effectivement sélectionnés."""
    import subprocess
    program = executable('codex-cli')
    apps = [bundle_version(app) for app in CODEX_APPS]
    selected = next((a for a in apps if program and Path(program).is_relative_to(a['path'])), None)
    selected = selected or next((a for a in apps if a['status'] == 'available'), None)
    selected = selected or next((a for a in apps if a['status'] != 'absent'), apps[0])
    result = {'desktop_version': selected['desktop_version'], 'desktop_build': selected['desktop_build'],
              'desktop_path': selected['path'], 'desktop_plist': selected['plist_path'],
              'desktop_status': selected['status'], 'cli_path': program, 'cli_version': None,
              'cli_status': 'absent' if program is None else 'unreadable', 'apps': apps}
    if program:
        try:
            reply = subprocess.run([program, '--version'], capture_output=True, text=True, timeout=3)
            if reply.returncode == 0 and reply.stdout.strip():
                result.update(cli_version=reply.stdout.strip(), cli_status='available')
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            pass
    return result


def socket_path(values):
    path = Path(values.get('socket', SOCKET))
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            # Codex publishes a symlink to its private runtime socket.
            for parent in (path.parent, path.resolve(strict=True).parent):
                directory = parent.stat()
                if directory.st_uid != os.getuid() or directory.st_mode & 0o022:
                    raise Unavailable('engine_unavailable', 'Répertoire du socket non privé.')
            if info.st_uid != os.getuid():
                raise Unavailable('engine_unavailable', 'Lien de socket non détenu par cet utilisateur.')
            path = path.resolve(strict=True)
            info = path.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise Unavailable('engine_unavailable', 'Socket non privé ou non détenu par cet utilisateur.')
    except OSError as error:
        raise Unavailable('engine_unavailable', 'Serveur partagé inaccessible ; aucun serveur de remplacement lancé.') from error
    return path


def select(engine, values):
    if engine == 'codex':
        # An explicitly configured socket must never silently fall back to a new writer.
        try:
            configured = 'socket' in values or SOCKET.exists() or SOCKET.is_symlink()
        except OSError as error:
            raise Unavailable('engine_unavailable', 'État du socket inaccessible ; aucune substitution CLI.') from error
        if configured:
            socket_path(values)
            return 'codex-server'
        return 'codex-cli'
    if engine in ('codex-server', 'codex-parent', 'claude-desktop'):
        socket_path(values)
        if engine == 'codex-parent' and not values.get('parent'):
            raise Unavailable('engine_unavailable', 'Identifiant de la session parente requis.')
    return engine


def command(engine, task, program, values=None, automatic=False):
    values = values or {}
    validate_session(engine, task['session'])
    if automatic and not engine.startswith('codex'):
        raise Unavailable('engine_unavailable', 'La disponibilité du quota de ce moteur n’est pas vérifiée automatiquement.')
    if engine == 'codex-cli':
        pins = ['-c', 'model_provider="openai"', '-c', 'forced_login_method="chatgpt"'] if automatic else []
        return [program, 'exec', *pins, '--json', '--sandbox', 'workspace-write', 'resume', task['session'], '-']
    if engine == 'harness-cli':
        if not values.get('profile'):
            raise Unavailable('engine_unavailable', 'Choisir explicitement un profil headless configuré ; facturation et modèle suivent ce profil.')
        profile_path = Path.home() / '.dsh/profiles' / values['profile'] / 'package.json'
        try:
            bundles = json.loads(profile_path.read_text())['dsh']['profile']['bundles']
        except (OSError, ValueError, KeyError, TypeError):
            raise Unavailable('engine_unavailable', 'Profil Harness existant non confirmé ; aucune initialisation automatique.')
        if '@deepseek-ai/dsh-headless' not in bundles:
            raise Unavailable('engine_unavailable', 'Profil Harness headless requis ; le profil Desktop reste inchangé.')
        return [program, '--profile', values['profile'], '--json', '--session-id', task['session'], '-']
    if engine == 'claude-code':
        raise Unavailable('engine_unavailable', 'Adaptateur Claude Code non validé ; détection seule pour le moment.')
    if engine == 'claude-desktop':
        raise Unavailable('engine_unavailable', 'Le endpoint Quick Entry utilise JSON-RPC, pas exec resume.')
    raise ValueError('Le serveur partagé utilise JSON-RPC, pas exec resume')


def capabilities(db):
    result = []
    for engine, (label, provider) in ENGINES.items():
        configured = db.execute('SELECT options FROM engine_settings WHERE engine=?', (engine,)).fetchone()
        values = json.loads(configured[0]) if configured else {}
        program = executable(engine)
        reason = None if program else 'Exécutable absent'
        if program and engine in ('codex', 'codex-server', 'codex-parent', 'claude-desktop'):
            try:
                select(engine, values)
            except Unavailable as error:
                reason = error.reason
        if program and engine in ('harness-cli', 'claude-code'):
            try:
                command(engine, {'session': 'session-check' if engine == 'harness-cli' else str(uuid.UUID(int=0))}, program, values)
            except Unavailable as error:
                reason = error.reason
        result.append({'id': engine, 'label': label, 'provider': provider, 'available': reason is None,
                       'reason': reason, 'automatic_quota': engine.startswith('codex'),
                       'scope': 'Transport détecté ; authentification et capacité réelle de reprise à vérifier.'})
    return result


def decorate(db, tasks):
    configured = {r[0]: r[1] for r in db.execute('SELECT task_id,engine FROM task_engines')}
    for task in tasks:
        engine = configured.get(task['id'], 'codex')
        label, provider = ENGINES.get(engine, ('Moteur inconnu', 'Inconnu'))
        task.update(engine=engine, engine_label=label, provider=provider)
    return tasks
