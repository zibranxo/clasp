"""
tests/unit/test_cmd_claude.py
=============================
Unit tests for clasp.cli.cmd_claude.
"""

from __future__ import annotations

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import os

from clasp.cli.cmd_claude import run_claude, _ensure_server_running, claude


def test_cmd_claude_functions_exist():
    """Test that the CLI claude functions exist."""
    assert callable(run_claude)
    assert callable(_ensure_server_running)
    assert callable(claude)


if __name__ == "__main__":
    test_cmd_claude_functions_exist()
    print("All cmd_claude tests passed!")