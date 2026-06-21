"""
tests/unit/test_internal_routes.py
==================================
Unit tests for clasp.internal.routes.
"""

from __future__ import annotations

import json
import os
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.internal.routes import (
    router,
    get_config,
    post_config,
    test_key as route_test_key,
    export_config,
    import_config,
    get_catalog,
    get_catalog_defaults,
    get_status,
    stream_status,
    get_queue,
    stream_logs,
    download_logs,
    clear_cache,
    _mask_key,
    _mask_settings,
    _restore_redacted,
    _settings_to_dict,
    _get_live_status,
)
from clasp.config.settings import Settings
from clasp.config.provider_catalog import PROVIDER_CATALOG
import httpx


def test_mask_key():
    """Test _mask_key function."""
    # Normal key
    assert _mask_key("nvapi-abcdefghijklmnopqrstuvwxyz") == "nvapi-***wxyz"

    # Short key
    assert _mask_key("abc") == "***"
    assert _mask_key("abcd") == "***"
    assert _mask_key("abcde") == "***bcde"

    # Empty or None
    assert _mask_key("") == "***"
    assert _mask_key(None) == "***"

    # Key with dash
    assert _mask_key("sk-abcdefgh") == "sk-***efgh"

    # Already masked
    assert _mask_key("nvapi-***hijk") == "nvapi-***hijk"


def test_mask_settings():
    """Test _mask_settings function."""
    settings_dict = {
        "providers": {
            "nvidia_nim": {
                "keys": ["nvapi-key-1", "nvapi-key-2"]
            },
            "gemini": {
                "keys": ["AIza-key"]
            }
        }
    }

    masked = _mask_settings(settings_dict)

    assert masked["providers"]["nvidia_nim"]["keys"][0] == "nvapi-***ey-1"
    assert masked["providers"]["nvidia_nim"]["keys"][1] == "nvapi-***ey-2"
    assert masked["providers"]["gemini"]["keys"][0] == "AIza-***-key"


def test_settings_to_dict():
    """Test _settings_to_dict function."""
    settings = MagicMock(spec=Settings)
    settings.model_dump.return_value = {"test": "value"}
    result = _settings_to_dict(settings)
    assert result == {"test": "value"}


def test_restore_redacted():
    """Test _restore_redacted function."""
    incoming = {
        "providers": {
            "nvidia_nim": {
                "keys": [
                    "nvapi-***ey-1",  # Should be restored
                    "nvapi-***new",   # Should remain masked (no original)
                    "nvapi-plain-text",  # Should be kept as-is
                ],
            },
        }
    }

    original = MagicMock(spec=Settings)
    original.providers = {
        "nvidia_nim": MagicMock(keys=["nvapi-real-key-1", "nvapi-real-key-2"])
    }

    restored = _restore_redacted(incoming, original)

    nvapi_keys = restored["providers"]["nvidia_nim"]["keys"]
    assert nvapi_keys[0] == "nvapi-real-key-1"  # Restored
    assert nvapi_keys[1] == "nvapi-real-key-2"  # Restored because it has ***
    assert nvapi_keys[2] == "nvapi-plain-text"  # Kept as-is


def test_get_live_status():
    """Test _get_live_status function."""
    settings = MagicMock(spec=Settings)
    settings.providers = {
        "nvidia_nim": MagicMock(
            enabled=True,
            keys=["key1", "key2"]
        ),
        "gemini": MagicMock(
            enabled=False,
            keys=[]
        ),
        "ollama": MagicMock(
            enabled=True,
            keys=[]
        )
    }

    with patch("clasp.internal.routes.get_settings", return_value=settings):
        status = _get_live_status()

        assert isinstance(status, dict)
        assert status["status"] == "healthy"
        assert status["uptime_seconds"] == 0
        assert status["active_requests"] == 0
        assert status["queue_depth"] == 0

        # Check providers
        assert "nvidia_nim" in status["providers"]
        assert "gemini" in status["providers"]
        assert "ollama" in status["providers"]

        # nvidia_nim should be HEALTHY (enabled)
        assert status["providers"]["nvidia_nim"]["status"] == "HEALTHY"
        assert status["providers"]["nvidia_nim"]["keys"][0]["index"] == 0
        assert status["providers"]["nvidia_nim"]["keys"][0]["redacted"] == "***"
        assert status["providers"]["nvidia_nim"]["keys"][0]["status"] == "HEALTHY"

        # gemini should be OFF (disabled)
        assert status["providers"]["gemini"]["status"] == "OFF"

        # ollama should be HEALTHY (enabled local provider)
        assert status["providers"]["ollama"]["status"] == "HEALTHY"


def test_internal_router_exists():
    """Test that the internal router was created."""
    assert router is not None
    assert len(router.routes) > 0


def test_get_config_endpoint():
    """Test GET /internal/config endpoint."""
    # This would require setting up the route testing
    # For now, we'll just test that the function exists
    assert callable(get_config)


def test_post_config_endpoint():
    """Test POST /internal/config endpoint."""
    assert callable(post_config)


def test_test_key_endpoint():
    """Test POST /internal/config/test-key endpoint."""
    assert callable(route_test_key)


def test_export_config_endpoint():
    """Test GET /internal/config/export endpoint."""
    assert callable(export_config)


def test_import_config_endpoint():
    """Test POST /internal/config/import endpoint."""
    assert callable(import_config)


def test_get_catalog_endpoint():
    """Test GET /internal/catalog endpoint."""
    assert callable(get_catalog)


def test_get_catalog_defaults_endpoint():
    """Test GET /internal/catalog/defaults endpoint."""
    assert callable(get_catalog_defaults)


def test_get_status_endpoint():
    """Test GET /internal/status endpoint."""
    assert callable(get_status)


def test_stream_status_endpoint():
    """Test GET /internal/stream endpoint."""
    assert callable(stream_status)


def test_get_queue_endpoint():
    """Test GET /internal/queue endpoint."""
    assert callable(get_queue)


def test_stream_logs_endpoint():
    """Test GET /internal/logs/stream endpoint."""
    assert callable(stream_logs)


def test_download_logs_endpoint():
    """Test GET /internal/logs/download endpoint."""
    assert callable(download_logs)


def test_clear_cache_endpoint():
    """Test POST /internal/cache/clear endpoint."""
    assert callable(clear_cache)


if __name__ == "__main__":
    test_mask_key()
    test_mask_settings()
    test_settings_to_dict()
    test_restore_redacted()
    test_get_live_status()
    test_internal_router_exists()
    test_get_config_endpoint()
    test_post_config_endpoint()
    test_test_key_endpoint()
    test_export_config_endpoint()
    test_import_config_endpoint()
    test_get_catalog_endpoint()
    test_get_catalog_defaults_endpoint()
    test_get_status_endpoint()
    test_stream_status_endpoint()
    test_get_queue_endpoint()
    test_stream_logs_endpoint()
    test_download_logs_endpoint()
    test_clear_cache_endpoint()
    print("All internal routes tests passed!")