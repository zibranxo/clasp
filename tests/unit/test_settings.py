"""
tests/unit/test_settings.py
===========================
Unit tests for clasp.config.settings.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.config.settings import Settings, get_settings, _get_config_path
from clasp.config.provider_catalog import PROVIDER_CATALOG


def test_get_config_path_default():
    """Test getting default config path."""
    # Temporarily remove CLASP_CONFIG_PATH if set
    old_val = os.environ.pop("CLASP_CONFIG_PATH", None)
    try:
        path = _get_config_path()
        expected = Path.home() / ".clasp" / "config.yaml"
        assert path == expected
    finally:
        if old_val is not None:
            os.environ["CLASP_CONFIG_PATH"] = old_val


def test_get_config_path_from_env():
    """Test getting config path from environment variable."""
    custom_path = "/custom/path/config.yaml"
    old_val = os.environ.get("CLASP_CONFIG_PATH")
    os.environ["CLASP_CONFIG_PATH"] = custom_path
    try:
        path = _get_config_path()
        assert path == Path(custom_path)
    finally:
        if old_val is not None:
            os.environ["CLASP_CONFIG_PATH"] = old_val
        else:
            os.environ.pop("CLASP_CONFIG_PATH", None)


def test_settings_defaults():
    """Test that Settings provides sensible defaults."""
    settings = Settings()

    # Server defaults
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 8082
    assert settings.server.api_key == "freecc"
    assert settings.server.request_timeout_seconds == 300
    assert settings.server.max_queue_depth == 50
    assert settings.server.max_queue_wait_seconds == 180
    assert settings.server.log_level == "INFO"
    assert settings.server.live_tui is False

    # Routing defaults
    assert settings.routing.strategy == "priority-chain"
    assert settings.routing.models.opus == "nvidia_nim/moonshotai/kimi-k2-thinking"
    assert settings.routing.models.sonnet == "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct"
    assert settings.routing.models.haiku == "cerebras/llama3.1-8b"
    assert settings.routing.models.fable == "gemini/models/gemini-2.5-flash"
    assert settings.routing.models.default == "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct"

    # Provider defaults (all disabled except ollama)
    assert settings.provider_chain == [
        "nvidia_nim",
        "gemini",
        "cerebras",
        "groq",
        "openrouter",
        "ollama",
    ]
    assert settings.providers["nvidia_nim"].enabled is False
    assert settings.providers["ollama"].enabled is True  # Ollama enabled by default
    assert settings.providers["ollama"].base_url == "http://localhost:11434"

    # Cache defaults
    assert settings.cache.enabled is True
    assert settings.cache.memory_max_entries == 500
    assert settings.cache.sqlite_ttl_seconds == 300
    assert settings.cache.sqlite_path == "~/.clasp/cache.db"

    # Optimizer defaults
    assert settings.optimizer.local_probe_answering is True
    assert settings.optimizer.system_prompt_dedup is True
    assert settings.optimizer.context_pruning.enabled is True
    assert settings.optimizer.context_pruning.strategy == "keep_edges"
    assert settings.optimizer.context_pruning.keep_first == 3
    assert settings.optimizer.context_pruning.keep_last == 10

    # Shared pool defaults
    assert settings.shared_pool.enabled is False
    assert settings.shared_pool.auth_tokens == []
    assert settings.shared_pool.per_user_rpm_limit == 20


def test_settings_enabled_providers():
    """Test enabled_providers() method."""
    settings = Settings()

    # Initially, only ollama should be enabled (from defaults)
    enabled = settings.enabled_providers()
    assert enabled == ["ollama"]

    # Enable a provider
    settings.providers["nvidia_nim"].enabled = True
    settings.providers["nvidia_nim"].keys = ["test-key-1", "test-key-2"]

    enabled = settings.enabled_providers()
    assert "nvidia_nim" in enabled
    assert "ollama" in enabled
    assert len(enabled) == 2

    # Disable ollama
    settings.providers["ollama"].enabled = False
    enabled = settings.enabled_providers()
    assert enabled == ["nvidia_nim"]


def test_settings_provider_chain_filtering():
    """Test that enabled_providers respects provider_chain order."""
    settings = Settings()

    # Custom provider chain
    settings.provider_chain = ["gemini", "nvidia_nim", "ollama"]

    # Enable all
    settings.providers["gemini"].enabled = True
    settings.providers["nvidia_nim"].enabled = True
    settings.providers["ollama"].enabled = True

    # Add keys
    settings.providers["gemini"].keys = ["gemini-key"]
    settings.providers["nvidia_nim"].keys = ["nim-key"]
    settings.providers["ollama"].keys = []  # Local provider doesn't need keys

    enabled = settings.enabled_providers()
    assert enabled == ["gemini", "nvidia_nim", "ollama"]  # Order from provider_chain


def test_settings_local_provider_without_keys():
    """Test that local providers work without API keys."""
    settings = Settings()
    settings.providers["ollama"].enabled = True
    settings.providers["ollama"].keys = []  # No keys for local provider

    enabled = settings.enabled_providers()
    assert "ollama" in enabled  # Should still be enabled


def test_settings_non_local_provider_requires_keys():
    """Test that non-local providers require API keys."""
    settings = Settings()
    settings.providers["nvidia_nim"].enabled = True
    settings.providers["nvidia_nim"].keys = []  # No keys

    enabled = settings.enabled_providers()
    assert "nvidia_nim" not in enabled  # Should be filtered out


def test_settings_environment_variable_override():
    """Test that environment variables override settings."""
    old_port = os.environ.get("CLASP_PORT")
    old_key = os.environ.get("CLASP_API_KEY")
    old_log = os.environ.get("CLASP_LOG_LEVEL")

    try:
        os.environ["CLASP_PORT"] = "9999"
        os.environ["CLASP_API_KEY"] = "test-key-from-env"
        os.environ["CLASP_LOG_LEVEL"] = "DEBUG"

        settings = Settings()

        assert settings.server.port == 9999
        assert settings.server.api_key == "test-key-from-env"
        assert settings.server.log_level == "DEBUG"

    finally:
        # Restore environment
        if old_port is not None:
            os.environ["CLASP_PORT"] = old_port
        else:
            os.environ.pop("CLASP_PORT", None)

        if old_key is not None:
            os.environ["CLASP_API_KEY"] = old_key
        else:
            os.environ.pop("CLASP_API_KEY", None)

        if old_log is not None:
            os.environ["CLASP_LOG_LEVEL"] = old_log
        else:
            os.environ.pop("CLASP_LOG_LEVEL", None)


def test_settings_provider_key_injection_from_env():
    """Test that provider API keys are injected from environment variables."""
    old_nim_key = os.environ.get("NVIDIA_NIM_API_KEY")
    old_gemini_key = os.environ.get("GEMINI_API_KEY")

    try:
        os.environ["NVIDIA_NIM_API_KEY"] = "nim-key-1,nim-key-2"
        os.environ["GEMINI_API_KEY"] = "gemini-key-1"

        settings = Settings()

        # NVIDIA NIM should have keys injected
        assert settings.providers["nvidia_nim"].enabled is True
        assert settings.providers["nvidia_nim"].keys == ["nim-key-1", "nim-key-2"]

        # Gemini should have key injected
        assert settings.providers["gemini"].enabled is True
        assert settings.providers["gemini"].keys == ["gemini-key-1"]

    finally:
        # Restore environment
        if old_nim_key is not None:
            os.environ["NVIDIA_NIM_API_KEY"] = old_nim_key
        else:
            os.environ.pop("NVIDIA_NIM_API_KEY", None)

        if old_gemini_key is not None:
            os.environ["GEMINI_API_KEY"] = old_gemini_key
        else:
            os.environ.pop("GEMINI_API_KEY", None)


def test_settings_provider_key_injection_deduplication():
    """Test that provider key injection deduplicates while preserving order."""
    old_nim_key = os.environ.get("NVIDIA_NIM_API_KEY")

    try:
        # Set up existing keys in config
        settings = Settings()
        settings.providers["nvidia_nim"].enabled = True
        settings.providers["nvidia_nim"].keys = ["existing-key-1", "existing-key-2"]

        # Inject keys from environment (some duplicates)
        os.environ["NVIDIA_NIM_API_KEY"] = "existing-key-2,new-key-1,existing-key-1,new-key-2"

        # Re-initialize settings to trigger env var processing
        settings = Settings()

        # Should have deduplicated keys, preserving original order then adding new ones
        expected = ["existing-key-1", "existing-key-2", "new-key-1", "new-key-2"]
        assert settings.providers["nvidia_nim"].keys == expected

    finally:
        if old_nim_key is not None:
            os.environ["NVIDIA_NIM_API_KEY"] = old_nim_key
        else:
            os.environ.pop("NVIDIA_NIM_API_KEY", None)


def test_settings_get_settings_singleton():
    """Test that get_settings returns the same instance."""
    # Clear cache first
    get_settings.cache_clear()

    settings1 = get_settings()
    settings2 = get_settings()

    assert settings1 is settings2  # Same object due to lru_cache

    # Clear cache and verify we can get a new one
    get_settings.cache_clear()
    settings3 = get_settings()
    assert settings3 is not settings1  # New instance after cache clear
    assert isinstance(settings3, Settings)


def test_settings_yaml_loading():
    """Test loading settings from YAML file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("""
server:
  host: "127.0.0.1"
  port: 9999
  api_key: "yaml-test-key"
  log_level: "DEBUG"

providers:
  nvidia_nim:
    enabled: true
    keys:
      - "yaml-nim-key"
  ollama:
    enabled: false
""")
        yaml_path = f.name

    try:
        old_env = os.environ.get("CLASP_CONFIG_PATH")
        os.environ["CLASP_CONFIG_PATH"] = yaml_path

        # Clear cache and load settings
        get_settings.cache_clear()
        settings = get_settings()

        assert settings.server.host == "127.0.0.1"
        assert settings.server.port == 9999
        assert settings.server.api_key == "yaml-test-key"
        assert settings.server.log_level == "DEBUG"

        assert settings.providers["nvidia_nim"].enabled is True
        assert settings.providers["nvidia_nim"].keys == ["yaml-nim-key"]
        assert settings.providers["ollama"].enabled is False

    finally:
        # Cleanup
        if old_env is not None:
            os.environ["CLASP_CONFIG_PATH"] = old_env
        else:
            os.environ.pop("CLASP_CONFIG_PATH", None)
        get_settings.cache_clear()
        Path(yaml_path).unlink(missing_ok=True)


def test_settings_invalid_yaml_handled_gracefully():
    """Test that invalid YAML doesn't crash settings loading."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write("invalid: yaml: : :")
        yaml_path = f.name

    try:
        old_env = os.environ.get("CLASP_CONFIG_PATH")
        os.environ["CLASP_CONFIG_PATH"] = yaml_path

        # Clear cache and load settings - should fall back to defaults
        get_settings.cache_clear()
        settings = get_settings()

        # Should have default values since YAML parsing failed
        assert settings.server.port == 8082  # Default
        assert settings.server.api_key == "freecc"  # Default

    finally:
        if old_env is not None:
            os.environ["CLASP_CONFIG_PATH"] = old_env
        else:
            os.environ.pop("CLASP_CONFIG_PATH", None)
        get_settings.cache_clear()
        Path(yaml_path).unlink(missing_ok=True)


if __name__ == "__main__":
    test_get_config_path_default()
    test_get_config_path_from_env()
    test_settings_defaults()
    test_settings_enabled_providers()
    test_settings_provider_chain_filtering()
    test_settings_local_provider_without_keys()
    test_settings_non_local_provider_requires_keys()
    test_settings_environment_variable_override()
    test_settings_provider_key_injection_from_env()
    test_settings_provider_key_injection_deduplication()
    test_settings_get_settings_singleton()
    test_settings_yaml_loading()
    test_settings_invalid_yaml_handled_gracefully()
    print("All settings tests passed!")