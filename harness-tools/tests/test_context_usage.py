import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from context_pack import prepare
from usage_report import report


class ContextTests(unittest.TestCase):
    def test_exact_instructions_and_citations(self):
        text = 'Constraint: preserve API.\n' * 12
        result = prepare('api.', [('AGENTS.md', 'Never delete.\n' * 1000)], [('code', text)], 1000)
        self.assertEqual(result['instructions'][0]['text'], 'Never delete.\n' * 1000)
        self.assertEqual(result['excerpts'][0]['text'], text)
        self.assertEqual(result['excerpts'][0]['line'], 1)

    def test_stable_prefix_and_budget(self):
        a = prepare('python', [('rules', 'keep')], [('a', 'python\n'*200)], 200)
        b = prepare('rust', [('rules', 'keep')], [('b', 'rust\n')], 200)
        self.assertEqual(a['prefix_sha256'], b['prefix_sha256'])
        self.assertLessEqual(a['excerpt_characters'], 200)
        self.assertGreater(a['omitted_blocks'], 0)

    def test_unknown_and_zero(self):
        a = {'request_id': 'a', 'usage': {'prompt_cache_hit_tokens': 0}}
        result = report([a])
        self.assertEqual(result['totals']['prompt_cache_hit_tokens'], 0)
        self.assertIsNone(result['totals']['prompt_cache_miss_tokens'])
        self.assertIsNone(result['totals']['billed_cost'])

    def test_duplicates_and_invalid(self):
        with self.assertRaises(ValueError):
            report([{'request_id': 'a'}, {'request_id': 'a'}])
        with self.assertRaises(ValueError):
            report([{'request_id': 'a', 'usage': {'completion_tokens': -1}}])

    def test_no_double_count_reasoning_or_currency(self):
        result = report([{'request_id': 'a', 'usage': {'completion_tokens': 20, 'reasoning_tokens': 10, 'billed_cost': 1}, 'currency': 'EUR'},
                         {'request_id': 'b', 'usage': {'completion_tokens': 30, 'billed_cost': 2}, 'currency': 'USD'}])
        self.assertEqual(result['totals']['completion_tokens'], 50)
        self.assertIsNone(result['totals']['reasoning_tokens'])
        self.assertIsNone(result['totals']['billed_cost'])

class HarnessUsageTests(unittest.TestCase):
    def test_native_usage_and_failed_attempt(self):
        from harness_usage import observations
        events = [
            {'seq': 0, 'type': 'step/start', 'time': 100, 'data': {'turn': 1, 'step': 1}},
            {'seq': 1, 'type': 'request/header', 'time': 110, 'data': {'header': {'config': {'provider': 'mock', 'model': 'm'}}}},
            {'seq': 2, 'type': 'assistant/attempt', 'time': 140, 'data': {'turn': 1, 'step': 1, 'stream': []}},
            {'seq': 3, 'type': 'assistant/message', 'time': 180, 'data': {'turn': 1, 'step': 1, 'usage': {'inputTokens': 20, 'cacheReadTokens': 40, 'outputTokens': 5}, 'stream': []}},
        ]
        rows = observations(events, 'test')
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['model'], 'm')
        self.assertEqual(rows[1]['usage']['prompt_cache_hit_tokens'], 40)
        self.assertIsNone(rows[1]['usage']['prompt_tokens'])
        self.assertIsNone(report(rows)['totals']['completion_tokens'])

    def test_disjoint_cache_writes(self):
        from harness_usage import observations
        event = {'seq': 0, 'type': 'assistant/message', 'time': 100, 'data': {
            'usage': {'inputTokens': 20, 'cacheReadTokens': 40, 'cacheWriteTokens': 10, 'outputTokens': 5}}}
        result = report(observations([event], 'writes'))
        self.assertEqual(result['totals']['prompt_tokens'], 70)
        self.assertEqual(result['totals']['prompt_cache_miss_tokens'], 30)
