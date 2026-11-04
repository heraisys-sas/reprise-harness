import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_orchestrator_memory as fixtures
from orchestrator_memory import Memory
from orchestrator_memory_service import MemoryService
from reprise_memory_adapter import Adapter


class AdapterTests(unittest.TestCase):
    def setUp(self):
        fixtures.MemoryTests.setUp(self)
        factory = lambda state: Memory(state, crypt=fixtures.fake_crypt)
        self.a = Adapter(self.state, self.p1, 'harness-a', allow_write=True, memory_factory=factory)
        self.b = Adapter(self.state, self.p1, 'harness-b', memory_factory=factory)
        self.other = Adapter(self.state, self.p2, 'harness-b', allow_write=True, memory_factory=factory)

    def tearDown(self):
        fixtures.MemoryTests.tearDown(self)

    def capture(self, text='Règle Atlas conservée', **extra):
        return self.a.call('reprise_memory_capture', dict(operation_id=fixtures.uid(), text=text,
                                                        source=fixtures.SOURCE, **extra))

    def test_shared_search_attribution_and_isolation(self):
        created = self.capture()
        ident = created['result']['id']
        found = self.b.call('reprise_memory_search', {'query': 'ATLAS', 'offset': 0})
        self.assertEqual(found['items'][0]['id'], ident)
        self.assertEqual(found['items'][0]['recorded_by']['harness_id'], 'harness-a')
        self.assertEqual(found['items'][0]['source'], fixtures.SOURCE)
        self.assertEqual(self.other.call('reprise_memory_search', {'query': 'Atlas', 'offset': 0})['items'], [])
        self.assertEqual(self.other.call('reprise_memory_get', {'fact_id': ident})['status'], 'not_found')
        self.assertEqual(self.b.call('reprise_memory_get', {'fact_id': ident, 'project_id': self.p1})['status'], 'invalid_request')

    def test_write_grant_source_spoof_and_retry(self):
        fields = dict(operation_id=fixtures.uid(), text='fait', source=fixtures.SOURCE)
        result = self.a.call('reprise_memory_capture', fields)
        self.assertEqual(self.a.call('reprise_memory_capture', fields), result)
        self.assertEqual(self.b.call('reprise_memory_capture', fields)['status'], 'permission_denied')
        self.assertNotIn('reprise_memory_capture', [t['name'] for t in self.b.tools()])
        fields['operation_id'] = fixtures.uid()
        fields['source'] = dict(fixtures.SOURCE, recorded_by={'harness_id': 'victim'})
        self.assertEqual(self.a.call('reprise_memory_capture', fields)['status'], 'invalid_request')

    def test_legacy_unknown_and_literal_search(self):
        created = self.store.create_fact(self.p1, fixtures.uid(), '100%_vérifié', fixtures.SOURCE)
        item = self.b.call('reprise_memory_get', {'fact_id': created['id']})['item']
        self.assertEqual(item['recorded_by'], {'status': 'unknown'})
        self.assertEqual(len(self.b.call('reprise_memory_search', {'query': '%_', 'offset': 0})['items']), 1)
        self.assertEqual(self.b.call('reprise_memory_search', {'query': '%not', 'offset': 0})['items'], [])
        self.assertEqual(self.b.call('reprise_memory_search', {'query': '', 'offset': 0})['status'], 'invalid_request')

    def test_maximal_fact_does_not_block_following_search_hits(self):
        long_text = 'Atlas' + 'a' * (16 * 1024 - 5)
        first = self.capture(long_text)['result']['id']
        second = self.capture('Atlas suivant')['result']['id']
        page = self.b.call('reprise_memory_search', {'query': 'Atlas', 'offset': 0})
        self.assertEqual([item['id'] for item in page['items']], [first, second])
        self.assertTrue(page['items'][0]['excerpt_truncated'])
        self.assertLessEqual(len(page['items'][0]['excerpt']), 256)
        self.assertEqual(self.b.call('reprise_memory_get', {'fact_id': first})['item']['text'], long_text)
