# -*- coding: utf-8 -*-
"""Tests de la politique gh v2 (#270 lot 2) : decide, priorites, migration v1, audit."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import reprise_gh


def _read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


class DecideTest(unittest.TestCase):
    def setUp(self):
        self.policy = reprise_gh.default_policy()

    def decide(self, actor, op, auth='ok', policy=None):
        return reprise_gh.decide(policy or self.policy, actor, op, 'o/r', auth)

    def test_defauts(self):
        self.assertEqual(self.decide('metaai', 'read', 'missing'), ('allow', 'rule', 'default-read'))
        self.assertEqual(self.decide('autoclaw', 'issue.comment'), ('allow', 'rule', 'default-issue'))
        self.assertEqual(self.decide('autoclaw', 'issue.create')[0], 'allow')
        self.assertEqual(self.decide('autoclaw', 'pull.review')[0], 'allow')
        self.assertEqual(self.decide('autoclaw', 'pull.merge')[0], 'review_required')
        self.assertEqual(self.decide('autoclaw', 'api.raw'), ('deny', 'default', None))
        self.assertEqual(self.decide('operator', 'api.raw', 'tty'), ('allow', 'rule', 'default-api-raw'))

    def test_ecriture_exige_un_jeton_valide_ou_un_terminal(self):
        for auth in ('missing', 'invalid', 'revoked', None):
            self.assertEqual(self.decide('autoclaw', 'issue.comment', auth)[:2],
                             ('deny', 'auth_%s' % (auth or 'missing')))
        self.assertEqual(self.decide('operator', 'issue.comment', 'no_tty')[:2],
                         ('deny', 'operator_requires_tty'))
        self.assertEqual(self.decide('operator', 'read', 'no_tty')[0], 'allow')
        # Un acteur a jeton ne peut pas se reclamer d'un terminal.
        self.assertEqual(self.decide('autoclaw', 'issue.comment', 'tty')[1], 'auth_tty')

    def test_operation_inconnue_default_absent_desactive(self):
        self.assertEqual(self.decide('autoclaw', 'repo.delete')[:2], ('deny', 'unknown_op'))
        bare = reprise_gh.validate_policy({'version': 2, 'rules': []})
        self.assertEqual(self.decide('autoclaw', 'read', policy=bare), ('deny', 'no_default', None))
        off = dict(self.policy, enabled=False)
        self.assertEqual(self.decide('autoclaw', 'read', policy=off)[:2], ('deny', 'disabled'))
        self.assertEqual(self.decide('  ', 'read')[:2], ('deny', 'actor_required'))

    def test_priorite_deny_review_allow_filtre_et_veto(self):
        policy = reprise_gh.validate_policy({'version': 2, 'default': 'allow', 'deny_actors': ['metaai'], 'rules': [
            {'id': 'a', 'op': 'issue.*', 'decision': 'allow'},
            {'id': 'r', 'op': 'issue.comment', 'decision': 'review_required'},
            {'id': 'd', 'op': 'issue.comment', 'decision': 'deny', 'actors': ['claude']},
        ]})
        self.assertEqual(self.decide('claude', 'issue.comment', policy=policy), ('deny', 'rule', 'd'))
        self.assertEqual(self.decide('autoclaw', 'issue.comment', policy=policy),
                         ('review_required', 'rule', 'r'))
        self.assertEqual(self.decide('autoclaw', 'issue.create', policy=policy), ('allow', 'rule', 'a'))
        self.assertEqual(self.decide('metaai', 'read', 'missing', policy=policy),
                         ('deny', 'deny_actor', 'deny_actors'))

    def test_politique_invalide_refusee(self):
        for policy in ({'version': 2, 'rules': [{'op': 'repo.delete', 'decision': 'allow'}]},
                       {'version': 2, 'rules': [{'op': 'read', 'decision': 'peut-etre'}]},
                       {'version': 2, 'rules': [{'op': 'read', 'decision': 'allow', 'repos': ['x']}]},
                       {'version': 2, 'frozen_repos': []},
                       {'version': 3}, [], {'version': 2, 'deny_actors': 'metaai'},
                       {'deny_actors': ['metaai'], 'rules': []},
                       {'enabled': True, 'write_actors': [], 'rules': []}):
            with self.subTest(policy=policy), self.assertRaises(reprise_gh.GhError):
                reprise_gh.validate_policy(policy)


class MigrationV1Test(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'gh-policy.json')
        patcher = mock.patch.object(reprise_gh, '_V1_WARNED', [])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_v1_traduite_en_memoire_sans_reecriture(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['metaai']}, self.path)
        before = (_read(self.path), os.stat(self.path).st_mtime_ns)
        err = io.StringIO()
        with redirect_stderr(err):
            first = reprise_gh.load_policy(self.path)
            second = reprise_gh.load_policy(self.path)
        self.assertEqual(first, reprise_gh.default_policy())
        self.assertEqual(second, first)
        self.assertEqual((_read(self.path), os.stat(self.path).st_mtime_ns), before)
        self.assertEqual(err.getvalue().count('write_actors ignore'), 1)
        # write_actors ignore : metaai sans jeton reste en lecture seule.
        self.assertEqual(reprise_gh.decide(first, 'metaai', 'issue.comment', None, 'missing')[0], 'deny')

    def test_v1_desactivee_reste_desactivee(self):
        reprise_gh.save_policy({'enabled': False}, self.path)
        policy = reprise_gh.load_policy(self.path)
        self.assertFalse(policy['enabled'])

    def test_politique_illisible_refuse_tout_et_audite(self):
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write('{pas du json')
        audit = os.path.join(self.dir, 'gh-audit.jsonl')
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            with self.assertRaisesRegex(reprise_gh.GhError, 'illisible'):
                reprise_gh.request('autoclaw', 'GET', '/repos/o/r', token_provider=lambda: 'SECRET',
                                   policy_path=self.path, audit_path=audit)
        fake.assert_not_called()
        line = json.loads(_read(audit))
        self.assertEqual((line['status'], line['decision'], line['reason']),
                         ('refused', 'deny', 'policy_unavailable'))


class RequestAuditTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.policy = os.path.join(self.dir, 'gh-policy.json')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')
        reprise_gh.save_policy(reprise_gh.default_policy(), self.policy)

    def request(self, *args, **kwargs):
        kwargs.update(policy_path=self.policy, audit_path=self.audit, token_provider=lambda: 'SECRET')
        return reprise_gh.request(*args, **kwargs)

    def test_review_required_refuse_sans_reseau(self):
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            with self.assertRaisesRegex(reprise_gh.GhError, 'revue requise'):
                self.request('autoclaw', 'PUT', '/repos/o/r/pulls/1/merge', op='pull.merge',
                             auth='ok', token_id='0123456789abcdef')
            with self.assertRaisesRegex(reprise_gh.GhError, 'GET ou HEAD'):
                self.request('autoclaw', 'POST', '/repos/o/r/issues', op='read', auth='ok')
        fake.assert_not_called()
        lines = [json.loads(x) for x in _read(self.audit).splitlines()]
        self.assertEqual([(x['op'], x['decision'], x['reason']) for x in lines],
                         [('pull.merge', 'review_required', 'rule'), ('read', 'deny', 'read_requires_get')])
        self.assertEqual(lines[0]['token_id'], '0123456789abcdef')
        self.assertNotIn('SECRET', _read(self.audit))

    def test_audit_porte_op_decision_regle_auth(self):
        class Response(object):
            status = 200

            def read(self):
                return b'{}'

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', lambda req, timeout: Response()):
            self.request('metaai', 'GET', '/repos/o/r', auth='missing', audit_extra={'channel': 'mcp'})
        line = json.loads(_read(self.audit))
        self.assertEqual({k: line[k] for k in ('op', 'decision', 'reason', 'rule', 'auth', 'token_id',
                                               'channel', 'status')},
                         {'op': 'read', 'decision': 'allow', 'reason': 'rule', 'rule': 'default-read',
                          'auth': 'missing', 'token_id': None, 'channel': 'mcp', 'status': 200})


if __name__ == '__main__':
    unittest.main()
