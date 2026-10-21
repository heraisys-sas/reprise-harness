"""Installation operations must preserve state using isolated fixture directories."""
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import state_install
from claude_desktop_quick import Endpoint


class StateInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'legacy'
        self.target = self.root / 'installed'

    def fixture(self, wal=False):
        self.source.mkdir()
        db = sqlite3.connect(self.source / 'tasks.sqlite')
        self.addCleanup(db.close)
        if wal:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('CREATE TABLE custom_results(id TEXT, result TEXT, claimed INTEGER)')
        db.execute('INSERT INTO custom_results VALUES (?, ?, ?)', ('request-1', 'valuable result', 0))
        db.execute('CREATE TABLE identities(account TEXT, salt BLOB)')
        db.execute('INSERT INTO identities VALUES (?, ?)', ('opaque-account', b'fixture-salt'))
        db.execute('CREATE TABLE attempts(request_key TEXT, status TEXT)')
        db.execute('INSERT INTO attempts VALUES (?, ?)', ('must-not-repeat', 'indeterminate'))
        db.commit()
        (self.source / 'nested').mkdir()
        (self.source / 'nested' / 'manifest.json').write_text('{"request_hash":"unchanged"}')
        return db

    def rows(self, directory, table):
        with closing(sqlite3.connect(directory / 'tasks.sqlite')) as db:
            return db.execute('SELECT * FROM ' + table).fetchall()

    def assert_preserved(self, directory):
        self.assertEqual(self.rows(directory, 'custom_results'), [('request-1', 'valuable result', 0)])
        self.assertEqual(self.rows(directory, 'identities'), [('opaque-account', b'fixture-salt')])
        self.assertEqual(self.rows(directory, 'attempts'), [('must-not-repeat', 'indeterminate')])
        self.assertEqual((directory / 'nested' / 'manifest.json').read_text(), '{"request_hash":"unchanged"}')

    def test_initialize_explicit_empty_state(self):
        result = state_install.initialize(self.target)
        self.assertIsInstance(result, dict)
        marker = json.loads((self.target / '.reprise-state.json').read_text())
        self.assertIsInstance(marker, dict)
        self.assertTrue(marker)

    def test_initialize_refuses_existing_directory_without_changes(self):
        self.target.mkdir()
        sentinel = self.target / 'keep'
        sentinel.write_text('existing')
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            state_install.initialize(self.target)
        self.assertEqual(sentinel.read_text(), 'existing')

    def test_import_preserves_all_tables_and_creates_backup(self):
        self.fixture()
        before = (self.source / 'tasks.sqlite').read_bytes()
        result = state_install.import_state(self.source, self.target, source_stopped=True)
        self.assertIsInstance(result, dict)
        backup = Path(result['backup'])
        self.assertNotEqual(backup, self.source)
        self.assertNotEqual(backup, self.target)
        self.assert_preserved(self.target)
        self.assert_preserved(backup)
        self.assertEqual((self.source / 'tasks.sqlite').read_bytes(), before)
        self.assertTrue((self.target / '.reprise-state.json').is_file())

    def test_import_includes_committed_wal(self):
        db = self.fixture(wal=True)
        self.assertTrue((self.source / 'tasks.sqlite-wal').is_file())
        before = {p.name: p.read_bytes() for p in self.source.iterdir() if p.is_file()}
        result = state_install.import_state(self.source, self.target, source_stopped=True)
        self.assert_preserved(self.target)
        self.assert_preserved(Path(result['backup']))
        for name, content in before.items():
            self.assertEqual((self.source / name).read_bytes(), content)
        self.assertEqual(db.execute('SELECT status FROM attempts').fetchone(), ('indeterminate',))
        self.assertFalse((self.target / 'tasks.sqlite-wal').exists())
        self.assertFalse((self.target / 'tasks.sqlite-shm').exists())

    def test_import_excludes_runtime_socket_and_locks(self):
        self.fixture()
        (self.source / 'worker.lock').write_text('stale lock')
        sock = socket.socket(socket.AF_UNIX)
        self.addCleanup(sock.close)
        sock.bind(str(self.source / 'service.sock'))
        state_install.import_state(self.source, self.target, source_stopped=True)
        self.assertFalse((self.target / 'worker.lock').exists())
        self.assertFalse((self.target / 'service.sock').exists())
        self.assertTrue((self.source / 'worker.lock').exists())
        self.assertTrue((self.source / 'service.sock').exists())

    def test_import_requires_explicit_stopped_attestation(self):
        self.fixture()
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            state_install.import_state(self.source, self.target, source_stopped=False)
        self.assertFalse(self.target.exists())
        self.assert_preserved(self.source)

    def test_import_refuses_existing_destination(self):
        self.fixture()
        self.target.mkdir()
        (self.target / 'keep').write_text('existing')
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            state_install.import_state(self.source, self.target, source_stopped=True)
        self.assertEqual((self.target / 'keep').read_text(), 'existing')

    def test_import_refuses_symlinks(self):
        self.fixture()
        outside = self.root / 'outside'
        outside.write_text('do not follow')
        (self.source / 'linked').symlink_to(outside)
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            state_install.import_state(self.source, self.target, source_stopped=True)
        self.assertFalse(self.target.exists())
        self.assertEqual(outside.read_text(), 'do not follow')

    def test_import_refuses_unknown_manifest(self):
        self.fixture()
        (self.source / '.reprise-state.json').write_text('{"format":999,"status":"ready"}')
        with self.assertRaises(ValueError):
            state_install.import_state(self.source, self.target, source_stopped=True)
        self.assertFalse(self.target.exists())

    def test_import_refuses_corrupt_database(self):
        self.source.mkdir()
        (self.source / 'tasks.sqlite').write_bytes(b'not a sqlite database')
        with self.assertRaises((ValueError, OSError, RuntimeError, sqlite3.DatabaseError)):
            state_install.import_state(self.source, self.target, source_stopped=True)
        self.assertFalse(self.target.exists())
        self.assertEqual((self.source / 'tasks.sqlite').read_bytes(), b'not a sqlite database')

    def test_import_keeps_endpoint_identity_and_uncertain_attempt(self):
        endpoint = Endpoint(self.source)
        scope = endpoint.scope
        result = endpoint.submit({'prompt': 'Fixture retained result', 'request_key': 'recorded'})
        endpoint.requests.begin('uncertain', {'prompt': 'Fixture uncertain call'})
        receipt = endpoint.requests.lookup({'request_key': 'uncertain'})
        endpoint.close()
        state_install.import_state(self.source, self.target, source_stopped=True)
        imported = Endpoint(self.target)
        self.addCleanup(imported.close)
        self.assertEqual(imported.scope, scope)
        self.assertEqual(imported.submit({'prompt': 'Fixture retained result', 'request_key': 'recorded'}), result)
        self.assertEqual(imported.requests.lookup({'request_key': 'uncertain'}), receipt)
        self.assertEqual(imported.requests.lookup({'request_key': 'uncertain'})['state'], 'indeterminate')

    def test_import_rejects_future_schema(self):
        db = self.fixture()
        db.execute('PRAGMA user_version=9999')
        db.commit()
        with self.assertRaises(ValueError):
            state_install.import_state(self.source, self.target, source_stopped=True)
        self.assertFalse(self.target.exists())
        self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 9999)

    def test_import_aborts_when_source_changes_during_copy(self):
        self.fixture()
        original = state_install._copy
        def mutate(*args):
            original(*args)
            (self.source / 'nested' / 'manifest.json').write_text('concurrent modification')
        with patch.object(state_install, '_copy', side_effect=mutate):
            with self.assertRaises(ValueError):
                state_install.import_state(self.source, self.target, source_stopped=True)
        self.assertFalse(self.target.exists())

    def test_publication_failure_keeps_verified_backup(self):
        self.fixture()
        original = Path.rename
        def refuse_publication(path, target):
            if Path(target) == self.target:
                raise OSError('fixture publication failure')
            return original(path, target)
        with patch.object(Path, 'rename', refuse_publication):
            with self.assertRaises(OSError):
                state_install.import_state(self.source, self.target, source_stopped=True)
        self.assertFalse(self.target.exists())
        backups = list(self.root.glob('installed.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assert_preserved(backups[0])
        self.assertTrue(state_install.initialized(backups[0]))

    def cli(self, *args):
        home = self.root / 'isolated-home'
        home.mkdir(exist_ok=True)
        env = dict(os.environ, HOME=str(home), AGENT_RESUME_STATE=str(self.root / 'unused-state'))
        return subprocess.run([sys.executable, str(ROOT / 'resume'), *map(str, args)],
                              env=env, cwd=self.root, text=True, capture_output=True, timeout=20)

    def test_cli_initialize(self):
        result = self.cli('state-init', '--target', self.target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsInstance(json.loads(result.stdout), dict)
        self.assertTrue((self.target / '.reprise-state.json').exists())
        self.assertFalse((self.root / 'unused-state').exists())

    def test_cli_import(self):
        self.fixture()
        result = self.cli('state-import', '--source', self.source, '--target', self.target, '--source-stopped')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(Path(json.loads(result.stdout)['backup']).is_dir())
        self.assert_preserved(self.target)
        self.assertFalse((self.root / 'unused-state').exists())

    def test_cli_import_without_attestation_refuses(self):
        self.fixture()
        result = self.cli('state-import', '--source', self.source, '--target', self.target)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertFalse((self.root / 'unused-state').exists())


if __name__ == '__main__':
    unittest.main()
