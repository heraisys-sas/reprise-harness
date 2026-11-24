import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from expert_mcp import Bridge
from metaai_taskstore import TaskStore


class RequestCalls:
    def call(self, name, args):
        if name not in ('reprise_backends', 'reprise_health', 'reprise_resume', 'reprise_history', 'reprise_requests'):
            args = {'request_key': str(uuid.uuid4()), **args}
        return self.bridge.call(name, args)


class MetaaiBridgeTests(RequestCalls, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state', daily_limit=2)
        self.bridge.program = 'codex'
        self.addCleanup(self.bridge.close)

    def values(self, enabled=True):
        root = Path(self.temp.name)
        return {'enabled': enabled,
                'taskstore': str(root / 'metaai-tasks.sqlite'),
                'dav_root': str(root / 'webdav')}

    def wire(self, values):
        # Même contrat que Bridge.__init__ : enabled:false laisse le store
        # fermé (la connexion est refusée au preflight).
        self.bridge.metaai_values = values
        if values.get('enabled', True) is False:
            return
        self.bridge.metaai_tasks = TaskStore(values['taskstore'])
        self.bridge.metaai_inbox = Path(values['dav_root']).expanduser() / 'inbox'
        self.addCleanup(self._close_metaai)

    def _close_metaai(self):
        store, self.bridge.metaai_tasks = self.bridge.metaai_tasks, None
        if store is not None:
            try:
                store._conn.close()
            except Exception:
                pass

    def test_backends_lists_metaai_declared_and_unconfigured_by_default(self):
        # #258 lot 3 : metaai est listé systématiquement ; `writes` est la
        # capacité déclarée (table WORKSPACE_WRITE_BACKENDS), configured reflète
        # la connexion (taskstore + dav_root).
        backends = self.call('reprise_backends', {})['backends']
        entry = next(row for row in backends if row['id'] == 'metaai')
        self.assertFalse(entry['configured'])
        self.assertTrue(entry['writes'])
        self.assertFalse(entry['task_return_supported'])
        self.assertEqual(entry['availability'], 'unverified')

    def test_backends_lists_metaai_configured_once_wired(self):
        self.wire(self.values())
        entry = next(row for row in self.call('reprise_backends', {})['backends']
                     if row['id'] == 'metaai')
        self.assertTrue(entry['configured'])
        self.assertTrue(entry['writes'])
        self.assertTrue(entry['task_return_supported'])

    def test_delegate_metaai_without_config_returns_engine_unavailable(self):
        payload = self.call('reprise_delegate', {'question': 'Revue', 'kind': 'review', 'backend': 'metaai'})
        self.assertEqual(payload['status'], 'engine_unavailable')
        self.assertIn('Connexion Meta AI non configurée', payload['reason'])
        self.assertFalse(payload['automatic_fallback'])

    def test_delegate_metaai_disabled_connection_refused(self):
        self.wire(self.values(enabled=False))
        payload = self.call('reprise_delegate', {'question': 'Revue', 'kind': 'review', 'backend': 'metaai'})
        self.assertEqual(payload['status'], 'engine_unavailable')
        self.assertIn('désactivée', payload['reason'])

    def test_delegate_metaai_enqueues_and_materializes_inbox(self):
        # Contrat pont WebDAV : enqueue TaskStore → claim → inbox/<id>.json
        # (payload JSON, question préfixée du contrat v2 par ancres) ; le reçu
        # du delegate est une admission (response None, admission_only).
        self.wire(self.values())
        result = self.call('reprise_delegate', {'question': 'Revue du module X', 'kind': 'review',
                                                'backend': 'metaai'})
        self.assertEqual(result['status'], 'finished')
        self.assertIsNone(result['response'])
        self.assertTrue(result['admission_only'])
        self.assertEqual(result['dispatch'], 'webdav_inbox')
        row = self.bridge.metaai_tasks.get(result['task_id'])
        self.assertIsNotNone(row)
        self.assertEqual(row['status'], 'dispatched')
        self.assertEqual(row['type'], 'expert_delegate')
        payload = json.loads(row['payload'])
        self.assertEqual(payload['request_key'], result['request_key'])
        self.assertIn('FICHIER:', payload['question'])
        self.assertIn('REMPLACER PAR:', payload['question'])
        self.assertTrue(payload['question'].endswith('Revue du module X'))
        inbox_file = Path(result['inbox_file'])
        self.assertTrue(inbox_file.is_file())
        materialized = json.loads(inbox_file.read_text())
        self.assertEqual(materialized['task_id'], result['task_id'])
        self.assertEqual(materialized['type'], 'expert_delegate')
        # Journal de routage : admission metaai enregistrée sur la clé appelant.
        journal = self.bridge.db.execute(
            "SELECT request_key FROM routing_events WHERE tool='reprise_delegate' "
            "AND channel='metaai' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(journal[0], result['request_key'])

    def test_wait_result_reads_answered_task_from_shared_store(self):
        # Boucle fermée : admission → record_result (watcher metaaid simulé)
        # → reprise_wait_result answered avec la réponse clé/valeur.
        self.wire(self.values())
        result = self.call('reprise_delegate', {'question': 'Question de test', 'kind': 'expertise',
                                                'backend': 'metaai'})
        task_id = result['task_id']
        self.bridge.metaai_tasks.record_result(
            task_id,
            {'task_id': task_id, 'reponse': 'Réponse metaai', 'confiance': 1},
            'task_id: ' + task_id + '\nreponse: Réponse metaai\nconfiance: 1',
            source='metaai')
        waited = self.call('wait_result', {'request_key': result['request_key'], 'wait_seconds': 5})
        self.assertEqual(waited['status'], 'finished')
        self.assertEqual(waited['backend'], 'metaai')
        self.assertEqual(waited['task_status'], 'answered')
        self.assertEqual(waited['result']['reponse'], 'Réponse metaai')
        self.assertTrue(waited['completed'])

    def test_wait_result_times_out_on_pending_task(self):
        self.wire(self.values())
        result = self.call('reprise_delegate', {'question': 'Question de test', 'kind': 'expertise',
                                                'backend': 'metaai'})
        waited = self.call('wait_result', {'request_key': result['request_key'], 'wait_seconds': 0.5})
        self.assertEqual(waited['status'], 'timeout')
        self.assertEqual(waited['backend'], 'metaai')
        self.assertFalse(waited['completed'])

    def test_wait_result_without_task_id_reports_not_found_on_metaai(self):
        # Reçu metaai sans task_id (admission perdue) : routage metaai via le
        # registre, refus not_found actionnable — jamais un sondage desktop.
        self.wire(self.values())
        self.bridge.requests.begin('sans-task', {'backend': 'metaai'})
        self.bridge.requests.finish('sans-task', {'status': 'finished', 'backend': 'metaai',
                                                  'admission_only': True, 'response': None})
        waited = self.call('wait_result', {'request_key': 'sans-task', 'wait_seconds': 1})
        self.assertEqual(waited['status'], 'not_found')
        self.assertEqual(waited['backend'], 'metaai')

    def test_experts_list_reports_metaai_handler_and_measurement(self):
        listed = self.bridge.call('reprise_experts_list', {})
        metaai = next(row for row in listed['backends'] if row['id'] == 'metaai')
        self.assertTrue(metaai['handler'])
        self.assertFalse(metaai['measured_available'])
        self.wire(self.values())
        metaai = next(row for row in self.bridge.call('reprise_experts_list', {})['backends']
                      if row['id'] == 'metaai')
        self.assertTrue(metaai['measured_available'])

    def test_close_releases_metaai_store(self):
        self.wire(self.values())
        self.bridge.close()
        self.assertIsNone(self.bridge.metaai_tasks)


if __name__ == '__main__':
    unittest.main()
