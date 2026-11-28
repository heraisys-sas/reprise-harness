# -*- coding: utf-8 -*-
"""Tests du pont MCP stdio du service gh (#270) : acteur fixe, double porte, sans reseau."""

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
import reprise_gh_mcp

TOKEN = 'ghp_FAUX_JETON_270_ne_doit_jamais_sortir'


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


def _frame(ident, method, params=None):
    message = {'jsonrpc': '2.0', 'id': ident, 'method': method}
    if params is not None:
        message['params'] = params
    return (json.dumps(message) + '\n').encode('utf-8')


def _read_text(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _call(ident, name, arguments):
    return _frame(ident, 'tools/call', {'name': name, 'arguments': arguments})


class GhMcpTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.policy = os.path.join(self.dir, 'gh-policy.json')
        self.audit = os.path.join(self.dir, 'gh-audit.jsonl')
        self.provider = mock.Mock(return_value=TOKEN)

    def server(self, actor='autoclaw', allow_write=False):
        return reprise_gh_mcp.GhServer(actor, allow_write=allow_write, policy_path=self.policy,
                                       audit_path=self.audit, token_provider=self.provider)

    def run_frames(self, server, *frames):
        out = io.BytesIO()
        reprise_gh_mcp.serve(server, io.BytesIO(b''.join(frames)), out)
        raw = out.getvalue()
        return raw, [json.loads(line) for line in raw.splitlines() if line.strip()]

    def audit_lines(self):
        if not os.path.exists(self.audit):
            return []
        return [json.loads(x) for x in _read_text(self.audit).splitlines() if x.strip()]

    def tool_names(self, server):
        _, replies = self.run_frames(server, _frame(1, 'tools/list'))
        return [tool['name'] for tool in replies[0]['result']['tools']], replies[0]['result']['tools']

    def test_initialize_et_ping(self):
        _, replies = self.run_frames(self.server(), _frame(1, 'initialize', {}), _frame(2, 'ping'))
        self.assertEqual(replies[0]['result']['serverInfo']['name'], 'reprise-gh')
        self.assertEqual(replies[1]['result'], {})

    def test_sans_allow_write_aucun_outil_d_ecriture(self):
        # Acteur pourtant liste en ecriture : le drapeau seul ferme la porte.
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw']}, self.policy)
        server = self.server(allow_write=False)
        names, _ = self.tool_names(server)
        self.assertEqual(names, ['reprise_gh_read'])
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            _, replies = self.run_frames(server, _call(2, 'reprise_gh_comment',
                                                       {'repo': 'o/r', 'number': 1, 'body': 'x'}))
        self.assertTrue(replies[0]['result']['isError'])
        fake.assert_not_called()
        self.provider.assert_not_called()

    def test_aucun_schema_ne_porte_actor_et_argument_actor_refuse(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw']}, self.policy)
        server = self.server(allow_write=True)
        names, tools = self.tool_names(server)
        self.assertEqual(names, ['reprise_gh_read', 'reprise_gh_comment', 'reprise_gh_create_issue'])
        for tool in tools:
            self.assertFalse(tool['inputSchema']['additionalProperties'])
            self.assertNotIn('actor', tool['inputSchema']['properties'])
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            _, replies = self.run_frames(
                server,
                _call(2, 'reprise_gh_read', {'path': '/repos/o/r', 'actor': 'claude'}),
                _call(3, 'reprise_gh_comment', {'repo': 'o/r', 'number': 1, 'body': 'x',
                                                'actor': 'claude'}),
                _call(4, 'reprise_gh_read', {'path': '/repos/o/r', 'extra': 1}))
        for reply in replies:
            self.assertTrue(reply['result']['isError'])
            self.assertIn('Argument inconnu', reply['result']['content'][0]['text'])
        fake.assert_not_called()
        self.provider.assert_not_called()

    def test_ecriture_refusee_acteur_hors_write_actors(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['claude']}, self.policy)
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            _, replies = self.run_frames(
                self.server(actor='metaai', allow_write=True),
                _call(1, 'reprise_gh_comment', {'repo': 'o/r', 'number': 270, 'body': 'x'}),
                _call(2, 'reprise_gh_create_issue', {'repo': 'o/r', 'title': 't', 'body': 'b'}))
        for reply in replies:
            self.assertTrue(reply['result']['isError'])
            self.assertIn('Ecriture refusee', reply['result']['content'][0]['text'])
        fake.assert_not_called()
        self.provider.assert_not_called()
        lines = self.audit_lines()
        self.assertEqual([x['status'] for x in lines], ['refused', 'refused'])
        self.assertTrue(all(x['actor'] == 'metaai' and x['channel'] == 'mcp' for x in lines))

    def test_ecriture_ok_double_porte_et_politique_relue(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw']}, self.policy)
        seen = []

        def fake_urlopen(req, timeout):
            seen.append((req.get_method(), req.full_url, json.loads(req.data)))
            return _FakeResponse({'number': 300, 'html_url': 'https://github.com/o/r/issues/300',
                                  'body': 'long'}, status=201)

        server = self.server(allow_write=True)
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen):
            _, replies = self.run_frames(
                server,
                _call(1, 'reprise_gh_create_issue', {'repo': 'o/r', 'title': 'Titre', 'body': 'Corps',
                                                     'labels': ['agent:claude']}),
                _call(2, 'reprise_gh_comment', {'repo': 'o/r', 'number': 270, 'body': 'Bonjour'}))
            # Politique relue a chaque appel : retrait de l'acteur = refus immediat.
            reprise_gh.save_policy({'enabled': True, 'write_actors': []}, self.policy)
            _, refused = self.run_frames(
                server, _call(3, 'reprise_gh_comment', {'repo': 'o/r', 'number': 270, 'body': 'x'}))
        self.assertFalse(replies[0]['result']['isError'])
        self.assertEqual(json.loads(replies[0]['result']['content'][0]['text']),
                         {'status': 201, 'number': 300, 'html_url': 'https://github.com/o/r/issues/300'})
        self.assertFalse(replies[1]['result']['isError'])
        self.assertEqual(seen[0][0], 'POST')
        self.assertTrue(seen[0][1].endswith('/repos/o/r/issues'))
        self.assertEqual(seen[0][2]['labels'], ['agent:claude'])
        self.assertTrue(seen[1][1].endswith('/repos/o/r/issues/270/comments'))
        self.assertEqual(len(seen), 2)
        self.assertTrue(refused[0]['result']['isError'])
        statuses = [(x['status'], x['actor'], x['channel']) for x in self.audit_lines()]
        self.assertEqual(statuses, [(201, 'autoclaw', 'mcp'), (201, 'autoclaw', 'mcp'),
                                    ('refused', 'autoclaw', 'mcp')])

    def test_read_refuse_post(self):
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            _, replies = self.run_frames(
                self.server(allow_write=True),
                _call(1, 'reprise_gh_read', {'path': '/repos/o/r/issues', 'method': 'POST'}))
        self.assertTrue(replies[0]['result']['isError'])
        self.assertIn('GET ou HEAD', replies[0]['result']['content'][0]['text'])
        fake.assert_not_called()
        self.provider.assert_not_called()

    def test_chemin_hors_hote_refuse_avant_jeton_issue_291(self):
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            _, replies = self.run_frames(
                self.server(),
                _call(1, 'reprise_gh_read', {'path': '.evil.example/x'}),
                _call(2, 'reprise_gh_read', {'path': '//evil.example/x'}))
        for reply in replies:
            self.assertTrue(reply['result']['isError'])
            self.assertIn('Chemin API invalide', reply['result']['content'][0]['text'])
        fake.assert_not_called()
        self.provider.assert_not_called()

    def test_lecture_jeton_absent_de_la_reponse_et_de_l_audit(self):
        seen = {}

        def fake_urlopen(req, timeout):
            seen['auth'] = req.headers.get('Authorization')
            return _FakeResponse({'number': 270, 'title': 'Service gh'})

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen):
            raw, replies = self.run_frames(
                self.server(), _call(1, 'reprise_gh_read', {'path': '/repos/o/r/issues/270'}))
        self.assertIn(TOKEN, seen['auth'])
        result = replies[0]['result']
        self.assertFalse(result['isError'])
        self.assertEqual(json.loads(result['content'][0]['text'])['data']['number'], 270)
        self.assertNotIn(TOKEN.encode(), raw)
        self.assertNotIn(TOKEN, _read_text(self.audit))
        line = self.audit_lines()[0]
        self.assertEqual((line['actor'], line['channel'], line['method'], line['status']),
                         ('autoclaw', 'mcp', 'GET', 200))

    def test_tools_call_accepte_meta_et_refuse_les_cles_inconnues(self):
        """Les clients MCP envoient `_meta` (progressToken) ; seule une clé inconnue est refusée."""
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen',
                               lambda req, timeout: _FakeResponse({'number': 270})):
            _, replies = self.run_frames(
                self.server(),
                _frame(1, 'tools/call', {'name': 'reprise_gh_read', '_meta': {'progressToken': 7},
                                         'arguments': {'path': '/repos/o/r/issues/270'}}),
                _frame(2, 'tools/call', {'name': 'reprise_gh_read', 'arguments': {'path': '/x'},
                                         'actor': 'claude'}),
                _frame(3, 'tools/call', {'name': 'reprise_gh_read', '_meta': 'x', 'arguments': {}}))
        self.assertFalse(replies[0]['result']['isError'])
        self.assertEqual(replies[1]['error']['code'], -32602)
        self.assertEqual(replies[2]['error']['code'], -32602)

    def test_erreur_interne_generique_sans_trace(self):
        provider = mock.Mock(side_effect=RuntimeError('detail ' + TOKEN))
        server = reprise_gh_mcp.GhServer('autoclaw', policy_path=self.policy,
                                         audit_path=self.audit, token_provider=provider)
        raw, replies = self.run_frames(server, _call(1, 'reprise_gh_read', {'path': '/repos/o/r'}))
        self.assertTrue(replies[0]['result']['isError'])
        self.assertIn('détails non exposés', replies[0]['result']['content'][0]['text'])
        self.assertNotIn(TOKEN.encode(), raw)
        self.assertNotIn(b'Traceback', raw)

    def test_sortie_bornee_et_tronquee(self):
        def fake_urlopen(req, timeout):
            return _FakeResponse({'blob': 'x' * 100000})

        with mock.patch.object(reprise_gh.urllib.request, 'urlopen', fake_urlopen):
            _, replies = self.run_frames(self.server(), _call(1, 'reprise_gh_read', {'path': '/repos/o/r'}))
        text = replies[0]['result']['content'][0]['text']
        self.assertLess(len(text), reprise_gh_mcp.MAX_OUTPUT_CHARS + 200)
        self.assertIn('sortie tronquée', text)

    def test_trame_trop_longue_refusee(self):
        big = _call(1, 'reprise_gh_read', {'path': '/' + 'a' * reprise_gh_mcp.MAX_INPUT_BYTES})
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            raw, _ = self.run_frames(self.server(), big, _frame(2, 'ping'))
        self.assertEqual(raw, b'')
        fake.assert_not_called()
        self.provider.assert_not_called()

    def test_corps_trop_long_refuse(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw']}, self.policy)
        body = 'x' * (reprise_gh_mcp.MAX_BODY_CHARS + 1)
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            _, replies = self.run_frames(
                self.server(allow_write=True),
                _call(1, 'reprise_gh_comment', {'repo': 'o/r', 'number': 1, 'body': body}),
                _call(2, 'reprise_gh_create_issue', {'repo': 'o/r', 'title': 't', 'body': body}))
        for reply in replies:
            self.assertTrue(reply['result']['isError'])
            self.assertIn('trop long', reply['result']['content'][0]['text'])
        fake.assert_not_called()
        self.provider.assert_not_called()

    def test_arguments_types_refuses(self):
        reprise_gh.save_policy({'enabled': True, 'write_actors': ['autoclaw']}, self.policy)
        cases = [
            ('reprise_gh_comment', {'repo': 'o/r', 'number': True, 'body': 'x'}),
            ('reprise_gh_comment', {'repo': 'o/r', 'number': '1', 'body': 'x'}),
            ('reprise_gh_comment', {'repo': 'o/r', 'number': 0, 'body': 'x'}),
            ('reprise_gh_create_issue', {'repo': 'o/r', 'title': ' ', 'body': 'b'}),
            ('reprise_gh_create_issue', {'repo': 'o/r', 'title': 't', 'body': 'b', 'labels': 'bug'}),
            ('reprise_gh_read', {}),
        ]
        with mock.patch.object(reprise_gh.urllib.request, 'urlopen') as fake:
            _, replies = self.run_frames(self.server(allow_write=True),
                                         *[_call(i, n, a) for i, (n, a) in enumerate(cases)])
        self.assertEqual(len(replies), len(cases))
        self.assertTrue(all(reply['result']['isError'] for reply in replies))
        fake.assert_not_called()
        self.provider.assert_not_called()

    def test_acteur_obligatoire_et_motif(self):
        parser = reprise_gh_mcp.build_parser()
        self.assertEqual(parser.parse_args(['--actor', 'auto.claw-1_x']).actor, 'auto.claw-1_x')
        self.assertFalse(parser.parse_args(['--actor', 'claude']).allow_write)
        for argv in ([], ['--actor', ''], ['--actor', 'a b'], ['--actor', 'claude;rm']):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                parser.parse_args(argv)
        with self.assertRaises(ValueError):
            reprise_gh_mcp.GhServer('')


if __name__ == '__main__':
    unittest.main()
