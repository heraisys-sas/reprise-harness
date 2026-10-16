import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]/'local-routing-eval'
spec = importlib.util.spec_from_file_location('local_routing_eval', ROOT/'evaluate.py')
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class LocalRoutingTests(unittest.TestCase):
    def test_exact_evidence_and_closed_tiers(self):
        raw = json.dumps({'tier': 'complex', 'evidence': 'migrate'})
        self.assertEqual(evaluation.validate(raw, 'Please migrate safely')['tier'], 'complex')
        for data in ([], None, 'text', {'tier': 'expert', 'evidence': 'migrate'},
                     {'tier': 'complex', 'evidence': 'not in task'},
                     {'tier': 'complex', 'evidence': ''},
                     {'tier': 'complex', 'evidence': 'x'*301}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                evaluation.validate(json.dumps(data), 'migrate'+'x'*400)

    def test_unknown_is_not_success(self):
        rows = [{'status': 'unavailable', 'critical': True, 'expected': 'complex', 'correct': None},
                {'status': 'ok', 'critical': True, 'expected': 'complex', 'tier': 'standard', 'correct': False}]
        self.assertEqual(evaluation.summarize(rows), {'total': 2, 'valid': 1, 'exact': 0,
                         'critical_total': 2, 'critical_underclassified': 1, 'unavailable_or_invalid': 1})

    def test_frozen_dataset_contract(self):
        rows = json.loads((ROOT/'cases.json').read_text())
        self.assertEqual(len(rows), 24)
        self.assertEqual(len({r['id'] for r in rows}), 24)
        for tier in evaluation.TIERS:
            self.assertEqual(sum(r['expected'] == tier for r in rows), 6)
        pairs = json.loads((ROOT/'resume-cases.json').read_text())
        self.assertEqual(len(pairs), 4)
        for row in pairs:
            self.assertTrue(row['instructions'])
            self.assertNotEqual(row['before_summary'], row['after_summary'])

    def test_local_process_limits(self):
        import sys
        code, out, err, stopped = evaluation.run_local([sys.executable, '-c', 'print("OK")'], '')
        self.assertEqual((code, out.strip(), stopped), (0, 'OK', None))
        _, _, _, stopped = evaluation.run_local([sys.executable, '-c', 'import time; time.sleep(5)'], '', timeout=.1)
        self.assertEqual(stopped, 'timeout')
        _, out, _, stopped = evaluation.run_local([sys.executable, '-c', 'print("x"*10000)'], '', limit=100)
        self.assertEqual(stopped, 'oversize')
        self.assertLessEqual(len(out), 101)

    def test_proposal_is_not_evidence_or_resume_success(self):
        row = {'status': 'invalid', 'critical': True, 'expected': 'complex',
               'candidate_tier': 'standard', 'candidate_correct': False, 'correct': None}
        pair = evaluation.pair_result({'id': 'pair'}, row, row)
        self.assertTrue(pair['proposal_invariant'])
        self.assertFalse(pair['both_proposals_expected'])
        self.assertFalse(pair['both_evidence_valid'])
        self.assertIsNone(pair['invariant'])
        self.assertEqual(evaluation.proposals([row])['critical_underclassified'], 1)
        self.assertEqual(evaluation.summarize([row])['valid'], 0)
