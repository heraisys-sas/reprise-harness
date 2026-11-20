"""Lot C (#238) : slots d'expertise active par backend dans expert_active_turns.

Contrat validé (avis Astra) : acquisition par INSERT atomique, libération
uniquement par observation d'un tour terminal (jamais par âge), réutilisation
du slot par un followup sur un thread déjà actif, refus bridge_busy à
l'enveloppe inchangée.
"""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from codex_expert_mcp import Bridge
from support_clock import FakeClock, ScriptedFactory, thread_reply, turn_payload

THREAD = '12345678-1234-1234-1234-123456789abc'
TURN = 'turn-1'
BUSY_ENVELOPE = {'status': 'busy', 'reason_code': 'bridge_busy',
                 'automatic_retry': False, 'dispatched': False,
                 'reason': 'Bridge busy: one active expertise per state directory; no automatic retry.'}


class ExpertActiveTurnsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        (self.root / 'rollout.jsonl').touch()
        self.state = Path(self.temp.name) / 'state'

    def bridge(self):
        bridge = Bridge(self.root, {}, self.state)
        bridge.program = 'codex'
        self.addCleanup(bridge.close)
        return bridge

    def occupy(self, bridge, backend, slot, key, thread_id=None, started_at=None):
        now = time.time()
        bridge.db.execute('INSERT INTO expert_active_turns VALUES (?,?,?,?,?,?)',
                          (backend, slot, thread_id, key, now if started_at is None else started_at, now))
        bridge.db.commit()

    def prepare(self, bridge, key='key'):
        bridge.requests.begin(key, {'question': 'Review'})
        for phase, turn in (('starting', None), ('active', TURN)):
            bridge.requests.attach(key, {'phase': phase, 'thread_id': THREAD, 'turn_id': turn,
                                         'started_at': 1000.0,
                                         'context': {'scope': bridge.scope, 'backend': 'codex'}})
        bridge.requests.finish(key, {'status': 'running', 'phase': 'active', 'work_completed': False,
                                     'thread_id': THREAD, 'turn_id': TURN, 'next_action': 'reconcile'})

    def test_codex_admits_two_parallel_and_refuses_third(self):
        bridge = self.bridge()
        first = bridge._acquire_slot('reprise_delegate', {'backend': 'codex', 'request_key': 'req-a'})
        second = bridge._acquire_slot('reprise_delegate', {'backend': 'codex', 'request_key': 'req-b'})
        self.assertEqual((first[0], first[1]), ('codex', '0'))
        self.assertEqual((second[0], second[1]), ('codex', '1'))
        self.assertEqual(bridge._acquire_slot('reprise_delegate', {'backend': 'codex', 'request_key': 'req-c'}), None)
        # Refus d'admission publique pendant que les deux slots sont tenus.
        with patch.object(bridge, '_call', side_effect=AssertionError('not dispatched')):
            result = bridge.call('reprise_delegate', {'question': 'Review', 'kind': 'expertise'})
        self.assertEqual(result, BUSY_ENVELOPE)
        self.assertEqual(bridge.db.execute('SELECT COUNT(*) FROM delegation_requests').fetchone()[0], 0)
        bridge._release_slot(first)
        bridge._release_slot(second)
        self.assertEqual(bridge.db.execute('SELECT COUNT(*) FROM expert_active_turns').fetchone()[0], 0)

    def test_claude_desktop_single_slot_refuses_second(self):
        bridge = self.bridge()
        first = bridge._acquire_slot('reprise_delegate', {'backend': 'claude-desktop', 'request_key': 'req-a'})
        self.assertEqual((first[0], first[1]), ('claude-desktop', '0'))
        self.assertEqual(bridge._acquire_slot('reprise_delegate', {'backend': 'claude-desktop', 'request_key': 'req-b'}), None)
        with patch.object(bridge, '_call', side_effect=AssertionError('not dispatched')):
            result = bridge.call('reprise_delegate', {'backend': 'claude-desktop', 'question': 'Review', 'kind': 'expertise'})
        self.assertEqual(result['status'], 'busy')

    def test_followup_on_active_thread_reuses_slot(self):
        bridge = self.bridge()
        # Les deux slots codex sont tenus ; le thread actif est propriétaire du slot '0'.
        self.occupy(bridge, 'codex', '0', 'other-req', thread_id=THREAD, started_at=time.time() - 50)
        self.occupy(bridge, 'codex', '1', 'held-b')
        before = bridge.db.execute('SELECT started_at, heartbeat_at FROM expert_active_turns WHERE backend=? AND slot=?',
                                   ('codex', '0')).fetchone()
        bridge.db.execute('INSERT INTO expert_threads VALUES (?,?)', (bridge.scope, THREAD))
        bridge.db.commit()
        with patch('codex_expert_mcp.review', return_value={'status': 'finished', 'resumable': True,
                                                            'thread_id': THREAD, 'response': 'Continued'}) as review:
            result = bridge.call('reprise_followup', {'request_key': 'follow-req', 'thread_id': THREAD,
                                                      'backend': 'codex', 'question': 'Continue'})
        self.assertEqual(result['status'], 'finished')
        review.assert_called_once()
        after = bridge.db.execute('SELECT started_at, heartbeat_at, request_key FROM expert_active_turns WHERE backend=? AND slot=?',
                                  ('codex', '0')).fetchone()
        # Même slot, même propriétaire, heartbeat rafraîchi ; pas de suppression.
        self.assertEqual((after[0], after[2]), (before[0], 'other-req'))
        self.assertGreater(after[1], before[1])
        # Un followup sur le request_key propriétaire garde une clé de libération,
        # et la libération ne retire que la ligne de CE propriétaire.
        key = bridge._acquire_slot('reprise_followup', {'backend': 'codex', 'thread_id': THREAD,
                                                        'request_key': 'other-req'})
        self.assertEqual(key, ('codex', '0', 'other-req'))
        bridge._release_slot(key)
        self.assertIsNone(bridge.db.execute('SELECT request_key FROM expert_active_turns WHERE backend=? AND slot=?',
                                            ('codex', '0')).fetchone())
        self.assertEqual(bridge.db.execute('SELECT request_key FROM expert_active_turns WHERE backend=? AND slot=?',
                                           ('codex', '1')).fetchone()[0], 'held-b')

    def test_reconcile_terminal_observation_releases_slot(self):
        bridge = self.bridge()
        self.prepare(bridge)
        self.occupy(bridge, 'codex', '0', 'key')
        factory = ScriptedFactory(FakeClock(), replies={
            'thread/read': thread_reply(self.root, THREAD, turns=[turn_payload(TURN, status='completed',
                items=[{'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Late deliverable'}])])})
        with patch('delegation_reconcile.Client', factory):
            result = bridge.call('reprise_reconcile', {'request_key': 'key'})
        self.assertEqual(result['status'], 'finished')
        self.assertEqual(bridge.db.execute('SELECT COUNT(*) FROM expert_active_turns WHERE request_key=?',
                                           ('key',)).fetchone()[0], 0)
        # Le slot libéré est immédiatement réadmissible.
        self.assertEqual(bridge._acquire_slot('reprise_delegate', {'backend': 'codex', 'request_key': 'req-new'})[1], '0')

    def test_stale_row_not_purged_by_age(self):
        bridge = self.bridge()
        # Ligne anciennes horodatages, aucun tour terminal associé : elle doit
        # survivre aux redémarrages (libération par observation uniquement).
        self.occupy(bridge, 'codex', '0', 'req-a', started_at=0.0)
        bridge.db.execute("UPDATE expert_active_turns SET heartbeat_at=0 WHERE backend='codex' AND slot='0'")
        bridge.db.commit()
        bridge.close()
        for _ in range(3):
            restarted = Bridge(self.root, {}, self.state)
            restarted.program = 'codex'
            row = restarted.db.execute('SELECT request_key FROM expert_active_turns WHERE backend=? AND slot=?',
                                       ('codex', '0')).fetchone()
            restarted.close()
            self.assertEqual(row, ('req-a',))

    def test_busy_envelope_field_identical(self):
        bridge = self.bridge()
        self.occupy(bridge, 'codex', '0', 'held-a')
        self.occupy(bridge, 'codex', '1', 'held-b')
        result = bridge.call('reprise_delegate', {'question': 'Review', 'kind': 'expertise'})
        self.assertEqual(result, BUSY_ENVELOPE)
        self.assertEqual(sorted(result), sorted(BUSY_ENVELOPE))

    def test_restart_recreates_table_and_keeps_live_rows(self):
        bridge = self.bridge()
        self.occupy(bridge, 'codex', '0', 'live-a')
        # Reçu terminal enregistré : ce slot-là doit être purgé à l'init.
        self.occupy(bridge, 'codex', '1', 'done')
        bridge.requests.begin('done', {'question': 'Old'})
        bridge.requests.finish('done', {'status': 'finished'})
        bridge.close()
        restarted = Bridge(self.root, {}, self.state)
        restarted.program = 'codex'
        self.addCleanup(restarted.close)
        rows = restarted.db.execute('SELECT request_key FROM expert_active_turns WHERE backend=? ORDER BY slot',
                                    ('codex',)).fetchall()
        self.assertEqual(rows, [('live-a',)])
        # Un second tour vivant survit à son tour à un redémarrage ; les deux
        # slots actifs bloquent ensuite toute nouvelle admission.
        self.occupy(restarted, 'codex', '1', 'live-b')
        restarted.close()
        final = Bridge(self.root, {}, self.state)
        final.program = 'codex'
        self.addCleanup(final.close)
        rows = final.db.execute('SELECT request_key FROM expert_active_turns WHERE backend=? ORDER BY slot',
                                ('codex',)).fetchall()
        self.assertEqual(rows, [('live-a',), ('live-b',)])
        with patch.object(final, '_call', side_effect=AssertionError('not dispatched')):
            result = final.call('reprise_delegate', {'question': 'Review', 'kind': 'expertise'})
        self.assertEqual(result, BUSY_ENVELOPE)


if __name__ == '__main__':
    unittest.main()
