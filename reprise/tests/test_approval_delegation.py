"""#117 T2b operator grant registry: no inference, native dispatch or real approval."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from approval_delegation import ApprovalDelegations, DURATIONS, main
from desktop_task_store import TaskStore

THREAD = '12345678-1234-1234-1234-123456789abc'


class ApprovalDelegationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        self.project.mkdir()
        self.other = self.root/'other'
        self.other.mkdir()
        self.workspace = self.root/'workspace'
        self.workspace.mkdir()
        self.state = self.root/'state'
        self.state.mkdir(mode=0o700)
        self.private = self.root/'private'
        self.private.mkdir(mode=0o700)
        self.shared = self.root/'shared'
        self.shared.mkdir(mode=0o755)
        self.manager = ApprovalDelegations(self.project, self.state)
        self.addCleanup(self.manager.close)

    def grant(self, suffix='one', **changes):
        args = dict(harness='asi1', workspace=str(self.workspace), thread_id=THREAD,
                    duration='24h', max_decisions=3, token_file=str(self.private/suffix),
                    now=100.0)
        args.update(changes)
        return self.manager.grant(**args)

    def test_only_hash_persisted_and_private_file(self):
        result = self.grant()
        self.assertEqual(result['status'], 'created_inactive')
        file = self.private/'one'
        self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        token = file.read_text().strip()
        self.assertGreaterEqual(len(token), 40)
        row = self.manager.store.db.execute("""SELECT token_sha256,thread_id,workspace,
            max_decisions,used_decisions FROM approval_delegations WHERE scope=?""",
            (self.manager.store.scope,)).fetchone()
        self.assertEqual(row[0], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotEqual(row[0], token)
        self.assertEqual(row[1:], (THREAD, str(self.workspace), 3, 0))
        self.assertNotIn(token, json.dumps(self.manager.list_grants(now=101)))
        self.assertNotIn('token_sha256', json.dumps(self.manager.list_grants(now=101)))

    def test_persisted_scoped_and_expiring_without_enabling_approvals(self):
        item = self.grant()
        same = ApprovalDelegations(self.project, self.state)
        other = ApprovalDelegations(self.other, self.state)
        try:
            self.assertEqual(same.list_grants(now=101)[0]['status'], 'inactive_pending_native_path')
            self.assertEqual(same.list_grants(now=100+DURATIONS['24h'])[0]['status'], 'expired')
            self.assertEqual(other.list_grants(now=101), [])
            with self.assertRaisesRegex(ValueError, 'introuvable'):
                other.revoke(item['grant_id'])
            self.assertIsNone(self.manager.store.pending_approval('no-native-approval'))
        finally:
            same.close()
            other.close()

    def test_revoke_is_idempotent_and_scope_bound(self):
        result = self.grant()
        self.assertEqual(self.manager.revoke(result['grant_id'], now=150)['status'], 'revoked')
        self.assertEqual(self.manager.revoke(result['grant_id'], now=151)['status'], 'revoked')
        item = self.manager.list_grants(now=151)[0]
        self.assertEqual(item['revoked_at'], 150)
        self.assertEqual(item['status'], 'revoked')

    def test_limits_malformed_identifiers_and_files_are_rejected(self):
        bad = (
            {'harness': 'other harness'},
            {'thread_id': 'not-a-uuid'},
            {'duration': 'forever'},
            {'max_decisions': 0},
            {'max_decisions': 101},
            {'max_decisions': True},
            {'token_file': str(self.project/'token')},
            {'token_file': str(self.shared/'token')},
            {'thread_id': None},
            {'harness': None},
        )
        for i, change in enumerate(bad):
            with self.subTest(change=change), self.assertRaises((ValueError, OSError)):
                self.grant(suffix=f'invalid-{i}', **change)
        self.assertEqual(self.manager.list_grants(), [])

    def test_existing_token_file_is_never_overwritten(self):
        token_file = self.private/'one'
        token_file.write_text('already exists')
        with self.assertRaises(FileExistsError):
            self.grant()
        self.assertEqual(token_file.read_text(), 'already exists')
        self.assertEqual(self.manager.list_grants(), [])

    def test_database_failure_does_not_leave_credential_file(self):
        self.manager.store.db.execute('''CREATE TRIGGER reject_delegation
            BEFORE INSERT ON approval_delegations BEGIN SELECT RAISE(ABORT, 'injected'); END''')
        with self.assertRaises(sqlite3.IntegrityError):
            self.grant()
        self.assertFalse((self.private/'one').exists())

    def test_no_mcp_entry_can_grant_or_respond(self):
        import codex_expert_mcp
        tools = {x['name'] for x in codex_expert_mcp.TOOLS}
        self.assertNotIn('approval-delegation-grant', tools)
        self.assertNotIn('approval_grant', tools)
        self.assertNotIn('approval_delegation', tools)
        self.assertIn('approval_respond', tools)
        self.assertIn("raise Unavailable('approval_disabled'", 
                      __import__('inspect').getsource(codex_expert_mcp.Bridge._approval_respond))

    def test_cli_list_does_not_print_secret(self):
        result = self.grant()
        from io import StringIO
        with patch('sys.stdout', new_callable=StringIO) as output:
            code = main(argv=['list', '--project', str(self.project),
                              '--state', str(self.state)])
        self.assertEqual(code, 0)
        listing = json.loads(output.getvalue())
        self.assertEqual(listing['grants'][0]['grant_id'], result['grant_id'])
        self.assertNotIn((self.private/'one').read_text().strip(), output.getvalue())


if __name__ == '__main__':
    unittest.main()
