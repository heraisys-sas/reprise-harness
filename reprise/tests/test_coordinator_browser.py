import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import test_coordinator_local as fixtures
import test_coordinator_candidate as candidate_fixtures


class BrowserTests(unittest.TestCase):
    setUp = fixtures.LocalExecutionTests.setUp
    script = fixtures.LocalExecutionTests.script
    reserve = fixtures.LocalExecutionTests.reserve
    host = fixtures.LocalExecutionTests.host
    source = candidate_fixtures.CandidateTests.source
    extract = candidate_fixtures.CandidateTests.extract
    import_into = candidate_fixtures.CandidateTests.import_into

    def test_minimal_project_pages_work_filter_and_no_reconciliation(self):
        imported = self.import_into(self.source())
        unbound = self.jobs.prepare_proposal(self.project, fixtures.uid(), fixtures.PROPOSAL)
        path = self.state/'coordinator-jobs.noindex/jobs.enc'
        before = path.read_bytes()
        with patch('coordinator_local._spawn', side_effect=AssertionError('no helper')):
            page = self.jobs.browse(self.project, limit=1)
            self.assertEqual(len(page['operations']), 1)
            self.assertEqual(page['next_offset'], 1)
            full = self.jobs.browse(self.project)
            self.assertEqual(len(full['operations']), 3)
            self.assertEqual(len(self.jobs.browse(self.project, work_id=self.work)['operations']), 2)
            self.assertEqual(self.jobs.browse(self.project, work_id=fixtures.uid())['operations'], [])
            rows = {row['operation_id']: row for row in full['operations']}
            self.assertEqual(rows[unbound['operation_id']]['work_association'], 'unbound')
            self.assertEqual(rows[imported['operation_id']]['work_id'], self.work)
            for row in full['operations']:
                self.assertEqual(set(row), {'project_id','operation_id','job_id','objective','status','revision',
                    'created_at','updated_at','kind','work_association','work_id','selectable'})
                self.assertLessEqual(len(row['objective']), 160)
        self.assertEqual(path.read_bytes(), before)

    def test_inconsistent_and_historical_rows_not_selectable(self):
        imported = self.import_into(self.source())
        legacy = self.jobs.prepare_proposal(self.project, fixtures.uid(), fixtures.PROPOSAL)
        with self.jobs._store(write=True) as data:
            for row in data['jobs']:
                if row['job_id'] == imported['job_id']:
                    row['operator_authorization'] = {'host_context': {'project_id':self.project,'work_id':fixtures.uid()}}
                if row['job_id'] == legacy['job_id']:
                    row.pop('operation_id')
                    row['status'] = 'recorded'
        page = self.jobs.browse(self.project)
        bad = next(row for row in page['operations'] if row['job_id']==imported['job_id'])
        self.assertEqual(bad['work_association'], 'inconsistent')
        self.assertIsNone(bad['work_id']); self.assertFalse(bad['selectable'])
        old = next(row for row in page['operations'] if row['job_id']==legacy['job_id'])
        self.assertIsNone(old['operation_id']); self.assertFalse(old['selectable'])
        self.assertEqual(old['status'], 'indeterminate')
        self.assertNotIn(bad, self.jobs.browse(self.project, work_id=self.work)['operations'])

    def test_closed_service_protocol_scope_and_no_helper(self):
        self.source(); host, call = self.host()
        with patch('coordinator_local._spawn', side_effect=AssertionError('no helper')):
            page = call('job_list', work_id=None, limit=20, offset=0)
            self.assertEqual(page['status'], 'finished', page)
            self.assertEqual(page['page']['project_id'], self.project)
            self.assertNotIn('job', page)
            for fields in ({'work_id':'invalid'}, {'limit':True}, {'offset':-1}, {'limit':51}, {'backend':'codex'}):
                request = dict(work_id=None, limit=20, offset=0); request.update(fields)
                self.assertNotEqual(call('job_list', **request)['status'], 'finished')
            with self.assertRaises(ValueError): self.jobs.browse(fixtures.uid())
