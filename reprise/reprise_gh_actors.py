"""Registre des acteurs du service gh Reprise (#270, lot 2) : jetons par acteur.

Le jeton sert a l'attribution, a la revocation et a la politique par acteur ; ce
n'est pas une frontiere de securite (meme compte Unix pour tous les agents).
Seule l'empreinte SHA-256 est stockee ; le secret n'existe que dans le fichier
de jeton prive remis au pont (`--token-file`). Motif d'emission repris de
approval_delegation.py (O_CREAT|O_EXCL|O_NOFOLLOW en 0600, dossier 0700 hors
depot). Aucun jeton n'est emis pour l'acteur reserve `operator`.

Format : `rgh_<token_id>_<secret>`. Comparaison par hmac.compare_digest, avec
un condense factice quand token_id est inconnu.

Convention du depot : compatible Python 3.9, zero dependance.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import stat
import time
import urllib.parse
from pathlib import Path

DEFAULT_STATE_DIR = os.path.join(
    os.path.expanduser('~'), 'Library', 'Application Support', 'Reprise'
)
DEFAULT_ACTORS_PATH = os.path.join(DEFAULT_STATE_DIR, 'gh-actors.sqlite')
OPERATOR = 'operator'
NAME_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
TOKEN_RE = re.compile(r'^rgh_([0-9a-f]{16})_([A-Za-z0-9_-]{20,64})$')
MAX_TOKEN_FILE_BYTES = 100
REPO_ROOT = Path(__file__).resolve().parent.parent
_DUMMY_DIGEST = hashlib.sha256(b'reprise-gh-dummy').hexdigest()

SCHEMA = """CREATE TABLE IF NOT EXISTS gh_actor_tokens (
    actor TEXT NOT NULL,
    token_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    harness TEXT NOT NULL,
    created_at REAL NOT NULL,
    revoked_at REAL
)"""


class ActorError(Exception):
    """Refus explicite du registre ou du fichier de jeton (jamais de secret)."""


class TokenFileError(ActorError):
    """Fichier de jeton present mais mal protege : le pont refuse de demarrer."""


def _digest(token):
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def _name(value, label):
    if not isinstance(value, str) or not NAME_RE.match(value):
        raise ActorError('%s invalide : motif [A-Za-z0-9._-]+ attendu' % label)
    return value


def _private_dir(path):
    """Dossier prive 0700 (cree au besoin) ; refuse un dossier lisible par d'autres."""
    os.makedirs(path, mode=0o700, exist_ok=True)
    info = os.stat(path)
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
        raise ActorError('Le dossier du registre gh doit etre prive (0700)')


def read_token_file(path):
    """Lit un jeton : None si absent ; TokenFileError si mal protege.

    Exigences : chemin absolu, pas de lien symbolique, fichier regulier detenu
    par l'utilisateur, mode exactement 0600, 100 octets au plus.
    """
    if not isinstance(path, str) or not os.path.isabs(path):
        raise TokenFileError('Fichier de jeton : chemin absolu attendu')
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return None
    except OSError:
        raise TokenFileError('Fichier de jeton illisible ou lien symbolique refuse') from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TokenFileError('Fichier de jeton : fichier regulier attendu')
        if info.st_uid != os.getuid():
            raise TokenFileError("Fichier de jeton : proprietaire different de l'utilisateur")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise TokenFileError('Fichier de jeton : mode 0600 exact attendu (%o)'
                                 % stat.S_IMODE(info.st_mode))
        if info.st_size > MAX_TOKEN_FILE_BYTES:
            raise TokenFileError('Fichier de jeton : %d octets au plus' % MAX_TOKEN_FILE_BYTES)
        raw = os.read(descriptor, MAX_TOKEN_FILE_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(raw) > MAX_TOKEN_FILE_BYTES:
        raise TokenFileError('Fichier de jeton : %d octets au plus' % MAX_TOKEN_FILE_BYTES)
    return raw.decode('ascii', 'replace').strip()


def verify(token, path=None):
    """(auth, actor, token_id, harness) ; auth ∈ ok | missing | invalid | revoked.

    Lecture seule (`mode=ro`) : un registre absent n'est jamais cree ici.
    """
    if not token:
        return 'missing', None, None, None
    match = TOKEN_RE.match(token) if isinstance(token, str) else None
    token_id = match.group(1) if match else None
    row = None
    path = path or DEFAULT_ACTORS_PATH
    if token_id and os.path.isfile(path):
        try:
            uri = 'file:%s?mode=ro' % urllib.parse.quote(os.path.abspath(path))
            db = sqlite3.connect(uri, uri=True, timeout=2)
            try:
                row = db.execute('SELECT actor, sha256, harness, revoked_at FROM gh_actor_tokens '
                                 'WHERE token_id=?', (token_id,)).fetchone()
            finally:
                db.close()
        except sqlite3.Error:
            row = None
    expected = row[1] if row else _DUMMY_DIGEST
    matches = hmac.compare_digest(_digest(token if match else 'invalid'), expected)
    if not row or not matches:
        return 'invalid', None, None, None
    if row[3] is not None:
        return 'revoked', row[0], token_id, row[2]
    return 'ok', row[0], token_id, row[2]


class ActorRegistry(object):
    """Registre ecrit par l'operateur : emission, revocation, liste (sans secret)."""

    def __init__(self, path=None, repo_root=None):
        self.path = os.path.abspath(path or DEFAULT_ACTORS_PATH)
        self.repo_root = Path(repo_root or REPO_ROOT).resolve()
        _private_dir(os.path.dirname(self.path))
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(descriptor)
        if stat.S_IMODE(os.stat(self.path).st_mode) & 0o077:
            raise ActorError('Le registre gh doit etre prive (0600)')
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.db.execute(SCHEMA)

    def close(self):
        self.db.close()

    def issue(self, actor, harness, out, replace=False, now=None):
        actor = _name(actor, 'Acteur')
        harness = _name(harness, 'Harness')
        if actor == OPERATOR:
            raise ActorError("Aucun jeton n'est emis pour l'acteur reserve « operator »")
        if not isinstance(out, str) or not os.path.isabs(out):
            raise ActorError('--out : chemin absolu attendu')
        destination = Path(out)
        if destination.name in ('', '.', '..'):
            raise ActorError('--out : nom de fichier invalide')
        parent = destination.parent.resolve(strict=True)
        if stat.S_IMODE(parent.stat().st_mode) & 0o077:
            raise ActorError('Le dossier du jeton doit etre prive (0700)')
        if parent == self.repo_root or self.repo_root in parent.parents:
            raise ActorError('Ne jamais placer de jeton dans le depot')
        now = time.time() if now is None else float(now)
        token_id = secrets.token_hex(8)
        token = 'rgh_%s_%s' % (token_id, secrets.token_urlsafe(32))
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
        try:
            descriptor = os.open(str(destination), flags, 0o600)
        except FileExistsError:
            raise ActorError('--out existe deja : jamais d\'ecrasement') from None
        revoked = 0
        try:
            with os.fdopen(descriptor, 'w', encoding='ascii') as handle:
                handle.write(token + '\n')
                handle.flush()
                os.fsync(handle.fileno())
            self.db.execute('BEGIN IMMEDIATE')
            try:
                if replace:
                    revoked = self.db.execute(
                        'UPDATE gh_actor_tokens SET revoked_at=? WHERE actor=? AND revoked_at IS NULL',
                        (now, actor)).rowcount
                self.db.execute('INSERT INTO gh_actor_tokens (actor, token_id, sha256, harness, created_at) '
                                'VALUES (?,?,?,?,?)', (actor, token_id, _digest(token), harness, now))
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise
        except BaseException:
            destination.unlink()
            raise
        return {'actor': actor, 'token_id': token_id, 'harness': harness,
                'token_file': str(destination), 'created_at': now, 'revoked_previous': revoked}

    def revoke(self, actor, token_id=None, now=None):
        actor = _name(actor, 'Acteur')
        now = time.time() if now is None else float(now)
        query = 'UPDATE gh_actor_tokens SET revoked_at=? WHERE actor=? AND revoked_at IS NULL'
        params = [now, actor]
        if token_id is not None:
            query += ' AND token_id=?'
            params.append(token_id)
        return self.db.execute(query, params).rowcount

    def list(self):
        rows = self.db.execute('SELECT actor, token_id, harness, created_at, revoked_at '
                               'FROM gh_actor_tokens ORDER BY created_at, token_id').fetchall()
        names = ('actor', 'token_id', 'harness', 'created_at', 'revoked_at')
        return [dict(zip(names, row)) for row in rows]
