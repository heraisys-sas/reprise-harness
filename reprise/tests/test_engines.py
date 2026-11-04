import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import engines
import scheduler
import task_archive

SESSION = '12345678-1234-1234-1234-123456789abc'


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,session TEXT,cwd TEXT,prompt TEXT,due REAL,status TEXT,attempts INTEGER)')
        engines.setup(self.db)
        scheduler.setup(self.db)

    def test_explicit_missing_socket_never_falls_back(self):
        with self.assertRaises(engines.Unavailable):
            engines.select('codex', {'socket': str(self.root/'missing.sock')})
        with patch.object(engines, 'SOCKET', self.root/'missing.sock'):
            self.assertEqual(engines.select('codex', {}), 'codex-cli')

    def test_broken_default_socket_link_never_falls_back_to_cli(self):
        endpoint = self.root/'control.sock'
        endpoint.symlink_to(self.root/'missing-daemon.sock')
        with patch.object(engines, 'SOCKET', endpoint):
            with self.assertRaises(engines.Unavailable):
                engines.select('codex', {})

    def test_inaccessible_default_socket_never_falls_back(self):
        with patch.object(Path, 'exists', side_effect=PermissionError('denied')):
            with self.assertRaises(engines.Unavailable):
                engines.select('codex', {})

    def test_socket_must_be_owned_and_private(self):
        path = self.root/'server.sock'
        path.write_text('not a socket')
        with self.assertRaises(engines.Unavailable):
            engines.socket_path({'socket': str(path)})
        path.unlink()
        with socket.socket(socket.AF_UNIX) as server:
            try:
                server.bind(str(path))
            except PermissionError:
                self.skipTest('Sandbox disallows socket binding')
            path.chmod(0o600)
            self.assertEqual(engines.select('codex', {'socket': str(path)}), 'codex-server')
            path.chmod(0o622)
            with self.assertRaises(engines.Unavailable):
                engines.socket_path({'socket': str(path)})

    def test_harness_requires_existing_headless_profile(self):
        task = {'session': 'session-known'}
        with patch.object(engines.Path, 'home', return_value=self.root):
            for values in ({}, {'profile': 'headless'}):
                with self.assertRaises(engines.Unavailable):
                    engines.command('harness-cli', task, 'dsh', values)
            profile = self.root/'.dsh/profiles/headless/package.json'
            profile.parent.mkdir(parents=True)
            profile.write_text(json.dumps({'dsh': {'profile': {'bundles': ['@deepseek-ai/dsh-headless']}}}))
            self.assertEqual(engines.command('harness-cli', task, 'dsh', {'profile': 'headless'}),
                             ['dsh', '--profile', 'headless', '--json', '--session-id', 'session-known', '-'])
            with self.assertRaises(engines.Unavailable):
                engines.command('harness-cli', task, 'dsh', {'profile': 'headless'}, automatic=True)

    def test_backup_preserves_route_but_restores_paused(self):
        self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', (SESSION, 'session-known', str(self.root), 'instruction', 0, 'ready', 1))
        engines.assign(self.db, SESSION, 'harness-cli', {'profile': 'headless'})
        path = self.root/'backup.zip'
        task_archive.backup(self.db, path)
        self.db.execute('DELETE FROM tasks')
        self.db.execute('DELETE FROM task_engines')
        self.db.commit()
        task_archive.restore(self.db, path)
        self.assertEqual(self.db.execute('SELECT status FROM tasks').fetchone()[0], 'paused')
        self.assertEqual(engines.route(self.db, SESSION), ('harness-cli', {'profile': 'headless'}))
        self.assertFalse(scheduler.enabled(self.db))

    def test_restore_does_not_change_existing_routes(self):
        self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', (SESSION, SESSION, str(self.root), 'instruction', 0, 'ready', 0))
        engines.assign(self.db, SESSION, 'codex-cli')
        path = self.root/'backup.zip'
        task_archive.backup(self.db, path)
        engines.assign(self.db, SESSION, 'codex-server', {'socket': '/private/tmp/new.sock'})
        self.db.commit()
        task_archive.restore(self.db, path)
        self.assertEqual(engines.route(self.db, SESSION)[0], 'codex-server')

    def test_connection_settings_restored_without_overwriting_existing_choice(self):
        self.db.execute('INSERT INTO engine_settings VALUES (?,?)', ('codex', json.dumps({'socket': '/local/server.sock'})))
        path = self.root/'backup.zip'
        task_archive.backup(self.db, path)
        self.db.execute('DELETE FROM engine_settings')
        self.db.commit()
        task_archive.restore(self.db, path)
        self.assertEqual(json.loads(self.db.execute('SELECT options FROM engine_settings').fetchone()[0])['socket'], '/local/server.sock')
        self.db.execute('UPDATE engine_settings SET options=?', (json.dumps({'socket': '/new/server.sock'}),))
        self.db.commit()
        task_archive.restore(self.db, path)
        self.assertEqual(json.loads(self.db.execute('SELECT options FROM engine_settings').fetchone()[0])['socket'], '/new/server.sock')
