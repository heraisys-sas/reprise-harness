import sqlite3
import json
import multiprocessing
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import delegation_store
from delegation_store import Store


def crash_after_handoff(path, binding, payload):
    db=sqlite3.connect(path,timeout=5)
    result=Store(db,'/project').guarded_handoff('grant',binding,'job','key',payload)
    os._exit(72 if result is not None else 73)


class GuardedAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'expert.sqlite')
        self.db = sqlite3.connect(self.path, timeout=5); self.addCleanup(self.db.close)
        self.store = Store(self.db, '/project')
        self.store.setup_guarded_authority()
        self.binding = {'project_id':'p', 'work_id':'w', 'job_id':'job', 'request_key':'key', 'project_root':'/project',
                        'socket':'socket-a', 'backend':'codex', 'mode':'expertise', 'workspace_write':False}
        self.payload = {'tool':'reprise_delegate', 'question':'first', 'backend':'codex', 'mode':'expertise'}

    def grant(self, maximum=1):
        self.store.create_guarded_grant('grant', self.binding, maximum, payload=self.payload)

    def used(self):
        return self.db.execute('SELECT used FROM guarded_grants WHERE grant_id=?', ('grant',)).fetchone()[0]

    def test_default_deny_and_explicit_binding(self):
        with self.assertRaises(ValueError):
            self.store.guarded_begin('grant', self.binding, 'job', 'key', self.payload)
        self.grant()
        for field in self.binding:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.store.guarded_begin('grant', self.binding | {field:'other'}, 'job', 'key', self.payload)
        with self.assertRaises(ValueError):
            self.store.guarded_begin('grant', self.binding, 'job', 'key', self.payload | {'mode':'implementation'})
        self.assertEqual(self.used(), 0)

    def test_admit_replay_conflicts_and_revoke(self):
        self.grant()
        self.assertIsNone(self.store.guarded_begin('grant', self.binding, 'job', 'key', self.payload))
        self.store.revoke_guarded_grant('grant')
        self.assertEqual(self.store.guarded_begin('grant', self.binding, 'job', 'key', self.payload)['status'], 'indeterminate')
        for job, key, payload in [('other','key',self.payload),('job','key',self.payload | {'question':'changed'}),
                                  ('job2','key2',self.payload | {'question':'other'})]:
            with self.assertRaises(ValueError):
                self.store.guarded_begin('grant', self.binding, job, key, payload)
        self.assertEqual(self.used(), 1)

    def test_existing_direct_request_and_parent_rules(self):
        self.store.create_guarded_grant('direct-grant',self.binding | {'request_key':'direct'},2,payload=self.payload)
        self.store.begin('direct', self.payload)
        with self.assertRaises(ValueError):
            self.store.guarded_begin('direct-grant', self.binding | {'request_key':'direct'}, 'job', 'direct', self.payload)
        self.store.create_guarded_grant('grant',self.binding | {'request_key':'other'},2,payload=self.payload | {'question':'other'},parent='direct')
        result=self.store.guarded_begin('grant', self.binding | {'request_key':'other'}, 'job', 'other', self.payload | {'question':'other'}, parent='direct')
        self.assertEqual(result['status'], 'indeterminate')
        self.assertIsNone(self.store.guarded_handoff('grant', self.binding | {'request_key':'other'}, 'job', 'other', self.payload | {'question':'other'}, parent='direct'))
        self.assertEqual(self.used(), 0)
        self.assertIsNone(self.db.execute('SELECT 1 FROM delegation_requests WHERE request_key=?', ('other',)).fetchone())

    def test_insert_failure_rolls_back_request_and_budget(self):
        self.grant()
        self.db.execute("CREATE TRIGGER fail_guard BEFORE INSERT ON guarded_admissions BEGIN SELECT RAISE(ABORT, 'failure'); END")
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.guarded_begin('grant', self.binding, 'job', 'key', self.payload)
        self.assertEqual(self.used(), 0)
        self.assertIsNone(self.db.execute('SELECT 1 FROM delegation_requests WHERE request_key=?', ('key',)).fetchone())

    def test_first_admission_must_match_question_files_and_parent(self):
        payload=self.payload | {'file_evidence':[{'path':'a.py','sha256':'a'*64}]}
        self.store.create_guarded_grant('grant',self.binding,1,payload=payload)
        variants=[(payload | {'question':'other'},None),
                  (payload | {'file_evidence':[{'path':'a.py','sha256':'b'*64}]},None),
                  (payload,'other-parent')]
        for candidate,parent in variants:
            with self.subTest(candidate=candidate,parent=parent), self.assertRaises(ValueError):
                self.store.guarded_begin('grant',self.binding,'job','key',candidate,parent=parent)
            self.assertEqual(self.used(),0)
            self.assertIsNone(self.db.execute('SELECT 1 FROM delegation_requests WHERE request_key=?',('key',)).fetchone())
            self.assertIsNone(self.db.execute('SELECT 1 FROM guarded_admissions WHERE request_key=?',('key',)).fetchone())
        self.assertIsNone(self.store.guarded_begin('grant',self.binding,'job','key',payload))
        self.assertEqual(self.used(),1)

    def test_mutation_while_waiting_for_transaction_cannot_change_admission(self):
        payload=self.payload | {'file_evidence':[{'path':'a.py','sha256':'a'*64}]}
        binding=dict(self.binding)
        self.store.create_guarded_grant('grant',binding,1,payload=payload)
        expected=delegation_store.digest({'payload':payload,'parent':None})
        ready=threading.Event(); proceed=threading.Event(); snapshotted=threading.Event(); outcomes=[]
        original_digest=delegation_store.digest
        def observed_digest(value):
            result=original_digest(value)
            if threading.current_thread() is worker:
                snapshotted.set()
            return result
        def admit():
            db=sqlite3.connect(self.path,timeout=5)
            try:
                store=Store(db,'/project'); ready.set()
                if not proceed.wait(5): raise RuntimeError('barrier timeout')
                try: outcomes.append(store.guarded_handoff('grant',binding,'job','key',payload))
                except Exception as exc: outcomes.append(exc)
            finally: db.close()
        worker=threading.Thread(target=admit); worker.start(); self.assertTrue(ready.wait(5))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            with patch.object(delegation_store,'digest',side_effect=observed_digest):
                proceed.set(); self.assertTrue(snapshotted.wait(5))
                payload['question']='mutated'
                payload['file_evidence'][0]['sha256']='b'*64
                binding['socket']='other'
                self.db.commit(); worker.join(10)
        finally:
            self.db.rollback()
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes),1)
        self.assertIsInstance(outcomes[0],delegation_store.GuardedHandoff)
        snapshot=json.loads(outcomes[0].snapshot_json)
        self.assertEqual(snapshot['payload']['question'],'first')
        self.assertEqual(snapshot['payload']['file_evidence'][0]['sha256'],'a'*64)
        self.assertEqual(snapshot['binding']['socket'],'socket-a')
        row=self.db.execute('SELECT identity FROM delegation_requests WHERE scope=? AND request_key=?',('/project','key')).fetchone()
        self.assertEqual(row,(expected,))
        self.assertEqual(self.used(),1)

    def test_migrated_grant_without_payload_identity_fails_closed(self):
        self.grant()
        self.db.execute('UPDATE guarded_grants SET payload_identity=NULL WHERE grant_id=?',('grant',)); self.db.commit()
        with self.assertRaises(ValueError):
            self.store.guarded_begin('grant',self.binding,'job','key',self.payload)
        self.assertEqual(self.used(),0)

    def test_handoff_is_immutable_private_and_only_for_new_admission(self):
        secret='unique-prompt-handoff-93fd7'; payload=self.payload | {'question':secret,'file_evidence':[{'path':'a','sha256':'a'*64}]}
        self.store.create_guarded_grant('grant',self.binding,1,payload=payload)
        handoff=self.store.guarded_handoff('grant',self.binding,'job','key',payload)
        self.assertIsInstance(handoff,delegation_store.GuardedHandoff)
        snapshot=handoff.snapshot_json
        payload['question']='changed'; payload['file_evidence'][0]['sha256']='b'*64
        self.assertEqual(json.loads(snapshot)['payload']['question'],secret)
        self.assertEqual(json.loads(snapshot)['payload']['file_evidence'][0]['sha256'],'a'*64)
        with self.assertRaises(AttributeError): handoff.snapshot_json=b'changed'
        self.assertNotIn(secret,repr(handoff)); self.assertNotIn(secret,str(handoff))
        self.assertNotIn(secret.encode(),Path(self.path).read_bytes())
        original=json.loads(snapshot)
        self.assertIsNone(self.store.guarded_handoff('grant',original['binding'],'job','key',original['payload']))
        self.assertEqual(self.used(),1)

    def test_handoff_concurrent_callers_one_envelope(self):
        self.grant(); barrier=threading.Barrier(2); results=[]
        def attempt():
            db=sqlite3.connect(self.path,timeout=5)
            try:
                store=Store(db,'/project'); barrier.wait(timeout=5)
                results.append(store.guarded_handoff('grant',self.binding,'job','key',self.payload))
            finally: db.close()
        threads=[threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10); self.assertFalse(thread.is_alive())
        self.assertEqual(sum(isinstance(item,delegation_store.GuardedHandoff) for item in results),1)
        self.assertEqual(sum(item is None for item in results),1)
        self.assertEqual(self.used(),1)

    def test_commit_failure_returns_no_handoff_and_rolls_back(self):
        self.grant(); self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('CREATE TABLE handoff_parent(id INTEGER PRIMARY KEY)')
        self.db.execute('CREATE TABLE handoff_child(id INTEGER REFERENCES handoff_parent(id) DEFERRABLE INITIALLY DEFERRED)')
        self.db.execute('CREATE TRIGGER fail_handoff_commit AFTER INSERT ON guarded_admissions BEGIN INSERT INTO handoff_child VALUES (999); END')
        self.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.guarded_handoff('grant',self.binding,'job','key',self.payload)
        self.assertEqual(self.used(),0)
        self.assertIsNone(self.db.execute('SELECT 1 FROM delegation_requests WHERE request_key=?',('key',)).fetchone())

    def test_identity_corruption_rolls_back_without_handoff(self):
        self.grant()
        self.db.execute("CREATE TRIGGER corrupt_handoff AFTER INSERT ON guarded_admissions BEGIN UPDATE guarded_admissions SET identity='bad' WHERE request_key=NEW.request_key; END")
        self.db.commit()
        with self.assertRaises(ValueError):
            self.store.guarded_handoff('grant',self.binding,'job','key',self.payload)
        self.assertEqual(self.used(),0)
        self.assertIsNone(self.db.execute('SELECT 1 FROM delegation_requests WHERE request_key=?',('key',)).fetchone())

    def test_lost_handoff_after_crash_never_reissued(self):
        self.grant()
        process=multiprocessing.Process(target=crash_after_handoff,args=(self.path,self.binding,self.payload))
        process.start(); process.join(10)
        self.assertEqual(process.exitcode,72)
        self.assertIsNone(self.store.guarded_handoff('grant',self.binding,'job','key',self.payload))
        self.assertEqual(self.used(),1)

    def test_exception_after_commit_never_reissues_handoff(self):
        self.grant()
        with patch.object(delegation_store,'GuardedHandoff',side_effect=RuntimeError('handoff failed')):
            with self.assertRaises(RuntimeError):
                self.store.guarded_handoff('grant',self.binding,'job','key',self.payload)
        self.assertEqual(self.used(),1)
        self.assertIsNotNone(self.db.execute('SELECT 1 FROM guarded_admissions WHERE request_key=?',('key',)).fetchone())
        self.assertIsNone(self.store.guarded_handoff('grant',self.binding,'job','key',self.payload))

    def test_authority_rejects_incompatible_connection_transaction_modes(self):
        self.grant()
        modes=[('isolation_level',None)]
        if hasattr(self.db,'autocommit'):
            modes.extend([('autocommit',True),('autocommit',False)])
        for field,value in modes:
            with self.subTest(field=field,value=value):
                db=sqlite3.connect(self.path,timeout=5)
                try:
                    store=Store(db,'/project')
                    setattr(db,field,value)
                    for action in (
                        lambda: store.setup_guarded_authority(),
                        lambda: store.create_guarded_grant('other',self.binding,1,payload=self.payload),
                        lambda: store.revoke_guarded_grant('grant'),
                        lambda: store.guarded_begin('grant',self.binding,'job','key',self.payload),
                        lambda: store.guarded_handoff('grant',self.binding,'job','key',self.payload),
                    ):
                        with self.assertRaises(ValueError): action()
                finally: db.close()
        self.assertEqual(self.used(),0)
        self.assertEqual(self.db.execute('SELECT revoked FROM guarded_grants WHERE grant_id=?',('grant',)).fetchone(),(0,))
        self.assertIsNone(self.db.execute('SELECT 1 FROM delegation_requests WHERE request_key=?',('key',)).fetchone())

    def test_no_handoff_when_context_manager_fails_to_commit(self):
        self.grant()
        class NoCommit(sqlite3.Connection):
            def __exit__(self, *args): return False
        db=sqlite3.connect(self.path,timeout=5,factory=NoCommit)
        try:
            store=Store(db,'/project'); db.commit()
            with self.assertRaises(ValueError):
                store.guarded_handoff('grant',self.binding,'job','key',self.payload)
            self.assertFalse(db.in_transaction)
        finally: db.close()
        self.assertEqual(self.used(),0)
        self.assertIsNone(self.db.execute('SELECT 1 FROM delegation_requests WHERE request_key=?',('key',)).fetchone())

    def test_silent_rollback_connection_cannot_issue_handoff(self):
        self.grant()
        class SilentRollback(sqlite3.Connection):
            def __exit__(self, *args):
                self.rollback()
                return False
        db=sqlite3.connect(self.path,timeout=5,factory=SilentRollback)
        try:
            store=Store(db,'/project'); db.commit()
            with self.assertRaises(ValueError):
                store.guarded_handoff('grant',self.binding,'job','key',self.payload)
        finally: db.close()
        self.assertEqual(self.used(),0)
        self.assertIsNone(self.db.execute('SELECT 1 FROM guarded_admissions WHERE request_key=?',('key',)).fetchone())

    def test_missing_row_after_commit_prevents_handoff(self):
        self.grant(); removed=[]
        def remove_after_commit(sql):
            if not removed and sql.lstrip().startswith('SELECT r.identity,r.state,a.identity'):
                removed.append(True)
                other=sqlite3.connect(self.path,timeout=5)
                try:
                    other.execute('DELETE FROM guarded_admissions WHERE request_key=?',('key',))
                    other.commit()
                finally: other.close()
        self.db.set_trace_callback(remove_after_commit)
        try:
            with self.assertRaises(RuntimeError):
                self.store.guarded_handoff('grant',self.binding,'job','key',self.payload)
        finally: self.db.set_trace_callback(None)
        self.assertTrue(removed)
        self.assertEqual(self.used(),1)

    def test_legacy_grant_table_is_migrated_without_erasing_rows(self):
        db=sqlite3.connect(':memory:'); self.addCleanup(db.close)
        store=Store(db,'/project')
        db.execute('CREATE TABLE guarded_grants(scope TEXT,grant_id TEXT,binding TEXT,max_requests INTEGER,used INTEGER DEFAULT 0,revoked INTEGER DEFAULT 0,PRIMARY KEY(scope,grant_id))')
        db.execute("INSERT INTO guarded_grants(scope,grant_id,binding,max_requests) VALUES ('/project','old','{}',1)")
        db.commit(); store.setup_guarded_authority()
        self.assertEqual(db.execute("SELECT grant_id,payload_identity FROM guarded_grants").fetchall(),[('old',None)])

    def test_concurrent_last_credit(self):
        self.grant(); barrier=threading.Barrier(2); outcomes=[]
        def attempt(n):
            db=sqlite3.connect(self.path,timeout=5)
            try:
                store=Store(db,'/project'); barrier.wait(timeout=5)
                try:
                    result=store.guarded_begin('grant',self.binding,'job','key',self.payload)
                    outcomes.append('admitted' if result is None else 'replayed')
                except ValueError: outcomes.append('blocked')
            finally: db.close()
        threads=[threading.Thread(target=attempt,args=(n,)) for n in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10); self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes,['admitted','replayed'])
        self.assertEqual(self.used(),1)

    def test_revoke_before_admission(self):
        self.grant(); self.store.revoke_guarded_grant('grant')
        with self.assertRaises(ValueError):
            self.store.guarded_begin('grant',self.binding,'job','key',self.payload)
        self.assertEqual(self.used(),0)

    def test_admission_and_revocation_serialize(self):
        self.grant(); inside=threading.Event(); release=threading.Event(); outcomes=[]
        def admit():
            db=sqlite3.connect(self.path,timeout=5)
            try:
                store=Store(db,'/project'); original=store._begin_locked
                def paused(*args):
                    inside.set()
                    if not release.wait(5): raise RuntimeError('barrier timeout')
                    return original(*args)
                store._begin_locked=paused
                store.guarded_begin('grant',self.binding,'job','key',self.payload)
                outcomes.append('admitted')
            finally: db.close()
        def revoke():
            db=sqlite3.connect(self.path,timeout=5)
            try:
                Store(db,'/project').revoke_guarded_grant('grant')
                outcomes.append('revoked')
            finally: db.close()
        first=threading.Thread(target=admit); first.start(); self.assertTrue(inside.wait(5))
        second=threading.Thread(target=revoke); second.start(); release.set()
        first.join(10); second.join(10)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertCountEqual(outcomes,['admitted','revoked'])
        self.assertEqual(self.db.execute('SELECT revoked,used FROM guarded_grants WHERE grant_id=?',('grant',)).fetchone(),(1,1))
        with self.assertRaises(ValueError):
            self.store.guarded_begin('grant',self.binding,'job2','key2',self.payload | {'question':'next'})
        self.assertEqual(self.used(),1)

    def test_two_grants_cannot_admit_distinct_keys_for_one_job_concurrently(self):
        self.store.create_guarded_grant('grant',self.binding,1,payload=self.payload | {'question':'key'})
        other=self.binding | {'request_key':'key2'}
        self.store.create_guarded_grant('grant2',other,1,payload=self.payload | {'question':'key2'})
        barrier=threading.Barrier(2); outcomes=[]
        def attempt(grant,binding,key):
            db=sqlite3.connect(self.path,timeout=5)
            try:
                store=Store(db,'/project'); barrier.wait(timeout=5)
                try:
                    store.guarded_begin(grant,binding,'job',key,self.payload | {'question':key})
                    outcomes.append('admitted')
                except ValueError: outcomes.append('blocked')
            finally: db.close()
        threads=[threading.Thread(target=attempt,args=args) for args in
                 [('grant',self.binding,'key'),('grant2',other,'key2')]]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10); self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes,['admitted','blocked'])
        self.assertEqual(self.db.execute('SELECT SUM(used) FROM guarded_grants').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM guarded_admissions').fetchone()[0],1)

    def test_long_paths_and_grant_cannot_be_repurposed(self):
        root='/' + 'a'*200; sock='/' + 'b'*200
        store=Store(self.db,root); binding=self.binding | {'project_root':root,'socket':sock}
        store.create_guarded_grant('long',binding,2,payload=self.payload)
        with self.assertRaises(ValueError):
            store.guarded_begin('long',binding,'another-job','key',self.payload)
        with self.assertRaises(ValueError):
            store.guarded_begin('long',binding,'job','another-key',self.payload)
        self.assertIsNone(store.guarded_begin('long',binding,'job','key',self.payload))

    def test_setup_ddl_failure_rolls_back_all_new_tables(self):
        db=sqlite3.connect(':memory:'); self.addCleanup(db.close)
        store=Store(db,'/project')
        def authorizer(action, arg1, arg2, dbname, source):
            return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX and arg1 == 'guarded_one_request_per_job' else sqlite3.SQLITE_OK
        db.set_authorizer(authorizer)
        with self.assertRaises(sqlite3.DatabaseError): store.setup_guarded_authority()
        db.set_authorizer(None)
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('guarded_grants',tables)
        self.assertNotIn('guarded_admissions',tables)

    def test_legacy_duplicate_jobs_prevent_migration_without_erasing_rows(self):
        db=sqlite3.connect(':memory:'); self.addCleanup(db.close)
        store=Store(db,'/project')
        db.execute('CREATE TABLE guarded_admissions(scope TEXT, request_key TEXT, grant_id TEXT, job_id TEXT, identity TEXT, PRIMARY KEY(scope,request_key))')
        db.executemany('INSERT INTO guarded_admissions VALUES (?,?,?,?,?)',
                       [('/project','one','g1','job','a'),('/project','two','g2','job','b')])
        db.commit()
        with self.assertRaises(sqlite3.IntegrityError): store.setup_guarded_authority()
        self.assertEqual(db.execute('SELECT COUNT(*) FROM guarded_admissions').fetchone()[0],2)
        self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='guarded_grants'").fetchone())


if __name__ == '__main__': unittest.main()
