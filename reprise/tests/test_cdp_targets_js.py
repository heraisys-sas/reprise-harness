"""Exécute les expressions paramétrées par cible sur un DOM synthétique."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))
import cdp_targets
import claude_desktop_apply as transport


class Capture:
    def evaluate(self, expression):
        self.expression = expression
        return True


def expression_of(call):
    capture = Capture()
    call(capture)
    return capture.expression


def payload():
    payload = {'selectors': {}, 'scripts': {}}
    for fixture_key, name in (('claude', 'claude-web'), ('mammouth', 'mammouth')):
        entry = cdp_targets.load(name)
        selectors = entry['selectors']
        text = f'prompt {fixture_key}'
        payload['selectors'][fixture_key] = dict(selectors)
        payload['scripts'][f'insert_{fixture_key}'] = expression_of(
            lambda cdp, t=text, s=selectors: transport._insert(cdp, t, selectors=s))
        payload['scripts'][f'arm_{fixture_key}'] = expression_of(
            lambda cdp, s=selectors: transport._arm(cdp, selectors=s))
        payload['scripts'][f'confirm_{fixture_key}'] = expression_of(
            lambda cdp, s=selectors: transport._confirm(
                cdp, {'n': 0}, selectors=s))
    return payload


NODE_FALLBACKS = (
    Path.home() / '.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node',
)


def working_node():
    candidates = [shutil.which('node'), *[str(p) for p in NODE_FALLBACKS if p.exists()]]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            probe = subprocess.run([candidate, '-e', 'process.exit(0)'], capture_output=True, timeout=15)
            if probe.returncode == 0:
                return candidate
        except (OSError, subprocess.SubprocessError):
            pass
    return None


class TargetExpressions(unittest.TestCase):
    @unittest.skipUnless(working_node(), 'Node exécutable introuvable sur le PATH')
    def test_selector_sets_on_synthetic_dom(self):
        fixture = Path(__file__).with_name('fixtures') / 'cdp_targets_dom.mjs'
        result = subprocess.run([working_node(), str(fixture)], input=json.dumps(payload()),
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertFalse(report['browser_used'])
        self.assertGreaterEqual(report['fixtures'], 8)

    def test_registre_fournit_les_deux_jeux(self):
        data = payload()
        self.assertEqual(sorted(data['selectors']), ['claude', 'mammouth'])
        self.assertNotEqual(data['selectors']['claude'],
                            data['selectors']['mammouth'])

    def test_scaffolding_disabled_by_default(self):
        with mock.patch.object(cdp_targets, '_OVERRIDE_FILE',
                               Path('/nonexistent/targets-override.json')):
            self.assertEqual(cdp_targets.enabled_names(), [])
            for name in ('claude-web', 'mammouth'):
                self.assertFalse(cdp_targets.load(name)['enabled'])


if __name__ == '__main__':
    unittest.main()
