import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).resolve().parents[1]/'routing-eval/run.py'
spec = importlib.util.spec_from_file_location('routing_eval_runner', path)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RoutingPlanTests(unittest.TestCase):
    def test_manifest_rejects_escape_symlink_and_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root/'prompt').write_text('fix bug')
            (root/'fixture').mkdir()
            (root/'link').symlink_to(root/'prompt')
            for name in ('../outside', str(root/'prompt'), 'link'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    runner.local_path(root, name)
            case = {'id': 'a', 'prompt_file': 'prompt', 'fixture_dir': 'fixture',
                    'checks': [{'name': 'test', 'argv': ['python3', '-c', 'raise SystemExit(1)']}]}
            (root/'cases.json').write_text(json.dumps([case, case]))
            with self.assertRaises(ValueError):
                runner.load_cases(root)

    def test_only_real_assertion_failure_rejects_starter(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root/'fixture').mkdir()
            for status, expected in ((0, False), (1, True), (2, False)):
                case = {'id': 'fixture', 'fixture_dir': 'fixture', 'checks': [
                    {'name': 'test', 'argv': ['python3', '-c', f'raise SystemExit({status})']}]}
                result = runner.check_starters(root, [case])
                self.assertEqual(result['all_starters_rejected'], expected)
                self.assertEqual(result['model_calls'], 0)
