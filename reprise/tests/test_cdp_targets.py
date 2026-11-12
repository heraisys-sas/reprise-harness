"""Registre de cibles CDP : validation stricte, désactivé par défaut, override."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import cdp_targets
import claude_desktop_apply as transport

VALID = {
    'enabled': False, 'origin': 'https://example.test',
    'chat_url_pattern': '/chat/{chat_id}', 'profile': 'chrome',
    'max_prompt_chars': 12000,
    'selectors': {'composer': '#c', 'send': '#s',
                  'user_message': '#u', 'assistant_message': '#a'},
}


class RegistryTests(unittest.TestCase):
    def test_shipped_targets_exist_and_are_disabled_by_default(self):
        registry = cdp_targets._registry()
        self.assertIn('claude-web', registry)
        self.assertIn('mammouth', registry)
        self.assertTrue(all(entry['enabled'] is False for entry in registry.values()))
        self.assertEqual(cdp_targets.enabled_names(), [])

    def test_shipped_claude_web_preserves_historical_selectors(self):
        entry = cdp_targets.load('claude-web')
        self.assertEqual(entry['origin'], 'https://claude.ai')
        self.assertEqual(entry['chat_url_pattern'], '/chat/{chat_id}')
        self.assertEqual(entry['profile'], 'chrome')
        self.assertEqual(entry['selectors']['composer'],
                         '[data-testid="compose-textarea"]')

    def test_load_unknown_or_invalid_name_returns_none(self):
        self.assertIsNone(cdp_targets.load('inconnu'))
        self.assertIsNone(cdp_targets.load(''))
        self.assertIsNone(cdp_targets.load(None))
        self.assertIsNone(cdp_targets.load(42))

    def test_load_returns_validated_copy(self):
        entry = cdp_targets.load('mammouth')
        self.assertIsNot(entry, cdp_targets.load('mammouth'))
        self.assertEqual(entry['profile'], 'mammouth')
        self.assertEqual(entry['selectors']['composer'],
                         '[data-testid="composer-input"]')


class ValidationTests(unittest.TestCase):
    def test_valid_entry_passes(self):
        self.assertEqual(cdp_targets._validate('x', dict(VALID)),
                         dict(VALID, selectors=dict(VALID['selectors'])))

    def _invalid(self, field, value):
        entry = dict(VALID)
        if value is None:
            del entry[field]
        else:
            entry[field] = value
        with self.assertRaises(ValueError):
            cdp_targets._validate('x', entry)

    def test_origin_must_be_https_with_netloc_and_no_path(self):
        for origin in ('http://example.test', 'https://', 'https://example.test/chat',
                       'ftp://example.test', None):
            self._invalid('origin', origin)

    def test_chat_url_pattern_rules(self):
        for pattern in ('/chat/', 'chat/{chat_id}', '/chat/{chat_id}?x',
                        '/chat/{chat_id}#top', 'https://example.test/{chat_id}', None):
            self._invalid('chat_url_pattern', pattern)

    def test_required_selectors(self):
        for field in cdp_targets._REQUIRED_SELECTORS:
            entry = dict(VALID, selectors=dict(VALID['selectors']))
            del entry['selectors'][field]
            with self.assertRaises(ValueError):
                cdp_targets._validate('x', entry)
        self._invalid('selectors', None)
        self._invalid('selectors', {'composer': ''})

    def test_max_prompt_chars_bounds(self):
        for value in (0, -1, 12001, '5000', 1.5, True):
            self._invalid('max_prompt_chars', value)
        self.assertEqual(cdp_targets._validate(
            'x', dict(VALID, max_prompt_chars=1))['max_prompt_chars'], 1)

    def test_profile_rules(self):
        for profile in ('', None, 'a/b', '../evil', '.hidden', 'sub/dir'):
            self._invalid('profile', profile)


class OverrideTests(unittest.TestCase):
    def override(self, payload):
        directory = tempfile.mkdtemp(prefix='cdp-targets-')
        path = Path(directory) / 'targets.json'
        path.write_text(json.dumps(payload))
        return mock.patch.object(cdp_targets, '_OVERRIDE_FILE', path)

    def test_override_wins_and_deep_merges_selectors(self):
        with self.override({'targets': {'claude-web': {
                'enabled': True,
                'selectors': {'assistant_message': '[data-testid="new-one"]'}}}}):
            entry = cdp_targets.load('claude-web')
            self.assertTrue(entry['enabled'])
            self.assertEqual(entry['selectors']['assistant_message'], '[data-testid="new-one"]')
            self.assertEqual(entry['selectors']['composer'],
                             '[data-testid="compose-textarea"]')
            self.assertEqual(entry['origin'], 'https://claude.ai')
            self.assertEqual(cdp_targets.enabled_names(), ['claude-web'])

    def test_override_can_declare_new_target(self):
        with self.override({'targets': {'autre': dict(VALID, enabled=True)}}):
            self.assertTrue(cdp_targets.load('autre')['enabled'])
            self.assertIn('autre', cdp_targets.enabled_names())

    def test_broken_override_is_ignored(self):
        directory = tempfile.mkdtemp(prefix='cdp-targets-')
        path = Path(directory) / 'targets.json'
        path.write_text('{not json')
        with mock.patch.object(cdp_targets, '_OVERRIDE_FILE', path):
            self.assertEqual(cdp_targets.enabled_names(), [])
        with mock.patch.object(cdp_targets, '_OVERRIDE_FILE', path.with_name('absent.json')):
            self.assertEqual(cdp_targets.enabled_names(), [])

    def test_invalid_override_entry_is_rejected_loudly(self):
        with self.override({'targets': {'x': dict(VALID, origin='http://x')}}):
            with self.assertRaises(ValueError):
                cdp_targets._registry()


if __name__ == '__main__':
    unittest.main()


class RouteGateTests(unittest.TestCase):
    def test_apply_disabled_target_reports_route_disabled(self):
        result = transport.apply('Prompt de test', '00000000-0000-0000-0000-000000000001', target='claude-web')
        self.assertEqual(result['ok'], False)
        self.assertEqual(result["stage"], 'route_disabled')

    def test_apply_unknown_target_reports_route_disabled(self):
        result = transport.apply('Prompt de test', '00000000-0000-0000-0000-000000000001', target='inconnu')
        self.assertEqual(result['ok'], False)
        self.assertEqual(result["stage"], 'route_disabled')


class TargetDomGenericity(unittest.TestCase):
    """Expressions réelles avec les sélecteurs de chaque cible, sur un DOM synthétique."""

    @unittest.skipUnless(shutil.which('node') or (
        Path.home() / '.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node').exists(),
        'node introuvable')
    def test_registry_selectors_on_synthetic_dom(self):
        fixture = Path(__file__).with_name('fixtures') / 'cdp_targets_dom.mjs'
        registry = cdp_targets._registry()
        payload_selectors = {'claude': registry['claude-web']['selectors'],
                             'mammouth': registry['mammouth']['selectors']}
        scripts = {}
        for key, name in (('claude', 'claude-web'), ('mammouth', 'mammouth')):
            config = registry[name]
            sel, origin = config['selectors'], config['origin']
            scripts[f'insert_{key}'] = ("(async () => {" + transport.js_helpers(sel, origin) +
                "\nconst text = " + json.dumps(f'prompt {key}') +
                ", composerSelector = " + transport._js_str(sel['composer']) + ";" + """
      const el = document.querySelector(composerSelector) || composer();
      if (!el) return {inserted: false};
      el.focus();
      let executed = false;
      try { executed = document.execCommand('insertText', false, text); } catch (error) {}
      if (!executed || readText(el).length === 0) { el.textContent = text; }
      return {inserted: canon(readText(el)) === canon(text)};
    })()""")
            scripts[f'arm_{key}'] = ("(function(){" + transport.js_helpers(sel) +
                "\nconst composerSelector = " + transport._js_str(sel['composer']) +
                ", sendSelector = " + transport._js_str(sel['send']) + ";" + """
      const el = document.querySelector(composerSelector) || composer();
      const buttons = [...document.querySelectorAll(sendSelector)];
      const button = buttons.length === 1 ? buttons[0] : null;
      if (!el || !button) return {ready: false, stage: 'composeur'};
      if (!button.disabled) return {ready: true, stage: 'composeur'};
      return {ready: false, stage: 'readiness_timeout'};
    })()""")
            scripts[f'confirm_{key}'] = ("(async () => {" + transport.js_helpers(sel) +
                "\nconst messageSelector = " + transport._js_str(sel['user_message']) + """
      const messages = () => document.querySelectorAll(messageSelector).length;
      const before = {"n": 0};
      const deadline = Date.now() + 4000;
      let outcome = {confirmed: false, url: location.pathname, user_messages: messages(), user_messages_before: before.n, composer_left: -1};
      while (Date.now() < deadline) {
        const el = composer();
        outcome.composer_left = el ? readText(el).length : 0;
        const n = messages();
        if (outcome.composer_left === 0 && n > before.n) { outcome.confirmed = true; outcome.user_messages = n; break; }
      }
      return outcome;
    })()""")
        node = shutil.which('node') or str(
            Path.home() / '.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node')
        result = subprocess.run([node, str(fixture)],
                                input=json.dumps({'selectors': payload_selectors, 'scripts': scripts}),
                                capture_output=True, text=True, timeout=60, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertFalse(report['browser_used'])
        self.assertGreaterEqual(report['fixtures'], 64)
