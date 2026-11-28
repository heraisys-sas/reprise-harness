# -*- coding: utf-8 -*-
"""Tests du registre des jetons d'acteur gh (#270 lot 2) et de la CLI operateur, sans reseau."""

import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import reprise_gh
import reprise_gh_actors
import reprise_gh_mcp

REPO = Path(__file__).resolve().parents[2]


def _read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, 'state', 'gh-actors.sqlite')
        self.registry = reprise_gh_actors.ActorRegistry(self.path, repo_root=REPO)
        self.addCleanup(self.registry.close)

    def issue(self, actor='autoclaw', name='jeton', replace=False):
        result = self.registry.issue(actor, 'h-' + actor, os.path.join(self.dir, name), replace=replace)
        return reprise_gh_actors.read_token_file(result['token_file']), result

    def test_emission_fichier_0600_et_empreinte_seule(self):
        token, result = self.issue()
        info = os.stat(result['token_file'])
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode) & 0o077, 0)
        self.assertRegex(token, r'^rgh_%s_[A-Za-z0-9_-]{40,}$' % result['token_id'])
        secret = token.rsplit('_', 1)[-1]
        with open(self.path, 'rb') as handle:
            self.assertNotIn(token.encode(), handle.read())
        self.assertNotIn(secret, json.dumps(result))
        listing = json.dumps(self.registry.list())
        self.assertNotIn(secret, listing)
        self.assertNotIn('sha256', listing)
        self.assertEqual(reprise_gh_actors.verify(token, self.path),
                         ('ok', 'autoclaw', result['token_id'], 'h-autoclaw'))

    def test_emission_refusee(self):
        with self.assertRaisesRegex(reprise_gh_actors.ActorError, 'operator'):
            self.registry.issue('operator', 'cli', os.path.join(self.dir, 'op'))
        with self.assertRaisesRegex(reprise_gh_actors.ActorError, 'absolu'):
            self.registry.issue('autoclaw', 'h', 'relatif/jeton')
        open_dir = os.path.join(self.dir, 'ouvert')
        os.mkdir(open_dir, 0o755)
        os.chmod(open_dir, 0o755)
        with self.assertRaisesRegex(reprise_gh_actors.ActorError, '0700'):
            self.registry.issue('autoclaw', 'h', os.path.join(open_dir, 'jeton'))
        self.issue(name='existant')
        with self.assertRaisesRegex(reprise_gh_actors.ActorError, 'existe'):
            self.issue(name='existant')
        inside = reprise_gh_actors.ActorRegistry(self.path, repo_root=self.dir)
        try:
            with self.assertRaisesRegex(reprise_gh_actors.ActorError, 'depot'):
                inside.issue('autoclaw', 'h', os.path.join(self.dir, 'dans-le-depot'))
        finally:
            inside.close()
        self.assertFalse(os.path.exists(os.path.join(self.dir, 'op')))

    def test_verify_invalide_absent_revoque(self):
        token, result = self.issue()
        self.assertEqual(reprise_gh_actors.verify(None, self.path)[0], 'missing')
        self.assertEqual(reprise_gh_actors.verify('', self.path)[0], 'missing')
        forged = token[:-4] + ('AAAA' if not token.endswith('AAAA') else 'BBBB')
        self.assertEqual(reprise_gh_actors.verify(forged, self.path)[0], 'invalid')
        self.assertEqual(reprise_gh_actors.verify('rgh_ffffffffffffffff_' + 'x' * 43, self.path)[0],
                         'invalid')
        self.assertEqual(reprise_gh_actors.verify('pas-un-jeton', self.path)[0], 'invalid')
        absent = os.path.join(self.dir, 'absent.sqlite')
        self.assertEqual(reprise_gh_actors.verify(token, absent)[0], 'invalid')
        self.assertFalse(os.path.exists(absent))
        self.assertEqual(self.registry.revoke('autoclaw'), 1)
        self.assertEqual(reprise_gh_actors.verify(token, self.path)[:2], ('revoked', 'autoclaw'))

    def test_replace_revoque_les_autres_et_revoke_par_token_id(self):
        first, _ = self.issue(name='a')
        second, result = self.issue(name='b', replace=True)
        self.assertEqual(result['revoked_previous'], 1)
        self.assertEqual(reprise_gh_actors.verify(first, self.path)[0], 'revoked')
        self.assertEqual(reprise_gh_actors.verify(second, self.path)[0], 'ok')
        third, third_result = self.issue(name='c')
        self.assertEqual(self.registry.revoke('autoclaw', third_result['token_id']), 1)
        self.assertEqual(reprise_gh_actors.verify(third, self.path)[0], 'revoked')
        self.assertEqual(reprise_gh_actors.verify(second, self.path)[0], 'ok')


class TokenFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.secret = 'rgh_0123456789abcdef_' + 'S' * 43

    def write(self, name, content, mode=0o600):
        path = os.path.join(self.dir, name)
        with open(path, 'w', encoding='ascii') as handle:
            handle.write(content)
        os.chmod(path, mode)
        return path

    def test_absent_vaut_none(self):
        self.assertIsNone(reprise_gh_actors.read_token_file(os.path.join(self.dir, 'absent')))

    def test_mal_protege_refuse_sans_citer_le_secret(self):
        link = os.path.join(self.dir, 'lien')
        os.symlink(self.write('cible', self.secret), link)
        cases = [self.write('ouvert', self.secret, 0o644), self.write('lecture', self.secret, 0o400),
                 self.write('gros', 'x' * 101), link, 'relatif']
        for path in cases:
            with self.subTest(path=path), self.assertRaises(reprise_gh_actors.TokenFileError) as ctx:
                reprise_gh_actors.read_token_file(path)
            self.assertNotIn(self.secret, str(ctx.exception))
        with mock.patch.object(reprise_gh_actors.os, 'getuid', return_value=os.getuid() + 1), \
                self.assertRaisesRegex(reprise_gh_actors.TokenFileError, 'proprietaire'):
            reprise_gh_actors.read_token_file(self.write('autre', self.secret))


class OperatorCliTest(unittest.TestCase):
    """`reprise gh actors …` : operateur seul, terminal requis, aucun secret affiche."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.registry = os.path.join(self.dir, 'gh-actors.sqlite')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')
        self.policy = os.path.join(self.dir, 'gh-policy.json')
        for target, name, value in ((reprise_gh_actors, 'DEFAULT_ACTORS_PATH', self.registry),
                                    (reprise_gh_actors, 'REPO_ROOT', REPO),
                                    (reprise_gh, 'DEFAULT_AUDIT_PATH', self.audit),
                                    (reprise_gh, 'DEFAULT_POLICY_PATH', self.policy)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, argv, tty=True):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(reprise_gh, '_isatty', return_value=tty), \
                mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake, \
                redirect_stdout(out), redirect_stderr(err):
            code = reprise_gh.main(argv)
        fake.assert_not_called()
        return code, out.getvalue(), err.getvalue()

    def test_issue_list_revoke_sans_secret(self):
        out_path = os.path.join(self.dir, 'autoclaw.token')
        code, out, err = self.run_cli(['actors', 'issue', '--actor', 'autoclaw', '--harness', 'autoclaw',
                                       '--out', out_path])
        self.assertEqual((code, err), (0, ''))
        token = reprise_gh_actors.read_token_file(out_path)
        code, listing, _ = self.run_cli(['actors', 'list'], tty=False)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(listing)[0]['actor'], 'autoclaw')
        code, revoked, _ = self.run_cli(['actors', 'revoke', '--actor', 'autoclaw'])
        self.assertEqual(code, 0)
        self.assertIn(': 1', revoked)
        for text in (out, listing, revoked, _read(self.audit)):
            self.assertNotIn(token, text)
            self.assertNotIn(token.rsplit('_', 1)[-1], text)
        self.assertEqual(reprise_gh_actors.verify(token, self.registry)[0], 'revoked')

    def test_issue_refuse_sans_terminal_ou_hors_operateur(self):
        out_path = os.path.join(self.dir, 'x.token')
        code, _, err = self.run_cli(['actors', 'issue', '--actor', 'autoclaw', '--harness', 'h',
                                     '--out', out_path], tty=False)
        self.assertEqual(code, 1)
        self.assertIn('operator_requires_tty', err)
        code, _, _ = self.run_cli(['--actor', 'claude', 'actors', 'issue', '--actor', 'autoclaw',
                                   '--harness', 'h', '--out', out_path])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(out_path))

    def token(self, actor='autoclaw'):
        registry = reprise_gh_actors.ActorRegistry(self.registry, repo_root=REPO)
        try:
            result = registry.issue(actor, actor, os.path.join(self.dir, actor + '.token'))
        finally:
            registry.close()
        return result['token_file'], result['token_id']

    def test_token_file_acteur_du_registre_et_coherence(self):
        path, token_id = self.token()
        body = os.path.join(self.dir, 'corps.md')
        with open(body, 'w', encoding='utf-8') as handle:
            handle.write('Bonjour')
        seen = []

        def fake_urlopen(req, timeout):
            seen.append(req.full_url)

            class Response(object):
                status = 201

                def read(self):
                    return b'{"html_url": "https://github.com/o/r/issues/270#c"}'

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False
            return Response()

        err = io.StringIO()
        with mock.patch.object(reprise_gh, '_run_credential_fill', lambda host: 'password=GHSECRET'), \
                mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = reprise_gh.main(['--token-file', path, 'comment', '270', '--repo', 'o/r',
                                    '--body-file', body])
            contradiction = reprise_gh.main(['--token-file', path, '--actor', 'claude', 'comment', '270',
                                             '--repo', 'o/r', '--body-file', body])
        self.assertEqual((code, contradiction), (0, 1))
        self.assertEqual(len(seen), 1)
        self.assertIn('contredit', err.getvalue())
        line = json.loads(_read(self.audit).splitlines()[-1])
        self.assertEqual((line['actor'], line['op'], line['auth'], line['token_id'], line['channel']),
                         ('autoclaw', 'issue.comment', 'ok', token_id, 'cli'))
        secret = reprise_gh_actors.read_token_file(path)
        self.assertNotIn(secret, _read(self.audit) + err.getvalue())

    def test_token_file_revoque_refuse(self):
        path, _ = self.token()
        registry = reprise_gh_actors.ActorRegistry(self.registry, repo_root=REPO)
        registry.revoke('autoclaw')
        registry.close()
        code, _, err = self.run_cli(['--token-file', path, 'api', 'GET', '/repos/o/r'])
        self.assertEqual(code, 1)
        self.assertIn('revoked', err)


class BridgeStartupTest(unittest.TestCase):
    """main() du pont : jeton mal protege = refus de demarrer ; sinon lecture seule ou ecriture."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.registry = os.path.join(self.dir, 'gh-actors.sqlite')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')
        self.policy = os.path.join(self.dir, 'gh-policy.json')

    def main(self, *argv):
        err = io.StringIO()
        servers = []
        with mock.patch.object(reprise_gh_mcp.os, 'umask'), \
                mock.patch.object(reprise_gh_mcp, 'serve', lambda server, i, o: servers.append(server)), \
                redirect_stderr(err):
            code = reprise_gh_mcp.main(['--registry', self.registry, '--audit', self.audit,
                                        '--policy', self.policy] + list(argv))
        return code, err.getvalue(), servers

    def test_jeton_mal_protege_refuse_le_demarrage_sans_secret(self):
        registry = reprise_gh_actors.ActorRegistry(self.registry, repo_root=REPO)
        result = registry.issue('autoclaw', 'autoclaw', os.path.join(self.dir, 'jeton'))
        registry.close()
        secret = reprise_gh_actors.read_token_file(result['token_file'])
        os.chmod(result['token_file'], 0o644)
        code, err, servers = self.main('--actor', 'autoclaw', '--allow-write',
                                       '--token-file', result['token_file'])
        self.assertEqual((code, servers), (2, []))
        self.assertIn('demarrage refuse', err)
        self.assertNotIn(secret, err)
        os.chmod(result['token_file'], 0o600)
        code, err, servers = self.main('--actor', 'autoclaw', '--allow-write',
                                       '--token-file', result['token_file'])
        self.assertEqual((code, err), (0, ''))
        self.assertTrue(servers[0].allow_write)
        self.assertNotIn(secret, _read(self.audit))

    def test_operator_refuse_et_jeton_absent_lecture_seule(self):
        code, err, servers = self.main('--actor', 'operator')
        self.assertEqual((code, servers), (2, []))
        code, err, servers = self.main('--actor', 'autoclaw', '--allow-write',
                                       '--token-file', os.path.join(self.dir, 'absent'))
        self.assertEqual(code, 0)
        self.assertFalse(servers[0].allow_write)
        self.assertEqual(servers[0].auth, 'missing')
        start = json.loads(_read(self.audit).splitlines()[-1])
        self.assertEqual((start['method'], start['auth'], start['writes'], start['allow_write_flag']),
                         ('START', 'missing', False, True))


if __name__ == '__main__':
    unittest.main()
