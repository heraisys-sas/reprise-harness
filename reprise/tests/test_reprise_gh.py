# -*- coding: utf-8 -*-
"""Tests du service gh Reprise (#270) : politique, audit, client REST."""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import reprise_gh


class PolicyTest(unittest.TestCase):
    # Detail de la politique v2 et de la migration v1 : test_reprise_gh_policy_v2.
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_politique_absente_v2_par_defaut(self):
        policy = reprise_gh.load_policy(os.path.join(self.dir, 'absent.json'))
        self.assertEqual(policy['version'], 2)
        self.assertEqual(reprise_gh.decide(policy, 'metaai', 'read', None, 'missing')[0], 'allow')
        self.assertEqual(reprise_gh.decide(policy, 'metaai', 'issue.comment', None, 'missing')[0], 'deny')

    def test_check_permission_et_init_policy_retires(self):
        self.assertFalse(hasattr(reprise_gh, 'check_permission'))
        self.assertFalse(hasattr(reprise_gh, 'init_policy'))


class TokenTest(unittest.TestCase):
    def test_resolve_token_lit_password(self):
        out = 'protocol=https\nhost=github.com\npassword=TOKEN123\n'
        token = reprise_gh.resolve_token(runner=lambda host: out)
        self.assertEqual(token, 'TOKEN123')

    def test_resolve_token_absent_refuse(self):
        with self.assertRaises(reprise_gh.GhError):
            reprise_gh.resolve_token(runner=lambda host: 'protocol=https\nhost=github.com\n')


def _lines(path):
    with open(path, encoding='utf-8') as handle:
        return [x for x in handle.read().splitlines() if x.strip()]


class _FakeResponse(object):
    def __init__(self, payload, status=200):
        self._raw = json.dumps(payload).encode('utf-8')
        self.status = status

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RequestTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.policy = os.path.join(self.dir, 'gh-policy.json')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')

    def test_jeton_github_introuvable_audite_unsent(self):
        reprise_gh.save_policy(reprise_gh.default_policy(), self.policy)

        def missing():
            raise reprise_gh.GhError('git credential fill a echoue (code 128)')

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as urlopen:
            with self.assertRaises(reprise_gh.GhError):
                reprise_gh.request('metaai', 'GET', '/repos/o/r/issues/270', token_provider=missing,
                                   policy_path=self.policy, audit_path=self.audit)
        urlopen.assert_not_called()
        line = json.loads(_lines(self.audit)[-1])
        self.assertEqual((line['status'], line['decision']), ('unsent', 'allow'))

    def test_lecture_auditee_sans_token_dans_le_journal(self):
        reprise_gh.save_policy(reprise_gh.default_policy(), self.policy)
        seen = {}

        def fake_urlopen(req, timeout):
            seen['url'] = req.full_url
            seen['auth'] = req.get_header('Authorization')
            return _FakeResponse({'ok': True})

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen):
            status, payload = reprise_gh.request(
                'metaai', 'GET', '/repos/o/r/issues/270',
                token_provider=lambda: 'SECRET',
                policy_path=self.policy, audit_path=self.audit)
        self.assertEqual(status, 200)
        self.assertEqual(payload, {'ok': True})
        self.assertTrue(seen['url'].endswith('/repos/o/r/issues/270'))
        self.assertIn('SECRET', seen['auth'])
        with open(self.audit, 'r', encoding='utf-8') as handle:
            lines = [json.loads(x) for x in handle if x.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['actor'], 'metaai')
        self.assertEqual(lines[0]['method'], 'GET')
        self.assertEqual(lines[0]['status'], 200)
        self.assertNotIn('SECRET', open(self.audit).read())

    def test_chemin_hors_hote_refuse_avant_jeton_issue_291(self):
        reprise_gh.save_policy(reprise_gh.default_policy(), self.policy)
        token = mock.Mock(return_value='SECRET')
        for path in ('.evil.example/x', '@evil.example/x', ':8443/x', '//evil.example/x',
                     'repos/o/r', '/repos/o r', '/repos/\nHost: x', '', None):
            with self.subTest(path=path), \
                    mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
                with self.assertRaisesRegex(reprise_gh.GhError, 'Chemin API invalide'):
                    reprise_gh.request('metaai', 'GET', path, token_provider=token,
                                       policy_path=self.policy, audit_path=self.audit)
                fake.assert_not_called()
        token.assert_not_called()
        lines = [json.loads(x) for x in open(self.audit) if x.strip()]
        self.assertTrue(lines and all(x['status'] == 'refused' for x in lines))
        self.assertNotIn('SECRET', open(self.audit).read())

    def test_chemin_valide_reste_sur_l_hote(self):
        self.assertEqual(reprise_gh.api_url('api.github.com', '/repos/o/r/issues?state=open&per_page=5'),
                         'https://api.github.com/repos/o/r/issues?state=open&per_page=5')

    def test_depot_invalide_refuse(self):
        for repo in ('o', 'o/r/x', '../r', 'o/..', 'evil.example/x?'):
            with self.subTest(repo=repo), self.assertRaisesRegex(reprise_gh.GhError, 'Dépôt invalide'):
                reprise_gh.comment('claude', 1, repo, 'x', token_provider=lambda: 'SECRET',
                                   policy_path=self.policy, audit_path=self.audit)

    def test_ecriture_refusee_audit_sans_appel_reseau(self):
        reprise_gh.save_policy(reprise_gh.default_policy(), self.policy)
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            with self.assertRaises(reprise_gh.GhError):
                reprise_gh.request(
                    'metaai', 'POST', '/repos/o/r/issues/270/comments',
                    body={'body': 'x'}, token_provider=lambda: 'SECRET',
                    policy_path=self.policy, audit_path=self.audit)
        fake.assert_not_called()
        lines = [json.loads(x) for x in open(self.audit) if x.strip()]
        self.assertEqual(lines[0]['status'], 'refused')

    def test_erreur_http_auditee_et_levee(self):
        reprise_gh.save_policy(reprise_gh.default_policy(), self.policy)

        def fake_urlopen(req, timeout):
            raise urllib.error.HTTPError(
                req.full_url, 404, 'Not Found', {}, io.BytesIO(b'{"message":"nope"}'))

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen):
            with self.assertRaises(reprise_gh.GhError) as ctx:
                reprise_gh.request(
                    'autoclaw', 'GET', '/repos/o/r/issues/999',
                    token_provider=lambda: 'SECRET',
                    policy_path=self.policy, audit_path=self.audit)
        self.assertIn('404', str(ctx.exception))
        lines = [json.loads(x) for x in open(self.audit) if x.strip()]
        self.assertEqual(lines[0]['status'], 404)

    def test_comment_vise_le_bon_chemin(self):
        seen = {}

        def fake_urlopen(req, timeout):
            seen['url'] = req.full_url
            seen['method'] = req.get_method()
            seen['body'] = req.data
            return _FakeResponse({'html_url': 'https://github.com/o/r/issues/270#comment'})

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen):
            status, payload = reprise_gh.comment(
                'autoclaw', 270, 'o/r', 'Bonjour', auth='ok', token_id='0123456789abcdef',
                token_provider=lambda: 'SECRET',
                policy_path=self.policy, audit_path=self.audit)
        self.assertEqual(status, 200)
        self.assertTrue(seen['url'].endswith('/repos/o/r/issues/270/comments'))
        self.assertEqual(seen['method'], 'POST')
        self.assertEqual(json.loads(seen['body'])['body'], 'Bonjour')


class CliTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.policy = os.path.join(self.dir, 'gh-policy.json')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')
        self._old = (reprise_gh.DEFAULT_POLICY_PATH, reprise_gh.DEFAULT_AUDIT_PATH)
        reprise_gh.DEFAULT_POLICY_PATH = self.policy
        reprise_gh.DEFAULT_AUDIT_PATH = self.audit

    def tearDown(self):
        reprise_gh.DEFAULT_POLICY_PATH, reprise_gh.DEFAULT_AUDIT_PATH = self._old

    def test_comment_refuse_sans_reseau_pour_acteur_lecture_seule(self):
        body = os.path.join(self.dir, 'corps.md')
        with open(body, 'w', encoding='utf-8') as handle:
            handle.write('texte')
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake, \
                redirect_stderr(io.StringIO()):
            code = reprise_gh.main([
                '--actor', 'metaai', 'comment', '270', '--repo', 'o/r',
                '--body-file', body])
        self.assertEqual(code, 1)
        fake.assert_not_called()

    def test_api_raw_operator_avec_terminal(self):
        body = os.path.join(self.dir, 'corps.json')
        with open(body, 'w', encoding='utf-8') as handle:
            handle.write('{"body": "ok"}')

        def fake_urlopen(req, timeout):
            return _FakeResponse({'number': 271})

        # Token hermetique : ci_test tourne sous HOME jetable, sans trousseau.
        # redirect_stdout : la CLI ecrit la reponse JSON sur stdout — ne pas fuiter dans le journal de tests.
        identifiants = 'protocol=https\nhost=github.com\npassword=SECRET'
        with mock.patch.object(reprise_gh, '_run_credential_fill', lambda host: identifiants), \
                mock.patch.object(reprise_gh, '_isatty', return_value=True), \
                mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen), \
                redirect_stdout(io.StringIO()):
            code = reprise_gh.main(['api', 'POST', '/repos/o/r/issues', '--body-file', body])
        self.assertEqual(code, 0)
        line = [json.loads(x) for x in _lines(self.audit)][-1]
        self.assertEqual((line['actor'], line['op'], line['decision'], line['auth'], line['rule']),
                         ('operator', 'api.raw', 'allow', 'tty', 'default-api-raw'))

    def test_ecriture_operator_sans_terminal_refusee(self):
        with mock.patch.object(reprise_gh, '_isatty', return_value=False), \
                mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake, \
                redirect_stderr(io.StringIO()) as err:
            code = reprise_gh.main(['api', 'POST', '/repos/o/r/issues', '--body', '{}'])
        self.assertEqual(code, 1)
        fake.assert_not_called()
        self.assertIn('operator_requires_tty', err.getvalue())
        line = [json.loads(x) for x in _lines(self.audit)][-1]
        self.assertEqual((line['decision'], line['reason']), ('deny', 'operator_requires_tty'))

    def test_api_raw_refuse_a_un_acteur_a_jeton_par_defaut(self):
        # api.raw est reserve a operator par la politique par defaut.
        policy = reprise_gh.default_policy()
        self.assertEqual(reprise_gh.decide(policy, 'autoclaw', 'api.raw', None, 'ok')[:2],
                         ('deny', 'default'))

    def test_lecture_operator_sans_terminal_permise(self):
        with mock.patch.object(reprise_gh, '_run_credential_fill',
                               lambda host: 'password=SECRET'), \
                mock.patch.object(reprise_gh, '_isatty', return_value=False), \
                mock.patch.object(reprise_gh.urllib.request, 'urlopen',
                                  lambda req, timeout: _FakeResponse({'ok': 1})), \
                redirect_stdout(io.StringIO()):
            code = reprise_gh.main(['api', 'GET', '/repos/o/r'])
        self.assertEqual(code, 0)


class InitTest(unittest.TestCase):
    # init est retire (lot 2) : il renvoie au format v2 sans rien ecrire.
    def test_init_retire_renvoie_au_format_v2(self):
        folder = tempfile.mkdtemp()
        policy = os.path.join(folder, 'gh-policy.json')
        ancien = (reprise_gh.DEFAULT_POLICY_PATH, reprise_gh.DEFAULT_AUDIT_PATH)
        reprise_gh.DEFAULT_POLICY_PATH = policy
        reprise_gh.DEFAULT_AUDIT_PATH = os.path.join(folder, 'gh-audit.jsonl')
        try:
            with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake, \
                    redirect_stderr(io.StringIO()) as err:
                code = reprise_gh.main(['init', '--write-actors', 'metaai'])
        finally:
            reprise_gh.DEFAULT_POLICY_PATH, reprise_gh.DEFAULT_AUDIT_PATH = ancien
        self.assertEqual(code, 1)
        fake.assert_not_called()
        self.assertIn('v2', err.getvalue())
        self.assertFalse(os.path.exists(policy))


# #270 lot 1 : credential fill sans prompt terminal, canal d'audit de la CLI.
class CredentialFillTest(unittest.TestCase):
    def test_credential_fill_sans_prompt_terminal(self):
        proc = mock.Mock(returncode=0, stdout='password=TOKEN_270\n')
        with mock.patch.object(reprise_gh.subprocess, 'run', return_value=proc) as run:
            out = reprise_gh._run_credential_fill('github.com')
        self.assertIn('TOKEN_270', out)
        env = run.call_args.kwargs['env']
        self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(env.get('PATH'), os.environ.get('PATH'))
        self.assertEqual(run.call_args.args[0], ['git', 'credential', 'fill'])


class CliAuditChannelTest(unittest.TestCase):
    def test_cli_audit_porte_channel_cli(self):
        folder = tempfile.mkdtemp()
        policy = os.path.join(folder, 'gh-policy.json')
        audit = os.path.join(folder, 'gh-audit.jsonl')
        ancien = (reprise_gh.DEFAULT_POLICY_PATH, reprise_gh.DEFAULT_AUDIT_PATH)
        reprise_gh.DEFAULT_POLICY_PATH, reprise_gh.DEFAULT_AUDIT_PATH = policy, audit
        try:
            with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake, \
                    redirect_stderr(io.StringIO()):
                code = reprise_gh.main(['--actor', 'metaai', 'api', 'POST', '/repos/o/r/issues'])
        finally:
            reprise_gh.DEFAULT_POLICY_PATH, reprise_gh.DEFAULT_AUDIT_PATH = ancien
        self.assertEqual(code, 1)
        fake.assert_not_called()
        with open(audit, encoding='utf-8') as handle:
            line = json.loads(handle.readline())
        self.assertEqual((line['status'], line['channel'], line['actor']), ('refused', 'cli', 'metaai'))

class ReviewFindingsTest(unittest.TestCase):
    """Non-regression de la revue de abe49b1 : constats 2 (redirections) et 7 (fusion api.raw)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.policy = os.path.join(self.dir, 'gh-policy.json')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')

    def test_constat2_redirection_refusee_et_jeton_jamais_recopie(self):
        import email.message
        import urllib.response
        seen = []

        class FakeHttps(urllib.request.HTTPSHandler):
            def https_open(self, req):
                seen.append((req.full_url, req.get_header('Authorization')))
                headers = email.message.Message()
                headers['Location'] = 'https://pipelines.example.net/logs.zip'
                response = urllib.response.addinfourl(io.BytesIO(b''), headers, req.full_url, 302)
                response.msg = 'Found'
                return response

        opener = urllib.request.build_opener(reprise_gh._NoRedirect, FakeHttps)
        with mock.patch.object(reprise_gh, '_OPENER', opener), \
                mock.patch.object(reprise_gh, '_run_credential_fill', lambda host: 'password=SECRET'):
            with self.assertRaisesRegex(reprise_gh.GhError, 'Redirection HTTP 302 refusee'):
                reprise_gh.request('metaai', 'GET', '/repos/o/r/actions/runs/1/logs',
                                   policy_path=self.policy, audit_path=self.audit)
        self.assertEqual(seen, [('https://api.github.com/repos/o/r/actions/runs/1/logs', 'Bearer SECRET')])
        line = json.loads(_lines(self.audit)[-1])
        self.assertEqual((line['status'], line['error']), (302, 'redirect_refused'))
        self.assertNotIn('SECRET', ''.join(_lines(self.audit)))
        # Meme avec le gestionnaire standard, Authorization (non redirige) n'est pas recopie.
        captured = {}

        def fake_urlopen(req, timeout):
            captured['req'] = req
            return _FakeResponse({})

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen):
            reprise_gh.request('metaai', 'GET', '/repos/o/r', token_provider=lambda: 'SECRET',
                               policy_path=self.policy, audit_path=self.audit)
        follow = urllib.request.HTTPRedirectHandler().redirect_request(
            captured['req'], None, 302, 'Found', {}, 'https://pipelines.example.net/x')
        self.assertIsNone(follow.get_header('Authorization'))

    def test_constat7_fusion_par_api_raw_refusee(self):
        for path in ('/repos/o/r/pulls/5/merge', '/REPOS/O/R/PULLS/5/MERGE', '/repos/o/r/pulls/5/merge/'):
            with self.subTest(path=path), \
                    mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
                with self.assertRaisesRegex(reprise_gh.GhError, 'reprise gh merge'):
                    reprise_gh.request('operator', 'PUT', path, {'sha': 'a' * 40}, auth='tty',
                                       token_provider=lambda: 'SECRET', policy_path=self.policy,
                                       audit_path=self.audit)
                fake.assert_not_called()
        line = json.loads(_lines(self.audit)[-1])
        self.assertEqual((line['op'], line['reason']), ('api.raw', 'merge_via_api_raw'))

    def test_contre_revue_autres_voies_de_fusion_refusees(self):
        provider = mock.Mock(return_value='SECRET')
        for method, path in (('POST', '/repos/o/r/merges'), ('POST', '/graphql'),
                             ('PATCH', '/repos/o/r/git/refs/heads/dev'), ('PUT', '/repos/o/r/pulls/5/%6Derge')):
            with self.subTest(path=path), \
                    mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
                with self.assertRaisesRegex(reprise_gh.GhError, 'reprise gh merge'):
                    reprise_gh.request('operator', method, path, {}, auth='tty', token_provider=provider,
                                       policy_path=self.policy, audit_path=self.audit)
                fake.assert_not_called()
        provider.assert_not_called()


if __name__ == '__main__':
    unittest.main()


class RepriseCliDispatchTest(unittest.TestCase):
    """`reprise gh …` délègue à reprise_gh.main avant tout import du scheduler (#270)."""

    SCRIPT = Path(__file__).parents[1] / 'reprise'

    def dispatch(self, argv):
        main_mock = mock.Mock(return_value=0)
        namespace = {'__name__': 'reprise_gh_cli_dispatch', '__file__': str(self.SCRIPT)}
        with mock.patch.object(sys, 'argv', ['reprise', *argv]), \
             mock.patch.object(reprise_gh, 'main', main_mock):
            with self.assertRaises(SystemExit) as exit_context:
                exec(compile(self.SCRIPT.read_text(), str(self.SCRIPT), 'exec'), namespace)
        return exit_context.exception.code, main_mock

    def test_acteur_operator_par_defaut(self):
        code, main_mock = self.dispatch(['gh', 'api', 'GET', '/repos/o/r/issues/270'])
        self.assertEqual(code, 0)
        main_mock.assert_called_once_with(['--actor', 'operator', 'api', 'GET', '/repos/o/r/issues/270'])

    def test_token_file_sans_injection_d_acteur(self):
        # Lot 2 : avec --token-file, l'acteur vient du registre ; aucune injection.
        for argv in (['gh', '--token-file', '/x/jeton', 'api', 'GET', '/x'],
                     ['gh', '--token-file=/x/jeton', 'api', 'GET', '/x']):
            _, main_mock = self.dispatch(argv)
            main_mock.assert_called_once_with(argv[1:])

    def test_acteur_explicite_conserve(self):
        for argv in (['gh', '--actor', 'claude', 'api', 'GET', '/x'], ['gh', '--actor=claude', 'api', 'GET', '/x']):
            _, main_mock = self.dispatch(argv)
            main_mock.assert_called_once_with(argv[1:])
