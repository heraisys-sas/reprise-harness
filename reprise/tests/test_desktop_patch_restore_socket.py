import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parents[1]))

import desktop_patch


class RestoreSocketDecisionTests(unittest.TestCase):
    def test_alive_daemon_keeps_socket(self):
        with mock.patch.object(desktop_patch, 'probe_dispatch_socket', return_value=True) as probe:
            self.assertFalse(desktop_patch._should_unlink_socket(Path('/tmp/fake.sock')))
            probe.assert_called_once_with(Path('/tmp/fake.sock'), timeout=1.0)

    def test_orphan_socket_is_unlinked(self):
        with mock.patch.object(desktop_patch, 'probe_dispatch_socket', return_value=False):
            self.assertTrue(desktop_patch._should_unlink_socket(Path('/tmp/fake.sock')))


if __name__ == '__main__':
    unittest.main()
