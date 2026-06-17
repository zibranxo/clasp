"""
tests/unit/test_pid.py
======================
Unit tests for clasp.utils.pid.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.utils.pid import (
    DEFAULT_PID_PATH,
    delete_pid,
    is_running,
    read_pid,
    write_pid,
)


def test_write_and_read_pid():
    """Test writing and reading PID file."""
    # Use a temporary path to avoid interfering with real PID file
    test_pid_path = DEFAULT_PID_PATH.parent / "test.pid"

    try:
        # Write PID
        write_pid(test_pid_path)

        # Check file exists
        assert test_pid_path.exists()

        # Read PID back
        pid = read_pid(test_pid_path)
        assert pid is not None
        assert isinstance(pid, int)
        assert pid > 0

        # Check it matches current process
        assert pid == os.getpid()

    finally:
        # Cleanup
        if test_pid_path.exists():
            test_pid_path.unlink()


def test_read_pid_nonexistent():
    """Test reading PID from non-existent file."""
    test_pid_path = DEFAULT_PID_PATH.parent / "nonexistent.pid"
    assert read_pid(test_pid_path) is None


def test_read_pid_invalid_content():
    """Test reading PID file with invalid content."""
    test_pid_path = DEFAULT_PID_PATH.parent / "invalid.pid"

    try:
        # Write invalid content
        test_pid_path.write_text("not a number", encoding="utf-8")

        # Should return None
        assert read_pid(test_pid_path) is None

    finally:
        if test_pid_path.exists():
            test_pid_path.unlink()


def test_is_running_false_when_not_running():
    """Test is_running returns False for non-existent PID."""
    test_pid_path = DEFAULT_PID_PATH.parent / "not_running.pid"
    assert test_pid_path.exists() is False  # Ensure it doesn't exist

    running, pid = is_running(test_pid_path)
    assert running is False
    assert pid is None


def test_is_running_true_when_running():
    """Test is_running returns True for current process."""
    test_pid_path = DEFAULT_PID_PATH.parent / "current.pid"

    try:
        # Write current PID
        write_pid(test_pid_path)

        # Check it's running
        running, pid = is_running(test_pid_path)
        assert running is True
        assert pid == os.getpid()

    finally:
        if test_pid_path.exists():
            test_pid_path.unlink()


def test_is_running_stale_pid_removed():
    """Test that stale PID files are removed."""
    test_pid_path = DEFAULT_PID_PATH.parent / "stale.pid"

    try:
        # Write a PID that's likely not valid (unless we're incredibly lucky)
        # Using 1 which is unlikely to be a valid process on most systems
        test_pid_path.write_text("1", encoding="utf-8")
        assert test_pid_path.exists()

        # Check is_running - should return False and remove file
        running, pid = is_running(test_pid_path)
        assert running is False
        assert pid is None
        assert not test_pid_path.exists()  # File should be removed

    except Exception:
        # Cleanup if test failed
        if test_pid_path.exists():
            test_pid_path.unlink()
        raise


def test_delete_pid():
    """Test deleting PID file."""
    test_pid_path = DEFAULT_PID_PATH.parent / "to_delete.pid"

    try:
        # Create file
        test_pid_path.touch()
        assert test_pid_path.exists()

        # Delete it
        delete_pid(test_pid_path)

        # Check it's gone
        assert not test_pid_path.exists()

    finally:
        # Ensure cleanup
        if test_pid_path.exists():
            test_pid_path.unlink()


def test_delete_pid_nonexistent():
    """Test deleting non-existent PID file (should not raise)."""
    test_pid_path = DEFAULT_PID_PATH.parent / "does_not_exist.pid"
    assert not test_pid_path.exists()

    # Should not raise
    delete_pid(test_pid_path)
    assert not test_pid_path.exists()


if __name__ == "__main__":
    test_write_and_read_pid()
    test_read_pid_nonexistent()
    test_read_pid_invalid_content()
    test_is_running_false_when_not_running()
    test_is_running_true_when_running()
    test_is_running_stale_pid_removed()
    test_delete_pid()
    test_delete_pid_nonexistent()
    print("All PID tests passed!")