from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from presentation import summarize
from detect import codex_source
import scheduler

class PresentationTests(unittest.TestCase):
    def test_repeated_causes_group_without_hiding_history(self):
        tasks = [dict(id=str(i), status=state, due=100, ended_at=200) for i,state in enumerate(
            ['blocked_trust']*3+['blocked_owner']*2+['blocked_parent']*2+['finished','cancelled','paused'])]
        result = summarize(tasks, now=300)
        self.assertEqual(result['review_count'], 7)
        self.assertEqual(result['cause_count'], 3)
        self.assertEqual(result['history_count'], 10)
        self.assertEqual(sum(len(g['task_ids']) for g in result['groups']), 7)
        self.assertFalse(result['pending'])

    def test_current_work_is_separate_from_old_failures(self):
        result = summarize([{'id':'old','status':'failed','due':1},
                            {'id':'next','status':'ready','due':500},
                            {'id':'live','status':'running','due':600}], now=100000)
        self.assertEqual([t['id'] for t in result['pending']], ['live','next'])
        self.assertEqual(result['groups'][0]['recent'], 0)
        self.assertEqual(result['review_count'], 1)

class SourceTests(unittest.TestCase):
    def test_subagents_and_guardians_are_never_independently_automatic(self):
        sources = [{'subagent': {'thread_spawn': {'parent_thread_id': 'parent'}}},
                   {'subagent': 'guardian'}, {'subagent': None}, '{"subagent": "guardian"}']
        for source in sources:
            metadata = codex_source(source)
            self.assertTrue(metadata['is_subagent'])
            self.assertFalse(metadata['automatic_resume_supported'])
        self.assertEqual(codex_source(sources[0])['parent_session'], 'parent')

    def test_unknown_source_is_not_automatic(self):
        for source in (None, {}, '{invalid', 'unknown', '"unknown"'):
            self.assertFalse(codex_source(source)['automatic_resume_supported'])
        for source in ('cli','vscode','exec','"cli"'):
            self.assertTrue(codex_source(source)['automatic_resume_supported'])

    def test_scheduler_rejects_child_before_admission(self):
        import tempfile
        with tempfile.TemporaryDirectory() as cwd:
            row = {'id':'12345678-1234-1234-1234-123456789abc','state':'quota_confirmed',
                   'cwd':cwd,'updated':100,'interrupted_at':100,'retry_at':None, 'automatic_resume_supported':True}
            self.assertTrue(scheduler.confirmed(row, 200))
            self.assertFalse(scheduler.confirmed(row | {'is_subagent':True}, 200))
            self.assertFalse(scheduler.confirmed(row | {'automatic_resume_supported':False}, 200))
            del row['automatic_resume_supported']
            self.assertFalse(scheduler.confirmed(row, 200))
