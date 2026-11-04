import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import claude_integration
import harness_integration


class ProjectVerificationTests(unittest.TestCase):
    def test_harness_refuses_other_installed_workspace_before_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / 'selected'
            installed = root / 'installed'
            selected.mkdir()
            installed.mkdir()
            old = {'status': 'installed', 'project': str(installed)}
            with patch('harness_integration.context', return_value=(root, root, root)), \
                 patch('harness_integration.inspect', return_value={}), \
                 patch('harness_integration.manifest', return_value=old):
                with self.assertRaisesRegex(ValueError, 'workspace sélectionné'):
                    harness_integration.verify(root, 'profile', project=selected)

    def test_claude_refuses_other_installed_workspace_before_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            selected = root / 'selected'
            installed = root / 'installed'
            selected.mkdir()
            installed.mkdir()
            entry = {'command': 'example'}
            old = {'status': 'installed', 'project': str(installed), 'connector_name': 'reprise-harness',
                   'entry_sha256': claude_integration.entry_hash(entry)}
            with patch('claude_integration.paths', return_value=(root, root / 'config', root)), \
                 patch('claude_integration.metadata', return_value=old), \
                 patch('claude_integration.read_config', return_value={'mcpServers': {'reprise-harness': entry}}):
                with self.assertRaisesRegex(ValueError, 'workspace sélectionné'):
                    claude_integration.verify(root, project=selected)


if __name__ == '__main__':
    unittest.main()
