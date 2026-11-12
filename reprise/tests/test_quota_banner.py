"""(#135) Lecteur bannière CDP : parsing HH:MM, garde anti-répétition, refus profil occupé.

Aucun Chrome réel n'est lancé : launcher/terminate sont des mocks.
"""
import importlib.machinery
import importlib.util
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import sqlite3
import weakref
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_quota
import claude_quota_banner
import scheduler

ZONE = ZoneInfo('Europe/Paris')


def load_fixtures():
    path = Path(__file__).parent / 'fixtures_quota_banner.json'
    return json.loads(path.read_text())


FIXTURES = load_fixtures()


def epoch(text_zone_time, zone=ZONE):
    return datetime.fromisoformat(text_zone_time).replace(tzinfo=zone).timestamp()


class FakeTransport:
    def __init__(self, text, fail_attach=False):
        self.text = text
        self.fail_attach = fail_attach
        self.calls = []

    def call(self, method, params=None):
        self.calls.append(method)
        if method == 'Target.getTargets':
            return {'targetInfos': [{'type': 'page', 'url': 'https://claude.ai/chat/abc', 'targetId': 'T1'}]}
        if method == 'Target.attachToTarget':
            if self.fail_attach:
                raise RuntimeError('attach refusé')
            return {'sessionId': 'S1'}
        return {}

    def attach_to(self, target_id):
        self.call('Target.attachToTarget', {'targetId': target_id, 'flatten': True})
        return 'S1'

    def evaluate(self, expression):
        self.calls.append('Runtime.evaluate')
        return self.text


class ParseBannerTests(unittest.TestCase):
    def test_elapsed_time_gives_elapsed_status(self):
        # (#135) Bannière non rafraîchie : « jusqu'à 20:10 » avec 20:10 déjà
        # passé → statut honnête 'elapsed', jamais 'unknown' ni future.
        now = epoch('2026-11-11T21:00:00')
        text = "Vous n'avez plus de messages gratuits jusqu'à 20:10."
        out = claude_quota_banner.parse_banner(text, now, ZONE)
        self.assertEqual(out['deadline_status'], 'elapsed')
        self.assertEqual(datetime.fromtimestamp(out['resets_at'], ZONE),
                         datetime(2026, 11, 11, 20, 10, tzinfo=ZONE))

    def test_future_time_stays_future(self):
        now = epoch('2026-11-11T10:00:00')
        text = "Vous n'avez plus de messages gratuits jusqu'à 20:10."
        out = claude_quota_banner.parse_banner(text, now, ZONE)
        self.assertEqual(out['deadline_status'], 'future')
        self.assertEqual(datetime.fromtimestamp(out['resets_at'], ZONE),
                         datetime(2026, 11, 11, 20, 10, tzinfo=ZONE))

    def test_reinitialisation_afternoon_future(self):
        now = epoch('2026-11-11T10:00:00')
        out = claude_quota_banner.parse_banner(FIXTURES['reinitialisation'], now, ZONE)
        self.assertEqual(out['deadline_status'], 'future')
        self.assertEqual(out['source'], 'banner_cdp')
        self.assertEqual(datetime.fromtimestamp(out['resets_at'], ZONE),
                         datetime(2026, 11, 11, 14, 40, tzinfo=ZONE))

    def test_en_pause_before_now_resolves_tomorrow(self):
        now = epoch('2026-11-11T16:00:00')
        out = claude_quota_banner.parse_banner(FIXTURES['en_pause'], now, ZONE)
        self.assertEqual(out['deadline_status'], 'future')
        self.assertEqual(datetime.fromtimestamp(out['resets_at'], ZONE),
                         datetime(2026, 11, 12, 9, 15, tzinfo=ZONE))

    def test_equal_time_is_unknown(self):
        now = epoch('2026-11-11T14:40:00')
        out = claude_quota_banner.parse_banner('Réinitialisation : 14:40', now, ZONE)
        self.assertEqual(out['deadline_status'], 'unknown')
        self.assertIsNone(out['resets_at'])

    def test_no_marker_is_unknown(self):
        now = epoch('2026-11-11T10:00:00')
        out = claude_quota_banner.parse_banner(FIXTURES['sans_banniere'], now, ZONE)
        self.assertEqual(out['deadline_status'], 'unknown')
        self.assertIsNone(out['resets_at'])

    def test_dated_text_uses_parse_pause(self):
        now = epoch('2026-11-11T10:00:00')
        text = ('En pause : votre utilisation gratuite sera de nouveau disponible '
                '12 nov. à 14:00. Vous pourrez continuer à ce moment-là.')
        out = claude_quota_banner.parse_banner(text, now, ZONE)
        self.assertEqual(out['deadline_status'], 'future')
        self.assertEqual(datetime.fromtimestamp(out['resets_at'], ZONE),
                         datetime(2026, 11, 12, 14, 0, tzinfo=ZONE))

    def test_raw_text_truncated(self):
        now = epoch('2026-11-11T10:00:00')
        text = 'Réinitialisation : ' + 'x' * 500 + ' 14:40'
        out = claude_quota_banner.parse_banner(text, now, ZONE)
        self.assertLessEqual(len(out['raw_text']),
                             claude_quota_banner.RAW_TEXT_MAX)

    def test_zone_required(self):
        now = epoch('2026-11-11T10:00:00')
        with self.assertRaises(ValueError):
            claude_quota_banner.resolve_day(14, 40, now, None)


class ReadBannerTests(unittest.TestCase):
    def _run(self, transport, text=None, busy=False, chrome_present=True):
        launched = []
        terminated = []

        def launcher(profile=None, origin=None):
            launched.append((profile, origin))
            return transport, ('PROC',)

        def terminate(proc):
            terminated.append(proc)

        chrome_bin = str(Path(__file__).resolve()) if chrome_present else '/absent/chrome'
        with patch.object(claude_quota_banner.time, 'sleep'), \
             patch('claude_cdp_pipe._profile_dir', return_value=Path(tempfile.mkdtemp()) / 'chrome'), \
             patch('claude_cdp_pipe._hostname_pid_lock', return_value=(Path(tempfile.mkdtemp()) / 'lock', None)), \
             patch('claude_cdp_pipe.check_profile_busy',
                   return_value={'ok': False, 'status': 'profile_busy', 'pid': 42} if busy else None), \
             patch('claude_cdp_pipe._chrome_bin', return_value=chrome_bin):
            out = claude_quota_banner.read_banner(now=epoch('2026-11-11T10:00:00'),
                                                  zone=ZONE, launcher=launcher,
                                                  terminate=terminate)
        return out, launched, terminated

    def test_profile_busy_refuses_launch(self):
        transport = FakeTransport('')
        out, launched, _ = self._run(transport, busy=True)
        self.assertEqual(out['status'], 'profile_busy')
        self.assertEqual(out['pid'], 42)
        self.assertEqual(launched, [])
        self.assertEqual(transport.calls, [])

    def test_launch_cycle_reads_banner_and_terminates(self):
        transport = FakeTransport('En pause. Réinitialisation : 14:40.')
        out, launched, terminated = self._run(transport)
        self.assertTrue(out['ok'])
        self.assertEqual(out['deadline_status'], 'future')
        self.assertEqual(len(launched), 1)
        self.assertEqual(terminated, [('PROC',)])
        self.assertIn('Target.attachToTarget', transport.calls)
        self.assertIn('Runtime.evaluate', transport.calls)

    def test_create_target_fallback_when_no_page(self):
        transport = FakeTransport('Réinitialisation : 14:40')
        original_call = transport.call

        def call(method, params=None):
            if method == 'Target.getTargets':
                # Première lecture vide, puis la cible créée apparaît.
                transport._empty = not getattr(transport, '_empty', False)
                if transport._empty:
                    return {'targetInfos': []}
            return original_call(method, params)

        transport.call = call
        out, launched, terminated = self._run(transport)
        self.assertTrue(out['ok'])
        self.assertIn('Target.createTarget', transport.calls)

    def test_no_text_reports_honest_unknown(self):
        transport = FakeTransport('')
        out, _, terminated = self._run(transport)
        # evaluate renvoie '' → aucune page exploitable → statut honnête.
        self.assertFalse(out['ok'])
        self.assertEqual(out['deadline_status'], 'unknown')
        self.assertEqual(terminated, [('PROC',)])

    def test_chrome_missing(self):
        transport = FakeTransport('')
        out, launched, _ = self._run(transport, chrome_present=False)
        self.assertEqual(out['status'], 'chrome_missing')
        self.assertEqual(launched, [])


class RefreshFromBannerTests(unittest.TestCase):
    def _db(self):
        folder = tempfile.mkdtemp()
        db = sqlite3.connect(Path(folder) / 'tasks.sqlite')
        self.addCleanup(db.close)
        scheduler.setup(db)
        claude_quota.initialize(db)
        return db

    def _block_without_deadline(self, db):
        # État du tick (#132) bloqué sans échéance : quota_state reste ok
        # (aucune ligne non expirée) mais l'état courant est indéterminé/bloqué.
        with db:
            scheduler.put(db, 'claude_quota_state', {'status': 'unavailable', 'resume_at': None})

    def test_ok_state_never_launches_chrome(self):
        db = self._db()
        reader = _mock_reader()
        out = claude_quota.refresh_from_banner(db, now=epoch('2026-11-11T10:00:00'),
                                               zone=ZONE, banner_reader=reader)
        self.assertEqual(out['status'], 'skipped')
        reader.assert_not_called()

    def test_known_deadline_never_launches_chrome(self):
        db = self._db()
        claude_quota.register_notice(db, '12345678-1234-1234-1234-123456789abc',
                                     'En pause : votre utilisation gratuite sera de nouveau disponible '
                                     '12 nov. à 14:00. Vous pourrez continuer à ce moment-là.',
                                     epoch('2026-11-11T10:00:00'), ZONE)
        reader = _mock_reader()
        out = claude_quota.refresh_from_banner(db, now=epoch('2026-11-11T10:00:00'),
                                               zone=ZONE, banner_reader=reader)
        self.assertEqual(out['status'], 'skipped')
        reader.assert_not_called()

    def test_blocked_without_deadline_records_banner_deadline(self):
        db = self._db()
        self._block_without_deadline(db)
        now = epoch('2026-11-11T10:00:00')
        resets = epoch('2026-11-11T14:40:00')
        reader = _mock_reader({'ok': True, 'deadline_status': 'future',
                               'resets_at': resets, 'raw_text': 'Réinitialisation : 14:40'})
        out = claude_quota.refresh_from_banner(db, now=now, zone=ZONE, banner_reader=reader)
        self.assertEqual(out['status'], 'deadline_recorded')
        state = claude_quota.quota_state(db, now=now)
        self.assertEqual(state['status'], 'blocked')
        self.assertEqual(state['resume_at'], resets)

    def test_anti_repeat_throttles_within_ten_minutes(self):
        db = self._db()
        self._block_without_deadline(db)
        now = epoch('2026-11-11T10:00:00')
        reader = _mock_reader({'ok': True, 'deadline_status': 'unknown',
                               'resets_at': None, 'raw_text': ''})
        claude_quota.refresh_from_banner(db, now=now, zone=ZONE, banner_reader=reader)
        out = claude_quota.refresh_from_banner(db, now=now + 60, zone=ZONE, banner_reader=reader)
        self.assertEqual(out['status'], 'throttled')
        self.assertEqual(reader.call_count, 1)
        out = claude_quota.refresh_from_banner(db, now=now + claude_quota.BANNER_MIN_INTERVAL + 1,
                                               zone=ZONE, banner_reader=reader)
        self.assertEqual(out['status'], 'no_deadline')
        self.assertEqual(reader.call_count, 2)

    def test_reader_exception_is_reported_not_raised(self):
        db = self._db()
        self._block_without_deadline(db)

        def reader(now=None, zone=None):
            raise OSError('cdp indisponible')

        out = claude_quota.refresh_from_banner(db, now=epoch('2026-11-11T10:00:00'),
                                               zone=ZONE, banner_reader=reader)
        self.assertEqual(out['status'], 'banner_failed')
        self.assertEqual(out['error'], 'OSError')


def _mock_reader(return_value=None):
    class _Reader:
        calls = []

        def __call__(self, now=None, zone=None):
            self.calls.append((now, zone))
            return return_value

        @property
        def call_count(self):
            return len(self.calls)

        def assert_not_called(self):
            assert not self.calls, 'reader appelé'

    return _Reader()


if __name__ == '__main__':
    unittest.main()

    def test_single_marker_conversation_text_is_rejected(self):
        # Revue 11/11 : un horaire dans la conversation ne doit pas être
        # pris pour une échéance — un seul marqueur est insuffisant.
        now = epoch('2026-11-11T10:00:00')
        out = claude_quota_banner.parse_banner(
            "Le restaurant est ouvert jusqu'à 08:00.", now, ZONE)
        self.assertEqual(out['deadline_status'], 'unknown')
        self.assertIsNone(out['resets_at'])
