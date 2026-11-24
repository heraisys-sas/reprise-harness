"""Read-only multi-store Codex session census; no Codex daemon or inference."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import codex_session_owners as owners

A = "a108fab0-599a-443d-91d3-91a746a94941"
B = "ebee923e-92bb-459f-93aa-06d8b318dadb"
SCRIPT = Path(__file__).resolve().parents[1] / 'codex_session_owners.py'


def fixture(root, rows, *, valid=True):
    root.mkdir(mode=0o700)
    path = root / 'state_5.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE threads (id TEXT, title TEXT, cwd TEXT, rollout_path TEXT,'
                   ' updated_at INTEGER, source TEXT, archived INTEGER)')
        for ident, cwd, updated, archived in rows:
            db.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?)',
                       (ident, 'SECRET_CHAT_TITLE', cwd, '/secret/rollout.jsonl',
                        updated, 'SECRET_PROVIDER_AUTH', archived))
    return path


class CodexOwnerCensusTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_two_independent_roots_never_merge_identical_uuid(self):
        first, second = self.root / 'desktop', self.root / 'shared'
        fixture(first, [(A, '/repo', 100, 0)])
        fixture(second, [(A, '/repo', 200, 0), (B, '/repo', 150, 1)])
        value = owners.census([first, second])
        self.assertEqual(value['status'], 'observed')
        self.assertEqual(value['session_count'], 2)
        a, b = [profile['sessions'][0] for profile in value['profiles']]
        self.assertEqual(a['thread_id'], b['thread_id'])
        self.assertNotEqual(a['session_key'], b['session_key'])
        self.assertNotEqual(a['profile_id'], b['profile_id'])
        for row in (a, b):
            self.assertEqual(row['owner_state'], 'unknown')
            self.assertIsNone(row['owner_instance_id'])
            self.assertEqual(row['write_ownership'], 'unverified')
            self.assertEqual(row['rpc_readability'], 'unverified')
            self.assertFalse(row['controllable'])
        self.assertFalse(value['server_contacted'])
        self.assertFalse(value['owner_ownership_verified'])

    def test_metadata_only_does_not_read_chat_titles_or_rollouts(self):
        home = self.root / 'codex'
        path = fixture(home, [(A, '/private/project', 42, 0)])
        before = path.stat().st_mtime_ns
        raw = json.dumps(owners.census([home]))
        self.assertNotIn('SECRET_CHAT_TITLE', raw)
        self.assertNotIn('SECRET_PROVIDER_AUTH', raw)
        self.assertNotIn('/secret/rollout', raw)
        self.assertNotIn('/private/project', raw)
        self.assertNotIn(str(home), raw)
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_missing_store_is_partial_and_never_created(self):
        home = self.root / 'missing'
        home.mkdir()
        result = owners.census([home])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['profiles'][0]['index_state'], 'index_missing')
        self.assertFalse((home / 'state_5.sqlite').exists())

    def test_home_not_directory_is_partial(self):
        # Issue #201 : un home qui est un fichier régulier est signalé, jamais
        # créé ni lu.
        regular = self.root / 'not-a-directory'
        regular.write_text('not a directory')
        result = owners.census([regular])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['profiles'][0]['index_state'], 'home_not_directory')
        self.assertEqual(result['session_count'], 0)
        self.assertEqual(result['profiles'][0]['sessions'], [])

    def test_bad_schema_and_corruption_do_not_bypass_failsafe(self):
        home = self.root / 'bad-schema'
        home.mkdir()
        with sqlite3.connect(home / 'state_5.sqlite') as db:
            db.execute('CREATE TABLE unrelated (x TEXT)')
        result = owners.census([home])
        self.assertEqual(result['profiles'][0]['index_state'], 'index_unreadable_or_schema_changed')
        self.assertEqual(result['status'], 'partial')
        corrupted = self.root / 'bad-db'
        corrupted.mkdir()
        (corrupted / 'state_5.sqlite').write_bytes(b'not sqlite')
        result = owners.census([corrupted])
        self.assertEqual(result['profiles'][0]['index_state'], 'index_unreadable_or_schema_changed')

    def test_symlink_roots_and_indexes_are_refused(self):
        owner = self.root / 'real'
        fixture(owner, [(A, '/repo', 100, 0)])
        alias = self.root / 'alias'
        alias.symlink_to(owner, target_is_directory=True)
        self.assertEqual(owners.census([alias])['profiles'][0]['index_state'], 'home_symlink')
        linked = self.root / 'linked'
        linked.mkdir()
        (linked / 'state_5.sqlite').symlink_to(owner / 'state_5.sqlite')
        self.assertEqual(owners.census([linked])['profiles'][0]['index_state'], 'index_symlink')

    def test_invalid_uuid_and_truncation_report_partial_coverage(self):
        home = self.root / 'mixed'
        fixture(home, [(A, '/repo', 300, 0), ('BAD ID', '/repo', 200, 0),
                       (B, '/repo', 100, 0)])
        one = owners.census([home], max_threads=2)
        self.assertEqual(one['status'], 'partial')
        self.assertEqual(one['profiles'][0]['invalid_records'], 1)
        self.assertTrue(one['profiles'][0]['truncated'])
        self.assertEqual(one['session_count'], 1)
        full = owners.census([home], max_threads=3)
        self.assertEqual(full['session_count'], 2)
        self.assertEqual(full['status'], 'partial')
        self.assertFalse(full['profiles'][0]['truncated'])

    def test_duplicate_roots_rejected_before_any_read(self):
        home = self.root / 'duplicate'
        home.mkdir()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            owners.census([home, home / '.'])
        for limit in (0, 501, 1.5, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                owners.census([home], max_threads=limit)

    def test_nonowned_or_nonregular_index_refused(self):
        home = self.root / 'bad-type'
        home.mkdir()
        (home / 'state_5.sqlite').mkdir()
        self.assertEqual(owners.census([home])['profiles'][0]['index_state'],
                         'index_not_owned_regular_file')

    def test_read_only_proof_requires_exact_thread_and_cwd(self):
        home = self.root / 'one'
        fixture(home, [(A, '/repo', 100, 0)])
        original = owners.census([home])['profiles'][0]['sessions'][0]
        for response, expected in [
            ({'id': A, 'cwd': '/repo', 'status': {'type': 'idle'}}, 'confirmed'),
            ({'id': B, 'cwd': '/repo'}, 'identity_mismatch'),
            ({'id': A, 'cwd': '/wrong'}, 'identity_mismatch'),
            ({'id': A}, 'identity_mismatch'),
        ]:
            with self.subTest(response=response):
                result = owners.read_proof(original, response)
                self.assertEqual(result['rpc_readability'], expected)
                self.assertEqual(result['owner_state'], 'unknown')
                self.assertFalse(result['controllable'])
        self.assertEqual(owners.read_proof(original, None)['rpc_readability'], 'unverified')

    def test_cli_has_stable_machine_readable_output_and_nonzero_partial(self):
        home = self.root / 'configured'
        fixture(home, [(A, '/repo', 100, 0)])
        completed = subprocess.run(
            [sys.executable, '-B', str(SCRIPT), '--codex-home', str(home)],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['session_count'], 1)
        missing = subprocess.run(
            [sys.executable, '-B', str(SCRIPT), '--codex-home', str(home / 'absent')],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(json.loads(missing.stdout)['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
