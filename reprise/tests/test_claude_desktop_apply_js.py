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
    # Empreinte canonique (#108) : la forme CRLF/NBSP doit canoniser vers ce texte.
    text = 'First paragraph.\n\nSecond one follows\nafter CRLF. Ends with NBSP.'
    transport._prepare(capture, path)
    prepare = capture.expression
    transport._send(capture, path, hashlib.sha256(transport.canonical_text(text).encode()).hexdigest())
    send = capture.expression
    # Second empreinte pour le cas ProseMirror du fixture (un LF rendu comme
    # une ligne vide) : canon() ignorant les lignes vides, elle égale celle du
    # texte source sans le double LF.
    prose_mirror_text = 'Para one.\nPara two.'
    transport._send(capture, path,
                    hashlib.sha256(transport.canonical_text(prose_mirror_text).encode()).hexdigest())
    return dict(prepare=prepare, send=send, prose_mirror_send=capture.expression,
                state=transport._js_state(transport.JS_HELPERS), text=text)


NODE_FALLBACKS = (
    Path.home() / '.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node',
)


def working_node():
    """Un node qui existe sur le PATH mais ne s'exécute pas (vendu par une app hôte) ne sert à rien."""
    candidates = [shutil.which('node'), *[str(p) for p in NODE_FALLBACKS if p.exists()]]
    for candidate in candidates:
        if not candidate:
            continue
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
        result = subprocess.run([working_node(), str(fixture)], input=json.dumps(scripts()),
                                capture_output=True, text=True, timeout=20, check=True)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'passed')
        self.assertFalse(report['browser_used'])
        self.assertGreaterEqual(report['fixtures'], 64)


if __name__ == '__main__':
    unittest.main()
