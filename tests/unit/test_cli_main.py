"""
tests/unit/test_cli_main.py
===========================
Unit tests for clasp.cli.main.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.cli.main import app


def test_cli_app_exists():
    """Test that the CLI app was created."""
    assert app is not None
    assert app.info.name == "clasp"
    assert "Claude API Switching Proxy" in app.info.help


def test_cli_commands_registered():
    """Test that expected commands are registered."""
    command_names = [command.name for command in app.registered_commands]
    assert "server" in command_names
    assert "claude" in command_names
    # Sprint 6 commands should exist as stubs
    assert "status" in command_names
    assert "stop" in command_names
    assert "reset" in command_names
    assert "init" in command_names


if __name__ == "__main__":
    test_cli_app_exists()
    test_cli_commands_registered()
    print("All CLI main tests passed!")