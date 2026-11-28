# -*- coding: utf-8 -*-
"""Tests du lot 3 de #270 : PR, revue croisee, fusion, contournement, CI rouge -> issue.

Sans reseau : urlopen est remplace par un GitHub factice ; tout l'etat vit en tempdir.
"""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import reprise_gh
import reprise_gh_actors
import reprise_gh_mcp
import reprise_gh_pulls

REPO_ROOT = Path(__file__).resolve().parents[2]
GH_TOKEN = 'ghp_FAUX_JETON_LOT3_jamais_expose'
SHA_A = 'a' * 40
SHA_B = 'b' * 40
MERGED = 'c' * 40


class _Response(object):
    def __init__(self, payload, status=200):
        self.status = status
        self._raw = json.dumps(payload).encode('utf-8')

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeGitHub(object):
    """API GitHub minimale : PR, commentaires, fusion, statuts, check-runs, labels, issues."""

    def __init__(self):
        self.head = SHA_A
        self.calls = []
        self.statuses = []
        self.check_runs = []
        self.label = True
        self.status_error = None
        self.issues = []
        self.merged = False
        self.case_insensitive = False
        self.fail_override_comment = False

    def __call__(self, req, timeout):
        parts = urllib.parse.urlsplit(req.full_url)
        method, path = req.get_method(), parts.path
        if self.case_insensitive:
            # Comme GitHub : OWNER/REPO insensibles a la casse.
            path = path.lower()
        body = json.loads(req.data) if req.data else None
        self.calls.append((method, path, body))
        if method == 'POST' and path == '/repos/o/r/pulls':
            return _Response({'number': 5, 'html_url': 'https://github.com/o/r/pull/5'}, 201)
        if method == 'GET' and path.startswith('/repos/o/r/pulls/'):
            return _Response({'number': int(path.rsplit('/', 1)[-1]), 'state': 'closed' if self.merged else 'open',
                              'merged': self.merged, 'head': {'sha': self.head},
                              'base': {'repo': {'full_name': 'O/R'}}})
        if method == 'POST' and path.endswith('/comments'):
            if self.fail_override_comment and 'reprise-override' in body['body']:
                raise urllib.error.HTTPError(req.full_url, 500, 'Err', {}, io.BytesIO(b'{}'))
            return _Response({'html_url': 'https://github.com/o/r/pull/5#c'}, 201)
        if method == 'PUT' and path.endswith('/merge'):
            if body['sha'] != self.head:
                raise urllib.error.HTTPError(req.full_url, 409, 'Conflict', {}, io.BytesIO(b'{}'))
            self.merged = True
            return _Response({'sha': MERGED, 'merged': True, 'message': 'Pull Request successfully merged'})
        if method == 'GET' and path.endswith('/status'):
            if self.status_error:
                raise urllib.error.HTTPError(req.full_url, self.status_error, 'Err', {}, io.BytesIO(b'{}'))
            return _Response({'state': 'pending' if not self.statuses else 'x', 'statuses': self.statuses})
        if method == 'GET' and path.endswith('/check-runs'):
            return _Response({'total_count': len(self.check_runs), 'check_runs': self.check_runs})
        if method == 'GET' and path == '/repos/o/r/labels/ci:red':
            if not self.label:
                raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, io.BytesIO(b'{}'))
            return _Response({'name': 'ci:red'})
        if method == 'GET' and path == '/repos/o/r/issues':
            return _Response([dict(body, number=901 + i) for i, body in enumerate(self.issues)])
        if method == 'POST' and path == '/repos/o/r/issues':
            self.issues.append(body)
            return _Response({'number': 900 + len(self.issues), 'html_url': 'https://github.com/o/r/issues/9'}, 201)
        raise AssertionError('appel GitHub inattendu : %s %s' % (method, path))

    def puts(self):
        return [c for c in self.calls if c[0] == 'PUT']


class MergeTestBase(unittest.TestCase):
    ACTORS = (('autoclaw', 'autoclaw'), ('claude', 'claude'), ('claude-desktop', 'claude'),
              ('codex', 'codex'), ('metaai', 'metaai'))

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.registry = os.path.join(self.dir, 'gh-actors.sqlite')
        self.reviews = os.path.join(self.dir, 'gh-reviews.sqlite')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')
        self.policy = os.path.join(self.dir, 'gh-policy.json')
        self.github = FakeGitHub()
        patcher = mock.patch.object(reprise_gh.urllib.request, 'urlopen', self.github)
        patcher.start()
        self.addCleanup(patcher.stop)
        registry = reprise_gh_actors.ActorRegistry(self.registry, repo_root=REPO_ROOT)
        self.secrets = {}
        try:
            for actor, harness in self.ACTORS:
                result = registry.issue(actor, harness, os.path.join(self.dir, actor + '.token'))
                self.secrets[actor] = reprise_gh_actors.read_token_file(result['token_file'])
        finally:
            registry.close()
        self.servers = {}

    def server(self, actor):
        if actor not in self.servers:
            self.servers[actor] = reprise_gh_mcp.GhServer(
                actor, allow_write=True, policy_path=self.policy, audit_path=self.audit,
                token_provider=lambda: GH_TOKEN, token=self.secrets[actor],
                registry_path=self.registry, reviews_path=self.reviews)
        return self.servers[actor]

    def call(self, caller, name, **arguments):
        result = self.server(caller).call(name, arguments)
        text = result['content'][0]['text']
        self.outputs.append(text)
        return result['isError'], (text if result['isError'] else json.loads(text))

    @property
    def outputs(self):
        if not hasattr(self, '_outputs'):
            self._outputs = []
        return self._outputs

    def create_pull(self, actor='autoclaw'):
        error, result = self.call(actor, 'reprise_gh_create_pull', repo='o/r', head='feature', base='dev',
                                  title='Lot', body='Corps')
        self.assertFalse(error, result)
        return result['number']

    def review(self, actor, verdict='approve', number=5):
        error, result = self.call(actor, 'reprise_gh_review', repo='o/r', number=number, verdict=verdict,
                                  body='Relu.')
        self.assertFalse(error, result)
        return result

    def merge(self, actor='codex', number=5, **extra):
        return self.call(actor, 'reprise_gh_merge', repo='o/r', number=number, **extra)

    def audit_lines(self):
        with open(self.audit, encoding='utf-8') as handle:
            return [json.loads(x) for x in handle.read().splitlines() if x.strip()]

    def assert_no_secret(self):
        with open(self.audit, encoding='utf-8') as handle:
            blobs = [handle.read()] + self.outputs
        for blob in blobs:
            self.assertNotIn(GH_TOKEN, blob)
            for secret in self.secrets.values():
                self.assertNotIn(secret, blob)


class ReviewRuleTest(MergeTestBase):
    def test_merge_sans_revue_refuse_zero_put(self):
        self.create_pull()
        error, text = self.merge()
        self.assertTrue(error)
        self.assertIn('review_required', text)
        self.assertIn('1 revue(s) approve manquante(s)', text)
        self.assertIn('reprise_delegate', text)
        self.assertEqual(self.github.puts(), [])
        refused = [x for x in self.audit_lines() if x.get('reason') == 'reviews_missing']
        self.assertEqual((refused[0]['op'], refused[0]['decision'], refused[0]['missing']),
                         ('pull.merge', 'review_required', 1))
        self.assert_no_secret()

    def test_auto_revue_ignoree_puis_revue_croisee_fusionne_avec_le_sha_revu(self):
        self.create_pull('autoclaw')
        review = self.review('autoclaw')
        self.assertEqual(review['head_sha'], SHA_A)
        marker = [c for c in self.github.calls if c[0] == 'POST' and c[1].endswith('/comments')][0][2]['body']
        self.assertIn('<!-- reprise-review: autoclaw autoclaw approve %s -->' % SHA_A, marker)
        error, text = self.merge()
        self.assertTrue(error)
        self.assertIn('harness exclus (auteur) : autoclaw', text)
        self.review('claude')
        error, result = self.merge(method='squash')
        self.assertFalse(error, result)
        self.assertEqual(self.github.puts(), [('PUT', '/repos/o/r/pulls/5/merge',
                                               {'sha': SHA_A, 'merge_method': 'squash'})])
        self.assertEqual((result['merge_sha'], result['head_sha'], result['reviewed_by']),
                         (MERGED, SHA_A, ['claude']))
        line = [x for x in self.audit_lines() if x['method'] == 'PUT' and x['status'] == 200][0]
        self.assertEqual((line['decision'], line['reason'], line['actor']), ('allow', 'review_satisfied', 'codex'))
        self.assert_no_secret()

    def test_deux_revues_du_meme_harness_comptent_pour_une_et_auteur_inconnu_exige_deux(self):
        # PR ouverte hors Reprise : auteur inconnu, 2 harness distincts requis.
        self.review('claude')
        self.review('claude-desktop')
        error, text = self.merge()
        self.assertTrue(error)
        self.assertIn('1 revue(s) approve manquante(s) sur 2', text)
        self.assertIn('inconnu', text)
        self.review('codex')
        error, result = self.merge('metaai')
        self.assertFalse(error, result)
        self.assertEqual(result['reviewed_by'], ['claude', 'codex'])

    def test_revue_perimee_si_le_head_change(self):
        self.create_pull()
        self.review('claude')
        self.github.head = SHA_B
        error, text = self.merge()
        self.assertTrue(error)
        self.assertIn(SHA_B[:12], text)
        self.assertEqual(self.github.puts(), [])
        self.review('claude')
        error, result = self.merge()
        self.assertFalse(error, result)
        self.assertEqual(self.github.puts()[0][2]['sha'], SHA_B)

    def test_request_changes_bloque(self):
        self.create_pull()
        self.review('claude')
        self.review('codex', 'request_changes')
        error, text = self.merge('metaai')
        self.assertTrue(error)
        self.assertIn('request_changes en cours depuis : codex', text)
        self.assertEqual(self.github.puts(), [])

    def test_harness_lu_depuis_le_jeton_jamais_des_arguments(self):
        self.create_pull()
        for extra in ({'harness': 'codex'}, {'actor': 'codex'}, {'sha': SHA_A}, {'author': 'x'}):
            error, text = self.call('autoclaw', 'reprise_gh_review', repo='o/r', number=5,
                                    verdict='approve', body='x', **extra)
            self.assertTrue(error)
            self.assertIn('Argument inconnu', text)
        error, text = self.merge(override_review=True, reason='contournement par MCP')
        self.assertTrue(error)
        self.assertIn('Argument inconnu', text)
        store = reprise_gh_pulls.ReviewStore(self.reviews)
        try:
            rows = store.db.execute('SELECT author_actor, author_harness FROM gh_pulls').fetchall()
        finally:
            store.close()
        self.assertEqual(rows, [('autoclaw', 'autoclaw')])
        self.assertEqual(self.github.puts(), [])

    def test_politique_min_reviewers_et_distinct_from_author(self):
        policy = reprise_gh.default_policy()
        for rule in policy['rules']:
            if rule['op'] == 'pull.merge':
                rule['review'] = {'min_reviewers': 2, 'distinct_from_author': False}
        reprise_gh.save_policy(policy, self.policy)
        self.create_pull()
        self.review('autoclaw')
        self.assertTrue(self.merge()[0])
        self.review('claude')
        self.assertFalse(self.merge()[0])


class OverrideTest(MergeTestBase):
    def cli(self, argv, tty=True):
        err, out = io.StringIO(), io.StringIO()
        patches = [mock.patch.object(reprise_gh, '_isatty', return_value=tty),
                   mock.patch.object(reprise_gh, '_run_credential_fill', lambda host: 'password=%s' % GH_TOKEN),
                   mock.patch.object(reprise_gh, 'DEFAULT_AUDIT_PATH', self.audit),
                   mock.patch.object(reprise_gh, 'DEFAULT_POLICY_PATH', self.policy),
                   mock.patch.object(reprise_gh_pulls, 'DEFAULT_REVIEWS_PATH', self.reviews)]
        for patcher in patches:
            patcher.start()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                code = reprise_gh.main(argv)
        finally:
            for patcher in patches:
                patcher.stop()
        self.outputs.extend([out.getvalue(), err.getvalue()])
        return code, out.getvalue(), err.getvalue()

    def test_contournement_terminal_et_motif_exiges(self):
        self.create_pull()
        base = ['merge', '--repo', 'o/r', '--number', '5', '--override-review']
        self.assertEqual(self.cli(base + ['--reason', 'urgence de prod'], tty=False)[0], 1)
        self.assertEqual(self.cli(base + ['--reason', 'court'])[0], 1)
        self.assertEqual(self.cli(base)[0], 1)
        code, _, err = self.cli(['merge', '--repo', 'o/r', '--number', '5'])
        self.assertEqual(code, 1)
        self.assertIn('review_required', err)
        self.assertEqual(self.github.puts(), [])
        self.assertFalse([c for c in self.github.calls if c[0] == 'POST' and c[1].endswith('/comments')])

    def test_contournement_commente_audite_et_enregistre(self):
        self.create_pull()
        code, out, err = self.cli(['merge', '--repo', 'o/r', '--number', '5', '--method', 'rebase',
                                   '--override-review', '--reason', 'Correctif de securite urgent'])
        self.assertEqual((code, err), (0, ''))
        self.assertIn('[contournement]', out)
        writes = [c for c in self.github.calls if c[0] in ('POST', 'PUT') and '/pulls/5' in c[1]
                  or c[1].endswith('/5/comments')]
        # Revue #270 constat 8 : commentaire poste apres la fusion reussie.
        self.assertEqual([c[0] for c in writes], ['PUT', 'POST'])
        self.assertIn('<!-- reprise-override: operator %s rebase -->' % SHA_A, writes[1][2]['body'])
        self.assertIn('Correctif de securite urgent', writes[1][2]['body'])
        self.assertEqual(writes[0][2], {'sha': SHA_A, 'merge_method': 'rebase'})
        put = [x for x in self.audit_lines() if x['method'] == 'PUT' and x['status'] == 200][0]
        self.assertEqual((put['actor'], put['decision'], put['reason'], put['override_reason'], put['channel']),
                         ('operator', 'allow', 'operator_override', 'Correctif de securite urgent', 'cli'))
        store = reprise_gh_pulls.ReviewStore(self.reviews)
        try:
            row = store.db.execute('SELECT actor, harness, override_reason, override_note FROM gh_merges').fetchone()
        finally:
            store.close()
        self.assertEqual(row, ('operator', 'operator', 'Correctif de securite urgent', 'posted'))
        self.assert_no_secret()


class CiTest(MergeTestBase):
    def merged(self, **ci):
        for key, value in ci.items():
            setattr(self.github, key, value)
        self.create_pull()
        self.review('claude')
        error, result = self.merge()
        self.assertFalse(error, result)
        return result

    def sweep(self):
        who = reprise_gh_pulls.identity('codex', 'codex', 'ok', None)
        store = reprise_gh_pulls.ReviewStore(self.reviews)
        try:
            return reprise_gh_pulls.ci_sweep(who, store, 'o/r', policy_path=self.policy,
                                             audit_path=self.audit, token_provider=lambda: GH_TOKEN)
        finally:
            store.close()

    def test_failure_une_issue_par_sha_meme_en_rejouant_le_sweep(self):
        result = self.merged(check_runs=[{'name': 'tests', 'status': 'completed', 'conclusion': 'failure',
                                          'html_url': 'https://github.com/o/r/runs/1'}])
        self.assertEqual((result['ci_state'], result['ci_issue_number']), ('failure', 901))
        issue = self.github.issues[0]
        self.assertEqual(issue['title'], 'CI rouge après fusion de #5 (%s)' % MERGED[:7])
        self.assertEqual(issue['labels'], ['ci:red'])
        for link in ('https://github.com/o/r/pull/5', 'https://github.com/o/r/commit/' + MERGED,
                     'https://github.com/o/r/runs/1'):
            self.assertIn(link, issue['body'])
        self.assertEqual(self.sweep(), [])
        self.assertEqual(self.sweep(), [])
        self.assertEqual(len(self.github.issues), 1)
        status_calls = [c[1] for c in self.github.calls if c[1].endswith('/status')]
        self.assertEqual(status_calls, ['/repos/o/r/commits/%s/status' % MERGED])

    def test_pending_puis_sweep_vers_failure_sans_label_absent(self):
        result = self.merged(statuses=[{'context': 'ci/build', 'state': 'pending'}], label=False)
        self.assertEqual((result['ci_state'], self.github.issues), ('pending', []))
        self.github.statuses = [{'context': 'ci/build', 'state': 'error', 'target_url': 'https://ci/1'}]
        swept = self.sweep()
        self.assertEqual([(x['ci_state'], x['ci_issue_number']) for x in swept], [('failure', 901)])
        self.assertNotIn('labels', self.github.issues[0])
        self.sweep()
        self.assertEqual(len(self.github.issues), 1)

    def test_success_et_none_sans_issue(self):
        result = self.merged(check_runs=[{'name': 't', 'status': 'completed', 'conclusion': 'success'}],
                             statuses=[{'context': 'c', 'state': 'success'}])
        self.assertEqual(result['ci_state'], 'success')
        self.assertEqual(self.github.issues, [])
        self.assertEqual(self.sweep(), [])

    def test_ci_illisible_fusion_ok_et_audit(self):
        result = self.merged(status_error=500)
        self.assertTrue(result['merged'])
        self.assertEqual(result['ci_state'], 'unknown')
        lines = [x for x in self.audit_lines() if x['status'] == 'ci_read_failed']
        self.assertEqual(len(lines), 1)
        self.assertIn('HTTP 500', lines[0]['error'])
        self.github.status_error = None
        self.github.check_runs = [{'name': 't', 'status': 'completed', 'conclusion': 'timed_out'}]
        self.assertEqual(self.sweep()[0]['ci_state'], 'failure')
        self.assert_no_secret()


class ReviewFindingsTest(MergeTestBase):
    """Non-regression de la revue independante de abe49b1 (constats 1, 3, 4, 5, 8)."""

    def store(self):
        store = reprise_gh_pulls.ReviewStore(self.reviews)
        self.addCleanup(store.close)
        return store

    def test_constat1_casse_du_repo_ne_contourne_pas_la_revue(self):
        self.github.case_insensitive = True
        self.create_pull('autoclaw')
        self.review('claude', 'request_changes')
        for actor in ('autoclaw', 'codex'):
            error, result = self.call(actor, 'reprise_gh_review', repo='O/r', number=5, verdict='approve',
                                      body='ok')
            self.assertFalse(error, result)
        error, text = self.call('autoclaw', 'reprise_gh_merge', repo='O/r', number=5)
        self.assertTrue(error)
        self.assertIn('request_changes en cours depuis : claude', text)
        self.assertEqual(self.github.puts(), [])
        rows = self.store().db.execute('SELECT DISTINCT repo FROM gh_reviews').fetchall()
        self.assertEqual(rows, [('o/r',)])
        self.assertEqual(self.store().pull_author('o/r', 5)['harness'], 'autoclaw')

    def test_constat3_ci_none_rebalayee_sept_jours(self):
        result = self.merged_none()
        self.assertEqual(result['ci_state'], 'none')
        self.assertEqual([x['merge_sha'] for x in self.store().ci_candidates()], [MERGED])
        self.github.check_runs = [{'name': 'tests', 'status': 'completed', 'conclusion': 'failure'}]
        self.assertEqual([x['ci_state'] for x in CiTest.sweep(self)], ['failure'])
        self.assertEqual(len(self.github.issues), 1)
        old = reprise_gh_pulls.ReviewStore(self.reviews)
        old.db.execute("UPDATE gh_merges SET ci_state='none', merged_at=?", (0,))
        old.close()
        self.assertEqual(self.store().ci_candidates(), [])

    def merged_none(self):
        self.create_pull()
        self.review('claude')
        error, result = self.merge()
        self.assertFalse(error, result)
        return result

    def test_constat4_issue_idempotente_reponse_perdue_et_reservation(self):
        self.merged_none()
        store = self.store()
        store.set_ci('o/r', MERGED, 'pending')
        self.github.check_runs = [{'name': 'tests', 'status': 'completed', 'conclusion': 'failure'}]
        original = FakeGitHub.__call__

        def lossy(github, req, timeout):
            response = original(github, req, timeout)
            if req.get_method() == 'POST' and req.full_url.endswith('/issues'):
                raise urllib.error.URLError('timed out')  # issue creee, reponse perdue
            return response

        with mock.patch.object(FakeGitHub, '__call__', lossy):
            CiTest.sweep(self)
        self.assertEqual(store.merge_row('o/r', MERGED)['ci_state'], 'failure_reporting')
        CiTest.sweep(self)  # reservation fraiche : rien
        self.assertEqual(len(self.github.issues), 1)
        self.assertFalse(store.reserve_report('o/r', MERGED))
        store.db.execute('UPDATE gh_merges SET ci_reserved_at=0')
        swept = CiTest.sweep(self)  # reservation perimee : marqueur retrouve, pas de doublon
        self.assertEqual([(x['ci_state'], x['ci_issue_number']) for x in swept], [('failure', 901)])
        self.assertEqual(len(self.github.issues), 1)
        self.assertEqual(CiTest.sweep(self), [])

    def test_constat5_pont_et_cli_resolvent_le_meme_registre(self):
        args = reprise_gh.build_parser().parse_args(['--registry', self.registry, 'merge', '--repo', 'o/r',
                                                     '--number', '5'])
        self.assertEqual(self.server('codex')._reviews_path,
                         reprise_gh_pulls.default_reviews_path(args.registry, args.reviews))
        self.assertEqual(reprise_gh_pulls.default_reviews_path(self.registry, '/x/r.sqlite'), '/x/r.sqlite')
        self.create_pull()
        self.review('claude')
        err = io.StringIO()
        with mock.patch.object(reprise_gh, '_isatty', return_value=True), \
                mock.patch.object(reprise_gh, '_run_credential_fill', lambda host: 'password=%s' % GH_TOKEN), \
                mock.patch.object(reprise_gh, 'DEFAULT_AUDIT_PATH', self.audit), \
                mock.patch.object(reprise_gh, 'DEFAULT_POLICY_PATH', self.policy), \
                mock.patch.object(reprise_gh_pulls, 'DEFAULT_REVIEWS_PATH', os.path.join(self.dir, 'jamais')), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = reprise_gh.main(['--registry', self.registry, 'merge', '--repo', 'o/r', '--number', '5'])
        self.assertEqual((code, err.getvalue()), (0, ''))
        self.assertEqual(len(self.github.puts()), 1)
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'jamais')))

    def test_constat8_commentaire_de_contournement_apres_fusion(self):
        self.create_pull()
        argv = ['merge', '--repo', 'o/r', '--number', '5', '--override-review', '--reason', 'Correctif urgent prod']
        self.github.head = SHA_A
        original = FakeGitHub.__call__

        def conflict(github, req, timeout):
            if req.get_method() == 'PUT':
                raise urllib.error.HTTPError(req.full_url, 409, 'Conflict', {}, io.BytesIO(b'{}'))
            return original(github, req, timeout)

        with mock.patch.object(FakeGitHub, '__call__', conflict):
            self.assertEqual(OverrideTest.cli(self, argv)[0], 1)
        self.assertFalse([c for c in self.github.calls if c[0] == 'POST' and c[1].endswith('/comments')])
        self.github.fail_override_comment = True
        code, _, err = OverrideTest.cli(self, argv)
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(len(self.github.puts()), 1)
        note = self.store().db.execute('SELECT override_note FROM gh_merges').fetchone()[0]
        self.assertTrue(note.startswith('failed: HTTP 500'))
        self.assertTrue([x for x in self.audit_lines() if x['status'] == 'override_comment_failed'])


class NormalizeTest(unittest.TestCase):
    def test_etats(self):
        who = reprise_gh_pulls.identity('codex', 'codex', 'ok', None)
        cases = [
            ([], [], 'none'),
            ([{'state': 'success'}], [], 'success'),
            ([{'state': 'pending'}], [], 'pending'),
            ([], [{'status': 'in_progress', 'conclusion': None}], 'pending'),
            ([], [{'status': 'completed', 'conclusion': 'cancelled'}], 'failure'),
            ([{'state': 'success'}], [{'status': 'completed', 'conclusion': 'neutral'}], 'success'),
            ([{'state': 'pending'}], [{'status': 'completed', 'conclusion': 'failure'}], 'failure'),
        ]
        for statuses, runs, expected in cases:
            responses = iter([(200, {'statuses': statuses}), (200, {'check_runs': runs})])
            with self.subTest(expected=expected), \
                    mock.patch.object(reprise_gh_pulls.reprise_gh, 'request',
                                      lambda *a, **k: next(responses)):
                self.assertEqual(reprise_gh_pulls.ci_state(who, 'o/r', MERGED)[0], expected)


if __name__ == '__main__':
    unittest.main()
