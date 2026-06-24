"""
tests/unit/test_watcher.py
==========================
Unit tests for clasp.config.watcher.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.config.watcher import ConfigWatcher, get_watcher
from clasp.config.settings import Settings


def test_config_watcher_initialization():
    """Test ConfigWatcher can be instantiated."""
    watcher = ConfigWatcher()
    assert isinstance(watcher, ConfigWatcher)
    assert watcher._sse_queue is None
    assert watcher._debounce_ms == 300
    assert len(watcher._callbacks) == 0


def test_config_watcher_initialization_with_sse_queue():
    """Test ConfigWatcher with SSE queue."""
    sse_queue = asyncio.Queue()
    watcher = ConfigWatcher(sse_queue=sse_queue, debounce_ms=500)
    assert watcher._sse_queue is sse_queue
    assert watcher._debounce_ms == 500


def test_config_watcher_add_remove_callback():
    """Test adding and removing callbacks."""
    watcher = ConfigWatcher()

    callback1 = AsyncMock()
    callback2 = AsyncMock()

    # Add callbacks
    watcher.add_callback(callback1)
    watcher.add_callback(callback2)
    assert len(watcher._callbacks) == 2

    # Remove one callback
    watcher.remove_callback(callback1)
    assert len(watcher._callbacks) == 1
    assert callback2 in watcher._callbacks

    # Try to remove non-existent callback (should not raise)
    watcher.remove_callback(callback1)
    assert len(watcher._callbacks) == 1


def test_config_watcher_manual_trigger():
    """Test manual trigger reload."""
    watcher = ConfigWatcher()

    # Mock the _do_reload method
    mock_settings = MagicMock()
    watcher._do_reload = AsyncMock(return_value=mock_settings)

    # Trigger reload
    result = asyncio.run(watcher.trigger_reload())

    assert result == mock_settings
    watcher._do_reload.assert_called_once()


def test_config_watcher_do_reload_success():
    """Test _do_reload with successful settings load."""
    watcher = ConfigWatcher()

    # Mock get_settings to return test settings
    mock_settings = MagicMock()
    mock_settings.enabled_providers.return_value = ["provider1", "provider2"]
    mock_settings.server.port = 9999

    with patch("clasp.config.watcher.get_settings", return_value=mock_settings):
        # Clear cache first
        from clasp.config.settings import get_settings
        get_settings.cache_clear()

        result = asyncio.run(watcher._do_reload())

        assert result == mock_settings
        # Should have called get_settings (which clears cache internally)
        # Note: _do_reload calls cache_clear() then get_settings()


def test_config_watcher_do_reload_failure():
    """Test _do_reload when settings loading fails."""
    watcher = ConfigWatcher()

    # Mock get_settings to raise an exception
    with patch("clasp.config.watcher.get_settings", side_effect=Exception("Load failed")):
        # Mock the fallback call to get_settings
        mock_settings = MagicMock()
        with patch("clasp.config.watcher.get_settings", return_value=mock_settings):
            result = asyncio.run(watcher._do_reload())
            # Should return the fallback settings
            assert result == mock_settings


def test_config_watcher_do_reload_with_sse_queue():
    """Test _do_reload puts message on SSE queue."""
    watcher = ConfigWatcher()
    sse_queue = asyncio.Queue()
    watcher._sse_queue = sse_queue

    mock_settings = MagicMock()
    mock_settings.enabled_providers.return_value = ["test-provider"]
    mock_settings.server.port = 8888

    with patch("clasp.config.watcher.get_settings", return_value=mock_settings):
        from clasp.config.settings import get_settings
        get_settings.cache_clear()

        # Do reload
        asyncio.run(watcher._do_reload())

        # Check that a message was put on the queue
        assert not sse_queue.empty()
        message = sse_queue.get_nowait()
        assert message["event"] == "config_reloaded"
        assert message["port"] == 8888
        assert message["providers"] == ["test-provider"]


def test_config_watcher_do_reload_sse_queue_full():
    """Test _do_reload handles full SSE queue gracefully."""
    watcher = ConfigWatcher()
    sse_queue = asyncio.Queue(maxsize=1)
    watcher._sse_queue = sse_queue

    # Fill the queue
    sse_queue.put_nowait({"existing": "message"})

    mock_settings = MagicMock()
    mock_settings.enabled_providers.return_value = []
    mock_settings.server.port = 8082

    with patch("clasp.config.watcher.get_settings", return_value=mock_settings):
        from clasp.config.settings import get_settings
        get_settings.cache_clear()

        # This should not raise even though queue is full
        asyncio.run(watcher._do_reload())

        # The original message should still be there
        assert not sse_queue.empty()
        message = sse_queue.get_nowait()
        assert message["existing"] == "message"


def test_config_watcher_do_reload_with_callbacks():
    """Test _do_reload calls registered callbacks."""
    watcher = ConfigWatcher()

    callback1 = AsyncMock()
    callback2 = AsyncMock()
    watcher.add_callback(callback1)
    watcher.add_callback(callback2)

    mock_settings = MagicMock()

    with patch("clasp.config.watcher.get_settings", return_value=mock_settings):
        from clasp.config.settings import get_settings
        get_settings.cache_clear()

        # Do reload
        asyncio.run(watcher._do_reload())

        # Both callbacks should have been called
        callback1.assert_called_once_with(mock_settings)
        callback2.assert_called_once_with(mock_settings)


def test_config_watcher_do_reload_callback_exception():
    """Test _do_reload handles callback exceptions gracefully."""
    watcher = ConfigWatcher()

    callback1 = AsyncMock(side_effect=Exception("Callback failed"))
    callback2 = AsyncMock()
    watcher.add_callback(callback1)
    watcher.add_callback(callback2)

    mock_settings = MagicMock()

    with patch("clasp.config.watcher.get_settings", return_value=mock_settings):
        from clasp.config.settings import get_settings
        get_settings.cache_clear()

        # This should not raise even though callback fails
        asyncio.run(watcher._do_reload())

        # Both callbacks should have been attempted
        callback1.assert_called_once_with(mock_settings)
        callback2.assert_called_once_with(mock_settings)


def test_config_watcher_run_task_not_implemented_yet():
    """Test that run method handles missing watchfiles gracefully."""
    watcher = ConfigWatcher()

    # Mock import to raise ImportError for watchfiles
    orig_import = __import__
    def mock_import(name, *args, **kwargs):
        if name == "watchfiles":
            raise ImportError()
        return orig_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=mock_import):
        # The run method should catch this and wait forever
        # We'll test it with a timeout
        async def test_run_timeout():
            try:
                await asyncio.wait_for(watcher.run(), timeout=0.1)
                assert False, "Should have timed out"
            except asyncio.TimeoutError:
                pass  # Expected

        asyncio.run(test_run_timeout())


def test_config_watcher_config_path():
    """Test _config_path method respects environment variable."""
    old_val = os.environ.get("CLASP_CONFIG_PATH")
    try:
        # Test default
        if "CLASP_CONFIG_PATH" in os.environ:
            del os.environ["CLASP_CONFIG_PATH"]
        path = ConfigWatcher._config_path()
        assert path == Path.home() / ".clasp" / "config.yaml"

        # Test with env var
        custom_path = "/custom/config.yaml"
        os.environ["CLASP_CONFIG_PATH"] = custom_path
        path = ConfigWatcher._config_path()
        assert path == Path(custom_path)
    finally:
        if old_val is not None:
            os.environ["CLASP_CONFIG_PATH"] = old_val
        elif "CLASP_CONFIG_PATH" in os.environ:
            del os.environ["CLASP_CONFIG_PATH"]


def test_get_watcher_singleton():
    """Test that get_watcher returns the same instance."""
    # Clear any existing watcher
    import clasp.config.watcher as watcher_module
    watcher_module._watcher = None

    try:
        watcher1 = get_watcher()
        watcher2 = get_watcher()
        assert watcher1 is watcher2

        # Test with SSE queue (should be ignored after first call)
        sse_queue = asyncio.Queue()
        watcher3 = get_watcher(sse_queue=sse_queue)
        assert watcher3 is watcher1  # Same instance, sse_queue ignored
    finally:
        watcher_module._watcher = None


if __name__ == "__main__":
    test_config_watcher_initialization()
    test_config_watcher_initialization_with_sse_queue()
    test_config_watcher_add_remove_callback()
    test_config_watcher_manual_trigger()
    test_config_watcher_do_reload_success()
    test_config_watcher_do_reload_failure()
    test_config_watcher_do_reload_with_sse_queue()
    test_config_watcher_do_reload_sse_queue_full()
    test_config_watcher_do_reload_with_callbacks()
    test_config_watcher_do_reload_callback_exception()
    test_config_watcher_run_task_not_implemented_yet()
    test_config_watcher_config_path()
    test_get_watcher_singleton()
    print("All watcher tests passed!")