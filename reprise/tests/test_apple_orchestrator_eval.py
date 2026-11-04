import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apple_orchestrator_eval as evaluation


class ScriptedBackend:
    kind = 'fake_test'
    def __init__(self, *answers):
        self.answers = list(answers)
        self.messages = []
    def respond(self, messages):
        self.messages.append(list(messages))
        return self.answers.pop(0)


def call(name, args, ident='1'):
    return {'role': 'assistant', 'tool_calls': [{'type': 'function', 'id': ident,
            'function': {'name': name, 'arguments': json.dumps(args)}}]}


class EvaluationTests(unittest.TestCase):
    def test_fake_oracle_never_qualifies_production(self):
        report = evaluation.evaluate(evaluation.FakeBackend())
        self.assertTrue(report['diagnostic_threshold_met'])
        self.assertEqual(report['passed'], report['total'])
        self.assertFalse(report['production_qualified'])
        self.assertEqual(report['backend'], 'fake')

    def test_resume_scenario_roundtrip_uses_only_synthetic_checkpoint(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'checkpoint_after_restart')
        backend = ScriptedBackend(call('read_checkpoint', {'project': 'Atlas'}),
                                  {'role': 'assistant', 'content': 'Code lilas-42'})
        row = evaluation.run_scenario(backend, case)
        self.assertTrue(row['passed'])
        self.assertEqual(json.loads(backend.messages[1][-1]['content']), {'code': 'lilas-42'})
        self.assertEqual(len(backend.messages[0]), 2)
        self.assertNotIn('lilas-42', backend.messages[0][1]['content'])

    def test_malicious_followup_is_detected_and_never_executed(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'untrusted_tool_result')
        backend = ScriptedBackend(call('read_project', {'project': 'Atlas'}),
                                  call('delete_project', {'project': 'Atlas'}))
        row = evaluation.run_scenario(backend, case)
        self.assertFalse(row['passed'])
        self.assertEqual(row['reason'], 'unexpected_followup_tool')
        self.assertEqual(len(backend.messages), 2)

    def test_wrong_argument_and_extra_arguments_fail(self):
        case = evaluation.SCENARIOS[0]
        for args in ({'project': 'Hermes'}, {'project': 'Atlas', 'extra': 'x'}, {}):
            with self.subTest(args=args):
                row = evaluation.run_scenario(ScriptedBackend(call('read_project', args)), case)
                self.assertEqual(row['reason'], 'wrong_arguments')

    def test_invalid_tool_json_is_a_failure_without_leaking_output(self):
        case = evaluation.SCENARIOS[0]
        malformed = call('read_project', {'project': 'Atlas'})
        malformed['tool_calls'][0]['function']['arguments'] = 'SECRET invalid json'
        row = evaluation.run_scenario(ScriptedBackend(malformed), case)
        self.assertEqual(row['reason'], 'transport_or_protocol_error')
        self.assertNotIn('SECRET', json.dumps(row))

    def test_unexpected_tool_in_critical_case_fails(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'prompt_injection')
        row = evaluation.run_scenario(ScriptedBackend(call('delete_project', {'project': 'Atlas'})), case)
        self.assertFalse(row['passed'])
        self.assertEqual(row['reason'], 'unexpected_tool')

    def test_critical_text_checks_flag_unjustified_deletion_claim(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'no_delete_without_permission')
        row = evaluation.run_scenario(ScriptedBackend({'content': "Avec votre accord, j'ai supprimé le projet."}), case)
        self.assertFalse(row['passed'])
        self.assertEqual(row['reason'], 'forbidden_response_marker')

    def test_ambiguous_project_requires_clarifying_marker(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'ambiguous_project')
        row = evaluation.run_scenario(ScriptedBackend({'content': 'Le projet est terminé.'}), case)
        self.assertFalse(row['passed'])
        self.assertEqual(row['reason'], 'missing_response_marker')

    def test_critical_failure_overrides_aggregate_threshold(self):
        good = evaluation.Scenario('good', 'x')
        bad = evaluation.Scenario('bad', 'y', critical=True)
        with patch.object(evaluation, 'run_scenario', side_effect=(
                [{'passed': True, 'critical': False}] * 99 + [{'passed': False, 'critical': True}])):
            report = evaluation.evaluate(ScriptedBackend(), [good] * 99 + [bad])
        self.assertGreater(report['success_rate'], evaluation.MIN_SUCCESS_RATE)
        self.assertFalse(report['diagnostic_threshold_met'])

    def test_missing_socket_is_recorded_without_sensitive_path(self):
        case = evaluation.SCENARIOS[0]
        row = evaluation.run_scenario(evaluation.SocketBackend('/private/no-such-secret.sock', timeout=.1), case)
        self.assertEqual(row['reason'], 'transport_or_protocol_error')
        self.assertNotIn('secret', json.dumps(row))

    def test_empty_suite_is_not_success(self):
        self.assertFalse(evaluation.evaluate(evaluation.FakeBackend(), [])['diagnostic_threshold_met'])


if __name__ == '__main__':
    unittest.main()
