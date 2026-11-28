"""Service gh Reprise : acces GitHub centralise et audite pour les harness (#270).

Directive operateur (26 novembre 2026) : soit centraliser tout, soit delivrer un
service « gh » a tous les harness dans leur workspace. Les deux se combinent :

- coeur central : ce module. Un seul point d'authentification (token resolu via
  `git credential fill`, jamais logge), une politique fail-closed par acteur
  (lecture autorisee si le service est active ; ecriture seulement pour les
  acteurs listes), un journal d'audit JSONL commun (horodatage, acteur, methode,
  chemin, statut HTTP — sans le token ni le corps de la requete).
- livraison par workspace : un shim `bin/reprise-gh` par harness appelle ce
  module avec l'identite de l'acteur (`--actor autoclaw`, `--actor claude`, ...).

Meta AI n'est pas dans les acteurs d'ecriture : lecture seule tracee. C'est la
reponse concrete a sa demande d'un « acces en lecture, stable » et d'un
« pouvoir traçable, pas un pouvoir sans controle ».

Fail-closed partout : politique absente = lecture seule pour tous ; acteur
inconnu en ecriture = refus avant tout appel reseau ; toute reprise apres echec
passe par un nouvel appel explicite (pas de relance automatique). `init`
n'auto-eleve jamais un acteur : l'acteur doit deja etre liste en ecriture dans
la politique courante ; le premier amorcage est une ecriture directe de
gh-policy.json par l'operateur (hors CLI), jamais une option de la CLI
(revue securite harness 26/11 : voie consent supprimee).

L'acteur (`--actor`) est une etiquette d'audit declaree par l'appelant : elle
n'est pas authentifiee et n'est pas une frontiere de securite. Toute ecriture
de politique est operateur-only (fichier direct) ; la frontiere reelle est le
compte Unix + le trousseau macOS, pas la chaine --actor.

Usage CLI :
    python3 reprise_gh.py --actor autoclaw api GET /repos/OWNER/REPO/issues/270
    python3 reprise_gh.py --actor autoclaw comment 270 --repo OWNER/REPO \
        --body-file commentaire.md

Convention du depot : compatible Python 3.9 (pas de PEP 604), zero dependance.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from typing import Callable, Dict, List, Optional

DEFAULT_STATE_DIR = os.path.join(
    os.path.expanduser('~'), 'Library', 'Application Support', 'Reprise'
)
DEFAULT_POLICY_PATH = os.path.join(DEFAULT_STATE_DIR, 'gh-policy.json')
DEFAULT_AUDIT_PATH = os.path.join(DEFAULT_STATE_DIR, 'gh-audit.jsonl')
DEFAULT_HOST = 'api.github.com'
CREDENTIAL_HOST = 'github.com'
READ_METHODS = ('GET', 'HEAD')
WRITE_METHODS = ('POST', 'PUT', 'PATCH', 'DELETE')
USER_AGENT = 'reprise-gh'
API_VERSION = '2022-11-28'


class GhError(Exception):
    """Erreur attendue du service (politique, credentials, HTTP explicite)."""


def resolve_token(host=CREDENTIAL_HOST, runner=None):
    """Resout le token GitHub via `git credential fill` (jamais logge)."""
    fill_runner = runner or _run_credential_fill
    out = fill_runner(host)
    for line in out.splitlines():
        if line.startswith('password='):
            password = line[len('password='):].strip()
            if password:
                return password
    raise GhError(
        'Aucun token GitHub dans le trousseau (git credential fill sans '
        'password) — enregistrer les identifiants une fois, par exemple avec '
        'un push manuel du depot.'
    )


def _run_credential_fill(host):
    try:
        proc = subprocess.run(
            ['git', 'credential', 'fill'],
            input='protocol=https\nhost=%s\n' % host,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhError('git credential fill indisponible : %s' % exc)
    if proc.returncode != 0:
        raise GhError('git credential fill a echoue (code %s)' % proc.returncode)
    return proc.stdout


def default_policy():
    """Politique fail-closed : service actif, ecriture pour personne."""
    return {'enabled': True, 'write_actors': []}


def load_policy(path=None):
    path = path or DEFAULT_POLICY_PATH
    if not os.path.exists(path):
        return default_policy()
    with open(path, 'r', encoding='utf-8') as handle:
        policy = json.load(handle)
    if not isinstance(policy, dict):
        raise GhError('Politique invalide (%s) : objet JSON attendu' % path)
    return {
        'enabled': bool(policy.get('enabled', True)),
        'write_actors': list(policy.get('write_actors', [])),
    }


def save_policy(policy, path=None):
    path = path or DEFAULT_POLICY_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(policy, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(tmp, path)


_PATH_RE = re.compile(r'^/[A-Za-z0-9._~!$&\'()*+,;=:@%/?-]*$')
_REPO_RE = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


def api_url(host, path):
    """URL de l'API ; refuse tout chemin qui ferait sortir de `host` (#291).

    Sans cette porte, `.evil.example/x` donnait https://api.github.com.evil.example/x
    et le jeton partait vers un hôte tiers.
    """
    if not isinstance(path, str) or not _PATH_RE.match(path) or path.startswith('//'):
        raise GhError('Chemin API invalide : chemin absolu « /… » sur %s attendu' % host)
    url = 'https://%s%s' % (host, path)
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme != 'https' or parts.hostname != host or parts.username is not None
            or parts.password is not None or parts.port is not None):
        raise GhError('Chemin API invalide : hôte résolu différent de %s' % host)
    return url


def repo_slug(repo):
    if not isinstance(repo, str) or not _REPO_RE.match(repo) or '..' in repo:
        raise GhError('Dépôt invalide : OWNER/REPO attendu')
    return repo


def check_permission(policy, actor, method):
    """Porte unique : lecture si actif, ecriture si acteur liste. Fail-closed."""
    if not policy.get('enabled', False):
        raise GhError('Service gh desactive par la politique (%s)' % 'gh-policy.json')
    actor = (actor or '').strip()
    if not actor:
        raise GhError('Acteur requis (--actor) : appel sans identite refuse')
    if method.upper() not in READ_METHODS and actor not in policy.get('write_actors', []):
        raise GhError(
            "Ecriture refusee pour l'acteur « %s » : non liste dans "
            "gh-policy.json (lecture seule tracee au journal d'audit)" % actor
        )


def init_policy(actor, write_actors, policy_path=None, audit_path=None):
    """Ecrit la politique, sans auto-elevation.

    Une seule voie : l'acteur est deja liste en ecriture dans la politique
    courante (check_permission OK). Sinon refus avant toute ecriture —
    l'amorcage est une ecriture directe de gh-policy.json par l'operateur,
    jamais une option de la CLI (revue securite harness 26/11 : voie consent
    supprimee).
    """
    policy_path = policy_path or DEFAULT_POLICY_PATH
    audit_path = audit_path or DEFAULT_AUDIT_PATH
    acteurs = [x.strip() for x in (write_actors or []) if x and x.strip()]
    try:
        check_permission(load_policy(policy_path), actor, 'POST')
    except GhError as exc:
        audit(audit_path, actor, 'INIT', policy_path, 'refused', error=str(exc))
        raise GhError(
            "init refuse pour « %s » : acteur non liste en ecriture. "
            "L'amorcage est une ecriture directe de gh-policy.json par "
            "l'operateur, jamais une option de la CLI : pas d'auto-elevation"
            % actor)
    save_policy({'enabled': True, 'write_actors': acteurs}, policy_path)
    audit(audit_path, actor, 'INIT', policy_path, 'ok', extra={'voie': 'acteur_liste'})
    return 'acteur_liste'


def audit(audit_path, actor, method, path, status, error=None, extra=None):
    entry = {
        'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'actor': actor,
        'method': method,
        'path': path,
        'status': status,
    }
    if error:
        entry['error'] = error[:200]
    if extra:
        entry.update(extra)
    os.makedirs(os.path.dirname(audit_path) or '.', exist_ok=True)
    with open(audit_path, 'a', encoding='utf-8') as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + '\n')


def request(
    actor,
    method,
    path,
    body=None,
    token_provider=None,
    host=DEFAULT_HOST,
    policy_path=None,
    audit_path=None,
    timeout=30,
):
    """Appel REST GitHub audite. body = objet JSON ou None. Retour = (statut, donnees)."""
    method = method.upper()
    policy = load_policy(policy_path)
    audit_path = audit_path or DEFAULT_AUDIT_PATH
    try:
        check_permission(policy, actor, method)
        url = api_url(host, path)
    except GhError as exc:
        audit(audit_path, actor, method, str(path)[:200], 'refused', error=str(exc))
        raise
    provider = token_provider or resolve_token
    token = provider()
    headers = {
        'Authorization': 'Bearer %s' % token,
        'Accept': 'application/vnd.github+json',
        'User-Agent': USER_AGENT,
        'X-GitHub-Api-Version': API_VERSION,
    }
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode('utf-8', 'replace')[:200]
        audit(audit_path, actor, method, path, exc.code, error=detail)
        raise GhError('HTTP %s sur %s %s : %s' % (exc.code, method, path, detail))
    except urllib.error.URLError as exc:
        audit(audit_path, actor, method, path, 0, error=str(exc.reason))
        raise GhError('Reseau indisponible pour %s %s : %s' % (method, path, exc.reason))
    payload = None
    if raw:
        try:
            payload = json.loads(raw.decode('utf-8'))
        except ValueError:
            payload = raw.decode('utf-8', 'replace')
    audit(audit_path, actor, method, path, status)
    return status, payload


def comment(actor, issue_number, repo, body_text, **kwargs):
    """Poster un commentaire d'issue (ecriture tracee)."""
    path = '/repos/%s/issues/%d/comments' % (repo_slug(repo), int(issue_number))
    return request(actor, 'POST', path, {'body': body_text}, **kwargs)


def create_issue(actor, repo, title, body_text, labels=None, **kwargs):
    """Ouvrir une issue (ecriture tracee)."""
    payload = {'title': title, 'body': body_text}
    if labels:
        payload['labels'] = list(labels)
    return request(actor, 'POST', '/repos/%s/issues' % repo_slug(repo), payload, **kwargs)


# ---------------------------------------------------------------- CLI

def _cmd_api(args):
    body = None
    if args.body_file:
        with open(args.body_file, 'r', encoding='utf-8') as handle:
            body = json.load(handle)
    elif args.body:
        body = json.loads(args.body)
    status, payload = request(args.actor, args.method, args.path, body)
    if payload is not None:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write('\n')
    return 0


def _cmd_comment(args):
    with open(args.body_file, 'r', encoding='utf-8') as handle:
        text = handle.read()
    status, payload = comment(args.actor, args.number, args.repo, text)
    url = payload.get('html_url') if isinstance(payload, dict) else None
    print('HTTP %s %s' % (status, url or ''))
    return 0


def _cmd_create_issue(args):
    with open(args.body_file, 'r', encoding='utf-8') as handle:
        text = handle.read()
    labels = [x for x in (args.labels or '').split(',') if x]
    status, payload = create_issue(args.actor, args.repo, args.title, text, labels)
    print('HTTP %s #%s %s' % (status, payload.get('number'), payload.get('html_url')))
    return 0


def _cmd_init(args):
    acteurs = [x.strip() for x in (args.write_actors or '').split(',') if x.strip()]
    voie = init_policy(args.actor, acteurs)
    print('Politique ecrite : %s (voie : %s)' % (DEFAULT_POLICY_PATH, voie))
    print('Acteurs en ecriture : %s' % (acteurs or '(aucun — lecture seule globale)'))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description='Service gh Reprise (#270)')
    parser.add_argument('--actor', required=True, help='harness appelant (audit + politique)')
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_api = sub.add_parser('api', help='appel REST generique')
    p_api.add_argument('method', choices=list(READ_METHODS) + list(WRITE_METHODS))
    p_api.add_argument('path', help='chemin API, ex. /repos/OWNER/REPO/issues/270')
    p_api.add_argument('--body', help='corps JSON litteral (ecriture)')
    p_api.add_argument('--body-file', help='fichier corps JSON (ecriture)')
    p_api.set_defaults(func=_cmd_api)
    p_com = sub.add_parser('comment', help='commenter une issue')
    p_com.add_argument('number', type=int)
    p_com.add_argument('--repo', required=True)
    p_com.add_argument('--body-file', required=True)
    p_com.set_defaults(func=_cmd_comment)
    p_new = sub.add_parser('create-issue', help='ouvrir une issue')
    p_new.add_argument('--repo', required=True)
    p_new.add_argument('--title', required=True)
    p_new.add_argument('--body-file', required=True)
    p_new.add_argument('--labels', default='')
    p_new.set_defaults(func=_cmd_create_issue)
    p_init = sub.add_parser('init', help='ecrire la politique (sans auto-elevation)')
    p_init.add_argument('--write-actors', default='')
    p_init.set_defaults(func=_cmd_init)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GhError as exc:
        print('Erreur : %s' % exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
