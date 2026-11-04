import base64
import hashlib
import multiprocessing
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import coordinator_jobs as jobs
from coordinator_worker import Worker
from coordinator_bridge import LocalBridge
from project_registry import ProjectRegistry


def uid(): return str(uuid.uuid4())


def crypt(data, purpose, operation):
    if purpose != jobs.PURPOSE: raise ValueError('purpose')
    if operation == 'seal':
        encoded = base64.b64encode(data)
        return b'TEST:' + hashlib.sha256(encoded).digest() + encoded
    if not data.startswith(b'TEST:') or hashlib.sha256(data[37:]).digest() != data[5:37]:
        raise ValueError('corrupt')
    return base64.b64decode(data[37:], validate=True)


PROPOSAL = {'objective': 'Examiner le code', 'acceptance': ['preuves'], 'constraints': ['pas de push'], 'questions': []}
CAP = {'id':'codex', 'configured':True, 'modes':['expertise', 'implementation'], 'workspace_write_authorized':True}


def auth(project, work):
    return {'project_id':project, 'work_id':work, 'routing_role':'hypervisor', 'routing_actor':'deepseek-harness',
            'backend':'codex', 'mode':'expertise', 'billing':'declared', 'billing_authorized':True,
            'permission_granted':True, 'workspace_write_authorized':False, 'max_requests':1,
            'max_seconds':20, 'max_cost_minor':0, 'currency':'EUR'}


class Gate:
    def __init__(self, revoke_at=None, invalid_flag=None):
        self.calls = 0; self.revoke_at = revoke_at; self.invalid_flag = invalid_flag
    def verify(self, job, snapshot):
        self.calls += 1
        if self.calls == self.revoke_at: return None
        a = job['authority']
        result = {key:a[key] for key in ('project_id','work_id','backend','mode','max_requests','max_seconds','max_cost_minor','currency')}
        result.update(job_id=job['job_id'], request_key=job['request_key'], project_root=snapshot['project_root'],
            permission_verified=True, billing_verified=True, local_request_budget_enforced=True,
            local_wait_deadline_enforced=True, billing_mode='subscription_no_overage',
            extra_usage_disabled_verified=True, hard_cost_cap_enforced=False, revocation_guard_enforced=True,
            evidence_id='fake-test-only')
        if self.invalid_flag: result[self.invalid_flag] = False
        return result


class Bridge:
    def __init__(self, root, *, behavior='recorded', barrier=None, on_submit=None):
        self.root = str(root); self.behavior = behavior; self.submissions=[]
        self.saved = {}; self.barrier=barrier; self.on_submit=on_submit
    def discover(self):
        return {'project_root':self.root, 'automatic_fallback':False, 'backends':[CAP]}
    def receipt(self, key):
        if self.barrier:
            barrier, self.barrier = self.barrier, None
            barrier.wait(timeout=5)
        return self.saved.get(key, {'status':'not_found', 'request_key':key})
    def submit(self, payload):
        self.submissions.append(payload)
        if self.on_submit: self.on_submit()
        if self.behavior == 'crash': raise OSError('lost response')
        if self.behavior == 'exit': os._exit(73)
        if self.behavior == 'busy': return {'status':'busy'}
        if self.behavior == 'uncertain':
            self.saved[payload['request_key']] = {'request_key':payload['request_key'], 'state':'indeterminate'}
            return {'status':'unknown'}
        self.saved[payload['request_key']] = {'status':'finished', 'request_key':payload['request_key'],
            'state':'recorded', 'available':True, 'result_sha256':'a'*64, 'attempt_id':'native-attempt'}
        return {'status':'finished'}


def crash_worker(state, root, project, job_id):
    Worker(jobs.Jobs(state, crypt=crypt), Bridge(root, behavior='exit'), gate=Gate()).run_once(project, job_id)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name); self.state=self.base/'state'; self.state.mkdir(mode=0o700)
        self.repo=self.base/'repo'; self.repo.mkdir()
        subprocess.run(['git','-C',str(self.repo),'init','-q'],check=True)
        with ProjectRegistry(self.state) as registry:
            self.project=registry.create('Atlas',self.repo,self.repo)['id']
        self.work=uid(); self.authority=auth(self.project,self.work)
        self.store=jobs.Jobs(self.state,crypt=crypt)
        self.job=self.store.prepare(uid(), PROPOSAL, authority=self.authority, capability=CAP)

    def worker(self, bridge=None, gate=None):
        return Worker(self.store, bridge or Bridge(self.repo), gate=gate)

    def test_no_verifier_means_no_dispatch_or_attempt(self):
        bridge=Bridge(self.repo)
        result=self.worker(bridge).run_once(self.project,self.job['job_id'])
        self.assertEqual(result['status'],'authorization_or_budget_unverified')
        self.assertEqual(bridge.submissions,[])
        self.assertEqual(self.store.get(self.project,self.job['job_id'])['status'],'draft')

    def test_each_claim_is_required_and_scope_pinned(self):
        for flag in ('permission_verified','billing_verified','local_request_budget_enforced',
                     'local_wait_deadline_enforced','extra_usage_disabled_verified','revocation_guard_enforced'):
            with self.subTest(flag=flag):
                bridge=Bridge(self.repo)
                result=self.worker(bridge,Gate(invalid_flag=flag)).run_once(self.project,self.job['job_id'])
                self.assertEqual(result['status'],'authorization_or_budget_unverified')
                self.assertEqual(bridge.submissions,[])
        class HardCap(Gate):
            def verify(inner, job, snapshot):
                proof=super(HardCap,inner).verify(job,snapshot)
                proof.update(billing_mode='hard_cost_cap', hard_cost_cap_enforced=False)
                return proof
        self.assertEqual(self.worker(Bridge(self.repo),HardCap()).run_once(self.project,self.job['job_id'])['status'], 'authorization_or_budget_unverified')
        class WrongScope(Gate):
            def verify(inner, job, snapshot):
                proof=super(WrongScope,inner).verify(job,snapshot); proof['project_id']=uid(); return proof
        self.assertEqual(self.worker(Bridge(self.repo),WrongScope()).run_once(self.project,self.job['job_id'])['status'], 'authorization_or_budget_unverified')

    def test_recorded_receipt_and_no_duplicate_dispatch(self):
        bridge=Bridge(self.repo); worker=self.worker(bridge,Gate())
        result=worker.run_once(self.project,self.job['job_id'])
        self.assertEqual(result['status'],'indeterminate')
        self.assertTrue(result['dispatch_attempted'])
        self.assertFalse(result['execution_proven'])
        self.assertEqual(bridge.submissions[0]['request_key'],self.job['request_key'])
        self.assertEqual(bridge.submissions[0]['backend'],'codex')
        self.assertEqual(bridge.submissions[0]['mode'],'expertise')
        self.assertEqual(self.store.get(self.project,self.job['job_id'])['status'],'indeterminate')
        self.assertEqual(self.store.get(self.project,self.job['job_id'])['authorization_evidence']['evidence_id'], 'fake-test-only')
        self.assertEqual(worker.run_once(self.project,self.job['job_id'])['status'],'indeterminate')
        self.assertEqual(len(bridge.submissions),1)
        self.assertEqual(worker.status(self.project,self.job['job_id'])['status'],'indeterminate')

    def test_explicit_verified_hard_cost_cap_is_a_distinct_route_policy(self):
        class HardCap(Gate):
            def verify(inner, job, snapshot):
                proof=super(HardCap,inner).verify(job,snapshot)
                proof.update(billing_mode='hard_cost_cap', hard_cost_cap_enforced=True,
                             extra_usage_disabled_verified=False)
                return proof
        bridge=Bridge(self.repo)
        result=self.worker(bridge,HardCap()).run_once(self.project,self.job['job_id'])
        self.assertEqual(result['status'],'indeterminate')

    def test_untrusted_or_incomplete_receipt_never_claims_recorded(self):
        bridge=Bridge(self.repo,behavior='uncertain'); worker=self.worker(bridge,Gate())
        self.assertEqual(worker.run_once(self.project,self.job['job_id'])['status'],'indeterminate')
        key=self.job['request_key']
        bridge.saved[key]={'request_key':key,'state':'recorded','available':True,'result_sha256':'bad'}
        self.assertEqual(worker.status(self.project,self.job['job_id'])['status'],'indeterminate')

    def test_revoked_between_validation_and_submission_is_not_admitted(self):
        bridge=Bridge(self.repo); worker=self.worker(bridge,Gate(revoke_at=2))
        self.assertEqual(worker.run_once(self.project,self.job['job_id'])['status'],'not_admitted')
        self.assertFalse(bridge.submissions)
        self.assertEqual(self.store.get(self.project,self.job['job_id'])['status'],'not_admitted')
        self.assertFalse(worker.run_once(self.project,self.job['job_id'])['dispatched'])

    def test_timeout_or_lost_response_is_indeterminate_no_replay(self):
        bridge=Bridge(self.repo,behavior='crash'); worker=self.worker(bridge,Gate())
        result=worker.run_once(self.project,self.job['job_id'])
        self.assertEqual(result['status'],'indeterminate')
        self.assertEqual(worker.status(self.project,self.job['job_id'])['status'],'indeterminate')
        self.assertEqual(worker.run_once(self.project,self.job['job_id'])['status'],'indeterminate')
        self.assertEqual(len(bridge.submissions),1)

    def test_busy_is_only_this_worker_not_admitted(self):
        bridge=Bridge(self.repo,behavior='busy'); worker=self.worker(bridge,Gate())
        self.assertEqual(worker.run_once(self.project,self.job['job_id']),
                         {'status':'not_admitted','reason':'bridge_busy','dispatched':False})
        self.assertEqual(self.store.get(self.project,self.job['job_id'])['status'],'not_admitted')

    def test_bridge_receipt_existing_prevents_new_attempt(self):
        bridge=Bridge(self.repo); bridge.saved[self.job['request_key']]={'status':'finished','state':'running'}
        self.assertEqual(self.worker(bridge,Gate()).run_once(self.project,self.job['job_id'])['status'], 'preexisting_receipt_or_unknown')
        self.assertFalse(bridge.submissions)
        self.assertEqual(self.store.get(self.project,self.job['job_id'])['status'],'draft')

    def test_missing_modes_and_snapshot_mutation_fail_closed(self):
        class LegacyBridge(Bridge):
            def discover(inner):
                snapshot=super(LegacyBridge,inner).discover()
                snapshot['backends']=[{'id':'codex','configured':True,'writes':False}]
                return snapshot
        bridge=LegacyBridge(self.repo)
        self.assertEqual(self.worker(bridge,Gate()).run_once(self.project,self.job['job_id'])['status'],
                         'authorization_or_budget_unverified')
        self.assertFalse(bridge.submissions)
        class MutatingGate(Gate):
            def verify(inner, job, snapshot):
                snapshot['project_root']=str(self.base)
                return super(MutatingGate,inner).verify(job,snapshot)
        bridge=Bridge(self.repo)
        self.assertEqual(self.worker(bridge,MutatingGate()).run_once(self.project,self.job['job_id'])['status'],
                         'authorization_or_budget_unverified')
        self.assertFalse(bridge.submissions)

    def test_questions_require_clarification_before_dispatch(self):
        draft=self.store.prepare(uid(),PROPOSAL | {'questions':['Quel périmètre ?']},authority=self.authority,capability=CAP)
        bridge=Bridge(self.repo)
        self.assertEqual(self.worker(bridge,Gate()).run_once(self.project,draft['job_id'])['status'],'clarification_required')
        self.assertFalse(bridge.submissions)

    def test_no_store_lock_during_inference(self):
        # A second independent handle can read and fsync while submit is blocked in worker.
        def verify():
            other=jobs.Jobs(self.state,crypt=crypt)
            self.assertEqual(other.get(self.project,self.job['job_id'])['status'],'dispatch_ready')
        bridge=Bridge(self.repo,on_submit=verify)
        self.assertEqual(self.worker(bridge,Gate()).run_once(self.project,self.job['job_id'])['status'],'indeterminate')

    def test_concurrent_workers_only_one_sends(self):
        barrier=threading.Barrier(2); bridges=[Bridge(self.repo,barrier=barrier),Bridge(self.repo,barrier=barrier)]
        results=[]
        def run(bridge):
            store=jobs.Jobs(self.state,crypt=crypt)
            results.append(Worker(store,bridge,gate=Gate()).run_once(self.project,self.job['job_id']))
        threads=[threading.Thread(target=run,args=(bridge,)) for bridge in bridges]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10); self.assertFalse(thread.is_alive())
        self.assertEqual(sum(len(bridge.submissions) for bridge in bridges),1)
        self.assertIn('indeterminate',[result['status'] for result in results])

    def test_competing_payload_receipt_after_preflight_is_never_attributed(self):
        class CompetingBridge(Bridge):
            def submit(inner, payload):
                inner.submissions.append(payload)
                inner.saved[payload['request_key']] = {'request_key':payload['request_key'],
                    'state':'recorded','available':True,'result_sha256':'a'*64}
                raise ValueError('request_key already bound to another immutable request')
        bridge=CompetingBridge(self.repo); worker=self.worker(bridge,Gate())
        self.assertEqual(worker.run_once(self.project,self.job['job_id'])['status'],'indeterminate')
        self.assertEqual(worker.status(self.project,self.job['job_id'])['status'],'indeterminate')
        restarted=Worker(jobs.Jobs(self.state,crypt=crypt),bridge,gate=Gate())
        self.assertEqual(restarted.status(self.project,self.job['job_id'])['status'],'indeterminate')
        self.assertEqual(restarted.run_once(self.project,self.job['job_id'])['status'],'indeterminate')
        self.assertEqual(len(bridge.submissions),1)

    def test_restart_from_intent_never_attributes_direct_client_receipt(self):
        self.store.reserve(self.project,self.job['job_id'],expected_revision=0,attempt_id=uid())
        bridge=Bridge(self.repo)
        bridge.saved[self.job['request_key']]={'request_key':self.job['request_key'],
            'state':'recorded','available':True,'result_sha256':'a'*64}
        restarted=Worker(jobs.Jobs(self.state,crypt=crypt),bridge,gate=Gate())
        self.assertEqual(restarted.status(self.project,self.job['job_id'])['status'],'indeterminate')
        self.assertEqual(restarted.run_once(self.project,self.job['job_id'])['status'],'dispatch_intent')
        self.assertFalse(bridge.submissions)

    def test_crash_after_dispatch_intent_remains_uncertain(self):
        process=multiprocessing.Process(target=crash_worker,args=(str(self.state),str(self.repo),self.project,self.job['job_id']))
        process.start(); process.join(15)
        self.assertEqual(process.exitcode,73)
        row=self.store.get(self.project,self.job['job_id'])
        self.assertEqual(row['status'],'dispatch_ready')
        bridge=Bridge(self.repo); worker=self.worker(bridge,Gate())
        self.assertEqual(worker.status(self.project,self.job['job_id'])['status'],'indeterminate')
        self.assertFalse(worker.run_once(self.project,self.job['job_id'])['dispatched'])
        self.assertFalse(bridge.submissions)

    def test_transition_cas_and_uncertainty_survive_reopen(self):
        attempt=uid(); other=uid(); job_id=self.job['job_id']
        self.store.reserve(self.project,job_id,expected_revision=0,attempt_id=attempt)
        with self.assertRaises(ValueError):
            self.store.reserve(self.project,job_id,expected_revision=0,attempt_id=other)
        with self.assertRaises(ValueError):
            self.store.confirm_authorization(self.project,job_id,attempt_id=other,evidence_id='test',proof_sha256='a'*64)
        with self.assertRaises(ValueError):
            self.store.record_outcome(self.project,job_id,attempt_id=attempt,status='recorded',receipt={})
        self.store.confirm_authorization(self.project,job_id,attempt_id=attempt,evidence_id='test',proof_sha256='a'*64)
        self.store.record_outcome(self.project,job_id,attempt_id=attempt,status='indeterminate')
        reopened=jobs.Jobs(self.state,crypt=crypt)
        self.assertEqual(reopened.get(self.project,job_id)['status'],'indeterminate')
        with self.assertRaises(ValueError):
            reopened.record_outcome(self.project,job_id,attempt_id=attempt,status='not_admitted')
        with self.assertRaises(ValueError):
            reopened.record_outcome(self.project,job_id,attempt_id=attempt,status='recorded',receipt={
                'request_key':uid(),'state':'recorded','available':True,'result_sha256':'a'*64})
        self.assertEqual(reopened.get(self.project,job_id)['status'],'indeterminate')

    def test_failed_ready_directory_fsync_never_submits(self):
        bridge=Bridge(self.repo); real_fsync=jobs.os.fsync; failed=[]
        def fail_ready(fd):
            if (stat.S_ISDIR(os.fstat(fd).st_mode)
                    and os.fstat(fd).st_ino == (self.state/'coordinator-jobs.noindex').stat().st_ino
                    and not failed and GateCounter.calls == 2):
                failed.append(True)
                raise OSError('simulated directory sync failure')
            return real_fsync(fd)
        GateCounter=Gate()
        with patch.object(jobs.os,'fsync',side_effect=fail_ready):
            result=self.worker(bridge,GateCounter).run_once(self.project,self.job['job_id'])
        self.assertTrue(failed)
        self.assertEqual(result['status'],'not_admitted')
        self.assertFalse(bridge.submissions)
        self.assertEqual(jobs.Jobs(self.state,crypt=crypt).get(self.project,self.job['job_id'])['status'],'not_admitted')

    def test_real_bridge_contract_discovery_and_thread_owner_without_inference(self):
        with patch('codex_expert_mcp.executable', return_value='/usr/bin/true'):
            bridge=LocalBridge(self.repo,self.state)
            try:
                discovered=bridge.discover()
                self.assertEqual(Path(discovered['project_root']),self.repo.resolve())
                codex=next(item for item in discovered['backends'] if item['id']=='codex')
                self.assertIn('expertise',codex['modes'])
                errors=[]
                def wrong_thread():
                    try: bridge.discover()
                    except Exception as exc: errors.append(type(exc))
                thread=threading.Thread(target=wrong_thread); thread.start(); thread.join(5)
                self.assertEqual(errors,[RuntimeError])
                self.assertEqual(bridge.receipt(uid())['status'],'not_found')
                payload={'request_key':uid(),'question':'synthetic'}
                with patch.object(bridge.bridge, 'call', return_value={'status':'busy'}) as call:
                    self.assertEqual(bridge.submit(payload), {'status':'busy'})
                    call.assert_called_once_with('reprise_delegate', payload)
            finally: bridge.close()


if __name__ == '__main__': unittest.main()
