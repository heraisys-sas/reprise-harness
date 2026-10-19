from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from desktop_job_probe import Probe


class DesktopJobProbeTests(unittest.TestCase):
    def test_claim_return_and_no_second_job_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            probe = Probe(folder)
            try:
                job = probe.call('get_task', {})
                self.assertEqual(probe.call('submit_result', {'job_id':job['job_id'], 'attempt_id':job['attempt_id'], 'answer':'2,5,7'})['status'], 'accepted')
            finally:
                probe.db.close()
            probe = Probe(folder)
            try:
                self.assertEqual(probe.call('get_task', {})['remaining_jobs'], 0)
                with self.assertRaises(ValueError):
                    probe.call('submit_result', {'job_id':job['job_id'], 'attempt_id':job['attempt_id'], 'answer':'duplicate'})
                self.assertEqual(probe.db.execute('SELECT answer,claims FROM job').fetchone(), ('2,5,7',1))
            finally:
                probe.db.close()

    def test_unclaimed_wrong_id_and_oversized_results_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            probe = Probe(folder)
            try:
                ident = probe.db.execute('SELECT id FROM job').fetchone()[0]
                with self.assertRaises(ValueError):
                    probe.call('submit_result', {'job_id':ident,'attempt_id':'unclaimed','answer':'early'})
                job = probe.call('get_task', {})
                for values in ({'job_id':'other','attempt_id':job['attempt_id'],'answer':'x'}, {'job_id':ident,'attempt_id':job['attempt_id'],'answer':'x'*121},
                               {'job_id':ident,'attempt_id':job['attempt_id'],'answer':' '}, {'job_id':ident,'attempt_id':job['attempt_id'],'answer':'x','path':'/'}):
                    with self.assertRaises(ValueError):
                        probe.call('submit_result',values)
                self.assertEqual(probe.db.execute('SELECT status FROM job').fetchone()[0],'claimed')
            finally:
                probe.db.close()

    def test_restart_never_reassigns_ambiguous_attempt_but_accepts_its_late_result(self):
        with tempfile.TemporaryDirectory() as folder:
            first = Probe(folder)
            job = first.call('get_task', {})
            self.assertNotIn('task', first.call('get_task', {}))
            second = Probe(folder)
            try:
                blocked = second.call('get_task', {})
                self.assertEqual(blocked['status'], 'indeterminate')
                self.assertNotIn('task', blocked)
                self.assertEqual(second.db.execute('SELECT claims FROM job').fetchone()[0],1)
                submission = {'job_id':job['job_id'],'attempt_id':job['attempt_id'],'answer':'2,5,7'}
                receipt = first.call('submit_result', submission)
                # Lost ACK: resubmit the identical result, not the reasoning task.
                self.assertEqual(second.call('submit_result', submission), receipt)
                with self.assertRaises(ValueError):
                    second.call('submit_result', submission | {'answer':'different'})
                with self.assertRaises(ValueError):
                    second.call('submit_result', submission | {'attempt_id':'other'})
            finally:
                first.db.close()
                second.db.close()
