from contextlib import closing
import json
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from delegation_collect import collect, main, write_output
from delegation_store import Store


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.state = self.root / 'state'
        self.state.mkdir()
        self.database = self.state / 'expert.sqlite'
        self.response = ('é🙂\\\"\n' * 15000)
        self.result = {'status': 'finished', 'response': self.response}
        with closing(sqlite3.connect(self.database)) as db, db:
            store = Store(db, str(self.project.resolve()))
            store.begin('test', {'prompt': 'test'})
            store.finish('test', self.result)

    def test_large_unicode_result_and_response_no_ack(self):
        before = self.database.read_bytes()
        text = collect(self.state, self.project, 'test')
        expected = dict(self.result, request_key='test')
        self.assertEqual(text, json.dumps(expected, sort_keys=True, ensure_ascii=False, separators=(',', ':')))
        self.assertEqual(collect(self.state, self.project, 'test', 'response'), self.response)
        self.assertEqual(self.database.read_bytes(), before)
        with closing(sqlite3.connect(self.database)) as db, db:
            self.assertEqual(db.execute('SELECT acknowledged FROM delegation_requests').fetchone()[0], 0)

    def test_other_scope_and_absent_key_rejected(self):
        for root, key in ((self.root, 'test'), (self.project, 'missing')):
            with self.assertRaises(ValueError):
                collect(self.state, root, key)

    def test_incomplete_and_unfinished_rejected(self):
        for change in ({'output_truncated': True}, {'output_complete': False}, {'status': 'needs_review'}):
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute('UPDATE delegation_requests SET result=?', (json.dumps(dict(self.result, **change)),))
            with self.assertRaises(ValueError):
                collect(self.state, self.project, 'test')

    def test_indeterminate_rejected(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("UPDATE delegation_requests SET state='indeterminate'")
        with self.assertRaises(ValueError):
            collect(self.state, self.project, 'test')

    def test_corrupt_page_or_manifest_rejected(self):
        original = Store.lookup
        for change in ({'offset': 1}, {'returned_length': 0}, {'next_offset': 0},
                       {'result_sha256': '0'*64}, {'text': 'wrong'}, {'has_more': False}):
            def lookup(store, args):
                value = original(store, args)
                return dict(value, **change) if args['view'] == 'page' else value
            with patch.object(Store, 'lookup', lookup), self.assertRaises(ValueError):
                collect(self.state, self.project, 'test')

    def test_hash_verification_independent_of_page_metadata(self):
        original = Store.lookup
        def lookup(store, args):
            value = original(store, args)
            if args['view'] == 'page':
                value['text'] = 'x' + value['text'][1:]
            return value
        with patch.object(Store, 'lookup', lookup), self.assertRaises(ValueError):
            collect(self.state, self.project, 'test')

    def test_exclusive_private_output_and_symlink(self):
        output = self.root / 'result'
        write_output(output, 'secret')
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            write_output(output, 'replacement')
        link = self.root / 'link'
        link.symlink_to(output)
        with self.assertRaises(FileExistsError):
            write_output(link, 'replacement')
        self.assertEqual(output.read_text(), 'secret')

    def test_missing_database_not_created(self):
        with self.assertRaises(FileNotFoundError):
            collect(self.root, self.project, 'test')
        self.assertFalse((self.root / 'expert.sqlite').exists())

    def test_cli(self):
        output = self.root / 'export'
        self.assertEqual(main(['--state', str(self.state), '--project-root', str(self.project),
                               '--request-key', 'test', '--output', str(output), '--field', 'response']), 0)
        self.assertEqual(output.read_text(), self.response)


if __name__ == '__main__':
    unittest.main()
