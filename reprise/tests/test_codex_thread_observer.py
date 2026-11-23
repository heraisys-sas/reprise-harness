"""Offline contract tests for the read-only recent Codex thread observer."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_thread_observer as observer


A = 'a108fab0-599a-443d-91d3-91a746a94941'
B = 'ebee923e-92bb-459f-93aa-06d8b318dadb'
C = 'f9ed2174-6654-4f5d-8f72-57447c0079f6'
SCRIPT = Path(__file__).resolve().parents[1] / 'reprise'


def fixture(path, rows):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = sqlite3.connect(path)
    try:
        database.execute('''CREATE TABLE threads (
            id TEXT, title TEXT, first_user_message TEXT, preview TEXT,
            updated_at_ms INTEGER, updated_at INTEGER, cwd TEXT, model TEXT, archived INTEGER,
            source TEXT, recency_at_ms INTEGER)''')
        database.executemany('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                             [row[:5] + (row[4],) + row[5:] for row in rows])
        database.commit()
    finally:
        database.close()


class FakeClient:
    responses = {}
    failed = False
    created = 0

    def __init__(self, *args, **kwargs):
        type(self).created += 1

    def call(self, method, params=None):
        if type(self).failed:
            raise TimeoutError('offline')
        if method == 'initialize':
            return {}
        return {'thread': type(self).responses[params['threadId']]}

    def send(self, message):
        return None

    def close(self):
        return None


class CodexThreadObserverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / 'codex'
        self.db = self.home / 'state_5.sqlite'
        self.patch = mock.patch.object(observer, 'CODEX_STATE_DB', self.db)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        FakeClient.responses = {}
        FakeClient.failed = False
        FakeClient.created = 0

    def add_rows(self, rows):
        fixture(self.db, rows)

    def row(self, ident, message, activity, *, cwd='/tmp/project', preview='preview', archived=0):
        return (ident, 'title', message, preview, activity, cwd, 'gpt-5', archived, 'cli', activity)

    def test_read_index_is_mode_ro_ordered_bounded_and_subject_is_safe(self):
        long = 'line one\n' + ('x' * 230)
        self.add_rows([
            self.row(A, long, 30), self.row(B, '', 20, preview=' preview\ntext '), self.row(C, 'old', 10),
        ])
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        rows = observer.read_index(db_path=self.db, max_rows=2)
        self.assertEqual([row['thread_id'] for row in rows], [A, B])
        self.assertEqual(rows[1]['subject'], 'preview text')
        self.assertNotIn('\n', rows[0]['subject'])
        self.assertLessEqual(len(rows[0]['subject']), 200)
        self.assertEqual(before, hashlib.sha256(self.db.read_bytes()).hexdigest())
        for value in (0, 21, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                observer.read_index(db_path=self.db, max_rows=value)

    def test_no_proofs_never_creates_an_rpc_client(self):
        self.add_rows([self.row(A, 'one', 30)])
        with mock.patch.object(observer, 'Client', side_effect=AssertionError('must not connect')):
            result = observer.inspect_threads(max_rows=1, with_proofs=False)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['rows'][0]['rpc_readability'], 'unverified')
        self.assertFalse(result['rows'][0]['controllable'])

    def test_read_proof_states_remain_read_only(self):
        self.add_rows([self.row(A, 'one', 30), self.row(B, 'two', 20), self.row(C, 'three', 10)])
        FakeClient.responses = {
            A: {'id': A, 'cwd': '/tmp/project'},
            B: {'id': B, 'cwd': '/other'},
        }
        with mock.patch.object(observer, 'Client', FakeClient), \
             mock.patch.object(observer.engines, 'executable', return_value='/fake/codex'), \
             mock.patch.object(observer, '_indexed_sessions', return_value={
                 A: {'thread_id': A, 'cwd_sha256': observer.census([self.home])['profiles'][0]['sessions'][0]['cwd_sha256']},
                 B: {'thread_id': B, 'cwd_sha256': observer.census([self.home])['profiles'][0]['sessions'][1]['cwd_sha256']},
             }):
            result = observer.inspect_threads(max_rows=3)
        self.assertEqual([row['rpc_readability'] for row in result['rows']],
                         ['confirmed', 'identity_mismatch', 'unverified'])
        for row in result['rows']:
            self.assertEqual(row['owner_state'], 'unknown')
            self.assertFalse(row['controllable'])

    def test_global_rpc_failure_degrades_without_losing_index_rows(self):
        self.add_rows([self.row(A, 'one', 30), self.row(B, 'two', 20)])
        FakeClient.failed = True
        with mock.patch.object(observer, 'Client', FakeClient), \
             mock.patch.object(observer.engines, 'executable', return_value='/fake/codex'):
            result = observer.inspect_threads(max_rows=2)
        self.assertEqual([row['thread_id'] for row in result['rows']], [A, B])
        self.assertEqual([row['rpc_readability'] for row in result['rows']], ['unavailable', 'unavailable'])

    def test_inspection_never_requests_foundation_model(self):
        self.add_rows([self.row(A, 'one', 30)])
        with mock.patch.object(observer.apple_expert, 'configured', side_effect=AssertionError('not requested')), \
             mock.patch.object(observer.apple_expert, 'review', side_effect=AssertionError('not requested')):
            result = observer.inspect_threads(max_rows=1, with_proofs=False)
        self.assertEqual(result['rows'][0]['summary_status'], 'not_requested')

    def test_unavailable_foundation_model_keeps_thread_list_intact(self):
        root = Path(self.tmp.name) / 'project'
        root.mkdir()
        self.add_rows([self.row(A, 'one', 30, cwd=str(root))])
        with mock.patch.object(observer.apple_expert, 'configured', return_value=False):
            result = observer.summarize_indexed_thread(thread_id=A)
        self.assertEqual(result['summary_status'], 'unavailable')
        self.assertEqual(observer.inspect_threads(max_rows=1, with_proofs=False)['rows'][0]['thread_id'], A)

    def test_successful_foundation_summary_is_available(self):
        root = Path(self.tmp.name) / 'project'
        root.mkdir()
        with mock.patch.object(observer.apple_expert, 'configured', return_value=True), \
             mock.patch.object(observer.apple_expert, 'review', return_value={'status': 'finished', 'response': 'Résumé local.'}) as review:
            result = observer.summarize_thread(cwd=str(root), excerpt=' sujet\nobservé ')
        self.assertEqual(result, {'summary_status': 'available', 'summary': 'Résumé local.'})
        self.assertEqual(review.call_args.args[0], root.resolve())

    def test_cli_parses_options_and_serializes_json(self):
        home = Path(self.tmp.name) / 'cli-home'
        fixture(home / '.codex' / 'state_5.sqlite', [self.row(A, 'CLI subject', 30)])
        environment = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE='1')
        completed = subprocess.run([sys.executable, '-B', str(SCRIPT), 'codex-threads', '--max', '1', '--no-proofs'],
                                   cwd=SCRIPT.parent, env=environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['rows'][0]['subject'], 'CLI subject')
        invalid = subprocess.run([sys.executable, '-B', str(SCRIPT), 'codex-threads', '--max', '21', '--no-proofs'],
                                 cwd=SCRIPT.parent, env=environment, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(invalid.returncode, 0)


if __name__ == '__main__':
    unittest.main()
