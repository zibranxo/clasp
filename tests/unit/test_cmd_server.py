"""
tests/unit/test_cmd_server.py
=============================
Unit tests for clasp.cli.cmd_server.
"""

from __future__ import annotations

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import os

from clasp.cli.cmd_server import run


def test_cmd_server_function_exists():
    """Test that the run function exists."""
    assert callable(run)


if __name__ == "__main__":
    test_cmd_server_function_exists()
    print("All cmd_server tests passed!")