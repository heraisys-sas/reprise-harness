"""Census CDP : validation dry-run, rapport, invariant lecture seule."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import cdp_census
import cdp_targets

VALID = {
    'enabled': False, 'origin': 'https://example.test',
    'chat_url_pattern': '/chat/{chat_id}', 'profile': 'chrome',
    'max_prompt_chars': 12000,
    'selectors': {'composer': '#c', 'send': '#s',
                  'user_message': '#u', 'assistant_message': '#a'},
}


class FakeTransport:
    """Transport CDP factice : enregistre les méthodes, renvoie des réponses.

    Refuse toute méthode d'écriture (Input.*) : le test échoue si le census
    tente une saisie ou un envoi — c'est l'assertion de l'invariant.
    """

    def __init__(self, origin='https://mammouth.ai'):
        self.calls = []
        self._sid = 0
        self.origin = origin

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method.startswith('Input.'):
            raise AssertionError(f'méthode interdite en census : {method}')
        if method == 'Target.getTargets':
            return {'targetInfos': [{'type': 'page', 'targetId': 't1',
                                     'url': self.origin + '/chat'}]}
        if method == 'Target.attachToTarget':
            self._sid += 1
            return {'sessionId': f's{self._sid}'}
        return {}

    def evaluate(self, expression):
        self.calls.append(('Runtime.evaluate', {'expression': expression}))
        return {
            'registry_selectors': {'composer': True, 'send': False,
                                   'user_message': False, 'assistant_message': False},
            'alternatives': {'composer': [{'tag': 'div', 'contenteditable': 'true'}],
                             'send': [{'tag': 'button', 'attrs': {}}]},
            'title': 'Exemple', 'url': self.origin + '/chat',
        }

    def close(self):
        pass


class FakeProc:
    def pid(self):
        return 0


class DryRunTests(unittest.TestCase):
    def test_valid_target_produces_plan(self):
        text = cdp_census.dry_run('mammouth')
        self.assertIn('config valide', text)
        self.assertIn('https://mammouth.ai', text)
        self.assertIn('composer', text)

    def test_unknown_target_raises(self):
        with self.assertRaises(RuntimeError):
            cdp_census.dry_run('inconnu')

    def test_target_without_selectors_is_rejected(self):
        # Une entrée sans sélecteurs échoue à la validation du registre.
        with self.assertRaises(ValueError):
            cdp_targets._validate('x', {k: v for k, v in VALID.items()
                                        if k != 'selectors'})
        with self.assertRaises(RuntimeError):
            cdp_census.dry_run('inconnu')

    def test_cli_dry_run_prints_without_chrome(self):
        with mock.patch.object(cdp_census.claude_cdp_pipe, 'launch_chrome') as launch:
            rc = cdp_census.main(['mammouth', '--dry-run'])
            self.assertEqual(rc, 0)
            launch.assert_not_called()


class CensusTests(unittest.TestCase):
    def census_with_fake(self, transport=None):
        launched = {}
        fake = transport if transport is not None else FakeTransport()

        def fake_launch(profile=None, origin=None):
            fake.origin = origin
            launched['profile'] = profile
            launched['origin'] = origin
            return fake, FakeProc()

        with mock.patch.object(cdp_census.claude_cdp_pipe, 'launch_chrome', fake_launch), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'terminate_chrome') as term:
            result = cdp_census.census('mammouth', timeout_s=5)
        term.assert_called_once()
        return result, fake, launched

    def test_census_collects_structured_readonly_data(self):
        result, fake, launched = self.census_with_fake()
        self.assertEqual(result['target'], 'mammouth')
        self.assertFalse(result['enabled'])
        self.assertEqual(result['collected']['registry_selectors']['composer'], True)
        self.assertIn('alternatives', result['collected'])
        self.assertIn('title', result['collected'])
        self.assertIn('url', result['collected'])
        self.assertEqual(launched['origin'], 'https://mammouth.ai')
        self.assertIn('mammouth', str(launched['profile']))

    def test_census_is_readonly_no_input_no_click(self):
        _, fake, _ = self.census_with_fake()
        for method, _params in fake.calls:
            self.assertFalse(method.startswith('Input.'),
                             f'méthode d\'écriture émise : {method}')
        expressions = [p['expression'] for m, p in fake.calls
                       if m == 'Runtime.evaluate']
        for expression in expressions:
            for forbidden in ('insertText', 'click(', '.submit', 'dispatchEvent',
                              'textContent =', 'innerHTML ='):
                self.assertNotIn(forbidden, expression)

    def test_census_uses_target_profile(self):
        result, _, launched = self.census_with_fake()
        self.assertIn('mammouth', str(launched['profile']))

    def test_census_unknown_target_raises_without_chrome(self):
        with mock.patch.object(cdp_census.claude_cdp_pipe, 'launch_chrome') as launch:
            with self.assertRaises(RuntimeError):
                cdp_census.census('inconnu')
            launch.assert_not_called()


class ScriptedTransport(FakeTransport):
    """Transport renvoyant une séquence de réponses d'évaluation.

    La dernière réponse de la séquence est répétée à l'infini ; chaque
    evaluate() consomme l'élément suivant. Refuse Input.* comme la base.
    """

    def __init__(self, responses, **kwargs):
        super().__init__(**kwargs)
        self.responses = list(responses)
        self.evaluations = 0

    def evaluate(self, expression):
        self.evaluations += 1
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


CHALLENGE_PAGE = {
    'registry_selectors': {'composer': False, 'send': False,
                           'user_message': False, 'assistant_message': False},
    'alternatives': {'composer': [], 'send': []},
    'title': 'Just a moment...',
    'url': 'https://mammouth.ai/chat',
    'challenge_suspected': True,
    'challenge_elements': 2,
    'iframes': [{'id': 'cf-chl-widget-1', 'class': 'challenge-platform'}],
}

CLEAN_PAGE = {
    'registry_selectors': {'composer': True, 'send': True,
                           'user_message': True, 'assistant_message': True},
    'alternatives': {'composer': [{'tag': 'div'}], 'send': []},
    'title': 'Claude', 'url': 'https://mammouth.ai/chat',
    'challenge_suspected': False, 'challenge_elements': 0, 'iframes': [],
}

EMPTY_PAGE = {
    'registry_selectors': {'composer': False, 'send': False,
                           'user_message': False, 'assistant_message': False},
    'alternatives': {'composer': [], 'send': []},
    'title': 'Claude', 'url': 'https://mammouth.ai/chat',
    'challenge_suspected': False, 'challenge_elements': 0, 'iframes': [],
}


class VerdictTests(unittest.TestCase):
    def run_census(self, responses, **kwargs):
        transport = ScriptedTransport(responses)
        with mock.patch.object(cdp_census.claude_cdp_pipe, 'launch_chrome',
                               lambda profile=None, origin=None: (transport, FakeProc())), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'terminate_chrome'), \
             mock.patch.object(cdp_census.time, 'sleep'):
            result = cdp_census.census('mammouth', timeout_s=5, **kwargs)
        return result, transport

    def test_challenge_page_reports_blocked_antibot(self):
        result, transport = self.run_census([CHALLENGE_PAGE])
        self.assertEqual(result['verdict'], 'blocked_antibot')
        self.assertTrue(result['collected']['challenge_suspected'])
        self.assertTrue(result['collected']['iframes'])
        # Poll épuisé : 1 évaluation initiale + 5 réévaluations.
        self.assertEqual(transport.evaluations, 6)

    def test_healthy_page_reports_ready(self):
        result, transport = self.run_census([CLEAN_PAGE])
        self.assertEqual(result['verdict'], 'ready')
        self.assertEqual(transport.evaluations, 1)

    def test_page_without_selectors_or_challenge_is_stale(self):
        result, _ = self.run_census([EMPTY_PAGE])
        self.assertEqual(result['verdict'], 'selectors_stale')

    def test_poll_stops_once_challenge_clears(self):
        result, transport = self.run_census(
            [CHALLENGE_PAGE, CLEAN_PAGE], wait_for_ready_s=5)
        self.assertEqual(result['verdict'], 'ready')
        self.assertEqual(transport.evaluations, 2)
        self.assertFalse(result['collected']['challenge_suspected'])


class ReportTests(unittest.TestCase):
    def test_report_writes_dated_json_and_returns_summary(self):
        directory = tempfile.mkdtemp(prefix='cdp-census-')
        fake = FakeTransport()
        with mock.patch.object(cdp_census, 'CENSUS_ROOT', Path(directory)), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'launch_chrome',
                               lambda profile=None, origin=None: (fake, FakeProc())), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'terminate_chrome'):
            summary = cdp_census.census_report('mammouth', timeout_s=5)
        files = list(Path(directory).glob('mammouth-*.json'))
        self.assertEqual(len(files), 1)
        payload = json.loads(files[0].read_text())
        self.assertEqual(payload['target'], 'mammouth')
        self.assertIn('collected', payload)
        self.assertIn('sélecteur registre composer', summary)
        self.assertIn(str(files[0]), summary)

    def test_report_creates_missing_directory(self):
        directory = Path(tempfile.mkdtemp(prefix='cdp-census-')) / 'sous' / 'rep'
        with mock.patch.object(cdp_census, 'CENSUS_ROOT', directory), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'launch_chrome',
                               lambda profile=None, origin=None: (FakeTransport(), FakeProc())), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'terminate_chrome'):
            cdp_census.census_report('mammouth', timeout_s=5)
        self.assertTrue(directory.is_dir())

    def test_report_summary_distinguishes_blocked_from_stale(self):
        directory = tempfile.mkdtemp(prefix='cdp-census-')
        blocked = ScriptedTransport([CHALLENGE_PAGE])
        with mock.patch.object(cdp_census, 'CENSUS_ROOT', Path(directory)), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'launch_chrome',
                               lambda profile=None, origin=None: (blocked, FakeProc())), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'terminate_chrome'), \
             mock.patch.object(cdp_census.time, 'sleep'):
            summary = cdp_census.census_report('mammouth', timeout_s=5,
                                               wait_for_ready_s=1)
        self.assertIn('challenge antibot probable', summary)
        self.assertIn('blocked_antibot', summary)
        stale = ScriptedTransport([EMPTY_PAGE])
        with mock.patch.object(cdp_census, 'CENSUS_ROOT', Path(directory)), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'launch_chrome',
                               lambda profile=None, origin=None: (stale, FakeProc())), \
             mock.patch.object(cdp_census.claude_cdp_pipe, 'terminate_chrome'):
            summary = cdp_census.census_report('mammouth', timeout_s=5)
        self.assertIn('possiblement obsolètes', summary)


if __name__ == '__main__':
    unittest.main()
