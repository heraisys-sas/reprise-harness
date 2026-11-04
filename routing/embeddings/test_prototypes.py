import json
import plistlib
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
HELPER = ROOT / 'plugin/native/RouteEmbedding'

class PrototypeTests(unittest.TestCase):
    def test_cache_is_equivalent_and_never_stores_the_query(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / 'examples.json'
            catalog.write_bytes((ROOT / 'plugin/native/examples.json').read_bytes())
            def run(texts, prepare=False):
                output = subprocess.check_output([str(HELPER), str(catalog)], input=json.dumps({'texts': texts, 'prepare': prepare}).encode(), timeout=5)
                return json.loads(output)
            run(['A short English sentence.', 'Une courte phrase française.'], True)
            cache = catalog.with_name('sentence-prototypes.plist')
            self.assertTrue(cache.exists())
            query = 'Explain tradeoffs among algorithmic alternatives.'
            cached = run([query])[0]
            self.assertNotIn(query.encode(), cache.read_bytes())
            data = plistlib.loads(cache.read_bytes())
            data['revisions']['en'] = -1
            cache.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))
            stale = run([query])[0]
            self.assertEqual(stale['tier'], cached['tier'])
            self.assertAlmostEqual(stale['score'], cached['score'])
            cache.write_bytes(b'invalid cache')
            corrupt = run([query])[0]
            self.assertEqual(corrupt['tier'], cached['tier'])
            self.assertAlmostEqual(corrupt['score'], cached['score'])
            run(['A short English sentence.', 'Une courte phrase française.'], True)
            examples = json.loads(catalog.read_text())
            examples[0]['text'] = 'A different example invalidates the digest.'
            catalog.write_text(json.dumps(examples))
            changed = run([query])[0]
            cache.unlink()
            fresh = run([query])[0]
            self.assertEqual(changed['tier'], fresh['tier'])
            self.assertAlmostEqual(changed['score'], fresh['score'])

if __name__ == '__main__':
    unittest.main()
