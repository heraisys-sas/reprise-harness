"""Execute the actual page expressions against a synthetic DOM, when Node exists."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_desktop_apply as transport


def scripts():
    class Capture:
        def evaluate(self, expression):
            self.expression = expression
            return True
    capture = Capture()
    path = '/chat/00000000-0000-0000-0000-000000000001'
    text = 'Only this bounded prompt'
    transport._prepare(capture, path)
    prepare = capture.expression
    transport._send(capture, path, hashlib.sha256(text.encode()).hexdigest())
    return dict(prepare=prepare, send=capture.expression, state=transport.JS_STATE, text=text)


class PageExpressions(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable; DOM fixture can run through the local JS tool')
    def test_actual_expressions_on_synthetic_dom(self):
        fixture = Path(__file__).with_name('fixtures') / 'desktop_apply_dom.mjs'
        result = subprocess.run([shutil.which('node'), str(fixture)], input=json.dumps(scripts()),
                                capture_output=True, text=True, timeout=20, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertFalse(report['browser_used'])
        self.assertGreaterEqual(report['fixtures'], 64)


if __name__ == '__main__':
    unittest.main()
