"""Revue MAJEUR 6 : wait_result depuis le thread worker.

Le worker de wait_result tourne hors du thread principal ; la lecture du
backend enregistré ne doit pas passer par la connexion partagée du Bridge
(check_same_thread=True → sqlite3.ProgrammingError) mais par une connexion
read-only dédiée (même motif que TaskStore.open_reader). Le routage
chatgpt/claude-desktop et l'erreur de clé invalide restent inchangés.
"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from desktop_task_store import TaskStore
from expert_mcp import Bridge


class WaitResultWorkerThreadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        (self.root / 'rollout.jsonl').touch()
        self.state = Path(self.temp.name) / 'state'

    def bridge(self):
        bridge = Bridge(self.root, {}, self.state)
        self.addCleanup(bridge.close)
        return bridge

    def prepare_states(self):
        # Les deux registres de retour existent : wait_result atteint
        # observe_result (not_found) au lieu d'échouer à l'ouverture.
        # state_location(root, name) = root/<name> hors bundle.
        TaskStore(self.root / 'quick-state', self.root)
        TaskStore(self.root / 'chatgpt-state', self.root)

    def record(self, bridge, key, backend):
        # Semis via la partition réelle de l'écrivain : Store(scope=str(root)).
        # (Revue MAJEUR 6 — le test initial semait sous bridge.scope, un digest
        # sha256, et validait un lecteur faux ; l'échec du shard chatgpt_bridge
        # l'a révélé.)
        scope = str(bridge.root)
        bridge.db.execute(
            'INSERT INTO delegation_requests(scope,request_key,identity,signature,parent,root,depth,attempt,state,result,acknowledged) '
            "VALUES(?,?,?,?,'',?,0,'a1','recorded',?,0)",
            (scope, key, 'identity-' + key, 'signature', scope,
             json.dumps({'backend': backend})))
        bridge.db.commit()

    def run_in_worker(self, bridge, args):
        outcome = {}

        def worker():
            try:
                outcome['result'] = bridge.wait_result(args)
            except BaseException as exc:  # le test capture tout, y compris ProgrammingError
                outcome['error'] = exc

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(15)
        self.assertFalse(thread.is_alive(), 'worker bloqué')
        return outcome

    def test_worker_thread_routes_chatgpt_from_ledger(self):
        bridge = self.bridge()
        self.prepare_states()
        self.record(bridge, 'rq-revue6-chatgpt', 'chatgpt')
        outcome = self.run_in_worker(bridge, {'request_key': 'rq-revue6-chatgpt', 'wait_seconds': 0.2})
        self.assertNotIn('error', outcome, repr(outcome.get('error')))
        self.assertEqual(outcome['result']['backend'], 'chatgpt')
        self.assertEqual(outcome['result']['status'], 'not_found')

    def test_worker_thread_defaults_to_claude_desktop(self):
        bridge = self.bridge()
        self.prepare_states()
        outcome = self.run_in_worker(bridge, {'request_key': 'rq-revue6-absent', 'wait_seconds': 0.2})
        self.assertNotIn('error', outcome, repr(outcome.get('error')))
        self.assertEqual(outcome['result']['backend'], 'claude-desktop')

    def test_main_thread_unchanged(self):
        bridge = self.bridge()
        self.prepare_states()
        result = bridge.wait_result({'request_key': 'rq-revue6-main', 'wait_seconds': 0.2})
        self.assertEqual(result['backend'], 'claude-desktop')

    def test_invalid_key_still_refused(self):
        bridge = self.bridge()
        with self.assertRaises(ValueError):
            bridge.wait_result({'request_key': '', 'wait_seconds': 0.2})


if __name__ == '__main__':
    unittest.main()
