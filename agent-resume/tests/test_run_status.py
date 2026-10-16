import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import run_status

class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE tasks(id TEXT,session TEXT,cwd TEXT,prompt TEXT,due REAL,status TEXT,attempts INTEGER)')
        run_status.setup(self.db)
        self.task = dict(id='test', session='session', cwd=str(self.root), prompt='continue')
        self.db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', ('test','session',str(self.root),'private prompt',0,'needs_review',1))
        self.db.commit()

    def tearDown(self):
        self.db.close(); self.temp.cleanup()

    def execute(self, body, timeout=2):
        fake = self.root / 'cli'
        fake.write_text('#!/usr/bin/env python3\n' + body)
        fake.chmod(0o700)
        return run_status.execute(self.db, self.task, [str(fake)], dict(os.environ), self.root/'test.log', timeout)

    def test_zero_exit_does_not_prove_completion(self):
        self.assertEqual(self.execute('pass\n'), 'needs_review')
        self.assertIn('sans événement', run_status.rows(self.db,self.root)[0]['reason'])

    def test_completed_turn_is_not_business_validation(self):
        body = "import json\nfor kind in ['turn.started','turn.completed']: print(json.dumps({'type':kind}),flush=True)\n"
        self.assertEqual(self.execute(body), 'finished')
        self.assertIn('non vérifié', run_status.rows(self.db,self.root)[0]['label'])

    def test_trust_rejection_has_exact_safe_reason(self):
        self.execute("import sys\nprint('Not inside a trusted directory and --skip-git-repo-check was not specified.')\nsys.exit(1)\n")
        row = run_status.rows(self.db,self.root)[0]
        self.assertEqual(row['status'], 'blocked_trust')
        self.assertNotIn('prompt', row)
        self.assertEqual(row['exit_code'], 1)

    def test_old_failure_is_classified_without_retry(self):
        (self.root/'test.log').write_text('Not inside a trusted directory')
        self.assertEqual(run_status.rows(self.db,self.root)[0]['status'],'blocked_trust')
        self.assertEqual(self.db.execute('SELECT attempts FROM tasks').fetchone()[0],1)

    def test_timeout_and_failed_turn(self):
        self.execute('import time\ntime.sleep(5)\n', .1)
        self.assertEqual(run_status.rows(self.db,self.root)[0]['status'],'timed_out')
        self.execute("import json\nprint(json.dumps({'type':'turn.failed','error':{'message':'usage_limit_exceeded'}}))\n")
        self.assertEqual(run_status.rows(self.db,self.root)[0]['status'],'quota_blocked')

    def test_pause_overrides_old_run_detail(self):
        run_status.save(self.db,'test','blocked_trust','Old reason')
        self.db.execute("UPDATE tasks SET status='paused'")
        self.assertEqual(run_status.rows(self.db,self.root)[0]['status'],'paused')
