"""Bounded delegation to a remote IronClaw/OpenClaw agent over SSH.

Joins an already-running remote agent: tasks are submitted with `ironclaw run -m`
and read back, mirroring the shape of codex_expert.review / claude_expert.review.
No forwarded TCP port is required: the transport is the SSH session itself.
A private key is never kept in clear on disk: bytes are materialized in a 0600
temporary file for one invocation and removed afterwards, or a plain path is
used as-is. No fallback and no automatic retry.
"""
from pathlib import Path
import os
import re
import subprocess
import tempfile

from engines import Unavailable

DEFAULT_UI_PORT = '3000'


def validate_values(values):
    """Validate the explicit IronClaw endpoint configuration; there is no default host."""
    if not isinstance(values, dict) or not isinstance(values.get('key'), (str, bytes)):
        raise ValueError('Configuration IronClaw requise : host, port, user, key')
    host, port, user = (values.get(name) for name in ('host', 'port', 'user'))
    host, port, user = (str(item) if isinstance(item, (str, int)) and not isinstance(item, bool) else ''
                        for item in (host, port, user))
    if not host or not port or not user:
        raise ValueError('Configuration IronClaw incomplète : host, port et user explicites requis')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}', host):
        raise ValueError('Hôte IronClaw invalide')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', user):
        raise ValueError('Utilisateur IronClaw invalide')
    if not re.fullmatch(r'[1-9][0-9]{0,4}', port) or int(port) > 65535:
        raise ValueError('Port IronClaw invalide')
    return host, port, user, values['key']


def _ui_port(values):
    port = str(values.get('ui_port', DEFAULT_UI_PORT))
    if not re.fullmatch(r'[1-9][0-9]{0,4}', port) or int(port) > 65535:
        raise ValueError('Port UI IronClaw invalide')
    return port


def _key_material(key):
    """Return (keyfile_path, cleanup). Bytes -> private temporary file removed afterwards.

    The key is briefly on disk (0600); an abrupt process kill before cleanup can leave it behind.
    Prefer a dedicated key file referenced by path.
    """
    if isinstance(key, bytes):
        if not key.strip():
            raise ValueError('Clé IronClaw vide')
        descriptor, tmp = tempfile.mkstemp(prefix='.ironclaw-key-')
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(key)
            os.chmod(tmp, 0o600)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise
        return tmp, lambda: Path(tmp).unlink(missing_ok=True)
    path = Path(key).expanduser()
    if not path.is_file():
        raise ValueError('Fichier de clé IronClaw introuvable')
    return str(path), lambda: None


def _ssh_base(program, keyfile, host, port, user):
    # -F /dev/null ignores ~/.ssh/config; the host key must already be in known_hosts.
    return [program, '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=12',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'IdentitiesOnly=yes',
            '-i', keyfile, '-p', str(port), f'{user}@{host}']


def _run(command, timeout, input_text=None):
    return subprocess.run(command, input=input_text, capture_output=True, text=True, timeout=timeout)


def _clean_answer(stdout):
    """Last non-empty, non-runtime-log line of stdout, or None."""
    lines = [line.strip() for line in stdout.splitlines()
             if line.strip() and ' INFO ' not in line and ' WARN ' not in line]
    return lines[-1] if lines else None


def submit(program, values, question, *, timeout=180):
    """Submit one bounded task and read the model reply back.

    Returns {'status': 'finished', 'response': answer, 'model': ...}.
    Raises engines.Unavailable for refused auth, expiry or non-zero exit.
    """
    host, port, user, key = validate_values(values)
    if not isinstance(question, str) or not question.strip() or len(question) > 12000:
        raise ValueError('Question bornée requise')
    # The prompt travels on stdin: it never appears in a local or remote process list.
    remote = 'ironclaw run -m "$(cat)" 2>/dev/null'

    keyfile, cleanup = _key_material(key)
    command = _ssh_base(program, keyfile, host, port, user) + [remote]
    try:
        try:
            proc = _run(command, timeout, input_text=question)
        except subprocess.TimeoutExpired:
            raise Unavailable('timed_out', 'Soumission IronClaw expirée ; aucune relance automatique.')
        text = (proc.stdout or '') + (proc.stderr or '')
        if 'Permission denied' in text or 'administratively prohibited' in text:
            raise Unavailable('engine_unavailable', 'Accès SSH IronClaw refusé ; clé ou droits insuffisants.')
        if proc.returncode != 0:
            raise Unavailable('blocked_permission', 'La commande distante a échoué ; aucune relance automatique.')
        answer = _clean_answer(proc.stdout or '')
        if not answer:
            raise Unavailable('needs_review', 'Pas de réponse détectée dans la sortie distante.')
        # Le moteur n'est pas devine par le transport : engine() l'interroge.
        return {'status': 'finished', 'response': answer}
    finally:
        cleanup()


def engine(program, values, *, timeout=30):
    """Moteur d'inférence réellement configuré côté distant (lecture seule).

    IronClaw expose un unique moteur par slot `[llm.<nom>]` dans
    config.toml. On lit la section `[llm.default]` (source de vérité du slot
    principal) sans appeler de modèle. Retourne {'provider': ..., 'model': ...}
    ou None si la configuration est illisible — jamais de valeur inventée.
    """
    host, port, user, key = validate_values(values)
    keyfile, cleanup = _key_material(key)
    # Sed ciblé : on n'extrait que provider_id/model de [llm.default], jamais
    # d'éventuels secrets présents ailleurs dans le fichier.
    command = _ssh_base(program, keyfile, host, port, user) + [
        "awk '/^\\[llm\\.default\\]/{f=1;next} /^\\[/{f=0} "
        "f&&/^(provider_id|model)[[:space:]]*=/{print}' "
        "/data/ironclaw-reborn/config.toml 2>/dev/null"]
    try:
        proc = _run(command, timeout)
    except subprocess.TimeoutExpired:
        return None
    finally:
        cleanup()
    if proc.returncode != 0:
        return None
    found = {}
    for line in (proc.stdout or '').splitlines():
        if '=' not in line:
            continue
        key_name, _, value = line.partition('=')
        value = value.strip().strip('"').strip("'")
        if key_name.strip() in ('provider_id', 'model') and value:
            found[key_name.strip()] = value
    return found or None


def health(program, values, *, timeout=30):
    """Deterministic read-only health of the remote runtime (no model call)."""
    host, port, user, key = validate_values(values)
    keyfile, cleanup = _key_material(key)
    command = _ssh_base(program, keyfile, host, port, user) + [
        'echo SHELL_OK; ironclaw status 2>/dev/null | head -3; '
        + 'curl -s -o /dev/null -w "UI=%{http_code}\n" --max-time 5 http://127.0.0.1:'
        + _ui_port(values) + '/']
    try:
        try:
            proc = _run(command, timeout)
        except subprocess.TimeoutExpired:
            return {'connected': False, 'reason': 'Connexion IronClaw expirée.'}
        if 'Permission denied' in (proc.stdout or '') + (proc.stderr or ''):
            return {'connected': False, 'reason': 'Authentification SSH refusée.'}
        out = (proc.stdout or '')
        return {'connected': proc.returncode == 0 and 'SHELL_OK' in out,
                'reason': 'Shell + UI vérifiés.' if proc.returncode == 0 and 'SHELL_OK' in out else 'Runtime distant injoignable.'}
    finally:
        cleanup()


def review(program, values, root, question, *, kind='expertise', level='standard',
           paths=None, timeout=300):
    """Delegate one bounded question to the remote agent; context files attached.

    Mirrors the shape of codex_expert.review / claude_expert.review: returns
    {status, resumable, project_root, kind, level, response, model, billing,
    automatic_fallback}. No fallback and no retry.
    """
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or not isinstance(question, str) or not question.strip() or len(question) > 12000:
        raise ValueError('Project directory and bounded question required')
    if kind not in ('expertise', 'review', 'batch') or level not in ('standard', 'deep'):
        raise ValueError('Invalid delegation kind or level')
    from claude_desktop import context_files
    prompt = question + context_files(root, paths)
    result = {'status': 'needs_review', 'resumable': False, 'project_root': str(root),
              'kind': kind, 'level': level, 'quota_remaining': None,
              'model': None, 'billing': None, 'automatic_fallback': False}
    try:  # la découverte du moteur ne doit jamais empêcher une délégation
        discovered = engine(program, values)
    except (ValueError, OSError):
        discovered = None
    if discovered:
        result['model'] = discovered.get('model')
        result['billing'] = f"Remote IronClaw runtime ({discovered.get('provider')} backend)"
    try:
        submitted = submit(program, values, prompt, timeout=timeout)
        return result | {'status': 'finished', 'response': submitted['response'],
                         'resumable': False}
    except Unavailable as error:
        return result | {'status': error.state, 'reason': error.reason}
