"""PR, revue croisee, fusion et suivi CI du service gh Reprise (#270, lot 3).

Decisions operateur du 28/11 : chaque route est un harness distinct ; une PR
d'auteur inconnu exige 2 revues ; le contournement se demande depuis Reprise
(CLI operateur, terminal, motif) et se trace ; la CI ne bloque pas, mais une CI
rouge apres fusion ouvre une issue (une seule par SHA).

Le harness d'un acteur vient toujours du registre des jetons (reprise_gh_actors),
jamais d'un argument. Les registres `gh_pulls`, `gh_reviews` et `gh_merges`
vivent dans `gh-reviews.sqlite` (0600, dossier 0700), a cote du registre des
acteurs : le pont MCP y ecrit sans toucher aux empreintes de jetons.

L'approbation GitHub native est inutilisable (compte partage) : la preuve de
revue est le registre Reprise, sur le head_sha exact lu par Reprise, et la
fusion envoie ce sha a GitHub (`PUT .../merge` avec `sha`), qui refuse si le
head a bouge depuis.

Convention du depot : compatible Python 3.9, zero dependance.
"""
import os
import re
import sqlite3
import stat
import time

import reprise_gh
import reprise_gh_actors

DEFAULT_REVIEWS_PATH = os.path.join(reprise_gh_actors.DEFAULT_STATE_DIR, 'gh-reviews.sqlite')
VERDICTS = ('approve', 'request_changes')
MERGE_METHODS = ('merge', 'squash', 'rebase')
MIN_OVERRIDE_REASON = 10
CI_LABEL = 'ci:red'
_SHA_RE = re.compile(r'^[0-9a-f]{40,64}$')
NONE_SWEEP_SECONDS = 7 * 24 * 3600   # CI `none` rebalayee 7 jours apres la fusion
REPORT_STALE_SECONDS = 10 * 60      # reservation failure_reporting reprise apres 10 min
_EXTRA_MERGE_COLUMNS = (('merged_at', 'REAL'), ('ci_reserved_at', 'REAL'), ('override_note', 'TEXT'))
_FAILED = ('failure', 'error', 'cancelled', 'timed_out')

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS gh_pulls (
        repo TEXT NOT NULL, number INTEGER NOT NULL, author_actor TEXT NOT NULL,
        author_harness TEXT NOT NULL, token_id TEXT, created_at REAL NOT NULL,
        PRIMARY KEY (repo, number))""",
    """CREATE TABLE IF NOT EXISTS gh_reviews (
        repo TEXT NOT NULL, number INTEGER NOT NULL, head_sha TEXT NOT NULL,
        reviewer_actor TEXT NOT NULL, reviewer_harness TEXT NOT NULL, token_id TEXT,
        verdict TEXT NOT NULL, ts REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS gh_merges (
        repo TEXT NOT NULL, number INTEGER NOT NULL, merge_sha TEXT NOT NULL,
        head_sha TEXT NOT NULL, actor TEXT NOT NULL, harness TEXT NOT NULL,
        override_reason TEXT, ci_state TEXT, ci_checked_at REAL, ci_issue_number INTEGER,
        merged_at REAL, ci_reserved_at REAL, override_note TEXT, PRIMARY KEY (repo, merge_sha))""",
)


def canonical_repo(repo, pull=None):
    """Cle de registre : full_name GitHub (base.repo, sinon racine) en minuscules, sinon repo.lower().

    GitHub ignore la casse d'OWNER/REPO : sans cette cle, `O/r` contournait les
    revues et l'auteur enregistres sous `o/r` (revue #270).
    """
    full_name = None
    if isinstance(pull, dict):
        base_repo = (pull.get('base') or {}).get('repo') if isinstance(pull.get('base'), dict) else None
        full_name = (base_repo or {}).get('full_name') if isinstance(base_repo, dict) else None
        full_name = full_name or pull.get('full_name')
    if isinstance(full_name, str) and reprise_gh._REPO_RE.match(full_name):
        return full_name.lower()
    return repo.lower()


def default_reviews_path(registry_path=None, reviews_path=None):
    """Regle unique, pont et CLI : --reviews, sinon frere de --registry, sinon defaut."""
    if reviews_path:
        return os.path.abspath(reviews_path)
    if registry_path:
        return os.path.join(os.path.dirname(os.path.abspath(registry_path)), 'gh-reviews.sqlite')
    return DEFAULT_REVIEWS_PATH


class ReviewStore(object):
    """Registres gh_pulls / gh_reviews / gh_merges (0600, dossier 0700)."""

    def __init__(self, path=None):
        self.path = os.path.abspath(path or DEFAULT_REVIEWS_PATH)
        try:
            reprise_gh_actors._private_dir(os.path.dirname(self.path))
        except reprise_gh_actors.ActorError as exc:
            raise reprise_gh.GhError(str(exc)) from None
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(descriptor)
        if stat.S_IMODE(os.stat(self.path).st_mode) & 0o077:
            raise reprise_gh.GhError('Le registre des revues gh doit etre prive (0600)')
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        for statement in SCHEMA:
            self.db.execute(statement)
        # Retrocompatible : registres crees avant ces colonnes.
        present = {row[1] for row in self.db.execute('PRAGMA table_info(gh_merges)')}
        for name, kind in _EXTRA_MERGE_COLUMNS:
            if name not in present:
                self.db.execute('ALTER TABLE gh_merges ADD COLUMN %s %s' % (name, kind))

    def close(self):
        self.db.close()

    def record_pull(self, repo, number, actor, harness, token_id, now=None):
        self.db.execute('INSERT OR REPLACE INTO gh_pulls VALUES (?,?,?,?,?,?)',
                        (repo, number, actor, harness, token_id, time.time() if now is None else now))

    def pull_author(self, repo, number):
        row = self.db.execute('SELECT author_actor, author_harness FROM gh_pulls WHERE repo=? AND number=?',
                              (repo, number)).fetchone()
        return {'actor': row[0], 'harness': row[1]} if row else None

    def record_review(self, repo, number, head_sha, actor, harness, token_id, verdict, now=None):
        self.db.execute('INSERT INTO gh_reviews VALUES (?,?,?,?,?,?,?,?)',
                        (repo, number, head_sha, actor, harness, token_id, verdict,
                         time.time() if now is None else now))

    def reviews(self, repo, number, head_sha):
        rows = self.db.execute('SELECT reviewer_actor, reviewer_harness, verdict FROM gh_reviews '
                               'WHERE repo=? AND number=? AND head_sha=? ORDER BY ts',
                               (repo, number, head_sha)).fetchall()
        return [{'actor': r[0], 'harness': r[1], 'verdict': r[2]} for r in rows]

    def record_merge(self, repo, number, merge_sha, head_sha, actor, harness, override_reason, now=None):
        self.db.execute('INSERT OR REPLACE INTO gh_merges (repo, number, merge_sha, head_sha, actor, harness, '
                        'override_reason, merged_at) VALUES (?,?,?,?,?,?,?,?)',
                        (repo, number, merge_sha, head_sha, actor, harness, override_reason,
                         time.time() if now is None else now))

    def merge_row(self, repo, merge_sha):
        row = self.db.execute('SELECT number, ci_state, ci_issue_number FROM gh_merges '
                              'WHERE repo=? AND merge_sha=?', (repo, merge_sha)).fetchone()
        return {'number': row[0], 'ci_state': row[1], 'ci_issue_number': row[2]} if row else None

    def set_ci(self, repo, merge_sha, state, issue_number=None, now=None):
        """Etat CI ; ne libere jamais une reservation failure_reporting sans issue."""
        self.db.execute("UPDATE gh_merges SET ci_state=?, ci_checked_at=?, "
                        "ci_issue_number=COALESCE(ci_issue_number, ?) WHERE repo=? AND merge_sha=? "
                        "AND (COALESCE(ci_state, '') != 'failure_reporting' OR ? IS NOT NULL)",
                        (state, time.time() if now is None else now, issue_number, repo, merge_sha,
                         issue_number))

    def reserve_report(self, repo, merge_sha, now=None):
        """Reservation atomique avant le POST de l'issue CI rouge ; False si deja prise."""
        now = time.time() if now is None else now
        cursor = self.db.execute(
            "UPDATE gh_merges SET ci_state='failure_reporting', ci_reserved_at=? "
            "WHERE repo=? AND merge_sha=? AND ci_issue_number IS NULL "
            "AND (COALESCE(ci_state, '') != 'failure_reporting' OR ci_reserved_at IS NULL "
            "OR ci_reserved_at < ?)", (now, repo, merge_sha, now - REPORT_STALE_SECONDS))
        return cursor.rowcount == 1

    def set_override_note(self, repo, merge_sha, note):
        self.db.execute('UPDATE gh_merges SET override_note=? WHERE repo=? AND merge_sha=?',
                        (note, repo, merge_sha))

    def ci_candidates(self, repo=None, now=None):
        """Fusions a reprendre : pending ; failure sans issue ; none de moins de 7 jours ;
        reservation failure_reporting perimee (plus de 10 minutes)."""
        now = time.time() if now is None else now
        query = ("SELECT repo, number, merge_sha FROM gh_merges WHERE (ci_state='pending' "
                 "OR (ci_state='failure' AND ci_issue_number IS NULL) "
                 "OR (ci_state='none' AND merged_at >= ?) "
                 "OR (ci_state='failure_reporting' AND ci_issue_number IS NULL "
                 "AND (ci_reserved_at IS NULL OR ci_reserved_at < ?)))")
        params = [now - NONE_SWEEP_SECONDS, now - REPORT_STALE_SECONDS]
        if repo is not None:
            query += ' AND repo=?'
            params.append(repo.lower())
        rows = self.db.execute(query + ' ORDER BY merged_at', params).fetchall()
        return [{'repo': r[0], 'number': r[1], 'merge_sha': r[2]} for r in rows]


def identity(actor, harness, auth, token_id):
    """Identite d'appel : le harness vient du registre des jetons, jamais d'un argument."""
    return {'actor': actor, 'harness': harness, 'auth': auth, 'token_id': token_id}


def _request(who, method, path, body=None, op=None, repo=None, **kwargs):
    return reprise_gh.request(who['actor'], method, path, body, op=op, repo=repo,
                              auth=who['auth'], token_id=who['token_id'], **kwargs)


def _audit(who, method, path, status, error=None, kwargs=None, **fields):
    kwargs = kwargs or {}
    extra = dict(kwargs.get('audit_extra') or {})
    extra.update({'auth': who['auth'], 'token_id': who['token_id']})
    extra.update(fields)
    reprise_gh.audit(kwargs.get('audit_path') or reprise_gh.DEFAULT_AUDIT_PATH, who['actor'], method,
                     path, status, error=error, extra=extra)


def _number(value):
    if type(value) is not int or value < 1:
        raise reprise_gh.GhError('number : entier positif attendu')
    return value


def _open_pull(who, repo, number, **kwargs):
    """Lit la PR et renvoie son head_sha ; refuse une PR fermee ou fusionnee."""
    _status, pull = _request(who, 'GET', '/repos/%s/pulls/%d' % (repo, number), op='read', **kwargs)
    if not isinstance(pull, dict):
        raise reprise_gh.GhError('PR #%d : reponse GitHub inattendue' % number)
    if pull.get('merged') or pull.get('state') != 'open':
        raise reprise_gh.GhError('PR #%d : fermee ou deja fusionnee' % number)
    head = (pull.get('head') or {}).get('sha')
    if not isinstance(head, str) or not _SHA_RE.match(head):
        raise reprise_gh.GhError('PR #%d : head_sha illisible' % number)
    return head, pull


def create_pull(who, store, repo, head, base, title, body, **kwargs):
    """POST /repos/{r}/pulls (op pull.create), puis enregistre l'auteur et son harness."""
    repo = reprise_gh.repo_slug(repo)
    payload = {'title': title, 'head': head, 'base': base, 'body': body}
    status, pull = _request(who, 'POST', '/repos/%s/pulls' % repo, payload, op='pull.create',
                            repo=repo, **kwargs)
    number = pull.get('number') if isinstance(pull, dict) else None
    if type(number) is int:
        store.record_pull(canonical_repo(repo, pull), number, who['actor'], who['harness'], who['token_id'])
    return {'status': status, 'number': number,
            'html_url': pull.get('html_url') if isinstance(pull, dict) else None}


def review(who, store, repo, number, verdict, body, **kwargs):
    """Revue Reprise : head_sha lu par Reprise, commentaire balise, puis registre."""
    repo = reprise_gh.repo_slug(repo)
    number = _number(number)
    if verdict not in VERDICTS:
        raise reprise_gh.GhError('verdict : approve ou request_changes')
    head_sha, pull = _open_pull(who, repo, number, **kwargs)
    key = canonical_repo(repo, pull)
    marker = '<!-- reprise-review: %s %s %s %s -->' % (who['actor'], who['harness'], verdict, head_sha)
    text = '%s\n\n**Revue Reprise** (%s, harness %s) : `%s` sur `%s`.\n\n%s' % (
        marker, who['actor'], who['harness'], verdict, head_sha[:12], body)
    status, payload = _request(who, 'POST', '/repos/%s/issues/%d/comments' % (repo, number),
                               {'body': text}, op='pull.review', repo=repo, **kwargs)
    store.record_review(key, number, head_sha, who['actor'], who['harness'], who['token_id'], verdict)
    return {'status': status, 'head_sha': head_sha, 'verdict': verdict,
            'html_url': payload.get('html_url') if isinstance(payload, dict) else None}


def evaluate(params, author, reviews):
    """Regle de fusion sur les revues du head courant.

    approve comptes par harness distincts, hors harness de l'auteur si
    distinct_from_author ; tout request_changes bloque ; minimum
    min_reviewers (auteur connu) ou min_reviewers_unknown_author.
    """
    excluded = set()
    if author and params.get('distinct_from_author', True):
        excluded.add(author['harness'])
    blockers = sorted({r['harness'] for r in reviews if r['verdict'] == 'request_changes'})
    approved = sorted({r['harness'] for r in reviews if r['verdict'] == 'approve'} - excluded)
    required = params['min_reviewers'] if author else params['min_reviewers_unknown_author']
    return {'satisfied': not blockers and len(approved) >= required, 'required': required,
            'approved_harnesses': approved, 'excluded_harnesses': sorted(excluded),
            'blocking_harnesses': blockers, 'missing': max(0, required - len(approved)),
            'author_known': bool(author)}


def _refusal_message(number, head_sha, verdict):
    parts = ['Fusion de #%d refusee (review_required) sur le head %s.' % (number, head_sha[:12])]
    if verdict['blocking_harnesses']:
        parts.append('request_changes en cours depuis : %s ; une nouvelle revue exige un nouveau head.'
                     % ', '.join(verdict['blocking_harnesses']))
    if verdict['missing']:
        parts.append('%d revue(s) approve manquante(s) sur %d (auteur %s)'
                     % (verdict['missing'], verdict['required'],
                        'connu' if verdict['author_known'] else 'inconnu : 2 harness distincts'))
        if verdict['excluded_harnesses']:
            parts.append('harness exclus (auteur) : %s' % ', '.join(verdict['excluded_harnesses']))
        if verdict['approved_harnesses']:
            parts.append('deja comptes : %s' % ', '.join(verdict['approved_harnesses']))
    parts.append('Faire relire par une autre route : reprise_gh_review depuis un autre harness, '
                 'ou reprise_delegate en mode revue.')
    return ' '.join(parts)


def merge(who, store, repo, number, method='merge', override_reason=None, **kwargs):
    """Fusion sous regle de revue ; contournement operateur seulement (CLI, terminal, motif).

    Apres fusion, lecture CI non bloquante (ci_check) ; son echec est audite.
    """
    repo = reprise_gh.repo_slug(repo)
    number = _number(number)
    if method not in MERGE_METHODS:
        raise reprise_gh.GhError('method : merge, squash ou rebase')
    path = '/repos/%s/pulls/%d/merge' % (repo, number)
    policy = reprise_gh.load_policy(kwargs.get('policy_path'))
    decision, reason, rule = reprise_gh.decide(policy, who['actor'], 'pull.merge', repo, who['auth'])
    if decision == 'deny':
        _audit(who, 'PUT', path, 'refused', kwargs=kwargs, op='pull.merge', decision=decision,
               reason=reason, rule=rule)
        raise reprise_gh.GhError('Fusion refusee pour « %s » (%s)' % (who['actor'], reason))
    if override_reason is not None:
        reason_text = override_reason.strip() if isinstance(override_reason, str) else ''
        if who['actor'] != reprise_gh.OPERATOR or who['auth'] != 'tty':
            raise reprise_gh.GhError('Contournement : operateur au terminal seulement')
        if len(reason_text) < MIN_OVERRIDE_REASON:
            raise reprise_gh.GhError('Contournement : --reason de %d caracteres au moins' % MIN_OVERRIDE_REASON)
        override_reason = reason_text
    head_sha, pull = _open_pull(who, repo, number, **kwargs)
    key = canonical_repo(repo, pull)
    grant, verdict = None, None
    if decision == 'review_required' and override_reason is None:
        verdict = evaluate(reprise_gh.review_params(policy, rule), store.pull_author(key, number),
                           store.reviews(key, number, head_sha))
        if not verdict['satisfied']:
            _audit(who, 'PUT', path, 'refused', kwargs=kwargs, op='pull.merge', decision=decision,
                   reason='reviews_missing', rule=rule, head_sha=head_sha, missing=verdict['missing'],
                   blocking=verdict['blocking_harnesses'])
            raise reprise_gh.GhError(_refusal_message(number, head_sha, verdict))
        grant = 'review_satisfied'
    elif decision == 'review_required':
        grant = 'operator_override'
    merge_kwargs = dict(kwargs)
    if override_reason is not None:
        merge_kwargs['audit_extra'] = dict(kwargs.get('audit_extra') or {}, override_reason=override_reason[:500])
    status, payload = _request(who, 'PUT', path, {'sha': head_sha, 'merge_method': method},
                               op='pull.merge', repo=repo, review_grant=grant, **merge_kwargs)
    merge_sha = payload.get('sha') if isinstance(payload, dict) else None
    if not (isinstance(payload, dict) and payload.get('merged') and isinstance(merge_sha, str)
            and _SHA_RE.match(merge_sha)):
        raise reprise_gh.GhError('Fusion de #%d : reponse GitHub sans sha de fusion' % number)
    store.record_merge(key, number, merge_sha, head_sha, who['actor'], who['harness'], override_reason)
    if override_reason is not None:
        # Commentaire poste apres la fusion reussie : jamais de faux contournement si le PUT echoue.
        note = ('<!-- reprise-override: %s %s %s -->\n\n**Fusion sans revue requise** par l\'operateur '
                '(contournement trace par Reprise) sur `%s`.\n\nMotif : %s'
                % (who['actor'], head_sha, method, head_sha[:12], override_reason))
        try:
            _request(who, 'POST', '/repos/%s/issues/%d/comments' % (repo, number), {'body': note},
                     op='pull.merge', repo=repo, review_grant=grant, **merge_kwargs)
            store.set_override_note(key, merge_sha, 'posted')
        except Exception as exc:
            message = str(exc) if isinstance(exc, reprise_gh.GhError) else type(exc).__name__
            store.set_override_note(key, merge_sha, 'failed: %s' % message[:200])
            _audit(who, 'POST', '/repos/%s/issues/%d/comments' % (repo, number), 'override_comment_failed',
                   error=message, kwargs=kwargs, op='pull.merge', merge_sha=merge_sha)
    result = {'status': status, 'merged': True, 'merge_sha': merge_sha, 'head_sha': head_sha,
              'override': override_reason is not None, 'reviewed_by': (verdict or {}).get('approved_harnesses')}
    result.update(_safe_ci(who, store, key, number, merge_sha, **kwargs))
    return result


def _safe_ci(who, store, repo, number, merge_sha, **kwargs):
    """CI non bloquante : un echec de lecture est audite, la fusion reste acquise."""
    try:
        state, issue_number = ci_check(who, store, repo, number, merge_sha, **kwargs)
        return {'ci_state': state, 'ci_issue_number': issue_number}
    except Exception as exc:
        message = str(exc) if isinstance(exc, reprise_gh.GhError) else type(exc).__name__
        try:
            store.set_ci(repo, merge_sha, 'pending')
        except sqlite3.Error:
            pass
        _audit(who, 'CI', '/repos/%s/commits/%s' % (repo, merge_sha), 'ci_read_failed', error=message,
               kwargs=kwargs, op='ci.read', number=number)
        return {'ci_state': 'unknown', 'ci_issue_number': None, 'ci_error': message[:200]}


def ci_state(who, repo, sha, **kwargs):
    """(etat, echecs) ; etat ∈ success | failure | pending | none.

    Combine les statuts (liste `statuses`, l'etat global vaut pending sans
    statut) et les check-runs. failure si un statut ou un check-run est
    failure, error, cancelled ou timed_out.
    """
    _s, combined = _request(who, 'GET', '/repos/%s/commits/%s/status' % (repo, sha), op='read', **kwargs)
    _c, runs = _request(who, 'GET', '/repos/%s/commits/%s/check-runs?per_page=100' % (repo, sha),
                        op='read', **kwargs)
    statuses = (combined or {}).get('statuses') or [] if isinstance(combined, dict) else []
    check_runs = (runs or {}).get('check_runs') or [] if isinstance(runs, dict) else []
    failed, pending = [], False
    for item in statuses:
        state = item.get('state')
        if state in _FAILED:
            failed.append({'name': item.get('context'), 'url': item.get('target_url'), 'state': state})
        elif state != 'success':
            pending = True
    for run in check_runs:
        if run.get('status') != 'completed':
            pending = True
        elif run.get('conclusion') in _FAILED:
            failed.append({'name': run.get('name'), 'url': run.get('html_url'), 'state': run.get('conclusion')})
    if failed:
        return 'failure', failed
    if pending:
        return 'pending', []
    return ('success' if statuses or check_runs else 'none'), []


def _label_exists(who, repo, **kwargs):
    try:
        _request(who, 'GET', '/repos/%s/labels/%s' % (repo, CI_LABEL), op='read', **kwargs)
        return True
    except reprise_gh.GhError:
        return False


def _marker(repo, number, sha):
    return '<!-- reprise-ci-red: %s#%d %s -->' % (repo, number, sha)


def _existing_ci_issue(who, repo, sha, **kwargs):
    """Issue ouverte portant deja le marqueur reprise-ci-red de ce sha (reponse perdue, course)."""
    _status, issues = _request(who, 'GET', '/repos/%s/issues?state=open&per_page=100' % repo,
                               op='read', **kwargs)
    for issue in issues if isinstance(issues, list) else []:
        body = issue.get('body') if isinstance(issue, dict) else None
        if isinstance(body, str) and 'reprise-ci-red:' in body and sha in body:
            number = issue.get('number')
            if type(number) is int:
                return number
    return None


def ci_check(who, store, repo, number, merge_sha, **kwargs):
    """Lit la CI du commit fusionne ; failure = une issue par SHA, puis enregistre.

    Idempotence : reservation atomique `failure_reporting` avant le POST, puis
    recherche du marqueur dans les issues ouvertes avant toute creation.
    """
    state, failed = ci_state(who, repo, merge_sha, **kwargs)
    row = store.merge_row(repo, merge_sha) or {}
    issue_number = row.get('ci_issue_number')
    if state != 'failure' or issue_number is not None:
        store.set_ci(repo, merge_sha, state, issue_number)
        return state, issue_number
    if not store.reserve_report(repo, merge_sha):
        return 'failure_reporting', None
    issue_number = _existing_ci_issue(who, repo, merge_sha, **kwargs)
    if issue_number is None:
        lines = [_marker(repo, number, merge_sha), '',
                 'CI rouge apres la fusion par Reprise.', '',
                 '- PR : https://github.com/%s/pull/%d' % (repo, number),
                 '- Commit : https://github.com/%s/commit/%s' % (repo, merge_sha), '', 'En echec :']
        lines += ['- %s (%s) %s' % (f['name'], f['state'], f['url'] or '') for f in failed]
        labels = [CI_LABEL] if _label_exists(who, repo, **kwargs) else []
        _status, payload = reprise_gh.create_issue(
            who['actor'], repo, 'CI rouge après fusion de #%d (%s)' % (number, merge_sha[:7]),
            '\n'.join(lines), labels, auth=who['auth'], token_id=who['token_id'], **kwargs)
        issue_number = payload.get('number') if isinstance(payload, dict) else None
    store.set_ci(repo, merge_sha, 'failure', issue_number)
    return 'failure', issue_number


def ci_sweep(who, store, repo=None, **kwargs):
    """Repasse sur les fusions pending (et rouges sans issue) ; op ci.sweep."""
    if repo is not None:
        repo = reprise_gh.repo_slug(repo).lower()
    policy = reprise_gh.load_policy(kwargs.get('policy_path'))
    decision, reason, rule = reprise_gh.decide(policy, who['actor'], 'ci.sweep', repo, who['auth'])
    if decision != 'allow':
        _audit(who, 'SWEEP', repo or '*', 'refused', kwargs=kwargs, op='ci.sweep', decision=decision,
               reason=reason, rule=rule)
        raise reprise_gh.GhError('ci-sweep refuse pour « %s » (%s)' % (who['actor'], reason))
    results = []
    for row in store.ci_candidates(repo):
        result = _safe_ci(who, store, row['repo'], row['number'], row['merge_sha'], **kwargs)
        results.append(dict(row, **result))
    return results
