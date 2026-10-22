import json
from pathlib import Path
import sqlite3
import sys
import unittest
import tempfile
import threading
from unittest.mock import patch
import subprocess
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ironclaw_ssh import Endpoint, ProbeError, request
from ironclaw_tasks import Tasks, TaskError


class TasksTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.endpoint = Endpoint('host.example','agent',22,'/tmp/key','/tmp/token')
        self.calls = []
        self.pages = [{'messages': [], 'next_cursor': None}]
        self.fail = False
        self.tasks = Tasks(self.db,self.endpoint,self.transport)

    def transport(self, endpoint, method, path, payload=None, **kwargs):
        self.assertFalse(self.db.in_transaction)
        self.calls.append((method,path,payload))
        if method == 'POST':
            row = self.db.execute('SELECT thread_action,message_action,state FROM ironclaw_tasks').fetchone()
            self.assertEqual(row[2], 'dispatching')
            self.assertIn(payload['client_action_id'], row[:2])
            if self.fail: raise TimeoutError('private')
        if path.endswith('/session'): return {'session_channel_extension_id':'channel'}
        if path.endswith('/threads'): return {'thread':{'thread_id':'thread'}}
        if path.endswith('/messages'): return {'thread_id':'thread','run_id':'run','status':'queued'}
        return self.pages.pop(0)

    def submit(self):
        return self.tasks.submit('key','question',allow_submit=True)

    def test_concurrent_equivalent_keys_cannot_dispatch_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requests.sqlite'
            admitted, release = threading.Event(), threading.Event()
            calls, errors = [], []
            def transport(endpoint, method, route, payload=None, **kwargs):
                calls.append((method, route))
                if route.endswith('/session'):
                    admitted.set()
                    if not release.wait(5):
                        raise TimeoutError('fixture wait')
                    return {'session_channel_extension_id': 'channel'}
                if route.endswith('/threads'):
                    return {'thread': {'thread_id': 'thread'}}
                return {'thread_id': 'thread', 'run_id': 'run', 'status': 'queued'}
            def first_request():
                db = sqlite3.connect(path)
                try:
                    Tasks(db, self.endpoint, transport).submit('first', 'same request', allow_submit=True)
                except Exception as error:
                    errors.append(error)
                finally:
                    db.close()
            first = threading.Thread(target=first_request)
            first.start()
            try:
                self.assertTrue(admitted.wait(5))
                db = sqlite3.connect(path)
                try:
                    with self.assertRaises(TaskError):
                        Tasks(db, self.endpoint, transport).submit('second', 'same request', allow_submit=True)
                finally:
                    db.close()
            finally:
                release.set()
                first.join(5)
            self.assertFalse(first.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(len([call for call in calls if call[0] == 'POST']), 2)

    def test_result_survives_database_close_without_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'requests.sqlite'
            def transport(endpoint, method, route, payload=None, **kwargs):
                if route.endswith('/session'):
                    return {'session_channel_extension_id': 'channel'}
                if route.endswith('/threads'):
                    return {'thread': {'thread_id': 'thread'}}
                if route.endswith('/messages'):
                    return {'thread_id': 'thread', 'run_id': 'run', 'status': 'queued'}
                return {'messages': [{'kind': 'assistant', 'status': 'finalized', 'turn_run_id': 'run', 'content': 'persisted answer'}], 'next_cursor': None}
            db = sqlite3.connect(path)
            try:
                tasks = Tasks(db, self.endpoint, transport)
                tasks.submit('persisted', 'question', allow_submit=True)
                self.assertEqual(tasks.poll('persisted')['state'], 'finished')
            finally:
                db.close()
            restored_db = sqlite3.connect(path)
            try:
                restored = Tasks(restored_db)
                self.assertEqual(restored.get('persisted')['result'], 'persisted answer')
                self.assertFalse(restored.get('persisted')['acknowledged'])
                self.assertTrue(restored.get('persisted', acknowledge=True)['acknowledged'])
                self.assertEqual(restored.list_requests(), [{'request_key': 'persisted', 'state': 'finished', 'acknowledged': True}])
            finally:
                restored_db.close()

    def test_persist_before_dispatch_and_no_duplicate(self):
        self.assertEqual(self.submit()['state'],'pending')
        calls = len(self.calls)
        self.submit()
        self.assertEqual(len(self.calls),calls)
        with self.assertRaises(TaskError): self.tasks.submit('key','different',allow_submit=True)

    def test_ambiguity_is_never_redispatched_after_reopen(self):
        self.fail = True
        with self.assertRaises(TaskError): self.submit()
        calls = len(self.calls)
        restored = Tasks(self.db,self.endpoint,self.transport)
        self.assertEqual(restored.submit('key','question',allow_submit=True)['state'],'ambiguous')
        self.assertEqual(len(self.calls), calls)

    def test_optin_required_before_any_write(self):
        with self.assertRaises(TaskError): self.tasks.submit('key','question')
        self.assertEqual(self.db.execute('SELECT count(*) FROM ironclaw_tasks').fetchone()[0],0)
        with self.assertRaises(ProbeError): request(self.endpoint,'POST','/api/webchat/v2/threads',{})

    def test_paginated_final_is_correlated_and_durable_until_ack(self):
        self.submit()
        self.pages = [
          {'messages':[{'kind':'assistant','status':'finalized','turn_run_id':'other','content':'wrong'},
                       {'kind':'assistant','status':'streaming','turn_run_id':'run','content':'partial'}],'next_cursor':'older'},
          {'messages':[{'kind':'assistant_message','status':'finalized','turn_run_id':'run','content':'answer'}],'next_cursor':None}]
        self.assertEqual(self.tasks.poll('key')['result'],'answer')
        self.assertFalse(self.tasks.get('key')['acknowledged'])
        self.assertTrue(self.tasks.get('key',acknowledge=True)['acknowledged'])
        self.assertEqual(Tasks(self.db,self.endpoint,self.transport).get('key')['result'],'answer')

    def test_gate_needs_operator_and_never_resolves(self):
        self.submit()
        self.assertEqual(self.tasks.observe_event('key','auth_required',{'prompt':{'turn_run_id':'run','gate_ref':'gate'}})['state'],'needs_operator')
        self.assertFalse(any('/resolve' in c[1] for c in self.calls))

    def test_cursor_loop_does_not_record_partial_success(self):
        self.submit()
        self.pages = [{'messages':[],'next_cursor':'same'}]*2
        with self.assertRaises(TaskError): self.tasks.poll('key')
        self.assertEqual(self.tasks.get('key')['state'],'pending')

    def test_crash_dispatching_is_never_replayed(self):
        self.submit()
        with self.db: self.db.execute("UPDATE ironclaw_tasks SET state='dispatching'")
        calls = len(self.calls)
        self.assertEqual(self.submit()['state'],'dispatching')
        self.assertEqual(len(self.calls), calls)

    def test_equivalent_other_key_cannot_bypass_ambiguity(self):
        self.fail = True
        with self.assertRaises(TaskError): self.submit()
        calls = len(self.calls)
        with self.assertRaises(TaskError): self.tasks.submit('other-key','question',allow_submit=True)
        self.assertEqual(len(self.calls), calls)

    def test_busy_and_unknown_ack_are_not_pending(self):
        for outcome,state in [('rejected_busy','blocked_busy'),('deferred_busy','deferred'),('unexpected','needs_review')]:
            db = sqlite3.connect(':memory:')
            self.addCleanup(db.close)
            def transport(ep,method,path,payload=None,**kwargs):
                if path.endswith('/session'): return {'session_channel_extension_id':'channel'}
                if path.endswith('/threads'): return {'thread':{'thread_id':'thread'}}
                return {'thread_id':'thread','run_id':'run','status':'queued','outcome':outcome}
            task = Tasks(db,self.endpoint,transport)
            self.assertEqual(task.submit('k','q',allow_submit=True)['state'], state)

    @patch('ironclaw_ssh._read_token', return_value='private-token')
    @patch('ironclaw_ssh.subprocess.run')
    def test_post_content_is_only_stdin_curl_escaped(self, run, token):
        run.return_value = subprocess.CompletedProcess([],0,'{"thread":{"thread_id":"t"}}\n200','')
        payload = {'client_action_id':'action','thread_id':'thread','content':'hello"\nurl = "http://evil"\n\\'}
        request(self.endpoint,'POST','/api/webchat/v2/channels/channel/messages',payload,allow_submit=True)
        args, kwargs = run.call_args
        self.assertNotIn('private-token',repr(args))
        data_lines = [x for x in kwargs['input'].splitlines() if x.startswith('data = ')]
        self.assertEqual(len(data_lines),1)
        self.assertNotIn('\nurl = "http://evil"',kwargs['input'])

    def test_deferred_known_run_can_finish_and_without_run_stays_local(self):
        self.submit()
        with self.db: self.db.execute("UPDATE ironclaw_tasks SET state='deferred'")
        self.pages = [{'messages':[{'kind':'assistant','status':'finalized','turn_run_id':'run','content':'done'}],'next_cursor':None}]
        self.assertEqual(self.tasks.poll('key')['result'], 'done')
        local = Tasks(self.db)
        self.assertEqual(local.get('key', acknowledge=True)['result'], 'done')
        self.assertEqual(local.list_requests(), [{'request_key':'key','state':'finished','acknowledged':True}])
        with self.assertRaises(TaskError): local.poll('key')
        with self.db: self.db.execute("UPDATE ironclaw_tasks SET state='deferred',run_id=NULL")
        calls = len(self.calls)
        self.assertEqual(self.tasks.poll('key')['state'], 'deferred')
        self.assertEqual(len(self.calls), calls)

    def test_deferred_gate_and_local_list_are_bounded(self):
        self.submit()
        with self.db: self.db.execute("UPDATE ironclaw_tasks SET state='deferred'")
        self.assertEqual(self.tasks.observe_event('key','gate',{'prompt':{'turn_run_id':'run','gate_ref':'g'}})['state'],'needs_operator')
        local = Tasks(self.db)
        with self.assertRaises(TaskError): local.list_requests(101)
        with self.assertRaises(TaskError): local.observe_event('key','gate',{})

    def test_changed_endpoint_cannot_poll_or_redispatch(self):
        self.submit()
        changed = Endpoint('other.example','agent',22,'/tmp/key','/tmp/token')
        restored = Tasks(self.db,changed,self.transport)
        with self.assertRaises(TaskError): restored.poll('key')
        self.assertEqual(restored.get('key')['state'], 'pending')
        with self.assertRaises(TaskError): restored.submit('key','question',allow_submit=True)

    def test_disallowed_routes_rejected_before_secret_read(self):
        for method,path in [('POST','/api/webchat/v2/evil'),('GET','http://evil')]:
            with self.assertRaises(ProbeError): request(self.endpoint,method,path,allow_submit=True)


if __name__ == '__main__': unittest.main()
