"""Service gh Reprise : acces GitHub centralise et audite pour les harness (#270).

Directive operateur (26 novembre 2026) : soit centraliser tout, soit delivrer un
service « gh » a tous les harness dans leur workspace. Les deux se combinent :

- coeur central : ce module. Un seul point d'authentification GitHub (token
  resolu via `git credential fill`, jamais logge), une politique v2 par
  operation (gh-policy.json, ecrite par l'operateur seulement) et un journal
  d'audit JSONL commun (horodatage, acteur, operation, decision, statut HTTP —
  sans token, ni secret d'acteur, ni corps de requete).
- livraison aux harness : le pont MCP stdio `reprise_gh_mcp.py`, lance par
  l'integration avec `--actor` et `--token-file` (jeton d'acteur emis par
  `reprise gh actors issue`, registre reprise_gh_actors). Sans jeton valide,
  le pont reste en lecture seule, meme avec `--allow-write`.
- CLI operateur : sans `--token-file`, la CLI agit en acteur reserve
  `operator` ; une ecriture `operator` exige un terminal (frein contre l'usage
  involontaire, pas une frontiere). Avec `--token-file`, l'acteur vient du
  registre et un `--actor` contradictoire est refuse.

Le jeton d'acteur sert a l'attribution, a la revocation et a la politique par
acteur ; ce n'est pas une frontiere de securite : tous les agents tournent sous
le meme compte Unix et peuvent lire jetons, registre et politique.

Politique v2 : regles par operation (`read`, `issue.comment`, `issue.create`,
`pull.review`, `pull.merge`, `api.raw`), filtre `actors` facultatif, veto
nominatif `deny_actors`, priorite deny > review_required > allow ; operation
inconnue ou `default` absent = deny. Une politique v1 (`write_actors`) est
traduite en memoire en v2 par defaut, sans reecriture du fichier ; write_actors
est ignore (avertissement unique). `init` est retire : la politique s'ecrit
directement par l'operateur.

Usage CLI :
    reprise gh api GET /repos/OWNER/REPO/issues/270
    reprise gh comment 270 --repo OWNER/REPO --body-file commentaire.md
    reprise gh actors issue --actor autoclaw --harness autoclaw --out /chemin/prive/jeton
    reprise gh actors revoke --actor autoclaw
    reprise gh actors list

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

import reprise_gh_actors

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
            # Jamais de prompt interactif : sans identifiant enregistre, echec net.
            env=dict(os.environ, GIT_TERMINAL_PROMPT='0'),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GhError('git credential fill indisponible : %s' % exc)
    if proc.returncode != 0:
        raise GhError('git credential fill a echoue (code %s)' % proc.returncode)
    return proc.stdout


OPERATOR = reprise_gh_actors.OPERATOR
OPERATIONS = ('read', 'issue.comment', 'issue.create', 'pull.review', 'pull.merge', 'api.raw')
DECISIONS = ('allow', 'review_required', 'deny')
_OP_PATTERNS = set(OPERATIONS) | {'issue.*', 'pull.*', 'api.*', '*'}
_RULE_KEYS = {'id', 'op', 'decision', 'actors'}
_POLICY_KEYS = {'version', 'enabled', 'default', 'deny_actors', 'rules'}
DEFAULT_RULES = (
    {'id': 'default-read', 'op': 'read', 'decision': 'allow'},
    {'id': 'default-issue', 'op': 'issue.*', 'decision': 'allow'},
    {'id': 'default-pull-review', 'op': 'pull.review', 'decision': 'allow'},
    {'id': 'default-pull-merge', 'op': 'pull.merge', 'decision': 'review_required'},
    {'id': 'default-api-raw', 'op': 'api.raw', 'decision': 'allow', 'actors': [OPERATOR]},
)
_V1_WARNED = []


def default_policy():
    """Politique v2 par defaut (fichier absent) : lecture pour tous, issue.* et
    pull.review allow, pull.merge review_required, api.raw pour operator seul."""
    return {'version': 2, 'enabled': True, 'default': 'deny', 'deny_actors': [],
            'rules': [dict(rule) for rule in DEFAULT_RULES]}


def _from_v1(policy):
    """v1 -> v2 en memoire ; le fichier n'est jamais reecrit."""
    if 'write_actors' in policy and not _V1_WARNED:
        _V1_WARNED.append(True)
        sys.stderr.write('reprise gh : politique v1 detectee, write_actors ignore ; '
                         'ecrire gh-policy.json au format v2 (voir HARNESS-INTEGRATION.md).\n')
    migrated = default_policy()
    migrated['enabled'] = bool(policy.get('enabled', True))
    return migrated


def _names(value, label):
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise GhError('Politique invalide : %s doit etre une liste de noms' % label)
    return list(value)


def validate_policy(policy):
    """Politique v2 normalisee ; GhError si la structure est invalide (fail-closed)."""
    if not isinstance(policy, dict):
        raise GhError('Politique invalide : objet JSON attendu')
    if policy.get('version', 1) == 1:
        # v1 = {enabled, write_actors} seulement : un v2 sans `version` est refuse,
        # jamais remplace silencieusement par les defauts (veto perdu).
        if set(policy) - {'version', 'enabled', 'write_actors'}:
            raise GhError('Politique invalide : "version": 2 requis pour rules/deny_actors/default')
        return _from_v1(policy)
    if policy.get('version') != 2 or set(policy) - _POLICY_KEYS:
        raise GhError('Politique invalide : version 2 et cles %s attendues' % sorted(_POLICY_KEYS))
    if not isinstance(policy.get('enabled', True), bool):
        raise GhError('Politique invalide : enabled booleen attendu')
    rules = policy.get('rules', [])
    if not isinstance(rules, list):
        raise GhError('Politique invalide : rules doit etre une liste')
    normalized = []
    for index, rule in enumerate(rules):
        if (not isinstance(rule, dict) or set(rule) - _RULE_KEYS
                or rule.get('op') not in _OP_PATTERNS or rule.get('decision') not in DECISIONS):
            raise GhError('Politique invalide : regle %d (op, decision, actors, id)' % index)
        entry = {'id': str(rule.get('id') or 'rule-%d' % index), 'op': rule['op'],
                 'decision': rule['decision']}
        if 'actors' in rule:
            entry['actors'] = _names(rule['actors'], 'actors')
        normalized.append(entry)
    return {'version': 2, 'enabled': policy.get('enabled', True),
            'default': policy.get('default'),
            'deny_actors': _names(policy.get('deny_actors', []), 'deny_actors'),
            'rules': normalized}


def load_policy(path=None):
    path = path or DEFAULT_POLICY_PATH
    if not os.path.exists(path):
        return default_policy()
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            policy = json.load(handle)
    except (OSError, ValueError):
        raise GhError('Politique illisible (%s) : refus de tout appel' % path) from None
    return validate_policy(policy)


def save_policy(policy, path=None):
    """Ecriture de politique pour l'operateur et les tests ; aucune CLI ne l'appelle."""
    path = path or DEFAULT_POLICY_PATH
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        json.dump(policy, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    os.replace(tmp, path)


def _op_matches(pattern, op):
    return pattern == op or pattern == '*' or (pattern.endswith('.*') and op.startswith(pattern[:-1]))


def decide(policy, actor, op, repo, auth):
    """(decision, reason, rule_id) ; decision ∈ allow | review_required | deny.

    auth : pour un acteur a jeton, ok | missing | invalid | revoked ; pour
    `operator` (CLI), tty | no_tty. `repo` est reserve (pas de restriction par
    depot dans ce lot). Toute ecriture d'un acteur sans jeton valide est refusee.
    """
    if not policy.get('enabled', False):
        return 'deny', 'disabled', None
    if op not in OPERATIONS:
        return 'deny', 'unknown_op', None
    actor = (actor or '').strip()
    if not actor:
        return 'deny', 'actor_required', None
    if actor in policy.get('deny_actors', []):
        return 'deny', 'deny_actor', 'deny_actors'
    if op != 'read':
        if actor == OPERATOR and auth != 'tty':
            return 'deny', 'operator_requires_tty', None
        if actor != OPERATOR and auth != 'ok':
            return 'deny', 'auth_%s' % (auth or 'missing'), None
    matched = {}
    for rule in policy.get('rules', []):
        if _op_matches(rule['op'], op) and ('actors' not in rule or actor in rule['actors']):
            matched.setdefault(rule['decision'], rule['id'])
    for decision in DECISIONS[::-1]:
        if decision in matched:
            return decision, 'rule', matched[decision]
    default = policy.get('default')
    if default in DECISIONS:
        return default, 'default', None
    return 'deny', 'no_default', None


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
        # Les champs du coeur (acteur, statut...) priment sur les extras.
        entry.update({k: v for k, v in extra.items() if k not in entry})
    os.makedirs(os.path.dirname(audit_path) or '.', mode=0o700, exist_ok=True)
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
    audit_extra=None,
    op=None,
    auth=None,
    token_id=None,
    repo=None,
):
    """Appel REST GitHub audite. body = objet JSON ou None. Retour = (statut, donnees).

    op : operation de politique (defaut : `read` pour GET/HEAD, sinon `api.raw`).
    auth/token_id : etat du jeton d'acteur (voir decide) ; token_id seul est audite.
    audit_extra : champs ajoutes a chaque ligne d'audit (ex. {'channel': 'mcp'}).
    """
    method = method.upper()
    op = op or ('read' if method in READ_METHODS else 'api.raw')
    audit_path = audit_path or DEFAULT_AUDIT_PATH
    extra = dict(audit_extra or {})
    extra.update({'op': op, 'auth': auth, 'token_id': token_id})
    try:
        if op == 'read' and method not in READ_METHODS:
            extra.update({'decision': 'deny', 'reason': 'read_requires_get', 'rule': None})
            raise GhError('Operation read : GET ou HEAD uniquement')
        decision, reason, rule = decide(load_policy(policy_path), actor, op, repo, auth)
        extra.update({'decision': decision, 'reason': reason, 'rule': rule})
        if decision == 'review_required':
            raise GhError("Operation %s : revue requise (non disponible dans ce lot)" % op)
        if decision != 'allow':
            raise GhError("Operation %s refusee pour l'acteur « %s » (%s)" % (op, actor, reason))
        try:
            url = api_url(host, path)
        except GhError:
            extra.update({'decision': 'deny', 'reason': 'invalid_path'})
            raise
    except GhError as exc:
        extra.setdefault('decision', 'deny')
        extra.setdefault('reason', 'policy_unavailable')
        audit(audit_path, actor, method, str(path)[:200], 'refused', error=str(exc), extra=extra)
        raise
    provider = token_provider or resolve_token
    try:
        token = provider()
    except GhError as exc:
        # Autorise par la politique mais jamais envoye : le journal le dit.
        audit(audit_path, actor, method, path, 'unsent', error=str(exc), extra=extra)
        raise
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
        audit(audit_path, actor, method, path, exc.code, error=detail, extra=extra)
        raise GhError('HTTP %s sur %s %s : %s' % (exc.code, method, path, detail))
    except urllib.error.URLError as exc:
        audit(audit_path, actor, method, path, 0, error=str(exc.reason), extra=extra)
        raise GhError('Reseau indisponible pour %s %s : %s' % (method, path, exc.reason))
    payload = None
    if raw:
        try:
            payload = json.loads(raw.decode('utf-8'))
        except ValueError:
            payload = raw.decode('utf-8', 'replace')
    audit(audit_path, actor, method, path, status, extra=extra)
    return status, payload


def comment(actor, issue_number, repo, body_text, **kwargs):
    """Poster un commentaire d'issue (operation issue.comment, tracee)."""
    repo = repo_slug(repo)
    path = '/repos/%s/issues/%d/comments' % (repo, int(issue_number))
    return request(actor, 'POST', path, {'body': body_text}, op='issue.comment', repo=repo, **kwargs)


def create_issue(actor, repo, title, body_text, labels=None, **kwargs):
    """Ouvrir une issue (operation issue.create, tracee)."""
    repo = repo_slug(repo)
    payload = {'title': title, 'body': body_text}
    if labels:
        payload['labels'] = list(labels)
    return request(actor, 'POST', '/repos/%s/issues' % repo, payload, op='issue.create',
                   repo=repo, **kwargs)


# ---------------------------------------------------------------- CLI

CLI_AUDIT = {'channel': 'cli'}


def _isatty():
    return sys.stdin.isatty() and sys.stdout.isatty()


def cli_identity(actor, token_file=None, registry_path=None):
    """(acteur, auth, token_id) de la CLI.

    Sans --token-file : `operator` (ou l'acteur explicite, alors sans jeton :
    lecture seule) ; auth tty|no_tty pour operator. Avec --token-file : acteur du
    registre ; jeton absent, invalide ou revoque = refus ; --actor contradictoire
    = refus.
    """
    if token_file is None:
        actor = actor or OPERATOR
        if actor == OPERATOR:
            return actor, ('tty' if _isatty() else 'no_tty'), None
        return actor, 'missing', None
    try:
        token = reprise_gh_actors.read_token_file(token_file)
    except reprise_gh_actors.ActorError as exc:
        raise GhError(str(exc)) from None
    auth, owner, token_id, _harness = reprise_gh_actors.verify(token, registry_path)
    if auth != 'ok':
        raise GhError("Jeton d'acteur refuse (auth : %s)" % auth)
    if actor and actor != owner:
        raise GhError("--actor « %s » contredit l'acteur du jeton" % actor)
    return owner, auth, token_id


def _identity_kwargs(args):
    return {'auth': args.auth, 'token_id': args.token_id, 'audit_extra': CLI_AUDIT}


def _cmd_api(args):
    body = None
    if args.body_file:
        with open(args.body_file, 'r', encoding='utf-8') as handle:
            body = json.load(handle)
    elif args.body:
        body = json.loads(args.body)
    status, payload = request(args.actor, args.method, args.path, body, **_identity_kwargs(args))
    if payload is not None:
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write('\n')
    return 0


def _cmd_comment(args):
    with open(args.body_file, 'r', encoding='utf-8') as handle:
        text = handle.read()
    status, payload = comment(args.actor, args.number, args.repo, text, **_identity_kwargs(args))
    url = payload.get('html_url') if isinstance(payload, dict) else None
    print('HTTP %s %s' % (status, url or ''))
    return 0


def _cmd_create_issue(args):
    with open(args.body_file, 'r', encoding='utf-8') as handle:
        text = handle.read()
    labels = [x for x in (args.labels or '').split(',') if x]
    status, payload = create_issue(args.actor, args.repo, args.title, text, labels,
                                   **_identity_kwargs(args))
    print('HTTP %s #%s %s' % (status, payload.get('number'), payload.get('html_url')))
    return 0


def _cmd_init(args):
    raise GhError("init est retire (#270 lot 2) : l'operateur ecrit directement "
                  "gh-policy.json au format v2 (voir HARNESS-INTEGRATION.md)")


def _operator_only(args, what):
    if args.token_file is not None or args.actor != OPERATOR:
        raise GhError('%s : reserve a l\'operateur (sans --token-file)' % what)
    if args.auth != 'tty':
        raise GhError('%s : terminal requis (operator_requires_tty)' % what)


def _cmd_actors_issue(args):
    _operator_only(args, 'actors issue')
    registry = reprise_gh_actors.ActorRegistry(reprise_gh_actors.DEFAULT_ACTORS_PATH)
    try:
        result = registry.issue(args.target, args.harness, args.out, replace=args.replace)
    finally:
        registry.close()
    audit(DEFAULT_AUDIT_PATH, OPERATOR, 'ACTORS_ISSUE', args.target, 'ok',
          extra=dict(CLI_AUDIT, token_id=result['token_id'], harness=result['harness'],
                     revoked_previous=result['revoked_previous']))
    print('Jeton emis pour %s (token_id %s) : %s' % (result['actor'], result['token_id'],
                                                    result['token_file']))
    if result['revoked_previous']:
        print('Jetons precedents revoques : %d' % result['revoked_previous'])
    return 0


def _cmd_actors_revoke(args):
    _operator_only(args, 'actors revoke')
    registry = reprise_gh_actors.ActorRegistry(reprise_gh_actors.DEFAULT_ACTORS_PATH)
    try:
        count = registry.revoke(args.target, args.revoke_token_id)
    finally:
        registry.close()
    audit(DEFAULT_AUDIT_PATH, OPERATOR, 'ACTORS_REVOKE', args.target, 'ok',
          extra=dict(CLI_AUDIT, token_id=args.revoke_token_id, revoked=count))
    print('Jetons revoques pour %s : %d' % (args.target, count))
    return 0


def _cmd_actors_list(args):
    if args.token_file is not None or args.actor != OPERATOR:
        raise GhError("actors list : reserve a l'operateur (sans --token-file)")
    registry = reprise_gh_actors.ActorRegistry(reprise_gh_actors.DEFAULT_ACTORS_PATH)
    try:
        rows = registry.list()
    finally:
        registry.close()
    json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')
    return 0


def _actor_name(value):
    if not reprise_gh_actors.NAME_RE.match(value or ''):
        raise argparse.ArgumentTypeError('acteur non vide attendu, motif [A-Za-z0-9._-]+')
    return value


def build_parser():
    parser = argparse.ArgumentParser(description='Service gh Reprise (#270)')
    parser.add_argument('--actor', type=_actor_name, default=None,
                        help="acteur (defaut : operator ; avec --token-file, controle de coherence)")
    parser.add_argument('--token-file', help="fichier de jeton d'acteur (chemin absolu, 0600)")
    sub = parser.add_subparsers(dest='cmd', required=True)
    p_api = sub.add_parser('api', help='appel REST generique (ecriture = api.raw)')
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
    p_init = sub.add_parser('init', help='retire : ecrire gh-policy.json v2 directement')
    p_init.add_argument('--write-actors', default='')
    p_init.set_defaults(func=_cmd_init)
    p_actors = sub.add_parser('actors', help="registre des jetons d'acteur (operateur)")
    actors = p_actors.add_subparsers(dest='actors_cmd', required=True)
    p_issue = actors.add_parser('issue', help='emettre un jeton (fichier 0600)')
    p_issue.add_argument('--actor', dest='target', type=_actor_name, required=True)
    p_issue.add_argument('--harness', type=_actor_name, required=True)
    p_issue.add_argument('--out', required=True, help='chemin absolu, dossier 0700 hors depot')
    p_issue.add_argument('--replace', action='store_true', help="revoque les autres jetons de l'acteur")
    p_issue.set_defaults(func=_cmd_actors_issue)
    p_revoke = actors.add_parser('revoke', help='revoquer les jetons d\'un acteur')
    p_revoke.add_argument('--actor', dest='target', type=_actor_name, required=True)
    p_revoke.add_argument('--token-id', dest='revoke_token_id')
    p_revoke.set_defaults(func=_cmd_actors_revoke)
    p_list = actors.add_parser('list', help='lister les jetons (sans secret)')
    p_list.set_defaults(func=_cmd_actors_list)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.cmd == 'init':
            return args.func(args)
        args.actor, args.auth, args.token_id = cli_identity(args.actor, args.token_file)
        return args.func(args)
    except (GhError, reprise_gh_actors.ActorError) as exc:
        print('Erreur : %s' % exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
