import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

spec = importlib.util.spec_from_file_location('quota', Path(__file__).parents[1] / 'quota.py')
quota = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quota)

class QuotaTests(unittest.TestCase):
    def test_no_inferred_recovery(self):
        self.assertIsNone(quota.next_retry(None, 100))
        self.assertIsNone(quota.next_retry({'ordinaryUsageAllowed': None, 'rateLimits': {'primary': {'usedPercent': 0}}}, 100))
        self.assertEqual(quota.next_retry({'ordinaryUsageAllowed': True}, 100), 220)
        self.assertEqual(quota.next_retry({'ordinaryUsageAllowed': False, 'rateLimits': {'primary': {'usedPercent': 100, 'resetsAt': 500}}}, 100), 620)

    def test_stdio_handshake_never_starts_a_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / 'codex'
            seen = Path(folder) / 'calls'
            fake.write_text('''#!/usr/bin/env python3
import sys,json
from pathlib import Path
for line in sys.stdin:
 r=json.loads(line)
 with Path(''' + repr(str(seen)) + ''').open('a') as f: f.write(r['method']+'\\n')
 if r.get('id')==1: print(json.dumps({'id':1,'result':{}}),flush=True)
 if r.get('id')==2: print(json.dumps({'id':2,'result':{'ordinaryUsageAllowed':True}}),flush=True)
''')
            fake.chmod(0o700)
            self.assertTrue(quota.read_limits(str(fake))['ordinaryUsageAllowed'])
            self.assertEqual(seen.read_text().splitlines(), ['initialize', 'initialized', 'account/rateLimits/read'])

    def test_unresponsive_helper_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = Path(folder) / 'codex'
            fake.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(30)\n')
            fake.chmod(0o700)
            start = time.monotonic()
            self.assertIsNone(quota.read_limits(str(fake), timeout=.2))
            self.assertLess(time.monotonic()-start, 3)
