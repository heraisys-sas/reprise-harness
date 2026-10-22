import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
from app_paths import native_binary

ROOT = Path(__file__).resolve().parent


def crypt(data, purpose, operation, *, interactive=False):
    if operation not in ('seal', 'open', 'check', 'authorize') or (interactive and operation != 'authorize'):
        raise ValueError('Interaction du coffre réservée à une autorisation explicite')
    if operation == 'authorize' and (not interactive or data):
        raise ValueError('L’autorisation du coffre nécessite une action utilisateur explicite')
    executable = native_binary(ROOT, 'Vault')
    try:
        result = subprocess.run([str(executable)], input=json.dumps({
            'operation': operation, 'payload': base64.b64encode(data).decode(),
            'purpose': purpose, 'interactive': interactive,
        }), text=True, capture_output=True, timeout=90 if interactive else 30)
        response = json.loads(result.stdout)
        if result.returncode:
            details = response.get('error', {}) if isinstance(response, dict) else {}
            code = details.get('code', 'unavailable')
            raise ValueError(f'Coffre Reprise indisponible ({code}) ; autorisez explicitement le coffre puis réessayez ; aucune copie en clair écrite')
        if operation == 'authorize':
            if response != {'authorized': True}:
                raise ValueError('Réponse d’autorisation invalide')
            return None
        if operation == 'check':
            if response != {'ready': True}:
                raise ValueError('Réponse de disponibilité invalide')
            return None
        return base64.b64decode(response, validate=True)
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as error:
        if isinstance(error, ValueError) and str(error).startswith('Coffre Reprise indisponible ('):
            raise
        raise ValueError('Coffre Reprise indisponible ; autorisez explicitement le coffre puis réessayez ; aucune copie en clair écrite') from error


def check(purpose='test-v1'):
    return crypt(b'', purpose, 'check')


def authorize(purpose='test-v1'):
    return crypt(b'', purpose, 'authorize', interactive=True)


def save(path, data, purpose):
    encrypted = crypt(data, purpose, 'seal')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix='.vault-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load(path, purpose):
    return crypt(path.read_bytes(), purpose, 'open')
