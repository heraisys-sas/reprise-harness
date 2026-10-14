import sys
import importlib.util
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))

spec = importlib.util.spec_from_file_location('analysis', Path(__file__).parents[1] / 'local_analysis.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class AnalysisTests(unittest.TestCase):
    def test_requires_exact_evidence(self):
        value = module.validate('{"status":"partial","reason":"Tests manquants","evidence":"Tests non exécutés"}', 'Tests non exécutés')
        self.assertEqual(value['status'], 'partial')
        with self.assertRaises(ValueError):
            module.validate('{"status":"probable_failure","reason":"Inventé","evidence":"échec"}', 'Tout va bien')

    def test_invalid_status_and_missing_fields(self):
        for raw in ['{}', '{"status":"restart"}', 'pas du JSON']:
            with self.assertRaises((ValueError, KeyError)):
                module.validate(raw, 'source')
