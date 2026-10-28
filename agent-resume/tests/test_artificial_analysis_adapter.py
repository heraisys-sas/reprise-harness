import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import artificial_analysis_adapter as aa
import external_rankings
import routing_events


def memory_db():
    db = sqlite3.connect(':memory:')
    routing_events.setup(db)
    return db


class AATests(unittest.TestCase):
    def test_load_key_missing_raises_without_default(self):
        with mock.patch.object(aa, 'key_path', return_value='/nonexistent/key'):
            with self.assertRaises(ValueError):
                aa.load_key()

    def test_fetch_requires_json_content_type(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = {'Content-Type': 'text/html'}
        with mock.patch.object(aa, 'load_key', return_value='k'), \
             mock.patch('artificial_analysis_adapter.urllib.request.urlopen',
                        return_value=response):
            with self.assertRaises(ValueError):
                aa.fetch()

    def test_model_id_from_slug_rejects_bad_names(self):
        self.assertEqual(aa._model_id({'slug': 'gemini-4-carbon'}), 'gemini-4-carbon')
        # Sans slug, le nom est normalisé (espaces → _) : accepté s'il reste conforme.
        self.assertEqual(aa._model_id({'name': 'Gemini 4 Carbon'}), 'Gemini_4_Carbon')
        self.assertIsNone(aa._model_id({'name': 'bad name / with <script>'}))

    def test_transform_maps_real_structure(self):
        payload = [{
            'slug': 'gemini-4-carbon',
            'pricing': {'price_1m_blended_3_to_1': 8, 'price_1m_input_tokens': 4},
            'median_time_to_first_token_seconds': 65.505,
            'evaluations': {'artificial_analysis_intelligence_index': 59.4},
        }, {'slug': 'minimal-model'}]
        records = aa.transform(payload)
        self.assertEqual(len(records), 3)
        by_metric = {r['metric']: r['value'] for r in records}
        self.assertEqual(by_metric['price_per_mtok'], 8.0)
        self.assertAlmostEqual(by_metric['latency_ms'], 65505.0)
        self.assertEqual(by_metric['quality_index'], 59.4)

    def test_end_to_end_ingest(self):
        db = memory_db()
        records = aa.transform([{
            'slug': 'gpt-6', 'pricing': {'price_1m_blended_3_to_1': 2.5},
            'evaluations': {'artificial_analysis_intelligence_index': 70.0},
        }])
        accepted, rejected = external_rankings.ingest(db, 'artificial-analysis', records)
        self.assertEqual((accepted, rejected), (2, 0))


if __name__ == '__main__':
    unittest.main()
