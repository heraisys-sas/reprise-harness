"""Issue #255 : commandes clone-* de la CLI reprise.

Contrat testé :
- clone-inspect / clone-plan / clone-verify délèguent à app_clones.main, impriment du JSON
  et n'écrivent jamais le manifeste (aucun appel réel à inventory/plan/verify ni _save_manifest) ;
- clone-apply transmet les noms cibles à apply() ; le vrai apply (seul écrivain du manifeste)
  est toujours substitué dans les tests, aucun clone réel n'est touché ;
- le bloc top-level clone-* de reprise court-circuite avant main() et transfère les arguments.
"""
import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))

import app_clones

_SCRIPT_PATH = Path(__file__).parents[1] / 'reprise'


class CloneCliCommandsTests(unittest.TestCase):
    """Chaque commande renvoie son JSON ; le manifeste n'est jamais écrit."""

    def run_main(self, command_args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = app_clones.main(command_args)
        return code, output.getvalue()

    def test_inspect_plan_verify_print_json_without_manifest_write(self):
        payloads = {
            'inspect': {'ok': True, 'clones': {'rechatgpt': {'present': True}}},
            'plan': {'ok': True, 'actions': [{'name': 'rechatgpt', 'action': 'recreate'}]},
            'verify': {'ok': True, 'clones': {'rechatgpt': {'ok': True, 'problems': []}}},
        }
        functions = {'inspect': 'inventory', 'plan': 'plan', 'verify': 'verify'}
        for action, payload in payloads.items():
            with self.subTest(action=action):
                with patch.object(app_clones, functions[action], return_value=payload) as reader, \
                     patch.object(app_clones, '_save_manifest') as save_manifest:
                    code, printed = self.run_main([action])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(printed), payload)
                reader.assert_called_once_with()
                save_manifest.assert_not_called()

    def test_apply_dispatches_names_and_real_apply_stays_mocked(self):
        payload = {'ok': True, 'applied': ['rechatgpt']}
        with patch.object(app_clones, 'apply', return_value=payload) as apply_mock, \
             patch.object(app_clones, '_save_manifest') as save_manifest:
            code, printed = self.run_main(['apply', 'rechatgpt'])
        self.assertEqual(code, 0)
        apply_mock.assert_called_once_with(names=['rechatgpt'])
        self.assertEqual(json.loads(printed), payload)
        save_manifest.assert_not_called()


class CloneCliDispatchTests(unittest.TestCase):
    """Le bloc top-level de reprise transfère clone-* vers app_clones.main avant main()."""

    def dispatch(self, argv, main_mock):
        # Exécution directe du script : les blocs top-level doivent tourner avec
        # l'argv patché ; reload() ne marche pas ici (module hors sys.path, 3.13).
        namespace = {'__name__': 'reprise_clone_cli_dispatch', '__file__': str(_SCRIPT_PATH)}
        with patch.object(sys, 'argv', ['reprise', *argv]), \
             patch.object(app_clones, 'main', main_mock):
            with self.assertRaises(SystemExit) as exit_context:
                exec(compile(_SCRIPT_PATH.read_text(), str(_SCRIPT_PATH), 'exec'), namespace)
        return exit_context.exception.code

    def test_clone_inspect_short_circuits_before_main(self):
        main_mock = Mock(return_value=0)
        code = self.dispatch(['clone-inspect'], main_mock)
        self.assertEqual(code, 0)
        main_mock.assert_called_once_with(['inspect'])

    def test_clone_apply_forwards_target_names(self):
        main_mock = Mock(return_value=0)
        code = self.dispatch(['clone-apply', 'rechatgpt'], main_mock)
        self.assertEqual(code, 0)
        main_mock.assert_called_once_with(['apply', 'rechatgpt'])


class VerifyFrozenTests(unittest.TestCase):
    """verify() n'exige un gel que pour kind='clone' (#256)."""

    def run_verify(self, kind):
        state = {'app': {
            'present': True, 'kind': kind, 'issues': [],
            'target_version': '1.2.3',
        }}
        with patch.object(app_clones, 'inventory', return_value=state), \
             patch.object(app_clones, '_load_manifest', return_value={'clones': {}}):
            return app_clones.verify()

    def test_patched_app_without_freeze_is_not_not_frozen(self):
        outcome = self.run_verify('patched')
        self.assertEqual(outcome['clones']['app']['problems'], [])
        self.assertTrue(outcome['clones']['app']['ok'])

    def test_clone_without_freeze_still_flagged_not_frozen(self):
        outcome = self.run_verify('clone')
        self.assertEqual(outcome['clones']['app']['problems'], ['not_frozen'])
        self.assertFalse(outcome['clones']['app']['ok'])


if __name__ == '__main__':
    unittest.main()
