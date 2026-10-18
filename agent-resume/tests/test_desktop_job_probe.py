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
                self.assertEqual(probe.call('submit_result', {'job_id':job['job_id'], 'answer':'2,5,7'})['status'], 'accepted')
            finally:
                probe.db.close()
            probe = Probe(folder)
            try:
                self.assertEqual(probe.call('get_task', {})['remaining_jobs'], 0)
                with self.assertRaises(ValueError):
                    probe.call('submit_result', {'job_id':job['job_id'], 'answer':'duplicate'})
                self.assertEqual(probe.db.execute('SELECT answer,claims FROM job').fetchone(), ('2,5,7',1))
            finally:
                probe.db.close()

    def test_unclaimed_wrong_id_and_oversized_results_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            probe = Probe(folder)
            try:
                ident = probe.db.execute('SELECT id FROM job').fetchone()[0]
                with self.assertRaises(ValueError):
                    probe.call('submit_result', {'job_id':ident,'answer':'early'})
                probe.call('get_task', {})
                for values in ({'job_id':'other','answer':'x'}, {'job_id':ident,'answer':'x'*121},
                               {'job_id':ident,'answer':' '}, {'job_id':ident,'answer':'x','path':'/'}):
                    with self.assertRaises(ValueError):
                        probe.call('submit_result',values)
                self.assertEqual(probe.db.execute('SELECT status FROM job').fetchone()[0],'claimed')
            finally:
                probe.db.close()
