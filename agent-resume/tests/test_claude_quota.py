from datetime import datetime
from pathlib import Path
import sqlite3
import sys
import unittest
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_quota as quota
import scheduler

ZONE = ZoneInfo('Europe/Paris')
TEXT = 'En pause : votre utilisation gratuite sera de nouveau disponible 24 oct. à 01:50. Vous pourrez continuer à ce moment-là.'
CHAT = '12345678-1234-1234-1234-123456789abc'
OTHER = '12345678-1234-1234-1234-123456789abd'
NOW = datetime(2026, 10, 23, 23, 0, tzinfo=ZONE).timestamp()
DUE = datetime(2026, 10, 24, 1, 50, tzinfo=ZONE).timestamp()


class ClaudeQuotaTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE scheduler_meta(key TEXT PRIMARY KEY,value TEXT)')
        self.db.execute("INSERT INTO scheduler_meta VALUES ('auto_enabled','true')")
        self.db.execute("INSERT INTO scheduler_meta VALUES ('claude_desktop_auto_enabled','true')")
        self.db.commit()
        self.observation = dict(status='observed', chat_id=CHAT, pause_text=TEXT,
                                evidence_sha256='a'*64, busy=False, continue_enabled=False, composer_empty=True)
        self.clicks = []

    def tick(self, now=NOW, clicker=None):
        def click(chat, fingerprint):
            self.clicks.append((chat, fingerprint))
            return {'status': 'clicked'}
        return quota.tick(self.db, lambda: self.observation.copy(), clicker or click, now, ZONE)

    def test_pause_date_and_timezone(self):
        self.assertEqual(quota.parse_pause(TEXT, NOW, ZONE), DUE)
        self.assertEqual(datetime.fromtimestamp(DUE, ZoneInfo('UTC')).isoformat(), '2026-10-23T23:50:00+00:00')
        self.assertIsNone(quota.parse_pause(TEXT, NOW-8*86400, ZONE))
        self.assertIsNone(quota.parse_pause('untrusted advice', NOW, ZONE))
        with self.assertRaises(ValueError):quota.parse_pause(TEXT, NOW, None)

    def test_year_rollover_is_near_only(self):
        now = datetime(2026,12,31,23,0,tzinfo=ZONE).timestamp()
        text = TEXT.replace('24 oct.', '1 janv.')
        self.assertEqual(quota.parse_pause(text,now,ZONE),datetime(2027,1,1,1,50,tzinfo=ZONE).timestamp())

    def test_dst_ambiguous_and_missing_rejected(self):
        for date, current in [('25 oct. à 02:30',datetime(2026,10,24,tzinfo=ZONE)),
                              ('29 mars à 02:30',datetime(2026,3,28,tzinfo=ZONE))]:
            text = TEXT.replace('24 oct. à 01:50',date)
            self.assertIsNone(quota.parse_pause(text,current.timestamp(),ZONE))

    def test_wait_due_then_submit_once_across_ticks(self):
        self.tick()
        self.assertEqual(quota.status(self.db)[0]['status'], 'waiting')
        self.observation['continue_enabled']=True
        self.tick(DUE-1)
        self.assertFalse(self.clicks)
        self.assertEqual(self.tick(DUE)['submitted'],1)
        self.tick(DUE+1)
        self.assertEqual(len(self.clicks),1)
        self.assertEqual(quota.status(self.db)[0]['status'],'submitted')

    def test_different_chat_waits_and_manual_change_supersedes(self):
        self.tick()
        self.observation['chat_id']=OTHER
        self.tick(DUE)
        self.assertFalse(self.clicks)
        self.observation.update(chat_id=CHAT,evidence_sha256='b'*64,pause_text=None,continue_enabled=True)
        self.tick(DUE)
        self.assertEqual(next(r for r in quota.status(self.db) if r['chat_id'] == CHAT)['status'],'superseded')
        self.assertFalse(self.clicks)

    def test_pause_and_capability_gates(self):
        self.tick()
        self.observation.update(continue_enabled=True,busy=True)
        self.tick(DUE)
        self.assertFalse(self.clicks)
        self.observation['busy']=False
        self.db.execute("UPDATE scheduler_meta SET value='false' WHERE key='auto_enabled'")
        self.db.commit()
        self.tick(DUE)
        self.assertFalse(self.clicks)

    def test_composer_and_proven_non_dispatch_wait_without_consuming(self):
        self.tick()
        self.observation.update(continue_enabled=True, composer_empty=False)
        self.tick(DUE)
        self.assertFalse(self.clicks)
        self.observation['composer_empty'] = True
        self.tick(DUE, lambda *args: {'status': 'refused', 'dispatch_attempted': False})
        self.assertEqual(quota.status(self.db)[0]['status'], 'waiting')
        self.assertEqual(self.tick(DUE+1)['submitted'], 1)

    def test_first_observation_just_after_reset_can_register(self):
        self.observation['continue_enabled'] = True
        self.assertEqual(self.tick(DUE+60)['submitted'], 1)

    def test_notice_does_not_authorize_click_without_native_observation(self):
        quota.register_notice(self.db, CHAT, TEXT, NOW, ZONE)
        result = quota.tick(self.db, lambda: {'status': 'unavailable'},
                            lambda *args: self.clicks.append(args), DUE, ZONE)
        self.assertEqual(result['submitted'], 0)
        self.assertEqual(quota.status(self.db)[0]['status'], 'awaiting_observation')
        self.assertFalse(self.clicks)
        self.tick()
        self.assertEqual({r['status'] for r in quota.status(self.db)}, {'superseded', 'waiting'})

    def test_scheduler_uses_daemon_api_and_never_equates_admission_with_dispatch(self):
        quota.initialize(self.db)
        quota.register_notice(self.db, CHAT, TEXT, NOW, ZONE)
        calls = []
        class Client:
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def call(self, name, args):
                calls.append((name, args))
                if name == 'quick_entry/capabilities':
                    return {'dispatch_supported': True, 'resume_validation_supported': True}
                if name == 'quick_entry/submit':
                    return {'status': 'finished', 'admission_only': True, 'dispatch_attempted': False}
                return {}
        result = quota.run_quick(self.db, DUE-1, Client)
        self.assertFalse(any(name == 'quick_entry/submit' for name, _ in calls))
        result = quota.run_quick(self.db, DUE, Client)
        self.assertEqual(result['submitted'], 0)
        self.assertEqual(quota.status(self.db)[0]['status'], 'admitted')
        message = next(args for name, args in calls if name == 'quick_entry/submit')
        self.assertEqual(message['prompt'], 'retry')
        self.assertEqual(message['chatId'], CHAT)
        quota.run_quick(self.db, DUE+1, Client)
        self.assertEqual(sum(name == 'quick_entry/submit' for name, _ in calls), 1)

    def test_missing_native_driver_cannot_enqueue_a_false_continuation(self):
        quota.initialize(self.db)
        quota.register_notice(self.db, CHAT, TEXT, NOW, ZONE)
        calls = []
        class Client:
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
            def call(self, name, args):
                calls.append(name)
                return {'dispatch_supported': False}
        result = quota.run_quick(self.db, DUE, Client)
        self.assertEqual(result['status'], 'dispatch_driver_unavailable')
        self.assertNotIn('quick_entry/submit', calls)
        self.assertEqual(quota.status(self.db)[0]['status'], 'awaiting_observation')

    def test_default_enabled_and_explicit_disable(self):
        self.db.execute("DELETE FROM scheduler_meta WHERE key='claude_desktop_auto_enabled'")
        self.db.commit()
        self.assertEqual(self.tick()['status'], 'observed')
        self.assertEqual(len(quota.status(self.db)), 1)
        with self.db:
            self.db.execute("INSERT INTO scheduler_meta VALUES ('claude_desktop_auto_enabled','false')")
        self.assertEqual(self.tick()['status'], 'paused')

    def test_failure_after_claim_and_restart_never_retry(self):
        self.tick()
        self.observation['continue_enabled']=True
        def fail(*args):
            self.clicks.append(args)
            raise TimeoutError()
        self.tick(DUE,fail)
        self.tick(DUE+1)
        self.assertEqual(len(self.clicks),1)
        self.assertEqual(quota.status(self.db)[0]['status'],'uncertain')
        self.db.execute("UPDATE claude_quota_resumes SET status='dispatching'")
        self.db.commit()
        self.tick(DUE+2)
        self.assertEqual(len(self.clicks),1)
        self.assertEqual(quota.status(self.db)[0]['status'],'uncertain')

    def test_reobservation_changed_prevents_admission(self):
        self.tick()
        self.observation['continue_enabled']=True
        readings=iter([self.observation,dict(self.observation,chat_id=OTHER)])
        quota.tick(self.db,lambda:next(readings),lambda *args:self.clicks.append(args),DUE,ZONE)
        self.assertFalse(self.clicks)
