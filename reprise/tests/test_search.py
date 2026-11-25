from contextlib import closing
import sys
import importlib.util
from pathlib import Path
import unittest
import json
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location('private_search', Path(__file__).parents[1] / 'private_search.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SearchTests(unittest.TestCase):
    def test_embeddings_keep_total_twenty_second_budget(self):
        from unittest.mock import patch
        texts = [f'text-{i}' for i in range(205)]
        calls = []

        def run(args, input, **kwargs):
            batch = json.loads(input)
            calls.append((batch, kwargs['timeout']))
            output = [{'language': 'en', 'vector': [int(text[5:])]} for text in batch]
            return SimpleNamespace(returncode=0, stdout=json.dumps(output))

        with patch.object(module, 'native_binary', return_value=Path('/fake/Embedding')), \
             patch.object(module.time, 'monotonic', side_effect=[0, 0, 5, 21]), \
             patch.object(module.subprocess, 'run', side_effect=run):
            results = module.embeddings(Path('/'), texts)

        self.assertEqual([len(batch) for batch, _ in calls], [100, 100])
        self.assertEqual([timeout for _, timeout in calls], [20, 15])
        self.assertEqual(len(results), len(texts))
        self.assertEqual(results[200:], [{'language': None, 'vector': None}] * 5)

    def test_search_marks_partial_semantic_coverage(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        import sqlite3
        import sys
        import tempfile
        from types import SimpleNamespace

        @contextmanager
        def in_memory_index(_state):
            db = sqlite3.connect(':memory:')
            try:
                yield db
            finally:
                db.close()

        now = module.time.time()
        snapshot = {'sessions': [
            {'provider': 'codex', 'id': 'with-vector', 'title': 'Has vector', 'label': 'shared topic', 'updated': now},
            {'provider': 'codex', 'id': 'without-vector', 'title': 'No vector', 'label': 'other topic', 'updated': now},
            {'provider': 'codex', 'id': 'different-dimensions', 'title': 'Different dimensions', 'label': 'third topic', 'updated': now},
        ]}

        def fake_embeddings(_root, texts):
            if texts == ['shared topic']:
                return [{'language': 'en', 'revision': 1, 'vector': [1.0, 0.0]}]
            return [
                {'language': 'en', 'revision': 1, 'vector': [1.0, 0.0]},
                {'language': None, 'vector': None},
                {'language': 'en', 'revision': 1, 'vector': [1.0, 0.0, 0.0]},
            ]

        with tempfile.TemporaryDirectory() as folder, \
             patch.dict(sys.modules, {'detect': SimpleNamespace(scan_all=lambda: snapshot)}), \
             patch.object(module, 'encrypted_index', side_effect=in_memory_index), \
             patch.object(module, 'embeddings', side_effect=fake_embeddings):
            result = module.search(Path(folder), Path('/'), 'shared topic')

        self.assertTrue(result['semantic_available'])
        self.assertTrue(result['semantic_partial'])

    def test_embeddings_batches_preserve_order_and_cardinality(self):
        from unittest.mock import patch
        for count, expected_sizes in ((100, [100]), (101, [100, 1]), (205, [100, 100, 5])):
            with self.subTest(count=count):
                texts = [f'text-{i}' for i in range(count)]
                calls = []

                # Make each result identify its original global input position.
                def run(args, input, **kwargs):
                    batch = json.loads(input)
                    calls.append(batch)
                    output = [{'language': 'en', 'vector': [int(text[5:])]} for text in batch]
                    return SimpleNamespace(returncode=0, stdout=json.dumps(output))

                with patch.object(module, 'native_binary', return_value=Path('/fake/Embedding')), \
                     patch.object(module.subprocess, 'run', side_effect=run):
                    results = module.embeddings(Path('/'), texts)

                self.assertEqual([len(batch) for batch in calls], expected_sizes)
                self.assertEqual([item['vector'] for item in results], [[i] for i in range(count)])
                self.assertEqual(len(results), count)

    def test_embeddings_failure_is_limited_to_middle_batch(self):
        from unittest.mock import patch
        texts = [f'text-{i}' for i in range(205)]
        calls = []

        def run(args, input, **kwargs):
            batch = json.loads(input)
            calls.append(batch)
            if len(calls) == 2:
                return SimpleNamespace(returncode=0, stdout='invalid json')
            output = [{'language': 'en', 'vector': [int(text[5:])]} for text in batch]
            return SimpleNamespace(returncode=0, stdout=json.dumps(output))

        with patch.object(module, 'native_binary', return_value=Path('/fake/Embedding')), \
             patch.object(module.subprocess, 'run', side_effect=run):
            results = module.embeddings(Path('/'), texts)

        self.assertEqual([len(batch) for batch in calls], [100, 100, 5])
        self.assertEqual(len(results), len(texts))
        self.assertEqual([item['vector'] for item in results[:100]], [[i] for i in range(100)])
        self.assertEqual(results[100:200], [{'language': None, 'vector': None}] * 100)
        self.assertEqual([item['vector'] for item in results[200:]], [[i] for i in range(200, 205)])

    def test_cosine_handles_absent_zero_and_different_dimensions(self):
        self.assertEqual(module.cosine([1,0], [1,0]), 1)
        self.assertEqual(module.cosine([0,0], [1,0]), 0)
        self.assertEqual(module.cosine([1], [1,2]), 0)
        self.assertEqual(module.cosine(None, [1]), 0)

    def test_exact_diagnostics_preserved(self):
        self.assertIn('src/parser.rs', module.words('Échec src/parser.rs E0308'))
        self.assertIn('e0308', module.words('Échec src/parser.rs E0308'))
        self.assertEqual(module.words('ÉCHEC'), module.words('echec'))

class CleanupTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.load_patch = patch.object(module, 'load', side_effect=lambda path, purpose: path.read_bytes())
        self.save_patch = patch.object(module, 'save', side_effect=lambda path, data, purpose: path.write_bytes(data))
        self.load_patch.start()
        self.save_patch.start()
        self.addCleanup(self.load_patch.stop)
        self.addCleanup(self.save_patch.stop)

    def test_retention_daily_and_manual_clear(self):
        import tempfile,sqlite3
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            directory = state/'search.noindex'
            directory.mkdir()
            with closing(sqlite3.connect(directory/'index.sqlite')) as db, db:
                module.schema(db)
                for ident,stamp in [('old',1),('recent',900000)]:
                    db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?)',(ident,'hash','title','provider','text','{}',stamp))
            self.assertEqual(module.cleanup(state, now=1000000),1)
            self.assertEqual(module.cleanup(state, now=1000010),0)
            self.assertEqual(module.cleanup(state, clear=True, now=1000020),1)
            self.assertEqual(module.cleanup(state, clear=True, now=1000030),0)

    def test_cleanup_absent_does_not_create_index(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(module.cleanup(Path(folder)),0)
            self.assertFalse((Path(folder)/'search.noindex').exists())

    def test_failed_encryption_keeps_legacy_index(self):
        import tempfile,sqlite3
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)
            directory=state/'search.noindex'
            directory.mkdir()
            legacy=directory/'index.sqlite'
            with closing(sqlite3.connect(legacy)) as db, db:
                module.schema(db)
            with patch.object(module, 'save', side_effect=ValueError('locked')):
                with self.assertRaises(ValueError):
                    module.cleanup(state, clear=True)
            self.assertTrue(legacy.exists())
            self.assertFalse((directory/'index.enc').exists())
