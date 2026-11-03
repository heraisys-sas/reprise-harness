import base64
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import coordinator_jobs as jobs
from project_registry import ProjectRegistry


def uid(): return str(uuid.uuid4())


def fake_crypt(data, purpose, operation):
    if purpose != jobs.PURPOSE:
        raise ValueError('purpose mismatch')
    if operation == 'seal':
        encoded = base64.b64encode(data)
        return b'TEST:' + hashlib.sha256(encoded).digest() + encoded
    if operation == 'open':
        if not data.startswith(b'TEST:') or hashlib.sha256(data[37:]).digest() != data[5:37]:
            raise ValueError('bad ciphertext')
        return base64.b64decode(data[37:], validate=True)
    raise ValueError('unexpected operation')


PROPOSAL = {'objective': 'secret objectif fictif', 'acceptance': ['test passe'],
            'constraints': ['pas de push'], 'questions': []}
CAPABILITY = {'id':'codex', 'configured':True, 'modes':['expertise', 'implementation'],
              'workspace_write_authorized':True}


def authority(project, work):
    return {'project_id':project, 'work_id':work, 'routing_role':'hypervisor',
            'routing_actor':'deepseek-harness', 'backend':'codex', 'mode':'expertise',
            'billing':'abonnement vérifié par hôte', 'billing_authorized':True, 'permission_granted':True,
            'workspace_write_authorized':False, 'max_requests':3, 'max_seconds':300,
            'max_cost_minor':0, 'currency':'EUR'}


def prepare_worker(state, operation, auth, queue):
    try:
        result = jobs.Jobs(state, crypt=fake_crypt).prepare(operation, PROPOSAL,
                                                           authority=auth, capability=CAPABILITY)
        queue.put(('ok', result['job_id'], result['request_key']))
    except Exception as exc:
        queue.put(('error', type(exc).__name__))


def crash_worker(state, operation, auth, when):
    store = jobs.Jobs(state, crypt=fake_crypt)
    original = jobs.os.replace
    def crash(source, dest, **kwargs):
        if when == 'before': os._exit(70)
        original(source, dest, **kwargs)
        os._exit(71)
    jobs.os.replace = crash
    store.prepare(operation, PROPOSAL, authority=auth, capability=CAPABILITY)


class JobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.state = base / 'state'; self.state.mkdir(mode=0o700)
        repo1, repo2 = base / 'repo1', base / 'repo2'
        for repo in (repo1, repo2):
            repo.mkdir(); subprocess.run(['git', '-C', str(repo), 'init', '-q'], check=True)
        with ProjectRegistry(self.state) as registry:
            self.p1 = registry.create('Atlas', repo1, repo1)['id']
            self.p2 = registry.create('Boreal', repo2, repo2)['id']
        self.work = uid()
        self.auth = authority(self.p1, self.work)
        self.store = jobs.Jobs(self.state, crypt=fake_crypt)

    def prepare(self, operation=None, **kw):
        return self.store.prepare(operation or uid(), kw.get('proposal', PROPOSAL),
                                  authority=kw.get('authority', self.auth),
                                  capability=kw.get('capability', CAPABILITY))

    def test_durable_idempotent_draft_and_scope(self):
        operation = uid()
        first = self.prepare(operation)
        self.assertEqual(first['status'], 'draft')
        self.assertFalse(first['dispatch_enabled'])
        self.assertEqual(first['authority']['work_id'], self.work)
        reopened = jobs.Jobs(self.state, crypt=fake_crypt)
        self.assertEqual(reopened.prepare(operation, PROPOSAL, authority=self.auth, capability=CAPABILITY), first)
        self.assertEqual(reopened.recover(self.p1, operation), first)
        self.assertEqual(reopened.get(self.p1, first['job_id']), first)
        self.assertIsNone(reopened.get(self.p2, first['job_id']))
        self.assertEqual(reopened.list(self.p2), [])
        self.assertEqual(len(reopened.list(self.p1)), 1)
        with self.assertRaises(ValueError):
            self.prepare(operation, proposal=PROPOSAL | {'objective':'autre'})
        with self.assertRaises(ValueError):
            self.prepare(operation, authority=self.auth | {'max_requests':4})

    def test_recovery_survives_revoked_capability_without_dispatch(self):
        operation = uid(); first = self.prepare(operation)
        with self.assertRaises(ValueError):
            self.prepare(operation, capability=CAPABILITY | {'configured':False})
        self.assertEqual(self.store.recover(self.p1, operation), first)
        self.assertIsNone(self.store.recover(self.p1, uid()))

    def test_recorded_transition_rejected_even_with_matching_key_and_result_hash(self):
        job=self.prepare(); attempt=uid()
        self.store.reserve(self.p1,job['job_id'],expected_revision=0,attempt_id=attempt)
        self.store.confirm_authorization(self.p1,job['job_id'],attempt_id=attempt,
                                         evidence_id='test',proof_sha256='a'*64)
        receipt={'request_key':job['request_key'],'state':'recorded',
                 'available':True,'result_sha256':'b'*64}
        with self.assertRaises(ValueError):
            self.store.record_outcome(self.p1,job['job_id'],attempt_id=attempt,status='recorded',receipt=receipt)
        self.assertEqual(self.store.get(self.p1,job['job_id'])['status'],'dispatch_ready')

    def test_legacy_recorded_is_hidden_without_mutating_history(self):
        operation=uid(); job=self.prepare(operation)
        with self.store._store(write=True) as data:
            row=data['jobs'][0]
            row['status']='recorded'
            row['result_receipt']={'request_key':job['request_key'],'state':'recorded','result_sha256':'b'*64}
        path=self.state/'coordinator-jobs.noindex'/'jobs.enc'
        historical=path.read_bytes()
        reopened=jobs.Jobs(self.state,crypt=fake_crypt)
        views=[reopened.get(self.p1,job['job_id']),reopened.recover(self.p1,operation),
               reopened.list(self.p1)[0],self.prepare(operation)]
        for view in views:
            self.assertEqual(view['status'],'indeterminate')
            self.assertIsNone(view['result_receipt'])
        self.assertEqual(path.read_bytes(),historical)
        with reopened._store() as data:
            self.assertEqual(data['jobs'][0]['status'],'recorded')
            self.assertEqual(data['jobs'][0]['result_receipt']['result_sha256'],'b'*64)

    def test_untrusted_proposal_cannot_supply_permissions_or_route(self):
        for field in ('backend', 'mode', 'project_id', 'permission_granted', 'max_cost_minor', 'routing_actor'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.prepare(proposal=PROPOSAL | {field:'forged'})
        self.assertEqual(self.store.list(self.p1), [])

    def test_routing_provenance_is_configurable_and_only_declared(self):
        first = self.prepare()
        second = self.prepare(authority=self.auth | {'routing_role':'coordinator', 'routing_actor':'apple-local'})
        self.assertEqual(first['authority']['routing_actor'], 'deepseek-harness')
        self.assertEqual(second['authority']['routing_actor'], 'apple-local')
        self.assertFalse(first['dispatch_enabled'])
        self.assertFalse(second['dispatch_enabled'])

    def test_authority_and_capability_must_match(self):
        invalid = [
            (self.auth | {'permission_granted':False}, CAPABILITY),
            (self.auth | {'billing_authorized':False}, CAPABILITY),
            (self.auth | {'backend':'claude-code'}, CAPABILITY),
            (self.auth | {'mode':'implementation'}, CAPABILITY),
            (self.auth | {'mode':'implementation','workspace_write_authorized':True}, CAPABILITY | {'workspace_write_authorized':False}),
            (self.auth, CAPABILITY | {'configured':False}),
            (self.auth, CAPABILITY | {'modes':[]}),
            (self.auth | {'max_requests':True}, CAPABILITY),
            (self.auth | {'max_seconds':0}, CAPABILITY),
            (self.auth | {'max_cost_minor':-1}, CAPABILITY),
            (self.auth | {'currency':'???'}, CAPABILITY),
            (self.auth | {'project_id':uid()}, CAPABILITY),
        ]
        for auth, cap in invalid:
            with self.subTest(auth=auth, cap=cap), self.assertRaises(ValueError):
                self.prepare(authority=auth, capability=cap)
        self.assertEqual(self.store.list(self.p1), [])
        result = self.prepare(authority=self.auth | {'mode':'implementation','workspace_write_authorized':True})
        self.assertEqual(result['authority']['mode'], 'implementation')
        self.assertFalse(result['dispatch_enabled'])

    def test_no_model_bridge_or_worker_dispatch(self):
        with patch.object(self.store, '_project'), patch('subprocess.Popen', side_effect=AssertionError('dispatch')):
            result = self.prepare()
        self.assertEqual(result['status'], 'draft')

    def test_no_plaintext_on_disk_and_coded_purpose(self):
        self.prepare()
        ciphertext = (self.state / 'coordinator-jobs.noindex' / 'jobs.enc').read_bytes()
        self.assertNotIn(b'secret objectif fictif', ciphertext)
        self.assertTrue(ciphertext.startswith(b'TEST:'))
        self.assertEqual((self.state / 'coordinator-jobs.noindex' / 'jobs.enc').stat().st_mode & 0o777, 0o600)
        self.assertEqual(jobs.PURPOSE, 'coordinator-jobs-v1')

    def test_concurrent_same_operation_creates_only_one_draft(self):
        operation, queue = uid(), multiprocessing.Queue()
        workers = [multiprocessing.Process(target=prepare_worker, args=(str(self.state),operation,self.auth,queue)) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(15); self.assertEqual(worker.exitcode, 0)
        results = [queue.get(timeout=3) for _ in workers]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], 'ok')
        self.assertEqual(len(self.store.list(self.p1)), 1)

    def test_concurrent_distinct_operations_do_not_lose_drafts(self):
        queue = multiprocessing.Queue()
        workers = [multiprocessing.Process(target=prepare_worker, args=(str(self.state),uid(),self.auth,queue)) for _ in range(2)]
        for worker in workers: worker.start()
        for worker in workers: worker.join(15); self.assertEqual(worker.exitcode, 0)
        results = [queue.get(timeout=3) for _ in workers]
        self.assertTrue(all(result[0] == 'ok' for result in results))
        self.assertNotEqual(results[0][1], results[1][1])
        self.assertEqual(len(self.store.list(self.p1)), 2)

    def test_failed_directory_fsync_requires_resync_before_replay_receipt(self):
        operation = uid()
        real_fsync = jobs.os.fsync
        failed = [False]
        def fail_first_folder_sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode) and os.fstat(fd).st_ino == (self.state / 'coordinator-jobs.noindex').stat().st_ino and not failed[0]:
                failed[0] = True
                raise OSError('simulated directory sync failure')
            return real_fsync(fd)
        with patch.object(jobs.os, 'fsync', side_effect=fail_first_folder_sync):
            with self.assertRaises(OSError): self.prepare(operation)
        self.assertTrue(failed[0])
        # Recover first performs the missing directory sync, then confirms the receipt.
        recovered = self.store.recover(self.p1, operation)
        self.assertEqual(recovered['status'], 'draft')
        self.assertEqual(self.prepare(operation), recovered)
        self.assertEqual(len(self.store.list(self.p1)), 1)

    def test_crash_before_and_after_replace_recover_same_operation(self):
        for when, code in (('before',70), ('after',71)):
            with self.subTest(when=when):
                operation = uid()
                process = multiprocessing.Process(target=crash_worker, args=(str(self.state),operation,self.auth,when))
                process.start(); process.join(15)
                self.assertEqual(process.exitcode, code)
                before = self.store.recover(self.p1, operation)
                if when == 'before': self.assertIsNone(before)
                else: self.assertEqual(before['status'], 'draft')
                after = self.prepare(operation)
                self.assertEqual(self.store.recover(self.p1, operation), after)
                if before is not None: self.assertEqual(after, before)

    def test_corruption_or_vault_failure_never_overwrites(self):
        first = self.prepare()
        path = self.state / 'coordinator-jobs.noindex' / 'jobs.enc'
        saved = path.read_bytes()
        path.write_bytes(b'corrupt')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(path.read_bytes(), b'corrupt')
        path.write_bytes(saved)
        def unavailable(*args): raise ValueError('vault unavailable')
        failed = jobs.Jobs(self.state, crypt=unavailable)
        with self.assertRaises(ValueError):
            failed.prepare(uid(), PROPOSAL, authority=self.auth, capability=CAPABILITY)
        self.assertEqual(path.read_bytes(), saved)
        self.assertEqual(self.store.get(self.p1, first['job_id']), first)

    def test_reject_symlink_and_insecure_state(self):
        directory = self.state / 'coordinator-jobs.noindex'
        (directory / 'jobs.enc').symlink_to('/etc/hosts')
        with self.assertRaises(OSError): self.prepare()
        (directory / 'jobs.enc').unlink()
        directory.chmod(0o755)
        with self.assertRaises(ValueError): self.prepare()
        directory.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
