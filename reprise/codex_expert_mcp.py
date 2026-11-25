#!/usr/bin/env python3
"""Compat shim: historical import/launch name of expert_mcp.py (#258).

Kept for one release cycle so profiles and tests still referencing
codex_expert_mcp keep working. Import expert_mcp instead.
"""
import warnings

warnings.warn('codex_expert_mcp is deprecated; use expert_mcp instead.',
              DeprecationWarning, stacklevel=2)

from expert_mcp import *  # noqa: F401,F403
from expert_mcp import ExpertStdioHandler  # noqa: F401

# Historical class name, alias of the renamed handler.
CodexStdioHandler = ExpertStdioHandler

if __name__ == '__main__':
    main()
