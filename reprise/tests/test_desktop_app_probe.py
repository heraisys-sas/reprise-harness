from pathlib import Path
import json
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).parents[1]))
from desktop_app_probe import AppProbe, EXTENSION, MIME, URI

HELLO={'method':'initialize','params':{'protocolVersion':'2025-11-25','clientInfo':{'name':'fixture','version':'1'},
       'capabilities':{'extensions':{EXTENSION:{'mimeTypes':[MIME]}}}}}

class AppProbeTests(unittest.TestCase):
    def test_capabilities_only_records_exact_extension_and_exposes_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            probe=AppProbe(folder,capabilities_only=True)
            try:
                self.assertEqual(probe.dispatch(HELLO)['capabilities'],{})
                evidence=json.loads((Path(folder)/'ui-capabilities.json').read_text())
                self.assertEqual(evidence['ui_capability'],{'mimeTypes':[MIME]})
                self.assertTrue(evidence['ui_supported'])
                self.assertEqual(probe.dispatch({'method':'tools/list'})['tools'],[])
                with self.assertRaises(ValueError):probe.call('get_task',{})
                self.assertEqual(probe.db.execute('SELECT claims FROM job').fetchone()[0],0)
            finally:probe.db.close()

    def test_no_ui_support_refuses_resources_and_job_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            probe=AppProbe(folder)
            try:
                probe.dispatch({'method':'initialize','params':{'capabilities':{'extensions':{}}}})
                self.assertEqual(probe.dispatch({'method':'resources/list'})['resources'],[])
                with self.assertRaises(ValueError):probe.call('get_task',{})
                with self.assertRaises(ValueError):probe.dispatch({'method':'resources/read','params':{'uri':URI}})
            finally:probe.db.close()

    def test_one_dispatch_survives_widget_reload_and_panel_does_not_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            probe=AppProbe(folder)
            try:
                probe.dispatch(HELLO)
                probe.call('open_test_panel',{})
                self.assertEqual(probe.db.execute('SELECT claims FROM job').fetchone()[0],0)
                self.assertEqual(probe.call('reserve_dispatch',{})['status'],'reserved')
                with self.assertRaises(ValueError):probe.call('reserve_dispatch',{})
                resource=probe.dispatch({'method':'resources/read','params':{'uri':URI}})['contents'][0]
                self.assertEqual(resource['mimeType'],MIME)
                self.assertEqual(resource['_meta']['ui']['csp']['connectDomains'],[])
                with self.assertRaises(ValueError):probe.dispatch({'method':'resources/read','params':{'uri':'file:///etc/passwd'}})
            finally:probe.db.close()
            probe=AppProbe(folder)
            try:
                probe.dispatch(HELLO)
                with self.assertRaises(ValueError):probe.call('reserve_dispatch',{})
                self.assertEqual(probe.db.execute('SELECT claims FROM job').fetchone()[0],0)
            finally:probe.db.close()
