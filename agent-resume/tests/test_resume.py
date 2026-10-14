import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest

loader = importlib.machinery.SourceFileLoader('resume', str(Path(__file__).parents[1] / 'resume'))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

class SchedulerTests(unittest.TestCase):
    def test_due_once_and_failure_does_not_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            module.STATE = Path(folder)
            db = module.database()
            fake = Path(folder) / 'codex'
            fake.write_text('#!/bin/sh\ncat >/dev/null\nexit 1\n')
            fake.chmod(0o700)
            ident = '12345678-1234-1234-1234-123456789abc'
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,0)', (ident, ident, folder, 'continue', 0, 'ready'))
            db.commit()
            module.run_due(db, str(fake))
            module.run_due(db, str(fake))
            row = db.execute('SELECT * FROM tasks').fetchone()
            self.assertEqual(row['status'], 'needs_review')
            self.assertEqual(row['attempts'], 1)
            db.close()

    def test_backup_restore_pauses_and_share_excludes_state(self):
        with tempfile.TemporaryDirectory() as folder:
            import os
            import json
            import zipfile
            env = dict(os.environ, AGENT_RESUME_STATE=folder + '/first')
            script = str(Path(__file__).parents[1] / 'resume')
            prompt = Path(folder) / 'prompt'
            prompt.write_text('private prompt')
            def run(*args):
                return subprocess.check_output([script, *args], env=env, cwd=folder, text=True)
            out = run('add', '--session', '12345678-1234-1234-1234-123456789abc', '--cwd', folder, '--at', '2030-01-01T00:00:00Z', '--prompt-file', str(prompt))
            run('enable', out.split()[0])
            archive = folder + '/backup.zip'
            run('backup', archive)
            env['AGENT_RESUME_STATE'] = folder + '/second'
            run('restore', archive)
            self.assertEqual(json.loads(run('status'))['status'], 'paused')
            share = run('share').strip()
            with zipfile.ZipFile(share) as z:
                self.assertFalse(any('state/' in n for n in z.namelist()))
                self.assertNotIn('private prompt', z.read('agent-resume/prompt.example.txt').decode())

if __name__ == '__main__':
    unittest.main()
