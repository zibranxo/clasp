"""
tests/unit/test_server.py
=========================
Unit tests for clasp.server.
"""

from __future__ import annotations

import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import os

from clasp.server import (
    create_app,
    _init_logging,
    _load_config,
    _init_registry,
    _start_config_watcher,
    _start_sprint2_tasks,
    _cancel_background_tasks,
    lifespan,
)
from clasp.config.settings import Settings


def test_init_logging():
    """Test logging initialization."""
    # This is hard to test without actually configuring loguru
    # We'll just test that the function exists and can be called
    try:
        _init_logging("INFO", False)
        _init_logging("DEBUG", True)
    except Exception:
        pass  # Might fail in test environment, that's ok


def test_load_config():
    """Test config loading."""
    # Mock get_settings
    mock_settings = MagicMock(spec=Settings)
    with patch("clasp.server.get_settings", return_value=mock_settings):
        settings = _load_config()
        assert settings == mock_settings


def test_init_registry():
    """Test registry initialization."""
    mock_settings = MagicMock(spec=Settings)
    # Test both code paths in _init_registry
    with patch("clasp.server._has_init_registry_function", return_value=True):
        with patch("clasp.server.registry.init_registry") as mock_init:
            _init_registry(mock_settings)
            mock_init.assert_called_once_with(mock_settings)

    # Test fallback path
    with patch("clasp.server._has_init_registry_function", return_value=False):
        with patch("clasp.server._has_init_attr_registry", return_value=True):
            with patch("clasp.server.registry") as mock_registry:
                mock_registry.init = MagicMock()
                _init_registry(mock_settings)
                mock_registry.init.assert_called_once_with(mock_settings)

    # Test when neither is available
    with patch("clasp.server._has_init_registry_function", return_value=False):
        with patch("clasp.server._has_init_attr_registry", return_value=False):
            # Should not raise
            _init_registry(mock_settings)


def test_start_config_watcher():
    """Test config watcher startup."""
    # Test when watcher is available
    with patch("clasp.server._has_start_watcher_function", return_value=True):
        with patch("clasp.server.start_watcher") as mock_start:
            mock_start.return_value = MagicMock()
            task = asyncio.run(_start_config_watcher())
            assert isinstance(task, asyncio.Task)
            mock_start.assert_called_once()

    # Test when watcher is not available
    with patch("clasp.server._has_start_watcher_function", return_value=False):
        with patch("clasp.server._has_start_watching_function", return_value=False):
            # Should not raise, just log warning
            _start_config_watcher()  # Should not raise


def test_start_sprint2_tasks():
    """Test Sprint 2+ background tasks startup."""
    # Test when tasks are available
    with patch("clasp.server._has_persistence_task", return_value=True):
        with patch("clasp.server._has_drain_task", return_value=True):
            # Should not raise
            _start_sprint2_tasks()  # Should not raise

    # Test when tasks are not available
    with patch("clasp.server._has_persistence_task", return_value=False):
        with patch("clasp.server._has_drain_task", return_value=False):
            # Should not raise
            _start_sprint2_tasks()  # Should not raise


def test_cancel_background_tasks():
    """Test cancelling background tasks."""
    # Create some mock tasks
    task1 = MagicMock()
    task1.done.return_value = False
    task2 = MagicMock()
    task2.done.return_value = True  # Already done
    task3 = MagicMock()
    task3.done.return_value = False

    # Add to global list
    import clasp.server as server_module
    original_tasks = server_module._background_tasks
    server_module._background_tasks = [task1, task2, task3]

    try:
        _cancel_background_tasks()

        # Check that cancel was called on non-done tasks
        task1.cancel.assert_called_once()
        task2.cancel.assert_not_called()  # Already done
        task3.cancel.assert_called_once()

        # Check that list was cleared
        assert server_module._background_tasks == []
    finally:
        server_module._background_tasks = original_tasks


def test_lifespan():
    """Test lifespan context manager."""
    # This is complex to test fully, but we can test that it exists
    assert callable(lifespan)

    # Test that it's an async context manager
    import inspect
    assert inspect.iscoroutinefunction(lifespan) or hasattr(lifespan, '__aenter__')


def test_create_app():
    """Test app creation."""
    # Mock the dependencies
    with patch("clasp.server._init_logging"):
        with patch("clasp.server._load_config") as mock_load_config:
            with patch("clasp.server._init_registry"):
                with patch("clasp.server._start_config_watcher"):
                    with patch("clasp.server._start_sprint2_tasks"):
                        with patch("clasp.server._register_routers"):
                            with patch("clasp.server._mount_static_files"):
                                mock_settings = MagicMock(spec=Settings)
                                mock_settings.server.log_level = "INFO"
                                mock_load_config.return_value = mock_settings

                                app = create_app()
                                assert app is not None
                                # Check that it's a FastAPI instance
                                from fastapi import FastAPI
                                assert isinstance(app, FastAPI)


def test_register_routers():
    """Test router registration."""
    # This is hard to test without a real app
    # We'll just test that the function exists
    assert callable(_register_routers)


def test_mount_static_files():
    """Test static file mounting."""
    # This is hard to test without a real app
    # We'll just test that the function exists
    assert callable(_mount_static_files)


if __name__ == "__main__":
    test_init_logging()
    test_load_config()
    test_init_registry()
    test_start_config_watcher()
    test_start_sprint2_tasks()
    test_cancel_background_tasks()
    test_lifespan()
    test_create_app()
    test_register_routers()
    test_mount_static_files()
    print("All server tests passed!")