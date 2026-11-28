import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
import routing_events
from expert_mcp import Bridge

PAYLOAD = {'question': 'Revue #265', 'backend': 'codex', 'kind': 'expertise', 'level': 'deep'}
DEAD_OWNER = 'local:999999999'


class TestReleaseUndispatched265(unittest.TestCase):
    """Issue #265: an indeterminate admission without any native turn leaves
    its lock only on proof of absence of dispatch, never by age."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(self.temp.name, {}, Path(self.temp.name) / 'state')
        self.addCleanup(self.bridge.close)

    def admit(self, key, owner=DEAD_OWNER, channel='codex', terminal=None, payload=PAYLOAD):
        self.assertIsNone(self.bridge.requests.begin(key, payload, owner=owner))
        event = routing_events.record(self.bridge.db, 'explicit', 'reprise_delegate', channel,
                                      request_key=key, admissibility='granted')
        if terminal:
            routing_events.finalize(self.bridge.db, event, terminal)

    def release(self, key):
        return self.bridge.call('reprise_reconcile', {'request_key': key, 'action': 'release_undispatched'})

    def test_dead_owner_without_turn_is_released_and_unblocks_equivalent(self):
        self.admit('crash-a1')
        # The indeterminate row blocks an equivalent admission under any key.
        blocked = self.bridge.requests.begin('crash-a2', PAYLOAD, owner=DEAD_OWNER)
        self.assertEqual(blocked['status'], 'indeterminate')
        result = self.release('crash-a1')
        self.assertEqual(result['status'], 'not_dispatched')
        self.assertEqual(result['proof'], 'admitting_owner_dead')
        self.assertFalse(result['work_completed'])
        receipt = self.bridge.requests.lookup({'request_key': 'crash-a1'})
        self.assertEqual(receipt['state'], 'recorded')
        self.assertIsNone(self.bridge.requests.begin('crash-a3', PAYLOAD, owner=DEAD_OWNER))

    def test_live_owner_is_refused(self):
        self.admit('live-a1', owner='local:' + str(os.getpid()))
        with self.assertRaisesRegex(ValueError, 'still alive'):
            self.release('live-a1')

    def test_persisted_turn_is_refused(self):
        self.admit('turn-a1')
        self.bridge.requests.attach('turn-a1', {'phase': 'starting', 'thread_id': 't-1', 'turn_id': None})
        with self.assertRaisesRegex(ValueError, 'native turn is persisted'):
            self.release('turn-a1')

    def test_legacy_row_needs_a_logged_pre_dispatch_refusal(self):
        self.admit('legacy-a1', owner=None, terminal='quota_blocked')
        self.assertEqual(self.release('legacy-a1')['proof'], 'legacy_pre_dispatch_refusal')
        other = dict(PAYLOAD, question='Autre question')
        self.admit('legacy-a2', owner=None, payload=other)
        with self.assertRaisesRegex(ValueError, 'Legacy admission'):
            self.release('legacy-a2')

    def test_non_codex_route_is_refused(self):
        self.admit('desk-a1', channel='claude-desktop', payload=dict(PAYLOAD, backend='claude-desktop'))
        with self.assertRaisesRegex(ValueError, 'only provable for a routed Codex'):
            self.release('desk-a1')

    def test_recorded_request_is_refused(self):
        self.admit('done-a1')
        self.bridge.requests.finish('done-a1', {'status': 'quota_blocked'})
        with self.assertRaisesRegex(ValueError, 'Only an indeterminate'):
            self.release('done-a1')


if __name__ == '__main__':
    unittest.main()
