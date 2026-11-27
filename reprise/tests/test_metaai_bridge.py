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
        self.assertEqual(result['dispatch'], 'files_inbox')
        # #277 : le reçu embarque le circuit demandeur (mode d'emploi du wait).
        self.assertIn('wait_result', result['circuit'])
        self.assertIn('ADMISSION', result['circuit'])
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

    # --- Issue #267 : connexion avec bridge_root (format actuel) sans dav_root.
    # Ces tests construisent le Bridge par le VRAI constructeur (kwarg
    # metaai_values) : wire() réglait metaai_tasks/metaai_inbox à la main et
    # court-circuitait __init__, ce qui a laissé passer le défaut (clé
    # bridge_root seule → store et inbox restés None → TypeError au dispatch,
    # refus ×2 live le 25/11).

    def bridge_values(self):
        root = Path(self.temp.name)
        return {'enabled': True,
                'taskstore': str(root / 'metaai-tasks.sqlite'),
                'bridge_root': str(root / 'bridge')}

    def rebuild(self, values):
        # Nouveau Bridge par __init__ ; le précédent est fermé par son cleanup.
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name)/'state',
                             daily_limit=2, metaai_values=values)
        self.bridge.program = 'codex'
        self.addCleanup(self.bridge.close)

    def test_bridge_init_accepts_bridge_root_only_config(self):
        # La connexion Reprise écrite par metaai-install.py porte bridge_root
        # (pas dav_root) : l'init doit résoudre store ET inbox.
        self.rebuild(self.bridge_values())
        self.assertIsInstance(self.bridge.metaai_tasks, TaskStore)
        self.assertEqual(self.bridge.metaai_inbox, Path(self.temp.name) / 'bridge' / 'inbox')
        entry = next(row for row in self.bridge.call('reprise_backends', {})['backends']
                     if row['id'] == 'metaai')
        self.assertTrue(entry['configured'])
        self.assertTrue(entry['task_return_supported'])

    def test_bridge_init_accepts_legacy_dav_root_config(self):
        # Ancien format de connexion (dav_root, avant le 24/11) : l'init doit
        # encore le résoudre via le repli du helper metaai_bridge_root.
        values = self.bridge_values()
        del values['bridge_root']
        values['dav_root'] = str(Path(self.temp.name) / 'webdav')
        self.rebuild(values)
        self.assertIsInstance(self.bridge.metaai_tasks, TaskStore)
        self.assertEqual(self.bridge.metaai_inbox, Path(self.temp.name) / 'webdav' / 'inbox')

    def test_retry_task_store_recreates_inbox_from_bridge_root(self):
        self.rebuild(self.bridge_values())
        self.bridge.metaai_tasks, self.bridge.metaai_inbox = None, None
        self.assertTrue(self.bridge._retry_task_store('metaai'))
        self.assertIsInstance(self.bridge.metaai_tasks, TaskStore)
        self.assertEqual(self.bridge.metaai_inbox, Path(self.temp.name) / 'bridge' / 'inbox')

    def test_delegate_metaai_with_bridge_root_only_config_materializes_inbox(self):
        # Le défaut historique : config bridge_root sans dav_root → inbox None
        # → TypeError au dispatch. Contracté : admission finished + inbox JSON
        # avec les ancres du contrat v2, tâche dispatched dans le store.
        self.rebuild(self.bridge_values())
        result = self.call('reprise_delegate', {'question': 'Revue du lot A', 'kind': 'review',
                                                'backend': 'metaai'})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(result['dispatch'], 'files_inbox')
        inbox_file = Path(result['inbox_file'])
        self.assertTrue(inbox_file.is_file())
        materialized = json.loads(inbox_file.read_text())
        self.assertEqual(materialized['task_id'], result['task_id'])
        row = self.bridge.metaai_tasks.get(result['task_id'])
        self.assertEqual(row['status'], 'dispatched')
        payload = json.loads(row['payload'])
        self.assertIn('FICHIER:', payload['question'])
        self.assertIn('REMPLACER PAR:', payload['question'])

    def test_delegate_metaai_without_bridge_root_refuses_before_enqueue(self):
        # Config avec taskstore mais aucun répertoire pont : refus
        # return_channel_unavailable AVANT l'enfilement — aucune ligne
        # dispatched sans inbox (pas de tâche bloquée), avec ou sans store.
        values = self.bridge_values()
        del values['bridge_root']
        self.rebuild(values)
        self.assertIsNone(self.bridge.metaai_tasks)
        payload = self.call('reprise_delegate', {'question': 'Revue', 'kind': 'review',
                                                 'backend': 'metaai'})
        self.assertEqual(payload['status'], 'return_channel_unavailable')
        self.assertIn('répertoire pont', payload['reason'])
        self.assertFalse(payload['automatic_fallback'])
        self.assertFalse((Path(self.temp.name) / 'bridge' / 'inbox').exists())
        store = TaskStore(values['taskstore'])
        self.addCleanup(store._conn.close)
        self.assertEqual(store.list_tasks(), [])
        # Store rétabli mais inbox toujours introuvable : même refus, levé
        # avant le retry — aucune tâche enfilée, le store reste vide. (Une
        # demande de même signature serait bloquée par l'anti-replay du
        # registre, state indeterminate du 1er refus ; d'où une question
        # distincte pour cette seconde admission.)
        self.bridge.metaai_tasks = store
        payload = self.call('reprise_delegate', {'question': 'Revue du lot B', 'kind': 'review',
                                                 'backend': 'metaai'})
        self.assertEqual(payload['status'], 'return_channel_unavailable')
        self.assertIn('répertoire pont', payload['reason'])
        self.assertEqual(store.list_tasks(), [])


if __name__ == '__main__':
    unittest.main()
