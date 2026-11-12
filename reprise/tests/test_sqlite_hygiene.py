"""Resource hygiene: SQLite connections opened by persistence code must be closed.

The sqlite3 module emits a ResourceWarning when a Connection is collected
while still open. The warnings surface at the GC trigger site, not the
allocation site, so the exercises below either drive a full lifecycle
(build, use, close) or force collection before restoring the filter.
"""
import gc
import tempfile
import unittest
import warnings
from pathlib import Path

import codex_expert_mcp
import desktop_job_probe
import desktop_task_store
import ux_review_probe


def error_on_resource_warnings():
    warnings.simplefilter('error', ResourceWarning)


class ExplicitCloseTests(unittest.TestCase):
    def test_task_store_close_releases_connection(self):
        error_on_resource_warnings()
        self.addCleanup(warnings.resetwarnings)
        with tempfile.TemporaryDirectory() as folder:
            store = desktop_task_store.TaskStore(Path(folder) / 'state', Path(folder))
            try:
                store.close()
            finally:
                del store
            gc.collect()

    def test_task_store_reader_close_releases_connection(self):
        error_on_resource_warnings()
        self.addCleanup(warnings.resetwarnings)
        with tempfile.TemporaryDirectory() as folder:
            writer = desktop_task_store.TaskStore(Path(folder) / 'state', Path(folder))
            try:
                writer.admit('k1', 'hello')
            finally:
                writer.close()
            reader = desktop_task_store.TaskStore.open_reader(Path(folder) / 'state', Path(folder))
            try:
                self.assertEqual(reader.observe_result('k1')['state'], 'pending')
            finally:
                reader.close()
            gc.collect()

    def test_ux_probe_and_job_probe_close(self):
        error_on_resource_warnings()
        self.addCleanup(warnings.resetwarnings)
        with tempfile.TemporaryDirectory() as folder:
            probe = ux_review_probe.Probe(Path(folder) / 'ux', 'Review', None)
            probe.close()
            job = desktop_job_probe.Probe(Path(folder) / 'job')
            job.close()
            del probe, job
            gc.collect()

    def test_bridge_close_releases_expert_and_desktop_databases(self):
        error_on_resource_warnings()
        self.addCleanup(warnings.resetwarnings)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'project'
            root.mkdir()
            bridge = codex_expert_mcp.Bridge(root, {}, Path(folder) / 'state')
            try:
                self.assertIsNotNone(bridge.desktop_tasks)
            finally:
                bridge.close()
            del bridge
            gc.collect()


if __name__ == '__main__':
    unittest.main()
