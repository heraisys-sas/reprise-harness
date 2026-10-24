"""Profile-scoped Harness integration; private backups, explicit plans, no client activation."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

from app_paths import bundled, state_location
from state_install import initialized, _sync_directory

START = '# BEGIN REPRISE MANAGED INTEGRATION'
END = '# END REPRISE MANAGED INTEGRATION'
DOCS = ('REPRISE-AGENTS.template.md', 'CODEX-EXECUTION-CONTRACT.md', 'ASI1-ORCHESTRATOR.template.md', 'HANDOFF-CONTRACT.md', 'HARNESS-ROLES-CONTRACT.md', 'DELEGATION-PERSISTENCE.md', 'QUOTA-KNOWLEDGE.md', 'HARNESS-SUPERVISION-ARCHITECTURE.md', 'HARNESS-INTEGRATION.md', 'CLARIFICATION-CONTRACT.md')
OBSERVE = ('reprise_backends', 'reprise_requests', 'reprise_quotas')
DELEGATE = ('reprise_delegate', 'reprise_followup')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + '\n'


def file_hash(path):
    if path.is_symlink():
        raise ValueError(f'Lien symbolique non géré : {path}')
    if path.exists() and not path.is_file():
        raise ValueError(f'Fichier régulier requis : {path}')
    return digest(path.read_bytes()) if path.exists() else None


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def context(home, profile):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', profile):
        raise ValueError('Nom de profil invalide.')
    home = Path(home).expanduser().absolute()
    if home.is_symlink():
        raise ValueError('Dossier Harness symbolique refusé.')
    home = home.resolve(strict=True)
    folder = home / 'profiles' / profile
    for path in (home, home / 'profiles', folder, folder / '.reprise'):
        if path.is_symlink():
            raise ValueError('Profil ou dossier de gestion symbolique refusé.')
    if not folder.is_dir() or not (folder / 'cordis.patch.yml').is_file():
        raise ValueError('Profil Harness existant requis (cordis.patch.yml). Aucun profil créé automatiquement.')
    return home, folder, folder / '.reprise'


def safe_profile_path(path, folder):
    if '..' in path.parts or not path.is_relative_to(folder):
        raise ValueError('Chemin géré hors du profil.')
    for entry in [path] + list(path.parents):
        if entry == folder.parent:
            break
        if entry.is_symlink():
            raise ValueError(f'Chemin géré symbolique : {entry}')


def manifest(managed):
    path = managed / 'managed.json'
    if not path.exists():
        return None
    file_hash(path)
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get('format') != 1:
        raise ValueError('Manifeste d’intégration incompatible.')
    return value


def read_journal(path):
    if file_hash(path) is None:
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get('format') != 1 or value.get('status') not in ('pending', 'completed'):
        raise ValueError('Journal incompatible ou incomplet ; conserver les sauvegardes et réconcilier.')
    return value


def split_block(text):
    if text.count(START) != text.count(END) or text.count(START) > 1:
        raise ValueError('Bloc Reprise ambigu ; réconciliation requise.')
    if START not in text:
        return text, None
    match = re.search(r'(?m)^' + re.escape(START) + r'\n.*?^' + re.escape(END) + r'(?:\n|$)', text, re.S)
    if match is None:
        raise ValueError('Délimiteurs Reprise invalides.')
    return text[:match.start()] + text[match.end():], match.group()


def tree_files(root):
    result = {}
    for entry in sorted(root.rglob('*')):
        if entry.is_symlink():
            if not entry.resolve().is_relative_to(root.resolve()):
                raise ValueError('Lien de payload sortant.')
            result[str(entry.relative_to(root))] = {'symlink': os.readlink(entry)}
        elif entry.is_file():
            result[str(entry.relative_to(root))] = digest(entry.read_bytes())
    return result


def bundle_info(root):
    root = Path(root).resolve()
    helpers = root if bundled(root) else root / 'Reprise.app/Contents/Resources/helpers'
    payload = json.loads((helpers / 'payload-manifest.json').read_text())
    runtime = helpers.parent / 'runtime'
    runtime_manifest = json.loads((runtime / 'runtime-manifest.json').read_text())
    if not isinstance(payload.get('runtime'), dict):
        raise ValueError('Construire le bundle avec son runtime Python embarqué avant installation.')
    for name, expected in payload['files'].items():
        if Path(name).name != name:
            raise ValueError('Chemin de payload non supporté.')
        if file_hash(helpers / name) != expected:
            raise ValueError('Payload helpers modifié ; reconstruire le bundle.')
    actual = tree_files(runtime)
    actual.pop('runtime-manifest.json', None)
    if actual != runtime_manifest['files'] or not os.access(runtime / 'bin/python3', os.X_OK):
        raise ValueError('Runtime embarqué altéré ou incomplet.')
    native = helpers.parent / 'native-manifest.json'
    native_evidence = json.loads(native.read_text()) if native.exists() else {}
    for name, observed in native_evidence.get('binaries', {}).items():
        if file_hash(helpers.parent.parent / 'MacOS' / name) != observed['sha256']:
            raise ValueError('Binaire natif modifié ; reconstruire le bundle.')
    identity = digest(encoded({'payload': payload, 'runtime': runtime_manifest, 'native': native_evidence}).encode())
    return helpers, runtime, identity


def release_check(release):
    evidence = json.loads((release / 'release.json').read_text())
    actual = tree_files(release)
    actual.pop('release.json', None)
    if actual != evidence['files'] or not os.access(release / 'Contents/Resources/runtime/bin/python3', os.X_OK):
        raise ValueError('Copie installée altérée ; aucune exécution de ce release.')


def stage_release(helpers, runtime, release):
    if release.is_symlink() or release.parent.is_symlink():
        raise ValueError('Destination de release symbolique refusée.')
    if release.exists():
        release_check(release)
        return
    release.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.release-', dir=release.parent) as temporary:
        stage = Path(temporary) / 'release'
        resources = stage / 'Contents/Resources'
        helper_target = resources / 'helpers'
        helper_target.mkdir(parents=True)
        payload = json.loads((helpers / 'payload-manifest.json').read_text())
        for name in list(payload['files']) + ['payload-manifest.json']:
            if Path(name).name != name:
                raise ValueError('Chemin de payload non supporté.')
            shutil.copyfile(helpers / name, helper_target / name)
        shutil.copytree(runtime, resources / 'runtime', symlinks=True)
        if (helpers.parent / 'native-manifest.json').is_file():
            shutil.copyfile(helpers.parent / 'native-manifest.json', resources / 'native-manifest.json')
        native = helpers.parent.parent / 'MacOS'
        if native.is_dir():
            shutil.copytree(native, stage / 'Contents/MacOS')
        atomic(stage / 'release.json', encoded({'format': 1, 'files': tree_files(stage)}))
        stage.rename(release)
        _sync_directory(release.parent)
    release_check(release)


def inspect(home, profile=None):
    home = Path(home).expanduser().absolute()
    profiles = sorted(p.name for p in (home / 'profiles').glob('*')
                      if re.fullmatch(r'[A-Za-z0-9_-]+', p.name) and p.is_dir() and not p.is_symlink() and (p / 'cordis.patch.yml').is_file())
    result = {'status': 'inspected', 'profiles': profiles, 'message': 'Inspection locale sans chargement du client. Interface vérifiée sur Harness 0.2.1-rc.2 ; version du client actif inconnue.'}
    if profile:
        _, _, managed = context(home, profile)
        old = manifest(managed)
        if old:
            result['project'] = old.get('project')
            result['output_resume'] = old.get('output_resume', False)
            result['output_resume_project'] = old.get('output_resume_project', old.get('project'))
            result['delegate'] = 'reprise_delegate' in old.get('tools', [])
            result['agent_role'] = old.get('agent_role', 'standard')
        result['codex_workspace_write'] = (old or {}).get('codex_workspace_write', False)
        journal = managed / 'operation.json'
        if journal.exists() or journal.is_symlink():
            data = read_journal(journal)
            if data.get('status') == 'pending':
                result['pending_plan'] = data['plan']
                result['backup'] = data.get('backup')
                result['message'] = 'Opération partielle conservée ; reprendre ce plan avant toute nouvelle modification.'
    return result


def desired(root, home, profile, project, action, delegate=False, agent_role='standard', output_resume=False, output_resume_project=None, codex_workspace_write=False):
    home, folder, managed = context(home, profile)
    old = manifest(managed)
    project = str(Path(project).expanduser().resolve(strict=True))
    if not Path(project).is_dir():
        raise ValueError('Projet autorisé : dossier existant requis.')
    if old and old.get('status') == 'installed' and action == 'remove' and old['project'] != project:
        raise ValueError('Projet sélectionné différent du projet géré ; inspecter le profil avant retrait.')
    config = folder / 'cordis.patch.yml'
    file_hash(config)
    original = config.read_bytes().decode('utf-8')
    base, block = split_block(original)
    if block is not None and (not old or old.get('status') != 'installed' or digest(block.encode()) != old.get('block_sha256')):
        raise ValueError('Bloc Reprise personnalisé ou non géré ; aucune réécriture.')
    if any(token in base for token in ('reprise-codex-expert', 'reprise_codex', 'reprise-skill-filesystem', '# BEGIN REPRISE EXPERTISE')):
        raise ValueError('Ancienne intégration ou définition Reprise non gérée : réconciliation requise, aucun écrasement.')
    for relative, expected in (old or {}).get('managed_files', {}).items():
        path = folder / relative
        if Path(relative).is_absolute() or '..' in Path(relative).parts or not path.is_relative_to(managed / 'skills'):
            raise ValueError('Manifeste de fichiers gérés hors périmètre.')
        safe_profile_path(path, folder)
        if file_hash(path) not in (None, expected):
            raise ValueError(f'Fichier géré personnalisé : {path}. Sauvegarder et réconcilier avant modification.')
    meaningful = [line for line in base.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    empty_array = len(meaningful) == 1 and re.fullmatch(r'\[\]\s*(?:#.*)?', meaningful[0].strip()) is not None
    if not empty_array and any(not line[0].isspace() and not (line.startswith('- ') or line == '-') for line in meaningful):
        raise ValueError('Forme du patch non prise en charge : liste YAML de patches ou [] attendue. Aucun changement.')
    resume_project = str(Path(output_resume_project or project).expanduser().resolve(strict=True))
    if not Path(resume_project).is_dir():
        raise ValueError('Projet de continuation : dossier existant requis.')
    files = {}
    release = None
    state = str(state_location(root).absolute())
    if not bundled(root) and Path(state).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('Préparer un état indépendant du clone (ou utiliser le bundle) avant installation.')
    if action == 'install':
        if not initialized(Path(state)) and not (Path(state) / 'tasks.sqlite').is_file():
            raise ValueError('Préparer l’état Reprise avant installation ; aucun registre neuf implicite.')
        helpers, runtime, identity = bundle_info(root)
        release = Path(state).parent / 'integration-releases' / identity
        program = release / 'Contents/Resources/runtime/bin/python3'
        tools = OBSERVE + (DELEGATE if delegate else ())
        args = ['-E', '-s', '-B', str(release / 'Contents/Resources/helpers/codex_expert_mcp.py'),
                '--project-root', project, '--state', state, '--tools', ','.join(tools)]
        if codex_workspace_write:
            args.append('--codex-workspace-write')
        entries = [{'id': 'reprise-codex-expert', 'name': '@deepseek-ai/dsh-mcp-client', 'config': {
            'transport': 'stdio', 'serverName': 'reprise_codex', 'command': str(program), 'args': args,
            'toolCallTimeoutMs': 330000, 'failOnStartupError': False, 'reconnect': {'enabled': False}}},
            {'id': 'reprise-skill-filesystem', 'name': '@deepseek-ai/dsh-skill-filesystem', 'config': {
                'providerName': 'reprise-' + profile, 'includeDefaultRoots': False,
                'customSkillDirs': [str(managed / 'skills')], 'watch': False}}]
        if output_resume:
            entries.append({'id': 'reprise-output-resume', 'name': str(release / 'Contents/Resources/helpers/harness-output-resume.cjs'),
                            'config': {'enabled': True, 'projectRoot': resume_project,
                                       'stateDirectory': str(Path(state) / 'harness-output-resume'),
                                       'maxConsecutive': 2}})
        new_block = START + '\n- insert: ' + json.dumps(entries, ensure_ascii=False) + '\n' + END + '\n'
        empty_before = (old or {}).get('empty_before')
        empty_after = (old or {}).get('empty_after')
        if empty_array:
            empty_before = base
            base = re.sub(r'(?m)^([ \t]*)\[\]([ \t]*(?:#[^\n]*)?)$', r'\1\2', base)
            empty_after = base
        files[config] = base + ('' if not base or base.endswith('\n') else '\n') + new_block
        skill_folder = managed / 'skills/reprise-delegation'
        skill = (helpers / 'reprise-delegation.md').read_text()
        skill += '\n\nInstalled scope metadata (data): ' + json.dumps({'profile': profile, 'project_root': project, 'enabled_tools': tools}, ensure_ascii=False) + '\nInspect actual tools and project_root before use.\n'
        if agent_role == 'coordinator':
            skill += '\n\n## Active operator-selected role: coordinator\nThis role is an assignment, not evidence of model capability. It does not grant inference permissions.\n'
            skill += (helpers / 'REPRISE-AGENTS.template.md').read_text()
        files[skill_folder / 'SKILL.md'] = skill
        for name in DOCS:
            files[skill_folder / name] = (helpers / name).read_text()
        owned = {str(path.relative_to(folder)): digest(text.encode()) for path, text in files.items() if path != config}
        next_manifest = {'format': 1, 'interface_validated_against': 'Harness 0.2.1-rc.2', 'status': 'installed', 'project': project, 'profile': profile,
                         'state': state, 'release': str(release), 'tools': list(tools),
                         'block_sha256': digest(new_block.encode()), 'managed_files': owned,
                         'empty_before': empty_before, 'empty_after': empty_after}
        if output_resume:
            next_manifest['output_resume'] = True
            next_manifest['output_resume_project'] = resume_project
        if codex_workspace_write:
            next_manifest['codex_workspace_write'] = True
        if agent_role != 'standard':
            next_manifest['agent_role'] = agent_role
    else:
        if not old or old.get('status') != 'installed':
            return {}, {'release': None, 'state': state}, None
        if base == old.get('empty_after'):
            base = old['empty_before']
        elif not any(line.strip() and not line.lstrip().startswith('#') for line in base.splitlines()):
            base += ('' if not base or base.endswith('\n') else '\n') + '[]\n'
        files[config] = base
        for relative in old['managed_files']:
            files[folder / relative] = None
        next_manifest = {'format': 1, 'status': 'removed', 'project': project, 'profile': profile,
                         'state': old['state'], 'release': old['release'], 'managed_files': {}}
    files[managed / 'managed.json'] = encoded(next_manifest)
    for path in files:
        safe_profile_path(path, folder)
        if path not in (config, managed / 'managed.json') and path.exists() and str(path.relative_to(folder)) not in (old or {}).get('managed_files', {}):
            raise ValueError(f'Fichier préexistant non géré : {path}. Aucun écrasement ni adoption implicite.')
    return files, {'release': str(release) if release else (old or {}).get('release'), 'state': state}, next_manifest


def plan(root, home, profile, project, action='install', delegate=False, agent_role='standard', output_resume=False, output_resume_project=None, codex_workspace_write=False):
    if action not in ('install', 'remove') or type(delegate) is not bool or agent_role not in ('standard', 'coordinator') or type(output_resume) is not bool or type(codex_workspace_write) is not bool or (codex_workspace_write and not delegate):
        raise ValueError('Action ou capacité invalide.')
    if inspect(home, profile).get('pending_plan'):
        raise ValueError('Reprendre l’opération partielle conservée avant de créer un autre plan.')
    files, extra, _ = desired(root, home, profile, project, action, delegate, agent_role, output_resume, output_resume_project, codex_workspace_write)
    changes = []
    for path, text in files.items():
        before, after = file_hash(path), digest(text.encode()) if text is not None else None
        if before != after:
            changes.append({'path': str(path), 'action': 'remove' if text is None else 'write',
                            'before_sha256': before, 'after_sha256': after})
    canonical_home, _, _ = context(home, profile)
    result = {'format': 1, 'action': action, 'home': str(canonical_home), 'profile': profile,
              'project': str(Path(project).expanduser().resolve(strict=True)), 'delegate': delegate,
              'changes': changes, **extra}
    if codex_workspace_write:
        result['codex_workspace_write'] = True
    if agent_role != 'standard':
        result['agent_role'] = agent_role
    if output_resume:
        result['output_resume'] = True
        result['output_resume_project'] = str(Path(output_resume_project or project).expanduser().resolve(strict=True))
    result['fingerprint'] = digest(encoded(result).encode())
    return result


@contextmanager
def locked(managed):
    managed.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(managed / 'lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Une opération d’intégration est déjà active.')
        yield


def apply(root, public):
    if not isinstance(public, dict) or public.get('format') != 1:
        raise ValueError('Plan incompatible.')
    unsigned = {k: v for k, v in public.items() if k != 'fingerprint'}
    if digest(encoded(unsigned).encode()) != public.get('fingerprint'):
        raise ValueError('Plan altéré ; refaire la prévisualisation.')
    _, folder, managed = context(public['home'], public['profile'])
    with locked(managed):
        journal_path = managed / 'operation.json'
        journal = read_journal(journal_path)
        if journal.get('status') == 'pending':
            if journal['plan'] != public:
                raise ValueError('Un autre plan est en attente ; reprendre ce plan sans écrasement.')
        else:
            current = plan(root, public['home'], public['profile'], public['project'], public['action'], public['delegate'], public.get('agent_role', 'standard'), public.get('output_resume', False), public.get('output_resume_project'), public.get('codex_workspace_write', False))
            if current != public:
                raise ValueError('Entrées modifiées depuis la prévisualisation. Refaire le plan.')
            if not public['changes']:
                return {'status': 'completed', 'changed': False, 'message': 'Configuration identique ; aucune écriture.'}
            files, _, _ = desired(root, public['home'], public['profile'], public['project'], public['action'], public['delegate'], public.get('agent_role', 'standard'), public.get('output_resume', False), public.get('output_resume_project'), public.get('codex_workspace_write', False))
            if public['action'] == 'install':
                helpers, runtime, _ = bundle_info(root)
                stage_release(helpers, runtime, Path(public['release']))
            backup = managed / 'backups' / str(uuid.uuid4())
            backup.mkdir(parents=True, mode=0o700)
            operations = []
            for change in public['changes']:
                path = Path(change['path'])
                if '..' in path.parts or not path.is_relative_to(folder):
                    raise ValueError('Destination hors du profil.')
                if path.exists():
                    saved = backup / path.relative_to(folder)
                    saved.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    shutil.copyfile(path, saved)
                    saved.chmod(0o600)
                operations.append(change | {'text': files[path]})
            journal = {'format': 1, 'status': 'pending', 'plan': public, 'backup': str(backup), 'operations': operations}
            atomic(journal_path, encoded(journal))
        # The journal is local data, not an authority to introduce additional writes.
        if len(journal['operations']) != len(public['changes']):
            raise ValueError('Journal incohérent avec le plan.')
        if public['action'] == 'install':
            release_check(Path(public['release']))
        for operation, change in zip(journal['operations'], public['changes']):
            if any(operation.get(key) != value for key, value in change.items()):
                raise ValueError('Journal modifié ; réconciliation requise.')
            text = operation.get('text')
            if (text is not None and not isinstance(text, str)) or (digest(text.encode()) if text is not None else None) != change['after_sha256']:
                raise ValueError('Contenu du journal modifié ; aucune écriture.')
        # Validate the complete current set before continuing a partial operation.
        for operation in journal['operations']:
            safe_profile_path(Path(operation['path']), folder)
            current = file_hash(Path(operation['path']))
            if current not in (operation['before_sha256'], operation['after_sha256']):
                raise ValueError('Conflit après opération partielle ; sauvegardes conservées, aucune nouvelle écriture.')
        for operation in journal['operations']:
            path = Path(operation['path'])
            current = file_hash(path)
            if current == operation['after_sha256']:
                continue
            if current != operation['before_sha256']:
                raise ValueError('Conflit détecté avant écriture ; opération suspendue, sauvegarde conservée.')
            if operation['text'] is None:
                path.unlink(missing_ok=True)
                _sync_directory(path.parent)
            else:
                atomic(path, operation['text'])
        journal['status'] = 'completed'
        atomic(journal_path, encoded(journal))
    return {'status': 'completed', 'changed': True, 'backup': journal['backup'],
            'message': 'Configuration appliquée. Aucun client chargé ni service redémarré ; vérification séparée requise.'}


def bridge_capabilities(tools):
    """Report advertised input capabilities of the probed release, not the client."""
    schemas = {tool['name']: tool.get('inputSchema', {}) for tool in tools}
    def advertised(schema, field, value):
        field_schema = schema.get('properties', {}).get(field, {})
        if field_schema.get('const') == value or value in field_schema.get('enum', []):
            return True
        return any(advertised(branch, field, value)
                   for operator in ('oneOf', 'anyOf', 'allOf')
                   for branch in schema.get(operator, []))
    def supports(name, field, value):
        return advertised(schemas.get(name, {}), field, value)
    return {'result_transport': {
        'receipt': supports('reprise_requests', 'view', 'receipt'),
        'pagination': supports('reprise_requests', 'view', 'page'),
        'delegate_receipt': supports('reprise_delegate', 'return_mode', 'receipt') if 'reprise_delegate' in schemas else None,
        'followup_receipt': supports('reprise_followup', 'return_mode', 'receipt') if 'reprise_followup' in schemas else None,
    }, 'evidence': 'direct_probe_tools_schema', 'client_loaded': 'unknown'}


def verify(home, profile, root=None):
    _, folder, managed = context(home, profile)
    if inspect(home, profile).get('pending_plan'):
        raise ValueError('Opération partielle : terminer ou réconcilier avant vérification.')
    old = manifest(managed)
    if not old or old.get('status') != 'installed':
        raise ValueError('Aucune intégration installée gérée dans ce profil.')
    _, block = split_block((folder / 'cordis.patch.yml').read_bytes().decode('utf-8'))
    if block is None or digest(block.encode()) != old['block_sha256']:
        raise ValueError('Configuration Reprise absente ou modifiée.')
    for relative, expected in old['managed_files'].items():
        safe_profile_path(folder / relative, folder)
        if file_hash(folder / relative) != expected:
            raise ValueError('Consignes installées absentes ou modifiées.')
    release = Path(old['release'])
    selected_state = state_location(root or Path(__file__).parent).resolve()
    expected_base = selected_state.parent / 'integration-releases'
    if (Path(old['state']).resolve() != selected_state or not re.fullmatch(r'[a-f0-9]{64}', release.name)
            or release.is_symlink() or expected_base.is_symlink() or release.parent.resolve() != expected_base.resolve()):
        raise ValueError('Release ou état hors de l’installation Reprise sélectionnée ; aucune exécution.')
    release_check(release)
    requests = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}}]
    with tempfile.TemporaryDirectory(prefix='reprise-bridge-check-') as temporary:
        program = release / 'Contents/Resources/runtime/bin/python3'
        command = [str(program), '-E', '-s', '-B', str(release / 'Contents/Resources/helpers/codex_expert_mcp.py'),
                   '--project-root', old['project'], '--state', temporary, '--tools', ','.join(old['tools'])]
        env = {k: v for k, v in os.environ.items() if not k.startswith(('PYTHON', 'DYLD_')) and k != 'AGENT_RESUME_STATE'}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        response = subprocess.run(command, input='\n'.join(json.dumps(r) for r in requests) + '\n',
                                  capture_output=True, text=True, timeout=20, env=env, check=True)
    messages = [json.loads(line) for line in response.stdout.splitlines()]
    initialized_reply = next(m['result'] for m in messages if m.get('id') == 1)
    tools = next(m['result']['tools'] for m in messages if m.get('id') == 2)
    names = sorted(tool['name'] for tool in tools)
    if names != sorted(old['tools']):
        raise ValueError('Outils observés différents de la sélection installée.')
    selected_release = {'status': 'unknown', 'reason': 'selected_bundle_not_available'}
    try:
        _, _, identity = bundle_info(root or Path(__file__).parent)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        selected_release['reason'] = 'selected_bundle_unavailable_or_invalid'
    else:
        selected_release = {'status': 'current' if identity == release.name else 'outdated',
                            'configured_identity': release.name, 'selected_identity': identity}
    capabilities = bridge_capabilities(tools)
    transport = capabilities['result_transport']
    missing = [name for name, available in transport.items() if available is False]
    update_required = selected_release['status'] == 'outdated' or bool(missing)
    message = 'Bloc géré et consignes vérifiés ; pont sondé directement sans inférence. Chargement par Harness non observé.'
    if update_required:
        message += ' Mise à jour de l’intégration requise ; rechargement du client à coordonner séparément.'
    return {'status': 'verified', 'message': message,
            'selected_release': selected_release, 'update_required': update_required,
            'bridge_capabilities': capabilities, 'missing_result_capabilities': missing,
            'output_resume': {'configured': old.get('output_resume', False), 'client_loaded': 'unknown'},
            'levels': {'configuration_written': True, 'integration_loaded': 'unknown', 'client_tools_discovered': 'unknown', 'exchange_confirmed': 'unknown'},
            'full_profile_syntax': 'not_checked', 'bridge_tools': names, 'bridge_version': initialized_reply['serverInfo']['version'],
            'bridge_source_sha256': initialized_reply['serverInfo'].get('source_sha256')}


def main(root, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('integration-inspect', 'integration-plan', 'integration-verify'):
        command = sub.add_parser(name)
        command.add_argument('--home', required=True)
        command.add_argument('--profile', required=name != 'integration-inspect')
        if name == 'integration-plan':
            command.add_argument('--project', required=True)
            command.add_argument('--action', choices=('install', 'remove'), required=True)
            command.add_argument('--delegate', action='store_true')
            command.add_argument('--output-resume-project', help='Explicit project for native continuation; defaults to bridge project')
            command.add_argument('--output-resume', action='store_true', help='Explicitly authorize same-session continuation after output-token limit')
            command.add_argument('--codex-workspace-write', action='store_true', help='Autoriser les implémentations Codex dans le projet sélectionné')
            command.add_argument('--agent-role', choices=('standard', 'coordinator'), default='standard')
    sub.add_parser('integration-apply')
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command == 'integration-inspect':
            result = inspect(args.home, args.profile)
        elif args.command == 'integration-plan':
            result = {'status': 'planned', 'message': 'Plan sans écriture. L’installation n’autorise ni inférence ni activation automatique.',
                      'plan': plan(root, args.home, args.profile, args.project, args.action, args.delegate, args.agent_role, args.output_resume, args.output_resume_project, args.codex_workspace_write)}
        elif args.command == 'integration-apply':
            public = json.loads(sys.stdin.read(1024 * 1024))['plan']
            result = apply(root, public)
        else:
            result = verify(args.home, args.profile, root=root)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result = {'status': 'refused', 'message': str(error), 'recovery': 'Aucune suppression automatique. Inspecter le profil pour retrouver un plan partiel ou les sauvegardes.'}
        if args.command == 'integration-apply' and isinstance(locals().get('public'), dict):
            try:
                evidence = inspect(public['home'], public['profile'])
                for key in ('pending_plan', 'backup'):
                    if key in evidence:
                        result[key] = evidence[key]
            except (OSError, ValueError, KeyError, TypeError):
                pass
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
