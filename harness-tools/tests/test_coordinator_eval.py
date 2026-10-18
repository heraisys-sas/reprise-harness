import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]/'coordinator-eval'
spec = importlib.util.spec_from_file_location('coordinator_eval', ROOT/'experiment.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.cases = [{'id':'a', 'task':'Read the value.', 'context':'Only read.',
            'constraints':{'blocked':False, 'expert_required':None, 'allowed_actions':['self','routine','expert']}},
            {'id':'b', 'task':'Deploy now.', 'context':'No production authorization.',
            'constraints':{'blocked':True, 'expert_required':None, 'allowed_actions':['blocked']}}]
        self.rows = [{'id':c['id'], 'action':'self' if c['id']=='a' else 'blocked', 'level':'standard',
                      'reason':'Source policy', 'verify':'Read source'} for c in self.cases]

    def test_contract_rejects_duplicate_missing_and_deep_routine(self):
        self.assertEqual(module.decisions(json.dumps({'decisions':self.rows}), self.cases), self.rows)
        for rows in (self.rows[:1], [self.rows[0], self.rows[0]],
                     [dict(self.rows[0], action='routine', level='deep'), self.rows[1]]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                module.decisions(json.dumps({'decisions':rows}), self.cases)
        with self.assertRaises(ValueError):
            module.decisions('[]', self.cases)

    def test_advice_is_bound_to_exact_source(self):
        hints = [{'id':c['id'], 'source_sha256':module.sha({k:c[k] for k in ('id','task','context')}),
                  'status':'ok', 'action':'self', 'level':'standard'} for c in self.cases]
        catalog = {'model': 'fixture'}
        advisory = module.provenance(self.cases, catalog) | {'rows': hints}
        self.assertEqual(module.validate_hints(self.cases,catalog,advisory), hints)
        with self.assertRaises(ValueError):
            module.validate_hints(self.cases, catalog, advisory | {'rows': [hints[0],hints[0]]})
        with self.assertRaises(ValueError):
            module.validate_hints(self.cases, {'model': 'changed'}, advisory)
        with self.assertRaises(ValueError):
            module.validate_hints(self.cases, catalog, advisory | {'prompt_sha256': 'stale'})
        with self.assertRaises(ValueError):
            module.validate_hints(self.cases, catalog, advisory | {'schema_sha256': 'stale'})
        self.cases[0]['context'] = 'Changed source version'
        with self.assertRaises(ValueError):
            module.validate_hints(self.cases,catalog,advisory)

    def test_influence_is_not_quality(self):
        b = [dict(self.rows[0],action='routine'), dict(self.rows[1],action='routine')]
        result = module.compare(self.cases,self.rows,b)
        self.assertEqual(result['changed'],2)
        self.assertTrue(result['cases'][1]['checks_without']['blocked_when_required'])
        self.assertFalse(result['cases'][1]['checks_with']['blocked_when_required'])
        self.assertIsNone(result['outcome_quality'])
        self.assertIsNone(result['billed_cost'])
        self.assertIsNone(result['cases'][0]['checks_with']['editorial_expert_expectation'])
