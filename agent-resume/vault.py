import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def crypt(data, purpose, operation):
    executable = ROOT / 'Reprise.app/Contents/MacOS/Vault'
    try:
        result = subprocess.run([str(executable)], input=json.dumps({'operation': operation, 'payload': base64.b64encode(data).decode(), 'purpose': purpose}), text=True, capture_output=True, timeout=30)
        if result.returncode:
            raise ValueError('Coffre verrouillé ou contenu altéré')
        return base64.b64decode(json.loads(result.stdout), validate=True)
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise ValueError('Coffre Reprise indisponible ; aucune copie en clair écrite') from error


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
