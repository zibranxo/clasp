"""
tests/unit/test_writer.py
=========================
Unit tests for clasp.config.writer.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.config.writer import (
    settings_to_dict,
    mask_keys,
    restore_keys,
    write_config,
    write_config_dict,
    _mask_key_string,
    mask_keys,
    restore_keys,
)
from clasp.config.settings import Settings


def test_settings_to_dict():
    """Test converting Settings to dict."""
    settings = Settings()
    # Modify some values for testing
    settings.server.port = 9999
    settings.server.api_key = "test-key"

    result = settings_to_dict(settings)

    assert isinstance(result, dict)
    assert result["server"]["port"] == 9999
    assert result["server"]["api_key"] == "test-key"
    assert result["server"]["host"] == "127.0.0.1"  # Default


def test_mask_key_string_basic():
    """Test _mask_key_string function."""
    # Test normal key
    assert _mask_key_string("nvapi-abcdefghijklmnop") == "nvapi-***mnop"

    # Test short key
    assert _mask_key_string("abc") == "***"
    assert _mask_key_string("abcd") == "***"
    assert _mask_key_string("abcde") == "***"

    # Test key with dash
    assert _mask_key_string("sk-abcdefgh") == "sk-***efgh"
    assert _mask_key_string("sk-ab") == "***"  # Too short

    # Test already masked key
    assert _mask_key_string("nvapi-***hijk") == "nvapi-***hijk"

    # Test empty key
    assert _mask_key_string("") == ""
    assert _mask_key_string(None) is None  # type: ignore


def test_mask_keys():
    """Test masking API keys in settings."""
    settings = Settings()
    # Set up some test keys
    settings.providers["nvidia_nim"].enabled = True
    settings.providers["nvidia_nim"].keys = [
        "nvapi-abcdefghijklmnopqrstuvwxyz",
        "nvapi-abcdefghijk",
        "short",
    ]
    settings.providers["gemini"].enabled = True
    settings.providers["gemini"].keys = ["AIzaSyAbcdefghijklmnopqrstuvwxyz1234"]

    masked = mask_keys(settings)

    # Check that keys are masked
    nvapi_keys = masked["providers"]["nvidia_nim"]["keys"]
    gemini_keys = masked["providers"]["gemini"]["keys"]

    assert nvapi_keys[0] == "nvapi-***wxyz"
    assert nvapi_keys[1] == "nvapi-***hijk"
    assert nvapi_keys[2] == "***"  # Short key
    assert gemini_keys[0] == "AIza-***1234"  # Last 4 chars
    # Actually it should be last 4: "234" but the key is longer
    # "AIzaSyAbcdefghijklmnopqrstuvwxyz1234" -> last 4 is "234"
    # So should be "AIzaSyAbcdefghijklmnopqrstuvwxyz***234"? No, let me recheck the logic

    # Looking at the implementation: it keeps last 4 chars
    # So "AIzaSyAbcdefghijklmnopqrstuvwxyz1234" -> last 4 is "234"
    # But wait, that's only 3 chars. Let me recount...
    # Actually the key in the test is "AIzaSyAbcdefghijklmnopqrstuvwxyz1234"
    # That's: AIzaSy (6) + Abcdefghijklmnopqrstuvwxyz (26) + 1234 (4) = 36 chars
    # Last 4: "234" is only 3? No, "1234" is 4 chars
    # So last 4 is "234"? No, it's "1234"
    # So result should be something like "AIzaSy...***234" but let me check actual impl

    # From implementation: suffix = key[-_KEEP_SUFFIX_LEN:] where _KEEP_SUFFIX_LEN = 4
    # So suffix = "1234"
    # Then it tries to find a dash for prefix
    # No dash in "AIzaSyAbcdefghijklmnopqrstuvwxyz1234", so prefix = key[:4] + "-" = "AIza" + "-" = "AIza-"
    # Result: "AIza-***1234"
    assert gemini_keys[0] == "AIza-***1234"


def test_restore_keys():
    """Test restoring masked keys from UI."""
    # Create original settings with real keys
    original = Settings()
    original.providers["nvidia_nim"].enabled = True
    original.providers["nvidia_nim"].keys = [
        "nvapi-real-key-1",
        "nvapi-real-key-2",
    ]
    original.providers["gemini"].enabled = True
    original.providers["gemini"].keys = ["AIza-real-key"]

    # Create incoming (from UI) with masked keys
    incoming = {
        "providers": {
            "nvidia_nim": {
                "enabled": True,
                "keys": [
                    "nvapi-***ey-1",  # Masked version of nvapi-real-key-1
                    "nvapi-***ey-2",  # Masked version of nvapi-real-key-2
                    "nvapi-***new",   # New key from UI
                ],
            },
            "gemini": {
                "enabled": True,
                "keys": ["AIza-***key"],  # Masked version of AIza-real-key
            },
        }
    }

    # Restore keys
    restored = restore_keys(incoming, original)

    # Check that masked keys were restored to real values
    nvapi_keys = restored["providers"]["nvidia_nim"]["keys"]
    gemini_keys = restored["providers"]["gemini"]["keys"]

    assert nvapi_keys[0] == "nvapi-real-key-1"
    assert nvapi_keys[1] == "nvapi-real-key-2"
    assert nvapi_keys[2] == "nvapi-***new"  # New key not in original, kept as-is
    assert gemini_keys[0] == "AIza-real-key"


def test_restore_keys_missing_original():
    """Test restoring when original doesn't have enough keys."""
    original = Settings()
    original.providers["nvidia_nim"].enabled = True
    original.providers["nvidia_nim"].keys = ["nvapi-real-key-1"]  # Only one key

    incoming = {
        "providers": {
            "nvidia_nim": {
                "enabled": True,
                "keys": [
                    "nvapi-***ey-1",  # Matches original
                    "nvapi-***ey-2",  # No match in original
                    "nvapi-***ey-3",  # No match in original
                ],
            },
        }
    }

    restored = restore_keys(incoming, original)

    nvapi_keys = restored["providers"]["nvidia_nim"]["keys"]
    assert nvapi_keys[0] == "nvapi-real-key-1"  # Restored
    assert nvapi_keys[1] == "nvapi-***ey-2"  # Not restored, kept masked
    assert nvapi_keys[2] == "nvapi-***ey-3"  # Not restored, kept masked


def test_restore_keys_non_masked_kept():
    """Test that non-masked keys from UI are kept as-is."""
    original = Settings()
    original.providers["nvidia_nim"].enabled = True
    original.providers["nvidia_nim"].keys = ["nvapi-real-key"]

    incoming = {
        "providers": {
            "nvidia_nim": {
                "enabled": True,
                "keys": [
                    "nvapi-***ey",  # Masked - should be restored
                    "nvapi-new-key-from-ui",  # Not masked - should be kept
                ],
            },
        }
    }

    restored = restore_keys(incoming, original)

    nvapi_keys = restored["providers"]["nvidia_nim"]["keys"]
    assert nvapi_keys[0] == "nvapi-real-key"  # Restored
    assert nvapi_keys[1] == "nvapi-new-key-from-ui"  # Kept as-is


def test_mask_settings():
    """Test _mask_settings function."""
    settings = Settings()
    settings.providers["nvidia_nim"].enabled = True
    settings.providers["nvidia_nim"].keys = ["nvapi-test-key"]
    settings.providers["gemini"].enabled = True
    settings.providers["gemini"].keys = ["AIza-test-key"]

    masked = mask_keys(settings)

    nvapi_keys = masked["providers"]["nvidia_nim"]["keys"]
    gemini_keys = masked["providers"]["gemini"]["keys"]

    assert nvapi_keys[0] == "nvapi-***-key"
    assert gemini_keys[0] == "AIza-***-key"


def test_restore_redacted():
    """Test _restore_redacted function."""
    original = Settings()
    original.providers["nvidia_nim"].enabled = True
    original.providers["nvidia_nim"].keys = ["nvapi-real-key-1", "nvapi-real-key-2"]

    incoming = {
        "providers": {
            "nvidia_nim": {
                "keys": [
                    "nvapi-***ey-1",  # Should restore to real-key-1
                    "nvapi-***ey-2",  # Should restore to real-key-2
                    "nvapi-***new",   # No original, keep masked
                ],
            },
        }
    }

    restored = restore_keys(incoming, original)

    nvapi_keys = restored["providers"]["nvidia_nim"]["keys"]
    assert nvapi_keys[0] == "nvapi-real-key-1"
    assert nvapi_keys[1] == "nvapi-real-key-2"
    assert nvapi_keys[2] == "nvapi-***new"


def test_write_config_dict():
    """Test writing config from dict."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "test.yaml"

        data = {
            "server": {
                "port": 9999,
                "api_key": "test-key",
            },
            "providers": {
                "nvidia_nim": {
                    "enabled": True,
                    "keys": ["test-key-1"],
                },
            },
        }

        # This should not raise
        write_config_dict(data, config_path)

        # Check file was created
        assert config_path.exists()

        # Check content
        content = config_path.read_text()
        assert "port: 9999" in content
        assert "api_key: test-key" in content
        assert "enabled: true" in content
        assert "test-key-1" in content


def test_write_config():
    """Test writing config from Settings object."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "test.yaml"

        settings = Settings()
        settings.server.port = 8888
        settings.server.api_key = "settings-test-key"
        settings.providers["nvidia_nim"].enabled = True
        settings.providers["nvidia_nim"].keys = ["settings-nim-key"]

        # This should not raise
        write_config(settings, config_path)

        # Check file was created
        assert config_path.exists()

        # Check content
        content = config_path.read_text()
        assert "port: 8888" in content
        assert "api_key: settings-test-key" in content
        assert "enabled: true" in content
        assert "settings-nim-key" in content


if __name__ == "__main__":
    test_settings_to_dict()
    test_mask_key_string_basic()
    test_mask_keys()
    test_restore_keys()
    test_restore_keys_missing_original()
    test_restore_keys_non_masked_kept()
    test_mask_settings()
    test_restore_redacted()
    test_write_config_dict()
    test_write_config()
    print("All writer tests passed!")