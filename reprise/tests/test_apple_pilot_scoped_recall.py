"""Synthetic v2 service fixtures; never call a model or a real user store."""
import copy
import sys
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parents[1]))
import apple_pilot_service
import orchestrator_memory
import coordinator_recall
import test_coordinator_recall as fixtures
import test_coordinator_local as local_fixtures
import test_orchestrator_memory as memory_fixtures


class ScopedRecallServiceTests(unittest.TestCase):
    setUp = fixtures.RecallTests.setUp
    script = fixtures.RecallTests.script
    prepare = fixtures.RecallTests.prepare

    def call(self, op, **fields):
        replies = []; done = threading.Event()
        def emit(result): replies.append(result); done.set()
        def memory_factory(_):
            if getattr(self, "memory_unavailable", False): raise OSError("synthetic memory unavailable")
            return self.memory
        host = apple_pilot_service.Service(self.state, emit,
            jobs_factory=lambda _: self.jobs, memory_factory=memory_factory,
            backend_factory=lambda: self.fail('no inference'))
        request = dict(id=local_fixtures.uid(), op=op, enabled=True,
                       project_id=self.project, work_id=self.work, operation_id=self.operation, **fields)
        with patch.object(orchestrator_memory.Memory, 'migrate_scopes_v2', side_effect=AssertionError('no migration')), \
             patch.object(orchestrator_memory.Memory, 'register_scope', side_effect=AssertionError('no registration')):
            host.handle(request)
            self.assertTrue(done.wait(5), 'service response missing')
            host.shutdown()
        return replies[0]

    def search(self, **fields):
        return self.call('job_memory_search', **dict(query='Rapport Alpha',project_offset=0,work_offset=0,**fields))

    def test_legacy_and_absent_scope_are_explicit_project_results_not_failures(self):
        self.prepare()
        for expected in ('legacy_store', 'not_registered'):
            if expected == 'not_registered': self.memory.migrate_scopes_v2()
            result = self.search()
            self.assertEqual(result['status'], 'finished', result)
            self.assertEqual(result['recall_context']['work_scope_status'], expected)
            self.assertEqual(len(result['sections']['project']['items']), 1)
            self.assertEqual(result['sections']['work'], {'items': [], 'next_offset': None})
        self.assertIsNone(self.jobs.recover(self.project,self.operation).get('memory_selection'))

    def test_qualified_preview_attach_and_read_reject_foreign_and_changed_sources(self):
        self.prepare(); self.memory.migrate_scopes_v2()
        scope = self.memory.register_scope('work', project_id=self.project, work_id=self.work)
        scoped = orchestrator_memory.ScopedMemory(self.state,scope,crypt=memory_fixtures.fake_crypt)
        operation=local_fixtures.uid()
        with patch('orchestrator_memory.uuid.uuid4', return_value=uuid.UUID(self.fact['id'])):
            work_fact = scoped.create_fact(scope,operation,'Rapport Alpha travail courant',memory_fixtures.SOURCE)
        other = self.memory.register_scope('work',project_id=self.project,work_id=local_fixtures.uid())
        foreign = orchestrator_memory.ScopedMemory(self.state,other,crypt=memory_fixtures.fake_crypt)
        foreign_fact = foreign.create_fact(other,local_fixtures.uid(),'Rapport Alpha interdit',memory_fixtures.SOURCE)
        result=self.search(); self.assertEqual(result['status'],'finished',result)
        self.assertEqual([f['id'] for f in result['sections']['work']['items']],[work_fact['id']])
        refs=coordinator_recall.qualified_references([{'scope_id':self.project,'fact_id':self.fact['id']},{'scope_id':scope,'fact_id':work_fact['id']}])
        preview=self.call('job_memory_preview_v2',fact_refs=refs)
        self.assertEqual(preview['memory_preview']['schema'],coordinator_recall.SCHEMA_V2)
        forbidden=self.call('job_memory_fact',fact_ref={'scope_id':other,'fact_id':foreign_fact['id']})
        self.assertNotEqual(forbidden['status'],'finished')
        snapshot=preview['memory_preview']; mutation=local_fixtures.uid()
        attached=self.call('job_memory_attach_v2',fact_refs=refs,snapshot_sha256=preview['snapshot_sha256'],selection_id=mutation,expected_revision=0,operator_confirmed=True)
        self.assertEqual(attached['status'],'finished',attached)
        self.assertEqual(attached['job']['memory_selection']['snapshot'],snapshot)
        self.assertEqual(attached['job']['status'],'preparation')
        context=self.jobs.consent_context(self.project,self.work)
        self.jobs.consent(self.project,self.operation,authorization_id=local_fixtures.uid(),
            expected_revision=attached['job']['revision'],expected_proposal_sha256=attached['job']['proposal_sha256'],
            host_context=context['context'],expected_context_sha256=context['context_sha256'],backend='foundation-models')
        self.memory_unavailable=True
        with patch.object(self.memory,'recall_scopes',side_effect=AssertionError('historical retry must not read memory')):
            retry=self.call('job_memory_attach_v2',fact_refs=refs,snapshot_sha256=preview['snapshot_sha256'],selection_id=mutation,expected_revision=0,operator_confirmed=True)
        self.assertEqual(retry['status'],'finished',retry)
        self.assertEqual(retry['job']['status'],'consented')
        self.assertEqual(retry['job']['memory_selection']['snapshot'],snapshot)


    def test_corrupt_scope_refuses_all_results_and_bad_payloads_do_not_attach(self):
        self.prepare()
        with patch.object(self.memory,'recall_scopes',side_effect=ValueError('Recall scope association conflict')):
            result=self.search()
        self.assertEqual(result['status'],'memory_scope_conflict',result)
        self.assertNotIn('sections',result)
        result=self.call('job_memory_search',query='x',project_offset=True,work_offset=0)
        self.assertNotEqual(result['status'],'finished')
        self.assertIsNone(self.jobs.recover(self.project,self.operation).get('memory_selection'))

    def test_pagination_is_independent_and_live_read_failure_is_not_empty_success(self):
        self.prepare()
        for i in range(21):
            self.memory.create_fact(self.project,local_fixtures.uid(),'Rapport Alpha '+str(i),memory_fixtures.SOURCE)
        first=self.search()
        self.assertEqual(len(first['sections']['project']['items']),20)
        self.assertEqual(first['sections']['project']['next_offset'],20)
        second=self.call('job_memory_search',query='Rapport Alpha',project_offset=20,work_offset=0)
        self.assertEqual(len(second['sections']['project']['items']),2)
        self.assertIsNone(second['sections']['project']['next_offset'])
        self.assertFalse({f['id'] for f in first['sections']['project']['items']} & {f['id'] for f in second['sections']['project']['items']})
        with patch.object(self.memory,'search_facts',side_effect=OSError('synthetic unavailable')):
            failed=self.search()
        self.assertEqual(failed['status'],'memory_unavailable')
        self.assertNotIn('sections',failed)
