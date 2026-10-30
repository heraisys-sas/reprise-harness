import base64
import hashlib
import json
import multiprocessing
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import sqlite3
from pathlib import Path
import unittest
from unittest import mock
from desktop_task_store import TaskStore


def _open_and_admit_in_process(state, project, index, barrier):
    barrier.wait(timeout=10)
    store = TaskStore(state, project)
    try:
        store.admit(f'process-{index}', f'prompt-{index}')
    finally:
        store.close()


class TaskStoreTests(unittest.TestCase):
    def test_restart_scope_ownership_and_ack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'a').mkdir(); (root/'b').mkdir()
            store = TaskStore(root/'state', root/'a')
            store.admit('k', 'hello', 'chat')
            store.admit('k', 'hello', 'chat')
            with self.assertRaises(ValueError): store.admit('k', 'changed', 'chat')
            receipt = store.claim('k', 'caller')
            with self.assertRaises(ValueError): store.claim('k', 'other')
            with self.assertRaises(ValueError): store.result('k', True)
            store.close()
            store = TaskStore(root/'state', root/'a')
            replay = store.claim('k', 'caller')  # contract: after a delivery attempt the replay carries no payload
            self.assertEqual((replay['attempt_id'], replay['delivery'], 'prompt' in replay), (receipt['attempt_id'], 'attempted', False))
            with self.assertRaises(ValueError): store.submit('k', 'wrong', 'answer')
            store.submit('k', receipt['attempt_id'], 'answer')
            store.submit('k', receipt['attempt_id'], 'answer')
            with self.assertRaises(ValueError): store.submit('k', receipt['attempt_id'], 'different')
            self.assertFalse(store.result('k')['acknowledged'])
            self.assertTrue(store.result('k', True)['acknowledged'])
            self.assertEqual(store.result('k', view='full')['answer'], 'answer')
            other = TaskStore(root/'state', root/'b')
            with self.assertRaises(ValueError): other.result('k')
            other.admit('k', 'independent')
            other.close(); store.close()

    def test_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp, tmp)
            with self.assertRaises(ValueError): store.admit('', 'hello')
            store.admit('k', 'hello')
            attempt = store.claim('k', 'caller')['attempt_id']
            with self.assertRaises(ValueError): store.submit('k', attempt, 'x'*2_000_001)
            with self.assertRaises(ValueError): store.result('k', 1)
            store.close()

    def test_listing_cursor_is_project_bound_and_excludes_later_admissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'a').mkdir(); (root/'b').mkdir()
            store = TaskStore(root/'state', root/'a')
            for key in ('one', 'two', 'three'):
                store.admit(key, 'private prompt ' + key)
            first = store.list_tasks(limit=2)
            self.assertEqual([item['request_key'] for item in first['tasks']], ['three', 'two'])
            self.assertNotIn('prompt', first['tasks'][0])
            store.admit('later', 'not in the current listing')
            second = store.list_tasks(limit=2, cursor=first['next_cursor'])
            self.assertEqual([item['request_key'] for item in second['tasks']], ['one'])
            self.assertIsNone(second['next_cursor'])
            other = TaskStore(root/'state', root/'b')
            with self.assertRaisesRegex(ValueError, 'foreign'):
                other.list_tasks(limit=2, cursor=first['next_cursor'])
            with self.assertRaises(ValueError): store.list_tasks(limit=0)
            with self.assertRaises(ValueError): store.list_tasks(limit=True)
            store.close(); other.close()

    def test_listing_reports_only_proven_state_and_migration_orders_legacy_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root/'project'; project.mkdir()
            db_path = root/'quick.sqlite'
            db = sqlite3.connect(db_path)
            db.execute('''CREATE TABLE desktop_tasks(scope TEXT NOT NULL, request_key TEXT NOT NULL,
                prompt TEXT NOT NULL, chat_id TEXT, claim_key TEXT, attempt_id TEXT, answer TEXT,
                acknowledged INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(scope,request_key))''')
            scope = str(project.resolve())
            db.executemany('INSERT INTO desktop_tasks VALUES(?,?,?,?,?,?,?,?)', [
                (scope, 'z-old', 'secret prompt', 'chat', 'secret claim', 'attempt', None, 0),
                (scope, 'a-new', 'another prompt', None, None, None, None, 0),
                (scope, 'done', 'third prompt', None, 'claim', 'attempt-done', 'secret answer', 1),
            ])
            db.commit(); db.close()
            store = TaskStore(root, project)
            page = store.list_tasks(limit=10)
            by_key = {item['request_key']: item for item in page['tasks']}
            self.assertEqual([item['request_key'] for item in page['tasks']], ['z-old', 'done', 'a-new'])
            self.assertEqual(by_key['z-old']['state'], 'attempted')
            self.assertFalse(by_key['z-old']['result_available'])
            self.assertEqual(by_key['done']['state'], 'recorded')
            self.assertTrue(by_key['done']['acknowledged'])
            self.assertTrue(all(set(item) == {'request_key','state','result_available','acknowledged'} for item in page['tasks']))
            store.close()

    def test_listing_does_not_fetch_large_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp, tmp)
            store.admit('large', 'prompt')
            attempt = store.claim('large', 'claim')['attempt_id']
            store.submit('large', attempt, 'x' * 2_000_000)
            statements = []
            store.db.set_trace_callback(statements.append)
            page = store.list_tasks()
            store.db.set_trace_callback(None)
            listing_sql = next(statement for statement in statements if 'FROM desktop_task_order o JOIN desktop_tasks t' in statement)
            self.assertIn('(t.answer IS NOT NULL)', listing_sql)
            self.assertNotIn('t.answer,', listing_sql)
            self.assertTrue(page['tasks'][0]['result_available'])
            self.assertNotIn('answer', page['tasks'][0])
            store.close()

    def test_listing_does_not_request_writer_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp, tmp)
            store.admit('readable', 'prompt')
            writer = sqlite3.connect(Path(tmp) / 'quick.sqlite', timeout=0.1)
            try:
                writer.execute('BEGIN IMMEDIATE')
                page = store.list_tasks()
                self.assertEqual(page['tasks'][0]['request_key'], 'readable')
            finally:
                writer.rollback()
                writer.close()
                store.close()

    def test_cursor_rejects_noncanonical_invalid_and_out_of_range_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TaskStore(tmp, tmp)
            for key in ('one', 'two'):
                store.admit(key, key)
            cursor = store.list_tasks(limit=1)['next_cursor']
            invalid = [cursor + '!!!!', cursor + '=', 'not-base64!', 'é']
            for value in invalid:
                with self.subTest(cursor=value):
                    with self.assertRaises(ValueError):
                        store.list_tasks(cursor=value)
            scope = hashlib.sha256(store.scope.encode()).hexdigest()
            huge = base64.urlsafe_b64encode(json.dumps({'v': 1, 'scope': scope,
                'high': 2**63, 'before': 2**63}).encode()).decode().rstrip('=')
            with self.assertRaises(ValueError):
                store.list_tasks(cursor=huge)
            too_high_before = base64.urlsafe_b64encode(json.dumps({'v': 1, 'scope': scope,
                'high': 1, 'before': 2}).encode()).decode().rstrip('=')
            with self.assertRaises(ValueError):
                store.list_tasks(cursor=too_high_before)
            bool_version = base64.urlsafe_b64encode(json.dumps({'v': True, 'scope': scope,
                'high': 1, 'before': 1}).encode()).decode().rstrip('=')
            with self.assertRaises(ValueError):
                store.list_tasks(cursor=bool_version)
            extra_field = base64.urlsafe_b64encode(json.dumps({'v': 1, 'scope': scope,
                'high': 1, 'before': 1, 'extra': 0}).encode()).decode().rstrip('=')
            with self.assertRaises(ValueError):
                store.list_tasks(cursor=extra_field)
            duplicate_key = ('{"v":1,"scope":' + json.dumps(scope) +
                ',"high":1,"before":1,"high":1}')
            duplicate_cursor = base64.urlsafe_b64encode(duplicate_key.encode()).decode().rstrip('=')
            with self.assertRaises(ValueError):
                store.list_tasks(cursor=duplicate_cursor)
            store.close()

    def test_order_backfill_only_runs_when_table_is_first_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / 'project'; project.mkdir()
            original_connect = sqlite3.connect
            statements = []
            def traced_connect(*args, **kwargs):
                connection = original_connect(*args, **kwargs)
                connection.set_trace_callback(statements.append)
                return connection
            with mock.patch('desktop_task_store.sqlite3.connect', side_effect=traced_connect):
                store = TaskStore(Path(tmp) / 'state', project)
                store.admit('legacy', 'prompt')
                order_before = store.db.execute('SELECT seq FROM desktop_task_order WHERE scope=?', (store.scope,)).fetchone()
                store.close()
                statements.clear()
                store = TaskStore(Path(tmp) / 'state', project)
                order_after = store.db.execute('SELECT seq FROM desktop_task_order WHERE scope=?', (store.scope,)).fetchone()
                store.close()
            self.assertEqual(order_before, order_after)
            self.assertFalse(any('SELECT scope,request_key FROM desktop_tasks' in sql for sql in statements))

    def test_concurrent_store_migration_and_admission_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root/'project'; project.mkdir()
            workers = 6
            barrier = threading.Barrier(workers)
            def open_and_admit(index):
                barrier.wait(timeout=5)
                store = TaskStore(root/'state', project)
                try:
                    store.admit(f'key-{index}', f'prompt-{index}')
                finally:
                    store.close()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(open_and_admit, range(workers)))
            store = TaskStore(root/'state', project)
            try:
                page = store.list_tasks(limit=10)
                self.assertEqual(len(page['tasks']), workers)
                seqs = store.db.execute('SELECT seq FROM desktop_task_order WHERE scope=?', (store.scope,)).fetchall()
                self.assertEqual(len(seqs), workers)
                self.assertEqual(len({seq for (seq,) in seqs}), workers)
            finally:
                store.close()

    def test_old_direct_writer_is_ordered_by_migration_trigger_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root/'project'; project.mkdir()
            store = TaskStore(root/'state', project)
            store.close()  # installs the trigger and completes the one-time backfill
            db_path = root/'state'/'quick.sqlite'
            legacy_writer = sqlite3.connect(db_path)
            try:
                statement = '''INSERT OR IGNORE INTO desktop_tasks(scope,request_key,prompt,chat_id)
                    VALUES(?,?,?,?)'''
                values = (str(project.resolve()), 'legacy-runtime', 'prompt from old daemon', None)
                legacy_writer.execute(statement, values)
                legacy_writer.execute(statement, values)
                legacy_writer.commit()
            finally:
                legacy_writer.close()
            store = TaskStore(root/'state', project)
            try:
                page = store.list_tasks()
                self.assertEqual([item['request_key'] for item in page['tasks']], ['legacy-runtime'])
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM desktop_task_order WHERE scope=?',
                    (store.scope,)).fetchone()[0], 1)
                trigger = store.db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='desktop_tasks_order_after_insert'").fetchone()
                self.assertIsNotNone(trigger)
            finally:
                store.close()

    def test_existing_order_table_without_trigger_backfills_orphans_and_repairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root/'project'; project.mkdir()
            store = TaskStore(root/'state', project)
            store.admit('already-indexed', 'prompt')
            store.close()
            db_path = root/'state'/'quick.sqlite'
            prototype_writer = sqlite3.connect(db_path)
            try:
                prototype_writer.execute('DROP TRIGGER desktop_tasks_order_after_insert')
                prototype_writer.execute('''INSERT INTO desktop_tasks(scope,request_key,prompt)
                    VALUES(?,?,?)''', (str(project.resolve()), 'orphaned-by-prototype', 'legacy prompt'))
                prototype_writer.commit()
            finally:
                prototype_writer.close()
            # Table exists but the prototype omitted the trigger, so a full idempotent
            # backfill is required before recreating the trigger in the same migration.
            store = TaskStore(root/'state', project)
            try:
                page = store.list_tasks(limit=10)
                self.assertEqual({item['request_key'] for item in page['tasks']},
                    {'already-indexed', 'orphaned-by-prototype'})
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM desktop_task_order WHERE scope=?',
                    (store.scope,)).fetchone()[0], 2)
                self.assertIsNotNone(store.db.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='desktop_tasks_order_after_insert'").fetchone())
            finally:
                store.close()

    def test_multi_process_first_migration_and_old_writer_are_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root/'project'; project.mkdir()
            state = root/'state'
            # First process pair exercises serialized, atomic schema creation and trigger install.
            context = multiprocessing.get_context('spawn')
            workers = 4
            barrier = context.Barrier(workers)
            processes = [context.Process(target=_open_and_admit_in_process,
                args=(str(state), str(project), index, barrier)) for index in range(workers)]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=30)
            self.assertTrue(all(process.exitcode == 0 for process in processes),
                [process.exitcode for process in processes])
            store = TaskStore(state, project)
            try:
                page = store.list_tasks(limit=10)
                self.assertEqual(len(page['tasks']), workers)
                legacy = sqlite3.connect(state/'quick.sqlite', timeout=2)
                try:
                    legacy.execute('''INSERT INTO desktop_tasks(scope,request_key,prompt) VALUES(?,?,?)''',
                        (store.scope, 'from-old-daemon', 'legacy prompt'))
                    legacy.commit()
                finally:
                    legacy.close()
                listed = store.list_tasks(limit=10)
                self.assertIn('from-old-daemon', [item['request_key'] for item in listed['tasks']])
            finally:
                store.close()
