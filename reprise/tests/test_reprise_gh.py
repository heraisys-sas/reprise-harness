# -*- coding: utf-8 -*-
"""Tests du service gh Reprise (#270) : politique, audit, client REST."""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import reprise_gh


class PolicyTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.policy = os.path.join(self.dir, 'gh-policy.json')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')

    def test_politique_absente_fail_closed_lecture_seule(self):
        policy = reprise_gh.load_policy(os.path.join(self.dir, 'absent.json'))
        self.assertTrue(policy['enabled'])
        self.assertEqual(policy['write_actors'], [])

    def test_lecture_autorisee_acteur_nomme(self):
        reprise_gh.check_permission({'enabled': True, 'write_actors': []}, 'metaai', 'GET')

    def test_ecriture_acteur_inconnu_refusee(self):
        with self.assertRaises(reprise_gh.GhError):
            reprise_gh.check_permission(
                {'enabled': True, 'write_actors': ['claude']}, 'metaai', 'POST')

    def test_ecriture_acteur_liste_permise(self):
        reprise_gh.check_permission(
            {'enabled': True, 'write_actors': ['claude']}, 'claude', 'POST')

    def test_service_desactive_refuse_tout(self):
        with self.assertRaises(reprise_gh.GhError):
            reprise_gh.check_permission({'enabled': False, 'write_actors': ['x']}, 'x', 'GET')

    def test_acteur_vide_refuse(self):
        with self.assertRaises(reprise_gh.GhError):
            reprise_gh.check_permission({'enabled': True, 'write_actors': []}, '  ', 'GET')

    def test_init_ecrit_politique(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw', 'claude']},
                               self.policy)
        policy = reprise_gh.load_policy(self.policy)
        self.assertEqual(policy['write_actors'], ['autoclaw', 'claude'])


class TokenTest(unittest.TestCase):
    def test_resolve_token_lit_password(self):
        out = 'protocol=https\nhost=github.com\npassword=TOKEN123\n'
        token = reprise_gh.resolve_token(runner=lambda host: out)
        self.assertEqual(token, 'TOKEN123')

    def test_resolve_token_absent_refuse(self):
        with self.assertRaises(reprise_gh.GhError):
            reprise_gh.resolve_token(runner=lambda host: 'protocol=https\nhost=github.com\n')


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

    def test_lecture_auditee_sans_token_dans_le_journal(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': []}, self.policy)
        seen = {}

        def fake_urlopen(req, timeout):
            seen['url'] = req.full_url
            seen['auth'] = req.headers.get('Authorization')
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

    def test_ecriture_refusee_audit_sans_appel_reseau(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': []}, self.policy)
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
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw']}, self.policy)

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
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw']}, self.policy)
        seen = {}

        def fake_urlopen(req, timeout):
            seen['url'] = req.full_url
            seen['method'] = req.get_method()
            seen['body'] = req.data
            return _FakeResponse({'html_url': 'https://github.com/o/r/issues/270#comment'})

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen):
            status, payload = reprise_gh.comment(
                'autoclaw', 270, 'o/r', 'Bonjour',
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
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            code = reprise_gh.main([
                '--actor', 'metaai', 'comment', '270', '--repo', 'o/r',
                '--body-file', body])
        self.assertEqual(code, 1)
        fake.assert_not_called()

    def test_api_ecriture_acteur_liste(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw']}, self.policy)
        body = os.path.join(self.dir, 'corps.json')
        with open(body, 'w', encoding='utf-8') as handle:
            handle.write('{"body": "ok"}')

        def fake_urlopen(req, timeout):
            return _FakeResponse({'number': 271})

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen):
            code = reprise_gh.main([
                '--actor', 'autoclaw', 'api', 'POST', '/repos/o/r/issues',
                '--body-file', body])
        self.assertEqual(code, 0)


class InitTest(unittest.TestCase):
    # init sans auto-elevation : acteur liste, ou consent operateur trace.
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.policy = os.path.join(self.dir, 'gh-policy.json')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')

    def test_init_acteur_non_liste_refuse_sans_auto_elevation(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['claude']}, self.policy)
        with self.assertRaises(reprise_gh.GhError):
            reprise_gh.init_policy('metaai', ['metaai'],
                                   policy_path=self.policy, audit_path=self.audit)
        self.assertEqual(reprise_gh.load_policy(self.policy)['write_actors'], ['claude'])
        lines = [json.loads(x) for x in open(self.audit) if x.strip()]
        self.assertEqual(lines[-1]['status'], 'refused')

    def test_init_politique_absente_refuse_sans_consent(self):
        with self.assertRaises(reprise_gh.GhError):
            reprise_gh.init_policy('metaai', ['metaai'],
                                   policy_path=self.policy, audit_path=self.audit)
        self.assertFalse(os.path.exists(self.policy))

    def test_init_acteur_liste_ok(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw']}, self.policy)
        voie = reprise_gh.init_policy('autoclaw', ['autoclaw', 'claude'],
                                      policy_path=self.policy, audit_path=self.audit)
        self.assertEqual(voie, 'acteur_liste')
        self.assertEqual(
            reprise_gh.load_policy(self.policy)['write_actors'], ['autoclaw', 'claude'])
        lines = [json.loads(x) for x in open(self.audit) if x.strip()]
        self.assertEqual(lines[-1]['status'], 'ok')
        self.assertEqual(lines[-1]['voie'], 'acteur_liste')

    def test_init_bootstrap_consent_operateur_trace(self):
        voie = reprise_gh.init_policy(
            'autoclaw', ['autoclaw'], consent='olivier 2026-11-26',
            policy_path=self.policy, audit_path=self.audit)
        self.assertEqual(voie, 'consentement_operateur')
        lines = [json.loads(x) for x in open(self.audit) if x.strip()]
        self.assertEqual(lines[-1]['consent_ref'], 'olivier 2026-11-26')

    def test_init_cli_refuse_et_n_ecrit_pas(self):
        ancien = (reprise_gh.DEFAULT_POLICY_PATH, reprise_gh.DEFAULT_AUDIT_PATH)
        reprise_gh.DEFAULT_POLICY_PATH = self.policy
        reprise_gh.DEFAULT_AUDIT_PATH = self.audit
        try:
            with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
                code = reprise_gh.main([
                    '--actor', 'metaai', 'init', '--write-actors', 'metaai'])
            self.assertEqual(code, 1)
            fake.assert_not_called()
            self.assertFalse(os.path.exists(self.policy))
        finally:
            reprise_gh.DEFAULT_POLICY_PATH, reprise_gh.DEFAULT_AUDIT_PATH = ancien


if __name__ == '__main__':
    unittest.main()
