"""Candidate import uses real guarded storage/process fixtures, never a real model."""
import copy
import json
import multiprocessing
import os
import stat
import sqlite3
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
import coordinator_candidate as candidates
import coordinator_jobs
from coordinator_local import LocalExecution, Control
from delegation_store import digest
import test_coordinator_local as fixtures
from test_coordinator_jobs import fake_crypt


def import_owned(state, project, operation, work, extracted, mutation, gate, output):
    gate.wait(5)
    try:
        row = coordinator_jobs.Jobs(state, crypt=fake_crypt).import_candidate(project, operation, extracted,
            import_id=mutation, work_id=work, expected_revision=None,
            expected_proposal_sha256=None, operator_confirmed=True)
        output.put(('ok', row['revision']))
    except ValueError as exc:
        output.put(('refused', str(exc)))


def write_probe(state, output):
    db = sqlite3.connect(str(Path(state)/'coordinator-jobs.noindex/local-admissions.sqlite'), timeout=.1)
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE guarded_grants SET used=used+1')
        db.execute('UPDATE guarded_grants SET used=used-1')
        db.commit()
        output.put('committed')
    except sqlite3.OperationalError:
        db.rollback()
        output.put('busy')
    finally:
        db.close()


class CandidateTests(unittest.TestCase):
    setUp = fixtures.LocalExecutionTests.setUp
    script = fixtures.LocalExecutionTests.script
    reserve = fixtures.LocalExecutionTests.reserve
    host = fixtures.LocalExecutionTests.host

    def source(self, questions=None):
        proposal = {'objective': 'Préparer un compte rendu', 'acceptance': ['Texte relu'],
                    'constraints': ['Données non vérifiées'],
                    'questions': ['Quel public ?'] if questions is None else questions}
        self.document = {'schema': candidates.SCHEMA, 'proposal': proposal}
        answer = json.dumps(self.document, ensure_ascii=False)
        self.script('import json\nprint(json.dumps('+repr({'available': True, 'answer': answer})+'))\n')
        self.run_id, _, _ = self.reserve()
        self.local.run(self.project, self.operation, self.run_id, Control())
        self.source_row = self.jobs.recover(self.project, self.operation)
        self.sha = self.source_row['local_execution']['receipt']['response_sha256']
        return self.extract()

    def extract(self, **changes):
        args = dict(project=self.project, operation=self.operation, work=self.work,
                    run=self.run_id, response_sha256=self.sha)
        args.update(changes)
        return candidates.extract(self.local, **args)

    def import_into(self, extracted, operation=None, **changes):
        self.target = operation or fixtures.uid()
        self.import_id = getattr(self, 'import_id', fixtures.uid())
        fields = dict(import_id=self.import_id, work_id=self.work,
                      expected_revision=None, expected_proposal_sha256=None, operator_confirmed=True)
        fields.update(changes)
        return self.jobs.import_candidate(self.project, self.target, extracted, **fields)

    def consent(self, row):
        context = self.jobs.consent_context(self.project, self.work)
        return self.jobs.consent(self.project, self.target, authorization_id=fixtures.uid(),
            expected_revision=row['revision'], expected_proposal_sha256=row['proposal_sha256'],
            host_context=context['context'], expected_context_sha256=context['context_sha256'], backend='foundation-models')

    def test_strict_parser_rejects_authority_extra_fields_and_non_json(self):
        self.assertEqual(candidates.parse(json.dumps({'schema': candidates.SCHEMA,
            'proposal': fixtures.PROPOSAL}))['proposal'], fixtures.PROPOSAL)
        invalid = ['```json\n{}\n```', '{"schema":"x","schema":"y"}', '[]',
                   '{"schema": NaN}', '{"schema":"future","proposal":{}}', 'x'*15000]
        for field in ('backend', 'permission_granted', 'work_id', 'dispatch_enabled'):
            value = {'schema': candidates.SCHEMA, 'proposal': dict(fixtures.PROPOSAL, **{field: True})}
            invalid.append(json.dumps(value))
        for value in invalid:
            with self.subTest(value=value[:70]), self.assertRaises(ValueError):
                candidates.parse(value)

    def test_extraction_is_attributable_without_replay_or_jobs_mutation(self):
        extracted = self.source()
        path = self.state/'coordinator-jobs.noindex/jobs.enc'
        before = path.read_bytes()
        db_path = self.state/'coordinator-jobs.noindex/local-admissions.sqlite'
        before_db = db_path.read_bytes()
        with patch('coordinator_local._spawn', side_effect=AssertionError('no generation')):
            self.assertEqual(self.extract(), extracted)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(db_path.read_bytes(), before_db)
        for changes in ({'work': fixtures.uid()}, {'run': fixtures.uid()}, {'response_sha256': '0'*64},
                        {'operation': fixtures.uid()}, {'project': fixtures.uid()}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.extract(**changes)
        forged = copy.deepcopy(self.source_row)
        forged['local_execution']['receipt']['admission_attempt_id'] = fixtures.uid()
        with patch.object(self.jobs, 'recover', return_value=forged), self.assertRaises(ValueError):
            self.extract()
        forged = copy.deepcopy(self.source_row)
        forged['local_execution']['receipt']['response'] = json.dumps({'schema': candidates.SCHEMA, 'proposal': fixtures.PROPOSAL})
        with patch.object(self.jobs, 'recover', return_value=forged), self.assertRaises(ValueError):
            self.extract()

    def test_import_explicit_different_operation_idempotent_and_no_authority(self):
        extracted = self.source()
        with self.assertRaises(ValueError):
            self.import_into(extracted, operator_confirmed=False)
        with self.assertRaises(ValueError):
            self.import_into(extracted, self.operation)
        with patch('coordinator_local._spawn', side_effect=AssertionError('no generation')):
            row = self.import_into(extracted)
            replay = self.import_into(extracted, self.target)
        self.assertEqual(row, replay)
        self.assertEqual(row['status'], 'preparation')
        self.assertIsNone(row['authority']); self.assertIsNone(row['request_key'])
        self.assertNotIn('operator_authorization', row['candidate_import'])
        self.assertEqual(row['candidate_import']['origin'], extracted['origin'])
        self.assertEqual(row['proposal'], self.document['proposal'])
        with self.assertRaises(ValueError):
            self.import_into(extracted, self.target, import_id=fixtures.uid())
        with self.assertRaises(ValueError):
            self.import_into(extracted, work_id=fixtures.uid())

    def test_existing_preparation_cas_and_source_revalidation(self):
        extracted = self.source()
        target = fixtures.uid()
        original = self.jobs.prepare_proposal(self.project, target, fixtures.PROPOSAL)
        revised = self.jobs.revise(self.project, target, dict(fixtures.PROPOSAL, objective='édition opérateur'), expected_revision=0)
        with self.assertRaisesRegex(ValueError, 'Revision conflict'):
            self.import_into(extracted, target, expected_revision=0, expected_proposal_sha256=original['proposal_sha256'])
        forged = copy.deepcopy(extracted); forged['origin']['work_id'] = fixtures.uid()
        with self.assertRaises(ValueError):
            self.import_into(forged, target, work_id=forged['origin']['work_id'],
                expected_revision=1, expected_proposal_sha256=revised['proposal_sha256'])
        row = self.import_into(extracted, target, expected_revision=1, expected_proposal_sha256=revised['proposal_sha256'])
        self.assertEqual(row['revision'], 2)
        self.assertEqual(self.jobs.recover(self.project, self.operation), self.source_row)

    def test_answer_resolution_history_cas_and_execution_snapshot(self):
        row = self.import_into(self.source())
        question = row['clarifications'][0]
        with self.assertRaisesRegex(ValueError, 'Clarification required'):
            self.consent(row)
        with self.assertRaisesRegex(ValueError, 'cannot be removed'):
            self.jobs.revise(self.project, self.target, dict(row['proposal'], questions=[]), expected_revision=0)
        resolution = fixtures.uid()
        fields = dict(question_id=question['id'], answer='Des lecteurs débutants', resolved=False,
                      resolution_id=resolution, expected_revision=0)
        answered = self.jobs.clarify_candidate(self.project, self.target, **fields)
        self.assertEqual(answered['proposal']['questions'], ['Quel public ?'])
        self.assertEqual(answered, self.jobs.clarify_candidate(self.project, self.target, **fields))
        with self.assertRaisesRegex(ValueError, 'identity conflict'):
            self.jobs.clarify_candidate(self.project, self.target, **dict(fields, answer='Autre réponse'))
        with self.assertRaisesRegex(ValueError, 'Revision conflict'):
            self.jobs.clarify_candidate(self.project, self.target, **dict(fields, resolution_id=fixtures.uid(), resolved=True))
        resolved = self.jobs.clarify_candidate(self.project, self.target,
            **dict(fields, resolution_id=fixtures.uid(), expected_revision=1, resolved=True))
        self.assertEqual(resolved['proposal']['questions'], [])
        self.assertEqual(resolved['clarifications'][0]['question'], 'Quel public ?')
        self.assertEqual(len(resolved['clarifications'][0]['history']), 2)
        wrong_context = self.jobs.consent_context(self.project, fixtures.uid())
        with self.assertRaisesRegex(ValueError, 'Host context changed'):
            self.jobs.consent(self.project, self.target, authorization_id=fixtures.uid(),
                expected_revision=resolved['revision'], expected_proposal_sha256=resolved['proposal_sha256'],
                host_context=wrong_context['context'], expected_context_sha256=wrong_context['context_sha256'], backend='foundation-models')
        consent = self.consent(resolved)
        plan = self.local.plan(self.project, self.target, 3)
        self.assertEqual(plan['manifest']['clarifications_sha256'], digest(resolved['clarifications']))
        run = fixtures.uid()
        row, _ = self.jobs.reserve_local(self.project, self.target, run_id=run,
            expected_revision=consent['revision'], manifest=plan['manifest'])
        facts = json.loads(self.local._parts(row)[3]['input']['facts'])
        self.assertEqual(facts['clarifications'][0]['answer'], 'Des lecteurs débutants')
        with self.assertRaisesRegex(ValueError, 'Mission locked'):
            self.jobs.clarify_candidate(self.project, self.target,
                **dict(fields, resolution_id=fixtures.uid(), expected_revision=row['revision']))

    def test_reopen_question_preserves_original_answers_and_requires_resolution(self):
        row = self.import_into(self.source())
        question = row['clarifications'][0]
        fields = dict(question_id=question['id'], answer='Pour les équipes', resolved=True,
                      resolution_id=fixtures.uid(), expected_revision=0)
        resolved = self.jobs.clarify_candidate(self.project, self.target, **fields)
        reopened = self.jobs.clarify_candidate(self.project, self.target,
            **dict(fields, answer='Public à confirmer', resolved=False, resolution_id=fixtures.uid(), expected_revision=1))
        self.assertEqual(reopened['proposal']['questions'], [question['question']])
        self.assertEqual(reopened['clarifications'][0]['history'][0]['answer'], 'Pour les équipes')
        self.assertEqual(reopened['clarifications'][0]['answer'], 'Public à confirmer')
        with self.assertRaisesRegex(ValueError, 'Clarification required'):
            self.consent(reopened)
        with self.assertRaises(ValueError):
            self.jobs.clarify_candidate(self.project, self.target,
                **dict(fields, answer='', resolution_id=fixtures.uid(), expected_revision=2))

    def test_missing_admission_registry_is_not_recreated_by_extraction(self):
        self.source()
        path = self.state/'coordinator-jobs.noindex/local-admissions.sqlite'
        path.unlink()
        with patch('coordinator_local._spawn', side_effect=AssertionError('no replay')):
            with self.assertRaises(FileNotFoundError):
                self.extract()
        self.assertFalse(path.exists())

    def test_import_after_replace_uncertainty_is_recovered_not_repeated(self):
        extracted = self.source(); self.target = fixtures.uid(); self.import_id = fixtures.uid()
        replaced = False; replace = os.replace; fsync = os.fsync
        def after_replace(*args, **kw):
            nonlocal replaced
            replace(*args, **kw); replaced = True
        def sync(fd):
            if replaced and stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError('simulated directory fsync')
            return fsync(fd)
        with patch.object(coordinator_jobs.os, 'replace', side_effect=after_replace), patch.object(coordinator_jobs.os, 'fsync', side_effect=sync):
            with self.assertRaises(coordinator_jobs.WriteUncertainError):
                self.import_into(extracted, self.target)
        recovered = coordinator_jobs.Jobs(self.state, crypt=fake_crypt).recover(self.project, self.target)
        replay = self.import_into(extracted, self.target)
        self.assertEqual(recovered, replay)
        self.assertEqual(recovered['revision'], 0)

    def test_two_importers_do_not_overwrite_each_other(self):
        extracted = self.source(); target = fixtures.uid()
        context = multiprocessing.get_context('fork'); gate = context.Event(); output = context.Queue()
        children = [context.Process(target=import_owned, args=(self.state, self.project, target, self.work,
                    extracted, fixtures.uid(), gate, output)) for _ in range(2)]
        for child in children: child.start()
        gate.set()
        for child in children:
            child.join(10); self.assertEqual(child.exitcode, 0)
        outcomes = [output.get(timeout=2)[0] for _ in children]
        self.assertEqual(sorted(outcomes), ['ok', 'refused'])

    def test_source_guard_is_short_and_has_no_crypto_or_jobs_wait_under_sql_lock(self):
        extracted = self.source(); context = multiprocessing.get_context('fork')
        observed = []; real_crypt = self.jobs.crypt; real_replace = os.replace
        def writer():
            output = context.Queue(); child = context.Process(target=write_probe, args=(self.state, output))
            child.start(); child.join(2)
            self.assertFalse(child.is_alive(), 'SQLite writer must have a bounded outcome')
            self.assertEqual(child.exitcode, 0)
            return output.get(timeout=1)
        def crypt(data, purpose, operation):
            if operation == 'seal':
                observed.append(('seal', writer()))
            return real_crypt(data, purpose, operation)
        def replace(*args, **kw):
            observed.append(('replace', writer()))
            return real_replace(*args, **kw)
        with patch.object(self.jobs, 'crypt', side_effect=crypt), patch.object(coordinator_jobs.os, 'replace', side_effect=replace):
            self.import_into(extracted)
        self.assertEqual(observed, [('seal', 'committed'), ('replace', 'busy')])
        self.assertEqual(writer(), 'committed')

    def test_busy_commit_guard_refuses_without_mutation_or_retry(self):
        extracted = self.source(); target = fixtures.uid()
        path = self.state/'coordinator-jobs.noindex/local-admissions.sqlite'
        blocker = sqlite3.connect(str(path), timeout=.1)
        blocker.execute('BEGIN EXCLUSIVE')
        try:
            started = time.monotonic()
            with self.assertRaisesRegex(ValueError, 'busy or unavailable'):
                self.import_into(extracted, target)
            self.assertLess(time.monotonic()-started, 2)
        finally:
            blocker.rollback(); blocker.close()
        self.assertIsNone(self.jobs.recover(self.project, target))
        self.assertFalse(list((self.state/'coordinator-jobs.noindex').glob('.jobs-*')))

    def test_source_changed_after_extraction_refuses_before_replace(self):
        extracted = self.source(); target = fixtures.uid()
        db = sqlite3.connect(str(self.state/'coordinator-jobs.noindex/local-admissions.sqlite'))
        with db:
            db.execute("UPDATE delegation_requests SET result='{}'")
        db.close()
        with self.assertRaisesRegex(ValueError, 'receipt changed'):
            self.import_into(extracted, target)
        self.assertIsNone(self.jobs.recover(self.project, target))
        self.assertFalse(list((self.state/'coordinator-jobs.noindex').glob('.jobs-*')))

    def test_jsonl_extract_import_clarify_and_closed_protocol(self):
        extracted = self.source(); host, call = self.host()
        with patch('coordinator_local._spawn', side_effect=AssertionError('no generation')):
            found = call('job_candidate_extract', operation_id=self.operation, work_id=self.work,
                         run_id=self.run_id, expected_response_sha256=self.sha)
            self.assertEqual(found['candidate'], extracted)
            target = fixtures.uid()
            fields = dict(operation_id=target, work_id=self.work, source_operation_id=self.operation,
                          source_run_id=self.run_id, expected_response_sha256=self.sha,
                          expected_candidate_sha256=extracted['candidate_sha256'], import_id=fixtures.uid(),
                          expected_revision=None, expected_proposal_sha256=None, operator_confirmed=True)
            response = call('job_candidate_import', **fields)
            self.assertEqual(response['status'], 'finished', response)
            row = response['job']
            answer = call('job_clarify', operation_id=target, question_id=row['clarifications'][0]['id'],
                          answer='Lecteurs novices', resolved=True, resolution_id=fixtures.uid(), expected_revision=0)
            self.assertEqual(answer['job']['proposal']['questions'], [])
            self.assertEqual(call('job_candidate_import', **dict(fields, expected_candidate_sha256='0'*64))['status'], 'invalid_proposal')
            self.assertEqual(call('job_candidate_import', **dict(fields, backend='codex'))['status'], 'invalid_request')
