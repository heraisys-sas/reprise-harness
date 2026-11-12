"""Cycle de délégation Desktop : admission, reçu et attente bornée (issue #102).

Contrats visés (API publique) :
- `claude_desktop.review(..., tasks=, max_wait=, clock=)` admet la tâche avant
  tout dispatch, augmente le prompt de la consigne MCP et ne confond jamais
  admission et succès.
- `ClaudeMCP` garde `await_task` caché/refusé par défaut, borne l'attente et
  n'efface jamais un résultat tardif ni ne le redélivre.
"""
import hashlib
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_desktop
from claude_mcp import ClaudeMCP
from desktop_task_store import TaskStore


class FakeTime:
    """Temps virtuel : wait() avance l'horloge et déclenche les événements dus."""

    def __init__(self):
        self.now, self.events, self.waits = 1000.0, [], []

    def clock(self):
        return self.now

    def at(self, delay, callback):
        self.events.append((self.now + delay, callback))

    def wait(self, cancel, seconds):
        self.waits.append(round(seconds, 3))
        self.now += seconds
        for event in sorted(self.events, key=lambda item: item[0]):
            if event[0] <= self.now and event in self.events:
                self.events.remove(event)
                event[1]()


def call(facade, name, arguments):
    response = facade.dispatch({'id': 1, 'method': 'tools/call',
                               'params': {'name': name, 'arguments': arguments}})
    return json.loads(response['content'][0]['text'])


class Cycle(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.state = self.root / 'state'
        self.store = TaskStore(self.state, self.project)
        self.addCleanup(self.store.close)
        self.socket = str(self.root / 'quick.sock')

    def review(self, **kwargs):
        values = {'socket': self.socket}
        return claude_desktop.review(self.project, 'Analyse ce point du contrat',
                                     kind='expertise', level='standard', program='superviseur',
                                     values=values, **kwargs)

    def row(self, key):
        return self.store.db.execute(
            'SELECT prompt,answer,delivery FROM desktop_tasks WHERE request_key=?', (key,)).fetchone()


class AdmissionBeforeDispatchTests(Cycle):
    def test_task_is_admitted_before_the_rpc_and_orchestration_is_declared(self):
        observed = {}
        store = self.store

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def call(self, method, params=None):
                if method == 'quick_entry/submit':
                    observed['row'] = store.db.execute(
                        'SELECT prompt,answer,delivery FROM desktop_tasks WHERE request_key=?',
                        ('cycle-1',)).fetchone()
                    observed['params'] = params
                    return {'status': 'finished', 'submission_id': 'sub-1',
                            'dispatch': 'dispatchOnQuickEntrySubmit'}
                return {}

            def send(self, message):
                pass

            def close(self):
                pass

        with patch('claude_desktop_rpc.Client', Client):
            result = self.review(request_key='cycle-1', tasks=store, max_wait=0)
        self.assertIsNotNone(observed.get('row'), 'la ligne doit exister AVANT l’appel RPC')
        prompt, answer, _delivery = observed['row']
        self.assertIn('cycle-1', prompt)
        self.assertIsNone(answer)
        params = observed['params']
        self.assertEqual(params['purpose'], 'delegation')
        self.assertIs(params['orchestrated'], True)
        self.assertIn('cycle-1', json.dumps(params, ensure_ascii=False))
        self.assertEqual(result['status'], 'finished')
        self.assertTrue(result['admission_only'])
        self.assertIsNone(result['response'])

    def test_prompt_carries_request_key_and_mcp_instruction_without_the_answer(self):
        captured = {}

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def call(self, method, params=None):
                if method == 'quick_entry/submit':
                    captured.update(params)
                    return {'status': 'finished', 'submission_id': 'sub-2'}
                return {}

            def send(self, message):
                pass

            def close(self):
                pass

        with patch('claude_desktop_rpc.Client', Client):
            self.review(request_key='cycle-2', tasks=self.store, max_wait=0)
        prompt = captured['prompt']
        self.assertIn('cycle-2', prompt)
        self.assertIn('submit_result', prompt)
        self.assertIn('get_task', prompt)
        # Une instruction d'appel, jamais la réponse ni un état calculé localement.
        self.assertNotIn('admission_only', prompt)
        self.assertNotIn('result_sha256', prompt)
        self.assertNotIn('dispatchOnQuickEntrySubmit', prompt)
        self.assertLessEqual(len(prompt), 12000)

    def test_admission_only_is_not_success_and_survives_a_restart(self):
        class Client:
            submits = 0

            def __init__(self, *args, **kwargs):
                pass

            def call(self, method, params=None):
                if method == 'quick_entry/submit':
                    Client.submits += 1
                    return {'status': 'finished', 'submission_id': 'sub-3'}
                return {}

            def send(self, message):
                pass

            def close(self):
                pass

        with patch('claude_desktop_rpc.Client', Client):
            first = self.review(request_key='cycle-3', tasks=self.store, max_wait=0)
            self.store.close()
            reopened = TaskStore(self.state, self.project)
            self.addCleanup(reopened.close)
            again = self.review(request_key='cycle-3', tasks=reopened, max_wait=0)
        self.assertIsNone(first['response'])
        self.assertTrue(first['admission_only'])
        self.assertNotEqual(first.get('status'), 'indeterminate')
        self.assertEqual(Client.submits, 1, 'un re-appel ne redispatche jamais')
        self.assertTrue(again['admission_only'])
        self.assertIsNone(again['response'])
        self.assertIsNone(reopened.db.execute(
            'SELECT answer FROM desktop_tasks WHERE request_key=?', ('cycle-3',)).fetchone()[0])

    def test_receipt_sha256_is_the_local_hash_of_the_answer(self):
        self.store.admit('cycle-4', 'Analyse ce point du contrat')
        attempt = self.store.claim('cycle-4', 'w1')['attempt_id']
        answer = '2,5,7 avec justification'
        self.store.submit('cycle-4', attempt, answer)
        facade = ClaudeMCP(self.store, Mock())
        manifest = call(facade, 'get_result', {'request_key': 'cycle-4'})
        page = call(facade, 'get_result', {'request_key': 'cycle-4', 'view': 'page',
                                           'result_sha256': manifest['result_sha256']})
        # Le reçu transporte l'empreinte du JSON canonique de la réponse.
        canonical = json.dumps({'answer': answer}, sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False)
        self.assertEqual(manifest['result_sha256'],
                         hashlib.sha256(canonical.encode()).hexdigest())
        self.assertEqual(page['result_sha256'], manifest['result_sha256'])
        self.assertEqual(hashlib.sha256(page['text'].encode()).hexdigest(),
                         manifest['result_sha256'])
        self.assertIn(answer, page['text'])


class UnavailableTests(Cycle):
    def test_absent_socket_is_engine_unavailable_without_dispatch(self):
        result = self.review(request_key='cycle-5')
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertFalse(result['automatic_fallback'])
        self.assertIsNone(result.get('response'))
        self.assertNotIn('thread_id', result)
        self.assertFalse(Path(self.socket).exists())
        with self.assertRaises(ValueError):
            self.store.result('cycle-5')  # aucune admission

    def test_orphan_socket_file_is_engine_unavailable(self):
        path = Path(self.socket)
        orphan = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        orphan.bind(str(path))
        path.chmod(0o600)
        orphan.close()  # fichier présent, aucun serveur à l'écoute
        self.addCleanup(path.unlink, missing_ok=True)
        result = self.review(request_key='cycle-6')
        self.assertEqual(result['status'], 'engine_unavailable')
        self.assertFalse(result['automatic_fallback'])
        self.assertIsNone(result.get('response'))
        with self.assertRaises(ValueError):
            self.store.result('cycle-6')

    def test_socket_lost_between_admission_and_submit_is_explicit(self):
        class BrokenClient:
            calls = []
            def __init__(self, *args, **kwargs):
                pass

            def call(self, method, params=None):
                self.calls.append(method)
                if method == 'quick_entry/submit':
                    raise OSError('endpoint closed between admission and submit')
                return {}

            def send(self, message):
                pass

            def close(self):
                pass

        with patch('claude_desktop_rpc.Client', BrokenClient):
            result = self.review(request_key='cycle-7', tasks=self.store, max_wait=0)
            # A new connection must see the durable intent and refuse replay.
            reopened = TaskStore(self.state, self.project)
            self.addCleanup(reopened.close)
            replay = self.review(request_key='cycle-7', tasks=reopened, max_wait=0)
        row = self.row('cycle-7')
        self.assertIsNotNone(row, 'la tâche doit rester admise malgré la perte du socket')
        self.assertIsNone(row[1])
        self.assertIsNone(row[2], 'la livraison MCP ne doit pas être marquée comme tentée')
        guard = self.store.db.execute('''SELECT legacy_unknown,review_started,dispatch_started,dispatch_receipt
            FROM desktop_dispatch_guard WHERE scope=? AND request_key=?''',
            (self.store.scope, 'cycle-7')).fetchone()
        self.assertEqual(guard, (0, 1, 0, None))
        self.assertEqual(replay['status'], 'indeterminate')
        self.assertEqual(BrokenClient.calls.count('quick_entry/submit'), 1)
        self.assertNotIn('quick_entry/dispatch', BrokenClient.calls)
        self.assertIn(result['status'], ('timed_out', 'engine_unavailable'))
        self.assertNotEqual(result['status'], 'finished')
        self.assertFalse(result['automatic_fallback'])
        self.assertIsNone(result.get('response'))


class BoundedWaitTests(Cycle):
    def test_expired_wait_reports_idle_and_keeps_the_receipt_pending(self):
        self.store.admit('pending', 'prompt en attente')
        time = FakeTime()
        facade = ClaudeMCP(self.store, Mock(), max_wait=5, clock=time.clock,
                           wait=time.wait, enable_await=True)
        before = call(facade, 'get_result', {'request_key': 'pending'})
        result = facade.await_task(self.store, {'request_key': 'absent', 'claim_key': 'w1',
                                                'wait_seconds': 5})
        payload = json.loads(result['content'][0]['text'])
        self.assertTrue(payload['idle'])
        self.assertFalse(payload['task'])
        self.assertEqual(payload['waited_seconds'], 5.0)
        after = call(facade, 'get_result', {'request_key': 'pending'})
        self.assertFalse(after['available'])
        self.assertFalse(after['acknowledged'])
        self.assertEqual(before['acknowledged'], after['acknowledged'])
        self.assertEqual(after['result_sha256'], before['result_sha256'])

    def test_await_task_is_hidden_and_refused_by_default(self):
        default = ClaudeMCP(self.store, Mock())
        self.assertFalse(default.enable_await)
        names = {item['name'] for item in default.dispatch({'method': 'tools/list'})['tools']}
        self.assertNotIn('await_task', names)
        self.store.admit('hidden', 'prompt caché')
        with self.assertRaises(ValueError):
            call(default, 'await_task', {'request_key': 'hidden', 'claim_key': 'w1'})
        row = self.row('hidden')
        self.assertIsNone(row[2])  # ni réservation ni livraison
        enabled = ClaudeMCP(self.store, Mock(), enable_await=True)
        names = {item['name'] for item in enabled.dispatch({'method': 'tools/list'})['tools']}
        self.assertIn('await_task', names)

    def test_late_result_is_collected_after_expiry_without_new_dispatch(self):
        time = FakeTime()
        facade = ClaudeMCP(self.store, Mock(), max_wait=2, clock=time.clock,
                           wait=time.wait, enable_await=True)
        idle = facade.await_task(self.store, {'request_key': 'late', 'claim_key': 'w1',
                                              'wait_seconds': 2})
        self.assertTrue(json.loads(idle['content'][0]['text'])['idle'])
        # Le résultat arrive après l'expiration, par le canal durable.
        self.store.admit('late', 'prompt tardif')
        attempt = self.store.reserve('late', 'w1')['attempt_id']
        self.assertTrue(self.store.submit('late', attempt, 'réponse tardive')['completed'])
        full = call(facade, 'get_result', {'request_key': 'late', 'view': 'full'})
        self.assertEqual(full['answer'], 'réponse tardive')
        self.assertFalse(full['acknowledged'])
        facade.bridge.dispatch.assert_not_called()

    def test_second_collection_returns_the_same_sha_without_a_payload(self):
        self.store.admit('collect', 'prompt')
        attempt = self.store.claim('collect', 'w1')['attempt_id']
        self.store.submit('collect', attempt, 'résultat unique')
        facade = ClaudeMCP(self.store, Mock())
        first = call(facade, 'get_result', {'request_key': 'collect', 'view': 'page'})
        self.assertIn('résultat unique', first['text'])
        second = call(facade, 'get_result', {'request_key': 'collect'})
        self.assertEqual(second['result_sha256'], first['result_sha256'])
        self.assertTrue(second['available'])
        self.assertNotIn('answer', second)
        self.assertNotIn('text', second)
        self.assertFalse(second['acknowledged'])
        full = call(facade, 'get_result', {'request_key': 'collect', 'view': 'full'})
        self.assertEqual(full['answer'], 'résultat unique')


if __name__ == '__main__':
    unittest.main()
