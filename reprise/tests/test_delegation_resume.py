"""Lot C / issue #239 : moteur de reprise observe-only des délégations.

Contrat testé :
- un job horodaté déclenche exclusivement delegation_reconcile.reconcile(action='observe') ;
- AUCUN chemin de re-dispatch (codex_expert.review, claude_review, chatgpt_quick.review) ;
- backoff exponentiel, abandon MAX_ATTEMPTS avec notification attention_required idempotente ;
- quota : cause + échéance persistées, attente sans incrément d'attempts ;
- claim atomique, budget, limite, kill-switch ; expert_active_turns jamais touchée.
"""
import ast
import importlib.machinery
import importlib.util
import inspect
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))

import scheduler
from delegation_store import Store

_loader = importlib.machinery.SourceFileLoader(
    'delegation_resume', str(Path(__file__).parents[1] / 'delegation_resume.py'))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
delegation_resume = importlib.util.module_from_spec(_spec)
sys.modules['delegation_resume'] = delegation_resume
_loader.exec_module(delegation_resume)


def make_database(folder):
    """Base neuve ; Store installe delegation_requests + notification_events."""
    db = sqlite3.connect(str(Path(folder) / 'delegation.sqlite'))
    # scheduler_meta : installee par reprise en production ; presente ici pour
    # les cles kill-switch / lazy-start.
    db.execute('CREATE TABLE IF NOT EXISTS scheduler_meta '
               '(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    db.commit()
    return db


def make_pending_receipt(db, root):
    """Fabrication d'un reçu non terminal : begin + attach (phase active)."""
    key = 'res-' + uuid.uuid4().hex
    store = Store(db, str(root))
    result = store.begin(key, {'tool': 'reprise_delegate', 'backend': 'codex',
                               'question': 'fixture', 'manifest': {'key': key}})
    # begin retourne None pour une nouvelle admission (le reçu reste indeterminate)
    if result is None:
        store.attach(key, {'phase': 'active', 'thread_id': 'thread-' + key[-8:]})
    return key


def schedule_due(db, request_key, root, backend='codex', *, due_at=900.0, now=1000.0):
    delegation_resume.schedule(db, request_key, backend, str(root), due_at=due_at, now=now)


def job_row(db, request_key):
    return db.execute('SELECT request_key, backend, root, due_at, attempts, status, last_error '
                      'FROM delegation_resume_jobs WHERE request_key=?',
                      (request_key,)).fetchone()


class DelegationResumeEngineTests(unittest.TestCase):
    """Tests 1 à 8 du lead + contrats complémentaires du plan validé."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.db = make_database(self.temp.name)
        self.addCleanup(self.db.close)

    # -- Test 1 ------------------------------------------------------------
    def test_due_job_runs_reconcile_once(self):
        key = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key, self.root)
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'finished', 'receipt': {}}) as reconcile:
            summary = delegation_resume.run_due(self.db, now=1000.0)
        reconcile.assert_called_once()
        self.assertEqual(reconcile.call_args.kwargs['action'], 'observe')
        self.assertEqual(reconcile.call_args.args[2], key)
        self.assertEqual(summary['status'], 'finished')
        self.assertEqual(summary['claimed'], 1)
        self.assertEqual(job_row(self.db, key)[5], 'done')

    # -- Test 2 ------------------------------------------------------------
    def test_non_terminal_reschedules_with_backoff(self):
        key = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key, self.root)
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'running', 'receipt': {}}) as reconcile:
            delegation_resume.run_due(self.db, now=1000.0)
        self.assertEqual(job_row(self.db, key)[4], 1)
        self.assertEqual(job_row(self.db, key)[5], 'scheduled')
        # backoff pré-incrément : premier réessai à 60 s (60 * 2**0)
        self.assertEqual(job_row(self.db, key)[3], 1060.0)
        reconcile.assert_called_once()
        # pas de retraitement avant l'échéance ; nouveau backoff au second tour
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'running', 'receipt': {}}) as reconcile:
            delegation_resume.run_due(self.db, now=1001.0)
        reconcile.assert_not_called()
        self.assertEqual(job_row(self.db, key)[3], 1060.0)
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'running', 'receipt': {}}) as reconcile:
            delegation_resume.run_due(self.db, now=1060.0)
        self.assertEqual(job_row(self.db, key)[4], 2)
        self.assertEqual(job_row(self.db, key)[3], 1180.0)  # 1060 + 60 * 2**1

    # -- Test 3 ------------------------------------------------------------
    def test_redispatch_never_called(self):
        # Preuve structurelle : aucune instruction du module n'importe un module
        # de dispatch (la docstring cite les noms interdits, le code ne les
        # charge jamais).
        tree = ast.parse(inspect.getsource(delegation_resume))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
        self.assertFalse(imported & {'codex_expert', 'claude_review',
                                     'chatgpt_quick', 'claude_desktop'})
        # Preuve comportementale : pendant run_due sur plusieurs jobs dus,
        # aucun symbol de dispatch n'est appelé ; reconcile n'observe que.
        keys = [make_pending_receipt(self.db, self.root) for _ in range(3)]
        for key in keys:
            schedule_due(self.db, key, self.root)
        with patch('codex_expert.review') as codex_review, \
                patch('codex_expert_mcp.claude_review') as claude, \
                patch('chatgpt_quick.review') as chatgpt, \
                patch('claude_desktop.review') as desktop, \
                patch('delegation_reconcile.reconcile',
                      return_value={'status': 'finished', 'receipt': {}}) as reconcile:
            summary = delegation_resume.run_due(self.db, now=1000.0, limit=3)
        codex_review.assert_not_called()
        claude.assert_not_called()
        chatgpt.assert_not_called()
        desktop.assert_not_called()
        self.assertEqual(reconcile.call_count, 3)
        self.assertTrue(all(call.kwargs['action'] == 'observe'
                            for call in reconcile.call_args_list))
        self.assertEqual(summary['claimed'], 3)

    # -- Test 4 ------------------------------------------------------------
    def test_attempts_max_abandons_with_notification(self):
        key = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key, self.root)
        outcome = {'status': 'owner_unavailable', 'reason': 'native owner not reachable'}
        for round_index in range(8):
            # now saute largement au-dela du backoff courant (60 s * 2**n,
            # plafonne a 3600 s) pour que le job soit re-claimé a chaque tour.
            with patch('delegation_reconcile.reconcile', return_value=dict(outcome)):
                delegation_resume.run_due(self.db, now=1000.0 + round_index * 4000.0)
            if round_index < 7:
                self.assertEqual(job_row(self.db, key)[5], 'scheduled')
        # 8e échec : abandon (next_attempts >= MAX_ATTEMPTS)
        self.assertEqual(job_row(self.db, key)[5], 'abandoned')
        self.assertEqual(job_row(self.db, key)[4], 8)
        events = self.db.execute(
            'SELECT event_id, kind FROM notification_events').fetchall()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], 'attention_required')
        self.assertIn(key, events[0][0])
        # réexécution (job re-schedulé manuellement) : aucune notification nouvelle
        self.db.execute("UPDATE delegation_resume_jobs SET status='scheduled'")
        self.db.commit()
        with patch('delegation_reconcile.reconcile', return_value=dict(outcome)):
            delegation_resume.run_due(self.db, now=99999.0)
        self.assertEqual(self.db.execute(
            'SELECT COUNT(*) FROM notification_events').fetchone()[0], 1)

    # -- Test 5 ------------------------------------------------------------
    def test_quota_refusal_persists_cause_and_due(self):
        import claude_quota
        claude_quota.initialize(self.db)
        key = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key, self.root, backend='claude-desktop')
        due = 6000.0
        self.db.execute(
            'INSERT INTO claude_quota_resumes (event_key, chat_id, observed_at, due, '
            "fingerprint, pause_hash, status) VALUES ('evt-1', 'chat-1', ?, ?, 'fp', 'ph', 'waiting')",
            (1000.0, due))
        self.db.commit()
        with patch('delegation_reconcile.reconcile') as reconcile:
            summary = delegation_resume.run_due(self.db, now=1000.0)
        reconcile.assert_not_called()
        row = job_row(self.db, key)
        # échéance de quota persistée : attente exacte, aucun incrément d'attempts
        self.assertEqual(row[3], due)
        self.assertEqual(row[5], 'scheduled')
        self.assertEqual(row[4], 0)
        self.assertTrue(any(entry['status'] == 'quota_wait'
                            for entry in summary['processed']))
        # le traitement ne peut reprendre qu'à l'échéance de quota
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'finished', 'receipt': {}}) as reconcile:
            delegation_resume.run_due(self.db, now=due - 1)
        reconcile.assert_not_called()
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'finished', 'receipt': {}}) as reconcile:
            delegation_resume.run_due(self.db, now=due)
        reconcile.assert_called_once()
        self.assertEqual(job_row(self.db, key)[5], 'done')
        # échéance inconnue : backoff fixe générique, jamais d'échéance inventée
        # (la pause persistée du backend est purgée : le gate est backend-global,
        # une pause claude quelconque bloque TOUS les jobs claude-desktop)
        self.db.execute("DELETE FROM claude_quota_resumes WHERE event_key='evt-1'")
        self.db.commit()
        key2 = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key2, self.root, backend='claude-desktop')
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'running', 'receipt': {}}) as reconcile:
            delegation_resume.run_due(self.db, now=1000.0)
        reconcile.assert_called_once()
        self.assertEqual(job_row(self.db, key2)[3], 1060.0)  # now + backoff_delay(0)
        self.assertEqual(job_row(self.db, key2)[4], 1)  # backoff générique : +1 attempt
        # le quota ne bloque pas les autres backends
        key3 = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key3, self.root, backend='codex')
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'finished', 'receipt': {}}) as reconcile:
            delegation_resume.run_due(self.db, now=1000.0)
        reconcile.assert_called_once()
        self.assertEqual(job_row(self.db, key3)[5], 'done')

    # -- Test 6 ------------------------------------------------------------
    def test_run_due_does_not_touch_active_turns(self):
        self.db.execute('''CREATE TABLE expert_active_turns(
            backend TEXT, slot TEXT, thread_id TEXT, request_key TEXT,
            started_at REAL, heartbeat_at REAL, PRIMARY KEY(backend, slot))''')
        turn = ('codex', '0', 'live-thread', 'live-request', 100.0, 200.0)
        self.db.execute('INSERT INTO expert_active_turns VALUES (?,?,?,?,?,?)', turn)
        self.db.commit()
        key = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key, self.root)
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'finished', 'receipt': {}}):
            delegation_resume.run_due(self.db, now=1000.0)
        self.assertEqual(self.db.execute(
            'SELECT * FROM expert_active_turns').fetchall(), [turn])
        self.assertEqual(job_row(self.db, key)[5], 'done')

    # -- Test 7 ------------------------------------------------------------
    def test_concurrent_claim_single_runner(self):
        folder = Path(self.temp.name)
        db_path = str(folder / 'delegation.sqlite')
        key = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key, self.root)
        self.db.commit()

        with patch('delegation_reconcile.reconcile',
                   side_effect=lambda *a, **k: (time.sleep(0.05),
                                                {'status': 'finished'})[1]) as reconcile:
            barrier = threading.Barrier(2)
            results = {}

            def worker(name):
                connection = sqlite3.connect(db_path, timeout=2.0)
                try:
                    barrier.wait(timeout=2)
                    results[name] = delegation_resume.run_due(connection, now=1000.0)
                finally:
                    connection.close()

            threads = [threading.Thread(target=worker, args=('a',)),
                       threading.Thread(target=worker, args=('b',))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
        claimed = sum(result.get('claimed', 0) for result in results.values())
        self.assertEqual(claimed, 1, results)
        reconcile.assert_called_once()
        self.assertEqual(job_row(self.db, key)[5], 'done')

    # -- Test 8 ------------------------------------------------------------
    def test_kill_switch(self):
        key = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key, self.root)
        scheduler.put(self.db, 'delegation_resume_enabled', '0')
        self.db.commit()
        with patch('delegation_reconcile.reconcile') as reconcile:
            summary = delegation_resume.run_due(self.db, now=1000.0)
        reconcile.assert_not_called()
        self.assertIs(summary['enabled'], False)
        self.assertEqual(summary['claimed'], 0)
        self.assertEqual(job_row(self.db, key)[5], 'scheduled')
        scheduler.put(self.db, 'delegation_resume_enabled', True)
        self.db.commit()
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'finished', 'receipt': {}}):
            summary = delegation_resume.run_due(self.db, now=1000.0)
        self.assertIs(summary['enabled'], True)
        self.assertEqual(job_row(self.db, key)[5], 'done')

    # -- Contrats complémentaires (plan validé) ----------------------------
    def test_schedule_upsert_idempotent(self):
        key = make_pending_receipt(self.db, self.root)
        schedule_due(self.db, key, self.root, due_at=900.0)
        schedule_due(self.db, key, self.root, due_at=950.0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM delegation_resume_jobs'
                                         ).fetchone()[0], 1)
        row = job_row(self.db, key)
        self.assertEqual(row[3], 950.0)
        self.assertEqual(row[4], 0)  # attempts réinitialisés par l'upsert
        self.assertIsNone(row[6])    # last_error effacé

    def test_limit_two_jobs_per_run(self):
        keys = [make_pending_receipt(self.db, self.root) for _ in range(3)]
        for key in keys:
            schedule_due(self.db, key, self.root)
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'finished', 'receipt': {}}) as reconcile:
            summary = delegation_resume.run_due(self.db, now=1000.0)
        self.assertEqual(summary['claimed'], 2)
        self.assertEqual(reconcile.call_count, 2)
        with patch('delegation_reconcile.reconcile',
                   return_value={'status': 'finished', 'receipt': {}}) as reconcile:
            delegation_resume.run_due(self.db, now=1000.0)
        reconcile.assert_called_once()
        statuses = [job_row(self.db, key)[5] for key in keys]
        self.assertEqual(sorted(statuses), ['done', 'done', 'done'])

    def test_budget_exhausted(self):
        keys = [make_pending_receipt(self.db, self.root) for _ in range(2)]
        for key in keys:
            schedule_due(self.db, key, self.root)
        with patch('delegation_reconcile.reconcile') as reconcile:
            summary = delegation_resume.run_due(self.db, now=1000.0, budget_seconds=0.0)
        reconcile.assert_not_called()
        self.assertEqual(summary['status'], 'budget_exhausted')
        self.assertEqual(summary['claimed'], 0)
        self.assertEqual(job_row(self.db, keys[0])[5], 'scheduled')

    def test_enabled_by_default_and_lazy_start_off(self):
        self.assertIs(delegation_resume.enabled(self.db), True)
        self.assertIs(delegation_resume.lazy_start_enabled(self.db), False)
        scheduler.put(self.db, 'delegation_lazy_start', '')
        self.assertIs(delegation_resume.lazy_start_enabled(self.db), False)
        scheduler.put(self.db, 'delegation_lazy_start', True)
        self.assertIs(delegation_resume.lazy_start_enabled(self.db), True)

    def test_engine_holds_no_dispatch_imports(self):
        # Aucun re-dispatch : pas d'attribut module ré-expédition, la seule
        # interaction native admise est reconcile(action='observe').
        self.assertFalse(hasattr(delegation_resume, 're_dispatch'))
        source = inspect.getsource(delegation_resume)
        self.assertIn("action='observe'", source)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn('review', node.func.attr)
                self.assertNotIn('dispatch', node.func.attr)


if __name__ == '__main__':
    unittest.main()
