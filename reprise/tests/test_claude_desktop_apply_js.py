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


def working_node():
    """Un node qui existe sur le PATH mais ne s'exécute pas (vendu par une app hôte) ne sert à rien."""
    candidate = shutil.which('node')
    if candidate:
        try:
            probe = subprocess.run([candidate, '-e', 'process.exit(0)'], capture_output=True, timeout=15)
            if probe.returncode == 0:
                return candidate
        except (OSError, subprocess.SubprocessError):
            pass
    return None


class PageExpressions(unittest.TestCase):
    @unittest.skipUnless(working_node(), "Node exécutable introuvable sur le PATH (les nodes vendus par les apps hôtes sont sondés et écartés)")
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
