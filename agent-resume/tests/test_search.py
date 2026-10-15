from contextlib import closing
import sys
import importlib.util
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
spec = importlib.util.spec_from_file_location('private_search', Path(__file__).parents[1] / 'private_search.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SearchTests(unittest.TestCase):
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
