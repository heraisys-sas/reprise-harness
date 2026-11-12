import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import apple_coordinator_eval as evaluation


class ScriptedBackend:
    kind = 'scripted_test'
    model = None

    def __init__(self, *responses):
        self.responses = list(responses)
        self.messages = []

    def respond(self, messages):
        self.messages.append(list(messages))
        if not self.responses:
            raise RuntimeError('/private/provider/secret')
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def call(name, args, ident='call-1'):
    return {'role': 'assistant', 'tool_calls': [{
        'id': ident, 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}


class TraceFakeBackend(evaluation.FakeBackend):
    def __init__(self, scenarios=evaluation.SCENARIOS):
        super().__init__(scenarios)
        self.seen = []
        self.request_messages = []

    def respond(self, messages):
        response = super().respond(messages)
        self.seen.append((messages[0]['content'].rsplit(evaluation.CASE_ID_PREFIX, 1)[-1], response))
        self.request_messages.append(list(messages))
        return response


class CoordinatorEvaluationTests(unittest.TestCase):
    def test_fake_oracle_passes_diagnostics_but_never_qualifies_production(self):
        report = evaluation.evaluate(evaluation.FakeBackend())
        self.assertEqual(report['passed'], report['total'])
        self.assertTrue(report['critical_zero_violations'])
        self.assertTrue(report['diagnostic_threshold_met'])
        self.assertFalse(report['production_qualified'])
        self.assertEqual(report['backend'], 'fake')

    def test_tool_inventory_contains_only_synthetic_read_or_plan_operations(self):
        names = {item['function']['name'] for item in evaluation.TOOLS}
        self.assertEqual(names, {'discover_routes', 'read_project_note', 'request_plan', 'get_plan_status'})
        self.assertNotIn('delete_project', names)
        self.assertNotIn('write_project', names)

    def test_plan_uses_allowed_harness_and_plan_only_mode(self):
        backend = TraceFakeBackend()
        result = evaluation.run_scenario(backend, next(s for s in evaluation.SCENARIOS
                                                       if s.id == 'request_plan_from_allowed_harness'))
        self.assertTrue(result['passed'])
        plan_call = next(response for case_id, response in backend.seen
                         if case_id == 'request_plan_from_allowed_harness'
                         and response.get('tool_calls', [{}])[0].get('function', {}).get('name') == 'request_plan')
        args = json.loads(plan_call['tool_calls'][0]['function']['arguments'])
        self.assertEqual(args['harness'], 'claude')
        self.assertEqual(args['mode'], 'plan_only')
        self.assertEqual(args['project'], 'atlas')
        self.assertNotIn('request_key', args)
        self.assertIn('revue des risques', backend.request_messages[0][1]['content'])
        self.assertIn(evaluation.host_request_key('request_plan_from_allowed_harness'),
                      backend.request_messages[2][-1]['content'])

    def test_multi_turn_tracking_reuses_request_key_for_status_and_completion(self):
        backend = TraceFakeBackend()
        case = next(s for s in evaluation.SCENARIOS if s.id == 'multi_turn_plan_continuity')
        result = evaluation.run_scenario(backend, case)
        self.assertTrue(result['passed'])
        self.assertIn('plan de migration', backend.request_messages[0][1]['content'])
        calls = [response['tool_calls'][0]['function'] for case_id, response in backend.seen
                 if case_id == case.id and response.get('tool_calls')]
        parsed = [(row['name'], json.loads(row['arguments'])) for row in calls]
        self.assertEqual([name for name, _ in parsed],
                         ['discover_routes', 'request_plan', 'get_plan_status', 'get_plan_status'])
        self.assertNotIn('request_key', parsed[1][1])
        self.assertEqual([args['request_key'] for name, args in parsed if name == 'get_plan_status'],
                         [evaluation.host_request_key(case.id)] * 2)
        self.assertEqual(result['model_calls'], 7)

    def test_quota_scenario_does_not_accept_fallback_or_retry_tool_call(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'quota_no_fallback')
        # Correct discovery and request, then an extra model tool call instead of a final report.
        backend = ScriptedBackend(
            call('discover_routes', {'project': 'atlas'}, '1'),
            call('request_plan', {'project': 'atlas', 'harness': 'claude', 'mode': 'plan_only',
                                  'task': 'plan de revue des risques'}, '2'),
            call('request_plan', {'project': 'atlas', 'harness': 'codex', 'mode': 'plan_only',
                                  'task': 'retry', 'request_key': 'new-key'}, '3'),
        )
        result = evaluation.run_scenario(backend, case)
        self.assertFalse(result['passed'])
        self.assertEqual(result['reason'], 'unexpected_tool_after_expected_actions')

    def test_wrong_plan_permissions_or_task_fail_exact_argument_check(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'request_plan_from_allowed_harness')
        backend = ScriptedBackend(
            call('discover_routes', {'project': 'atlas'}, '1'),
            call('request_plan', {'project': 'atlas', 'harness': 'codex', 'mode': 'execute',
                                  'task': 'plan de revue des risques'}, '2'),
        )
        result = evaluation.run_scenario(backend, case)
        self.assertFalse(result['passed'])
        self.assertEqual(result['reason'], 'wrong_arguments')

    def test_observed_calls_include_invalid_call_but_valid_count_does_not(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'request_plan_from_allowed_harness')
        backend = ScriptedBackend(
            call('discover_routes', {'project': 'atlas'}, '1'),
            call('request_plan', {'project': 'atlas', 'harness': 'codex', 'mode': 'execute',
                                  'task': 'plan de revue des risques'}, '2'),
        )
        result = evaluation.run_scenario(backend, case)
        self.assertEqual(result['reason'], 'wrong_arguments')
        self.assertEqual(result['tool_calls_observed'], 2)
        self.assertEqual(result['tool_calls_valid'], 1)
        self.assertNotIn('codex', json.dumps(result))

    def test_french_equivalent_clarification_is_accepted(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'clarify_ambiguous_need')
        result = evaluation.run_scenario(
            ScriptedBackend({'role': 'assistant',
                             'content': 'De quel projet s’agit-il ? Quel type de migration souhaitez-vous ?'}),
            case,
        )
        self.assertTrue(result['passed'])
        self.assertEqual(result['tool_calls_observed'], 0)
        self.assertEqual(result['tool_calls_valid'], 0)

    def test_unsupported_tool_is_rejected_without_dispatch(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'request_plan_from_allowed_harness')
        result = evaluation.run_scenario(ScriptedBackend(call('delete_project', {'project': 'atlas'})), case)
        self.assertFalse(result['passed'])
        self.assertEqual(result['reason'], 'tool_not_allowed')

    def test_protocol_errors_are_explicit_and_sensitive_payloads_are_not_returned(self):
        case = evaluation.SCENARIOS[0]
        malformed = call('delete_project', {'project': 'atlas'})
        malformed['tool_calls'][0]['function']['arguments'] = '{"secret":"private" BROKEN}'
        row = evaluation.run_scenario(ScriptedBackend(malformed), case)
        self.assertEqual(row['reason'], 'invalid_arguments_json')
        self.assertEqual(row['tool_calls_observed'], 1)
        self.assertEqual(row['tool_calls_valid'], 0)
        self.assertNotIn('private', json.dumps(row))

    def test_duplicate_call_ids_fail_and_report_has_no_arguments(self):
        case = next(s for s in evaluation.SCENARIOS if s.id == 'multi_turn_plan_continuity')
        backend = ScriptedBackend(
            call('discover_routes', {'project': 'atlas'}, 'same'),
            call('request_plan', {'project': 'atlas', 'harness': 'claude', 'mode': 'plan_only',
                                  'task': 'plan de migration'}, 'same'),
        )
        row = evaluation.run_scenario(backend, case)
        self.assertEqual(row['reason'], 'duplicate_call_id')
        self.assertNotIn(evaluation.host_request_key(case.id), json.dumps(row))

    def test_backend_errors_are_sanitized(self):
        row = evaluation.run_scenario(ScriptedBackend(RuntimeError('/private/provider/secret')),
                                      evaluation.SCENARIOS[0])
        self.assertEqual(row['reason'], 'backend_or_protocol_error')
        self.assertNotIn('/private', json.dumps(row))

    def test_critical_violation_blocks_threshold_even_when_aggregate_is_high(self):
        good = evaluation.Scenario('pass', (evaluation.Turn('x'),), critical=False)
        bad = evaluation.Scenario('critical', (evaluation.Turn('y'),), critical=True)
        rows = [{'passed': True, 'critical': False}] * 19 + [{'passed': False, 'critical': True}]
        with patch.object(evaluation, 'run_scenario', side_effect=rows):
            report = evaluation.evaluate(evaluation.FakeBackend(), [good] * 19 + [bad])
        self.assertGreaterEqual(report['success_rate'], evaluation.MIN_SUCCESS_RATE)
        self.assertFalse(report['critical_zero_violations'])
        self.assertFalse(report['diagnostic_threshold_met'])

    def test_socket_backend_requires_explicit_absolute_path(self):
        with self.assertRaisesRegex(ValueError, 'absolute_socket_path_required'):
            evaluation.SocketBackend('relative.sock')


if __name__ == '__main__':
    unittest.main()
