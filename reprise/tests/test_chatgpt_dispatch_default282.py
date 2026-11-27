import sys
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).parents[1]))
import claude_mcp
import expert_mcp


class _Stop(Exception):
    pass


class TestChatGptDispatchDefault282(unittest.TestCase):
    """Issue #282: every CLI bridge pushes admitted ChatGPT tasks to the holder
    by default; --no-chatgpt-dispatch is the only way to stay admission-only."""

    def bridge_kwargs(self, module, argv):
        captured = {}

        def fake_bridge(*args, **kwargs):
            captured.update(kwargs)
            raise _Stop()

        with mock.patch.object(module, 'Bridge', side_effect=fake_bridge), \
                mock.patch.object(module, 'TaskStore', create=True), \
                mock.patch.object(sys, 'argv', [module.__file__] + argv), \
                self.assertRaises(_Stop):
            module.main()
        return captured

    def test_expert_mcp_dispatch_default_on_and_opt_out(self):
        base = ['--project-root', '/tmp', '--state', '/tmp/state', '--chatgpt-state', '/tmp/cg']
        self.assertIs(self.bridge_kwargs(expert_mcp, base)['chatgpt_dispatch'], True)
        self.assertIs(self.bridge_kwargs(expert_mcp, base + ['--no-chatgpt-dispatch'])['chatgpt_dispatch'], False)

    def test_claude_mcp_dispatch_default_on_and_opt_out(self):
        base = ['--project-root', '/tmp', '--state', '/tmp/q', '--expert-state', '/tmp/e',
                '--chatgpt-state', '/tmp/cg']
        self.assertIs(self.bridge_kwargs(claude_mcp, base)['chatgpt_dispatch'], True)
        self.assertIs(self.bridge_kwargs(claude_mcp, base + ['--no-chatgpt-dispatch'])['chatgpt_dispatch'], False)


if __name__ == '__main__':
    unittest.main()
