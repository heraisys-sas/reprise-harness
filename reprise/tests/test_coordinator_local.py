import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from test_coordinator_jobs import fake_crypt, PROPOSAL
from coordinator_jobs import Jobs
from coordinator_local import LocalExecution, Control, SUPERVISION_CONTRACT
from project_registry import ProjectRegistry
from apple_pilot_service import Service


def uid(): return str(uuid.uuid4())


def run_owned(state, project, operation, run, helper):
    LocalExecution(Jobs(state,crypt=fake_crypt),helper=helper).run(project,operation,run,Control())


class LocalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.state = self.base/'state'; self.state.mkdir(mode=0o700)
        self.repo = self.base/'project'; self.repo.mkdir()
        subprocess.run(['git','-C',str(self.repo),'init','-q'],check=True)
        with ProjectRegistry(self.state) as registry:
            self.project = registry.create('Local', self.repo, self.repo)['id']
        self.jobs = Jobs(self.state, crypt=fake_crypt); self.operation = uid(); self.work = uid()
        row = self.jobs.prepare_proposal(self.project, self.operation, PROPOSAL)
        context = self.jobs.consent_context(self.project, self.work)
        self.jobs.consent(self.project,self.operation,authorization_id=uid(),expected_revision=0,
            expected_proposal_sha256=row['proposal_sha256'],host_context=context['context'],
            expected_context_sha256=context['context_sha256'],backend='foundation-models')
        self.helper = self.base/'helper'
        self.script("import json,sys\nx=json.load(sys.stdin)\nprint(json.dumps({'available':True,'answer':'local nonce result'}))\n")
        self.local = LocalExecution(self.jobs, helper=self.helper)

    def script(self, text):
        prefix = ('#!/usr/bin/python3\nimport sys,json,signal,os\n'
            'if sys.argv[1:]==["--supervision-contract"]:\n'
            ' print('+repr(json.dumps(SUPERVISION_CONTRACT))+'); sys.exit(0)\n'
            'if len(sys.argv)==3 and sys.argv[1]=="--deadline-seconds":\n'
            ' signal.signal(signal.SIGALRM,lambda *_: os._exit(124)); signal.alarm(int(sys.argv[2]))\n')
        self.helper.write_text(prefix+text); self.helper.chmod(0o700)

    def reserve(self, seconds=3, run=None):
        plan = self.local.plan(self.project,self.operation,seconds)
        row = self.jobs.recover(self.project,self.operation)
        run = run or uid()
        result, fresh = self.jobs.reserve_local(self.project,self.operation,run_id=run,
            expected_revision=row['revision'],manifest=plan['manifest'])
        self.assertTrue(fresh)
        return run, plan, result

    def host(self):
        output=[]
        host=Service(self.state,output.append,jobs_factory=lambda _:self.jobs,
            local_factory=lambda jobs:LocalExecution(jobs,helper=self.helper),
            backend_factory=lambda:self.fail('AFM conversation must not be created'))
        self.addCleanup(host.shutdown)
        def call(op, **fields):
            ident=uid(); host.handle({'id':ident,'op':op,'enabled':True,'project_id':self.project,**fields})
            end=time.monotonic()+5
            while time.monotonic()<end:
                matches=[row for row in output if row['id']==ident]
                if matches:return matches[-1]
                time.sleep(.01)
            self.fail('host response missing')
        return host, call

    def test_jsonl_local_consent_is_explicit_and_codex_is_not_substituted(self):
        host,call=self.host(); operation=uid()
        row=call('job_prepare',operation_id=operation,proposal=PROPOSAL)['job']
        context=call('job_consent_context',work_id=self.work)
        fields=dict(operation_id=operation,work_id=self.work,authorization_id=uid(),expected_revision=0,
            expected_proposal_sha256=row['proposal_sha256'],expected_context_sha256=context['context_sha256'])
        local=call('job_authorize_local',**fields)
        self.assertEqual(local['job']['operator_authorization']['backend'],'foundation-models')
        codex_op=uid(); row=call('job_prepare',operation_id=codex_op,proposal=PROPOSAL)['job']
        codex=call('job_authorize',**dict(fields,operation_id=codex_op,authorization_id=uid(),expected_proposal_sha256=row['proposal_sha256']))
        self.assertEqual(codex['job']['operator_authorization']['backend'],'codex')
        denied=call('job_local_plan',operation_id=codex_op,max_seconds=3)
        self.assertNotEqual(denied['status'],'finished')
        self.assertIsNone(self.jobs.recover(self.project,codex_op)['local_execution'])

    def test_service_start_replay_and_recovery_are_one_generation(self):
        marker=self.base/'generations'
        self.script("from pathlib import Path\nPath("+repr(str(marker))+").open('a').write('generation\\n')\nprint(json.dumps({'available':True,'answer':'service result'}))\n")
        host,call=self.host()
        plan=call('job_local_plan',operation_id=self.operation,max_seconds=3)['local_plan']
        fields=dict(operation_id=self.operation,run_id=uid(),expected_revision=1,
                    expected_manifest_sha256=plan['manifest_sha256'])
        started=call('job_local_start',**fields)
        self.assertEqual(started['status'],'finished')
        end=time.monotonic()+5
        while time.monotonic()<end:
            row=call('job_recover',operation_id=self.operation)['job']
            if row['status']=='local_recorded': break
            time.sleep(.02)
        self.assertEqual(row['local_execution']['receipt']['response'],'service result')
        replay=call('job_local_start',**fields)
        self.assertEqual(replay['job']['status'],'local_recorded')
        self.assertEqual(marker.read_text().splitlines(),['generation'])

    def test_shutdown_fences_a_start_paused_after_durable_intent(self):
        host,call=self.host()
        plan=call('job_local_plan',operation_id=self.operation,max_seconds=3)['local_plan']
        reserved=threading.Event(); resume=threading.Event(); original=self.jobs.reserve_local
        def paused(*a,**kw):
            result=original(*a,**kw); reserved.set(); resume.wait(5); return result
        with patch.object(self.jobs,'reserve_local',side_effect=paused):
            ident=uid(); host.handle({'id':ident,'op':'job_local_start','enabled':True,'project_id':self.project,
                'operation_id':self.operation,'run_id':uid(),'expected_revision':1,
                'expected_manifest_sha256':plan['manifest_sha256']})
            self.assertTrue(reserved.wait(5))
            stopped=[]; closing=threading.Thread(target=lambda:stopped.append(host.shutdown(timeout=5)))
            closing.start()
            end=time.monotonic()+2
            while not host.job_closing and time.monotonic()<end:time.sleep(.01)
            self.assertTrue(host.job_closing); resume.set(); closing.join(6)
        self.assertEqual(stopped,[True]); self.assertEqual(host.local_runs,{})
        self.assertEqual(self.jobs.recover(self.project,self.operation)['status'],'local_indeterminate')
        self.assertFalse((self.state/'coordinator-jobs.noindex/local-admissions.sqlite').exists())

    def test_concurrent_owners_consume_only_one_handoff(self):
        marker=self.base/'generations'
        self.script("from pathlib import Path\nPath("+repr(str(marker))+").open('a').write('generation\\n')\nprint(json.dumps({'available':True,'answer':'one'}))\n")
        run,_,_=self.reserve()
        owners=[multiprocessing.Process(target=run_owned,args=(str(self.state),self.project,self.operation,run,str(self.helper))) for _ in range(2)]
        for owner in owners:owner.start()
        for owner in owners:owner.join(8); self.assertEqual(owner.exitcode,0)
        self.assertEqual(marker.read_text().splitlines(),['generation'])
        self.assertEqual(self.local.recover(self.project,self.operation)['status'],'local_recorded')

    def test_status_reader_preserves_other_thread_sqlite_posix_lock(self):
        run, _, _ = self.reserve()
        database = self.state/'coordinator-jobs.noindex/local-admissions.sqlite'
        # A competing process must remain blocked after another thread opens and
        # closes its status connection. A raw fd close would drop our OS locks.
        probe = """import sqlite3,sys
connection=sqlite3.connect(sys.argv[1],timeout=.1)
try:
 connection.execute('BEGIN IMMEDIATE'); print('acquired');connection.rollback()
except sqlite3.OperationalError: print('locked')
finally: connection.close()
"""
        with self.local._store(str(self.repo)) as store:
            store.db.execute('BEGIN IMMEDIATE')
            reader=threading.Thread(target=lambda:self.local.recover(self.project,self.operation,active=True))
            reader.start();reader.join(5);self.assertFalse(reader.is_alive())
            result=subprocess.run([sys.executable,'-c',probe,str(database)],capture_output=True,text=True,check=True)
            self.assertEqual(result.stdout.strip(),'locked')
            store.db.rollback()
        result=subprocess.run([sys.executable,'-c',probe,str(database)],capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'acquired')

    def test_owner_crash_keeps_native_helper_expiration_without_pid_kill(self):
        expired=self.base/'expired'; started=self.base/'started'
        self.script("import pathlib,time\n"
            "def expire(*_):\n pathlib.Path("+repr(str(expired))+").write_text('expired'); os._exit(124)\n"
            "signal.signal(signal.SIGALRM,expire)\npathlib.Path("+repr(str(started))+").write_text('started')\ntime.sleep(20)\n")
        run, _, _ = self.reserve(seconds=2)
        owner=multiprocessing.Process(target=run_owned,args=(str(self.state),self.project,self.operation,run,str(self.helper)))
        owner.start()
        end=time.monotonic()+5
        while not started.exists() and time.monotonic()<end: time.sleep(.02)
        self.assertTrue(started.exists())
        owner.kill(); owner.join(3)
        end=time.monotonic()+4
        while not expired.exists() and time.monotonic()<end: time.sleep(.02)
        self.assertTrue(expired.exists(), 'the helper must expire after losing its owner')
        with patch('coordinator_local._spawn',side_effect=AssertionError('no redispatch')):
            row=self.local.recover(self.project,self.operation)
        self.assertEqual(row['status'],'local_indeterminate')
        self.assertIsNone(row['local_execution']['receipt'])

    def test_one_admission_result_and_recovery_without_redispatch(self):
        run, plan, row = self.reserve()
        self.local.run(self.project,self.operation,run,Control())
        done = self.local.recover(self.project,self.operation)
        self.assertEqual(done['status'],'local_recorded')
        receipt = done['local_execution']['receipt']
        self.assertEqual(receipt['status'],'finished')
        self.assertEqual(receipt['response'],'local nonce result')
        self.assertTrue(receipt['process_started'] and receipt['process_reaped'])
        with patch('coordinator_local._spawn',side_effect=AssertionError('no replay')):
            self.local.run(self.project,self.operation,run,Control())
            self.local.recover(self.project,self.operation)
        same, fresh = self.jobs.reserve_local(self.project,self.operation,run_id=run,
            expected_revision=1,manifest=plan['manifest'])
        self.assertFalse(fresh)
        with self.assertRaises(ValueError):
            self.jobs.reserve_local(self.project,self.operation,run_id=uid(),expected_revision=1,manifest=plan['manifest'])
        self.assertNotIn(b'local nonce result',(self.state/'coordinator-jobs.noindex/local-admissions.sqlite').read_bytes())

    def test_owned_process_is_stopped_while_generation_is_pending(self):
        self.script('import sys,time\nsys.stdin.read()\ntime.sleep(20)\n')
        run, _, _ = self.reserve(); control = Control()
        worker = threading.Thread(target=self.local.run,args=(self.project,self.operation,run,control))
        worker.start()
        end = time.monotonic()+5
        while time.monotonic()<end:
            row=self.jobs.recover(self.project,self.operation)
            if row['status']=='local_running': break
            time.sleep(.02)
        self.assertEqual(row['status'],'local_running')
        with control.lock:
            self.jobs.local_update(self.project,self.operation,run,stop=True)
            control.cancelled.set()
        worker.join(5); self.assertFalse(worker.is_alive())
        result=self.local.recover(self.project,self.operation)['local_execution']['receipt']
        self.assertEqual(result['status'],'interrupted'); self.assertEqual(result['stop_reason'],'user_stop')
        self.assertTrue(result['process_reaped']); self.assertFalse(result['work_completed'])

    def test_deadline_and_invalid_output_are_terminal_without_fallback(self):
        self.script('import time\ntime.sleep(20)\n')
        run, _, _ = self.reserve(seconds=1)
        self.local.run(self.project,self.operation,run,Control())
        result=self.local.recover(self.project,self.operation)['local_execution']['receipt']
        self.assertEqual(result['stop_reason'],'execution_deadline')
        self.assertTrue(result['process_reaped'])

    def test_changed_helper_and_lost_intent_are_not_retried(self):
        run, _, _=self.reserve(); self.script('raise SystemExit(1)\n')
        with patch('coordinator_local._spawn',side_effect=AssertionError('not allowed')):
            self.local.run(self.project,self.operation,run,Control())
        row=self.local.recover(self.project,self.operation)
        self.assertEqual(row['status'],'local_indeterminate')
        self.assertIsNone(row['local_execution']['receipt'])

    def test_new_controller_recovers_answer_after_projection_write_failure(self):
        run, _, _=self.reserve(); original=self.jobs.local_update
        def fail(*a,**kw):
            if kw.get('stage')=='recorded': raise OSError('projection failed')
            return original(*a,**kw)
        with patch.object(self.jobs,'local_update',side_effect=fail):
            self.local.run(self.project,self.operation,run,Control())
        recovered=LocalExecution(Jobs(self.state,crypt=fake_crypt),helper=self.helper).recover(self.project,self.operation)
        self.assertEqual(recovered['local_execution']['receipt']['response'],'local nonce result')
        self.assertEqual(recovered['status'],'local_recorded')

    def test_cancel_before_admission_and_concurrent_reservations_do_not_spawn(self):
        run, _, _=self.reserve(); control=Control(); control.cancelled.set()
        with patch('coordinator_local._spawn',side_effect=AssertionError('cancelled')):
            self.local.run(self.project,self.operation,run,control)
        self.assertEqual(self.local.recover(self.project,self.operation)['status'],'local_indeterminate')
