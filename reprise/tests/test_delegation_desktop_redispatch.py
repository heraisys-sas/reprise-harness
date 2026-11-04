"""Non-redispatch Desktop : reçus, clés équivalentes et réconciliation (issue #102).

Tout passe par l'API publique du pont (`reprise_delegate`, `reprise_reconcile`,
`reprise_requests`) et par `TaskStore` ; le dispatch Desktop est simulé par un
patch de `codex_expert_mcp.claude_desktop.review`. Un reçu existant vaut
collecte, jamais un second envoi.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import Bridge
from desktop_task_store import TaskStore

CHAT = '178dcceb-fcd3-4f2d-ac4f-116a8a153bc3'
ADMISSION = {'status': 'running', 'phase': 'active', 'thread_id': CHAT,
             'resumable': False, 'response': None, 'admission_only': True,
             'dispatch': 'dispatchOnQuickEntrySubmit'}


class DesktopRedispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.state = self.root / 'expert'
        self.socket = str(self.root / 'quick.sock')
        Path(self.socket).touch()
        self.bridge = self.make_bridge()
        self.addCleanup(self.bridge.db.close)

    def make_bridge(self):
        return Bridge(str(self.project), {}, self.state, desktop_socket=self.socket)

    def delegate(self, bridge, key, **extra):
        args = {'question': 'Analyse le point du contrat', 'kind': 'expertise',
                'backend': 'claude-desktop', 'request_key': key, **extra}
        return bridge.call('reprise_delegate', args)

    def stored(self, bridge, key):
        return bridge.db.execute(
            'SELECT state,result FROM delegation_requests WHERE scope=? AND request_key=?',
            (bridge.requests.scope, key)).fetchone()

    def test_same_request_key_returns_the_receipt_and_dispatches_once(self):
        with patch('codex_expert_mcp.claude_desktop.review',
                   return_value=dict(ADMISSION)) as review:
            first = self.delegate(self.bridge, 'desk-1')
            second = self.delegate(self.bridge, 'desk-1')
        self.assertEqual(review.call_count, 1)
        self.assertIsNone(first['response'])
        self.assertTrue(first['admission_only'])
        self.assertIsNone(second['response'])
        self.assertTrue(second['admission_only'])
        self.assertIn(second['status'], ('running', 'awaiting_approval', 'finished'))
        self.assertNotEqual(second['status'], 'indeterminate')
        self.assertEqual(self.bridge.db.execute(
            'SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 1)
        self.assertIsInstance(self.bridge.desktop_tasks, TaskStore)

    def test_restart_with_an_equivalent_new_key_is_indeterminate_without_dispatch(self):
        with patch('codex_expert_mcp.claude_desktop.review',
                   return_value=dict(ADMISSION)):
            self.delegate(self.bridge, 'desk-2')
        restarted = self.make_bridge()
        self.addCleanup(restarted.db.close)
        with patch('codex_expert_mcp.claude_desktop.review',
                   return_value=dict(ADMISSION)) as review:
            result = self.delegate(restarted, 'desk-2-retry')
        review.assert_not_called()  # signature sémantique équivalente déjà incertaine
        self.assertEqual(result['status'], 'indeterminate')
        self.assertIsNone(result.get('response'))
        self.assertEqual(self.stored(restarted, 'desk-2-retry'), None)

    def test_claim_on_an_attempted_task_reopens_without_the_prompt(self):
        store = TaskStore(self.state, self.project)
        self.addCleanup(store.close)
        store.admit('desk-3', 'prompt privé', chat_id=CHAT)
        first = store.claim('desk-3', 'claim-stable')
        self.assertEqual(first['delivery'], 'handed_over')
        attempt = first['attempt_id']
        self.assertEqual(store.reserve('desk-3', 'claim-stable')['state'], 'attempted')
        replay = store.claim('desk-3', 'claim-stable')
        self.assertEqual(replay['delivery'], 'attempted')
        self.assertEqual(replay['attempt_id'], attempt)
        self.assertNotIn('prompt', replay)
        self.assertNotIn('prompt privé', json.dumps(replay, ensure_ascii=False))
        row = store.db.execute(
            'SELECT claim_key,attempt_id,delivery,answer FROM desktop_tasks '
            'WHERE scope=? AND request_key=?', (store.scope, 'desk-3')).fetchone()
        self.assertEqual(row, ('claim-stable', attempt, 'attempted', None))

    def test_reconcile_observe_never_submits_and_keeps_admission_only(self):
        with patch('codex_expert_mcp.claude_desktop.review',
                   return_value=dict(ADMISSION)):
            self.delegate(self.bridge, 'desk-4')
        with patch('codex_expert_mcp.reconcile_turn',
                   return_value={'status': 'running', 'phase': 'active',
                                 'next_action': 'reconcile'}) as reconcile:
            observed = self.bridge.call('reprise_reconcile', {'request_key': 'desk-4',
                                                              'action': 'observe'})
        reconcile.assert_called_once()
        self.assertEqual(observed['status'], 'running')
        self.assertNotEqual(observed['status'], 'finished')
        self.assertIsNone(observed.get('response'))
        state, raw = self.stored(self.bridge, 'desk-4')
        self.assertEqual(state, 'running')
        self.assertTrue(json.loads(raw)['admission_only'])
        listed = self.bridge.call('reprise_requests', {'request_key': 'desk-4'})
        self.assertEqual(listed['state'], 'running')
        full = self.bridge.call('reprise_requests', {'request_key': 'desk-4', 'view': 'full'})
        self.assertTrue(full['result']['admission_only'])
        self.assertIsNone(full['result']['response'])

    def test_wait_expiry_then_same_key_never_redispatches(self):
        expired = dict(ADMISSION, status='awaiting_approval', phase='active')
        with patch('codex_expert_mcp.claude_desktop.review',
                   return_value=expired) as review:
            first = self.delegate(self.bridge, 'desk-5')
            second = self.delegate(self.bridge, 'desk-5')
        self.assertEqual(review.call_count, 1)
        for result in (first, second):
            self.assertTrue(result['admission_only'])
            self.assertIsNone(result['response'])
            self.assertIn(result['status'], ('awaiting_approval', 'running'))
            self.assertNotEqual(result['status'], 'finished')


if __name__ == '__main__':
    unittest.main()
