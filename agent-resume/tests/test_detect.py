import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('detect', Path(__file__).parents[1] / 'detect.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class DetectionTests(unittest.TestCase):
    def inspect(self, events):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session.jsonl'
            path.write_text('\n'.join(json.dumps({'type': 'event_msg', 'payload': e}) for e in events))
            return module.inspect(path, now=2000)

    def test_complete_and_recent_are_not_interrupted(self):
        self.assertIsNone(self.inspect([{'type': 'task_complete'}]))
        self.assertIsNone(self.inspect([{'type': 'task_started', 'started_at': 1900}]))

    def test_manual_stop_never_becomes_quota_retry(self):
        result = self.inspect([{'type': 'turn_aborted', 'reason': 'interrupted'}])
        self.assertEqual(result['state'], 'stopped')
        self.assertIsNone(result['retry_at'])

    def test_unfinished_without_quota_stays_uncertain(self):
        self.assertEqual(self.inspect([{'type': 'task_started', 'started_at': 1}])['state'], 'unfinished')

    def test_exhausted_windows_use_latest_reset(self):
        events = [{'type': 'task_started', 'started_at': 1}, {'type': 'token_count', 'rate_limits': {'primary': {'used_percent': 100, 'resets_at': 3000}, 'secondary': {'used_percent': 100, 'resets_at': 4000}}}]
        self.assertEqual(self.inspect(events)['retry_at'], 4120)

class HarnessDetectionTests(unittest.TestCase):
    def test_errors_and_abort(self):
        for code, expected in [('QUOTA', 'quota_candidate'), ('ACCOUNT_QUOTA', 'quota_candidate'), ('RATE_LIMIT', 'rate_limited'), ('AUTH', 'failed')]:
            self.assertEqual(module.harness_status({}, {'kind': 'error', 'code': code})['state'], expected)
        self.assertIsNone(module.harness_status({}, {'kind': 'stop'}))
        self.assertEqual(module.harness_status({}, {'kind': 'aborted'})['state'], 'stopped')

    def test_waiting_user_and_open_turn(self):
        record = {'rows': {'userQuestions': {'val': {'questions': {'active': [{}]}}}}}
        self.assertEqual(module.harness_status(record)['state'], 'needs_user')
        record = {'rows': {'turnBoundary': {'val': {'openTurnStartSeq': 1}}}}
        self.assertIsNone(module.harness_status(record, modified=1900, now=2000))
        self.assertEqual(module.harness_status(record, modified=1, now=2000)['state'], 'unfinished')

class ActivityTests(unittest.TestCase):
    def test_active_and_quota_metadata_survive_completed_turn(self):
        import time
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session.jsonl'
            events = [
                {'type': 'event_msg', 'payload': {'type': 'task_started', 'started_at': time.time()}},
                {'type': 'event_msg', 'timestamp': '2026-10-14T08:00:00Z', 'payload': {'type': 'token_count', 'rate_limits': {'primary': {'used_percent': 25}}}}
            ]
            path.write_text('\n'.join(json.dumps(e) for e in events))
            details = {}
            module.inspect(path, details=details)
            self.assertTrue(details['active'])
            self.assertEqual(details['rates']['primary']['used_percent'], 25)
            events.append({'type': 'event_msg', 'payload': {'type': 'task_complete'}})
            path.write_text('\n'.join(json.dumps(e) for e in events))
            module.inspect(path, details=details)
            self.assertFalse(details['active'])
            self.assertEqual(details['measured_at'], '2026-10-14T08:00:00Z')

class ClaudeTests(unittest.TestCase):
    def inspect(self, events, age=0):
        import os,time
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'session.jsonl'
            path.write_text('\n'.join(json.dumps(e) for e in events))
            now = time.time()
            os.utime(path, (now-age, now-age))
            return module.inspect_claude(path, now)

    def test_activity_completion_and_sidechain(self):
        user = {'type': 'user', 'message': {}}
        self.assertTrue(self.inspect([user])['active'])
        completed = {'type': 'assistant', 'message': {'stop_reason': 'end_turn'}}
        self.assertFalse(self.inspect([user, completed])['active'])
        self.assertFalse(self.inspect([completed, dict(user, isSidechain=True)])['active'])
        self.assertEqual(self.inspect([user], age=1000)['session']['state'], 'unfinished')

    def test_structured_rate_limit(self):
        event = {'type': 'system', 'subtype': 'api_error', 'error': {'type': 'rate_limit_error'}}
        value = self.inspect([event])
        self.assertFalse(value['active'])
        self.assertEqual(value['session']['state'], 'rate_limited')

class CoworkTests(unittest.TestCase):
    def test_recent_activity_not_archived_or_completed(self):
        record = {'lastActivityAt': 1950000}
        self.assertTrue(module.cowork_active(record, now=2000))
        self.assertFalse(module.cowork_active(dict(record, isArchived=True), now=2000))
        self.assertFalse(module.cowork_active(dict(record, isAgentCompleted=True), now=2000))
        self.assertFalse(module.cowork_active({'lastActivityAt': 1000000}, now=2000))
        self.assertFalse(module.cowork_active({'lastActivityAt': 3000000}, now=2000))
        self.assertFalse(module.cowork_active({}, now=2000))

    def test_same_session_counted_once(self):
        import time
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for part in ('a', 'b'):
                target = root / 'local-agent-mode-sessions' / part / 'sessions'
                target.mkdir(parents=True)
                (target / 'local_test.json').write_text(json.dumps({'sessionId': 'same', 'lastActivityAt': time.time()*1000}))
            self.assertEqual(module.scan_cowork(root)['active'], 1)

class AutoClawTests(unittest.TestCase):
    def test_running_stopped_failed_and_done(self):
        record = {'sessionId': 'a', 'updatedAt': 1950000, 'status': 'running'}
        self.assertTrue(module.autoclaw_session(record, now=2000)['active'])
        stopped = module.autoclaw_session(dict(record, autoclawUserStoppedAt=1950000), now=2000)
        self.assertFalse(stopped['active'])
        self.assertEqual(stopped['summary']['state'], 'stopped')
        self.assertEqual(module.autoclaw_session(dict(record, status='failed'), now=2000)['summary']['state'], 'failed')
        self.assertIsNone(module.autoclaw_session(dict(record, status='done'), now=2000)['summary'])
        self.assertEqual(module.autoclaw_session(dict(record, updatedAt=1000000), now=2000)['summary']['state'], 'unfinished')

    def test_invalid_time_and_old_sessions(self):
        self.assertIsNone(module.autoclaw_session({'updatedAt': 'invalid'}, now=2000))
        self.assertIsNone(module.autoclaw_session({'updatedAt': 1}, now=40*86400))
        self.assertIsNone(module.autoclaw_session({'updatedAt': 3000000}, now=2000))
