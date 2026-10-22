import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('release_build_review', Path(__file__).parents[1] / 'release_build.py')
b = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(b)


class ReleaseBuildTests(unittest.TestCase):
    def fixture(self, root):
        (root / 'Native.swift').write_text('source')
        (root / 'state').mkdir()
        (root / 'state/tasks.sqlite').write_text('private state')
        app = root / 'builds/current/Reprise.app'
        (app / 'Contents/Resources').mkdir(parents=True)
        (app / 'Contents/runtime').write_text('payload')
        evidence = {'format': 1, 'inputs': b.inputs(root), 'bundle': b.bundle_files(app)}
        (app / 'Contents/Resources/build-evidence.json').write_text(json.dumps(evidence))
        return app

    def test_sources_change_refuses_stale_build_but_state_does_not(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td);app = self.fixture(root)
            b.check(app, root)
            (root / 'state/tasks.sqlite').write_text('new state')
            b.check(app, root)
            (root / 'Native.swift').write_text('changed source')
            with self.assertRaisesRegex(ValueError, 'périmé'):
                b.check(app, root)

    def test_tampered_bundle_and_escaping_symlink_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td);app = self.fixture(root)
            (app / 'Contents/runtime').write_text('altered')
            with self.assertRaisesRegex(ValueError, 'modifié'):
                b.check(app, root)
            (app / 'escape').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'Lien sortant'):
                b.check(app, root)

    def test_open_uses_no_development_args_and_no_state_override(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td);app = self.fixture(root)
            with patch.dict(os.environ, {'AGENT_RESUME_STATE': '/wrong/clone/state'}), patch.object(b.subprocess, 'run') as run:
                b.launch(app, root)
            argv = run.call_args.args[0]
            self.assertEqual(argv, ['/usr/bin/open', '-n', '-a', str(app.resolve())])
            self.assertNotIn('AGENT_RESUME_STATE', run.call_args.kwargs['env'])

    def test_invalid_build_never_launches(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);app=self.fixture(root)
            (root/'Native.swift').write_text('changed')
            with patch.object(b.subprocess,'run') as run, self.assertRaises(ValueError):
                b.launch(app,root)
            run.assert_not_called()


class RelaunchScriptTests(unittest.TestCase):
    def test_default_relaunch_does_not_touch_daemons_or_development_mode(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            folder = Path(td)
            python = folder / 'python3'
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" > "$CALLS"\n')
            python.chmod(0o700)
            log = folder / 'calls'
            env = dict(os.environ, PATH=str(folder) + ':/usr/bin:/bin', CALLS=str(log))
            result = subprocess.run(['/bin/sh', str(Path(__file__).parents[1] / 'restart-chain'), '--no-build'], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('release_build.py open', log.read_text())
            self.assertNotIn('daemon', log.read_text())
            self.assertNotIn('--args', log.read_text())
