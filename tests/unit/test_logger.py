"""
tests/unit/test_logger.py
=========================
Unit tests for clasp.utils.logger.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.utils.logger import setup_logging, get_log_file
from loguru import logger


def test_setup_logging_basic():
    """Test basic logging setup."""
    # Reset logger state
    logger.remove()

    # Setup with default level
    setup_logging()

    # Check that we have sinks
    assert len(logger._core.handlers) >= 1

    # Test logging works
    logger.info("Test message")

    # Cleanup
    logger.remove()


def test_setup_logging_with_level():
    """Test logging setup with specific level."""
    logger.remove()

    setup_logging(log_level="DEBUG")

    # Debug should be enabled
    logger.debug("Debug message")

    logger.remove()


def test_setup_logging_with_debug_flag():
    """Test logging setup with debug flag overriding level."""
    logger.remove()

    setup_logging(log_level="INFO", debug=True)

    # Debug should be enabled due to flag
    logger.debug("Debug message with flag")

    logger.remove()


def test_get_log_file():
    """Test getting log file path."""
    log_file = get_log_file()

    # Should be a Path object
    assert isinstance(log_file, Path)

    # Should be in .clasp/logs/router.log
    assert log_file.name == "router.log"
    assert "logs" in str(log_file)
    assert ".clasp" in str(log_file)


def test_logging_to_file():
    """Test that logging actually writes to file."""
    logger.remove()

    # Create temporary directory for test
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "test.log"

        # Add file sink
        logger.add(str(log_path), level="INFO", format="{message}")

        # Log something
        test_message = "Test log message for file"
        logger.info(test_message)

        # Force flush
        logger.remove()

        # Check file contents
        assert log_path.exists()
        content = log_path.read_text()
        assert test_message in content


if __name__ == "__main__":
    test_setup_logging_basic()
    test_setup_logging_with_level()
    test_setup_logging_with_debug_flag()
    test_get_log_file()
    test_logging_to_file()
    print("All logger tests passed!")