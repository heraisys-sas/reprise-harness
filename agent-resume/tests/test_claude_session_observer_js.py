"""The collector script runs against a synthetic DOM, never a browser."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
from claude_session_observer import notice_script


class QuotaNoticeExpressions(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable; fixture can run through the local JS tool')
    def test_quota_notice_dom(self):
        path = Path(__file__).with_name('fixtures') / 'claude_quota_notice_dom.mjs'
        script = notice_script('00000000-0000-0000-0000-000000000001')
        result = subprocess.run([shutil.which('node'), str(path)], input=json.dumps(script),
                                capture_output=True, text=True, timeout=20, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertEqual(report['fixtures'], 15)
        self.assertFalse(report['browser_used'])
