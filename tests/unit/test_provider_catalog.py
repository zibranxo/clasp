"""
tests/unit/test_provider_catalog.py
===================================
Unit tests for clasp.config.provider_catalog.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.config.provider_catalog import ProviderProfile, PROVIDER_CATALOG, get_profile


def test_provider_profile_creation():
    """Test creating a ProviderProfile."""
    profile = ProviderProfile(
        display_name="Test Provider",
        base_url="https://test.example.com/v1",
        transport="openai_chat",
        rpm_limit=100,
        tpm_limit=10000,
        daily_token_limit=1000000,
        rpm_soft_threshold=0.8,
        cooldown_seconds=60,
        backoff_base_seconds=30,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=True,
        max_context_tokens=32768,
        tier="free",
        free_tier_note="Test note",
    )

    assert profile.display_name == "Test Provider"
    assert profile.base_url == "https://test.example.com/v1"
    assert profile.transport == "openai_chat"
    assert profile.rpm_limit == 100
    assert profile.tpm_limit == 10000
    assert profile.daily_token_limit == 1000000
    assert profile.rpm_soft_threshold == 0.8
    assert profile.cooldown_seconds == 60
    assert profile.backoff_base_seconds == 30
    assert profile.supports_tools is True
    assert profile.supports_vision is False
    assert profile.supports_thinking is True
    assert profile.max_context_tokens == 32768
    assert profile.tier == "free"
    assert profile.free_tier_note == "Test note"


def test_provider_profile_frozen():
    """Test that ProviderProfile is frozen (immutable)."""
    profile = ProviderProfile(
        display_name="Test",
        base_url="https://test.com",
        transport="openai_chat",
        rpm_limit=10,
        tpm_limit=None,
        daily_token_limit=None,
        rpm_soft_threshold=0.8,
        cooldown_seconds=60,
        backoff_base_seconds=30,
        supports_tools=True,
        supports_vision=True,
        supports_thinking=False,
        max_context_tokens=4096,
        tier="free",
        free_tier_note="test",
    )

    # Should not be able to modify fields
    try:
        profile.display_name = "Modified"
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass  # Expected


def test_provider_catalog_has_required_providers():
    """Test that PROVIDER_CATALOG contains all expected providers."""
    expected_providers = {
        "nvidia_nim",
        "gemini",
        "cerebras",
        "groq",
        "openrouter",
        "mistral",
        "together",
        "fireworks",
        "ollama",
        "lm_studio",
        "mistral_codestral",
        "deepseek",
        "kimi",
        "llamacpp",
        "opencode",
        "opencode_go",
        "wafer",
        "zai",
    }

    assert set(PROVIDER_CATALOG.keys()) == expected_providers


def test_nvidia_nim_profile():
    """Test NVIDIA NIM provider profile."""
    profile = PROVIDER_CATALOG["nvidia_nim"]

    assert profile.display_name == "NVIDIA NIM"
    assert profile.base_url == "https://integrate.api.nvidia.com/v1"
    assert profile.transport == "openai_chat"
    assert profile.rpm_limit == 40
    assert profile.tpm_limit is None
    assert profile.daily_token_limit is None
    assert profile.rpm_soft_threshold == 0.80
    assert profile.cooldown_seconds == 60
    assert profile.backoff_base_seconds == 60
    assert profile.supports_tools is True
    assert profile.supports_vision is False
    assert profile.supports_thinking is True
    assert profile.max_context_tokens == 128_000
    assert profile.tier == "free"
    assert profile.free_tier_note == "40 RPM free • build.nvidia.com"


def test_gemini_profile():
    """Test Gemini provider profile."""
    profile = PROVIDER_CATALOG["gemini"]

    assert profile.display_name == "Google Gemini AI Studio"
    assert profile.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert profile.transport == "openai_chat"
    assert profile.rpm_limit == 15
    assert profile.tpm_limit == 1_000_000
    assert profile.daily_token_limit == 1_000_000
    assert profile.rpm_soft_threshold == 0.80
    assert profile.cooldown_seconds == 60
    assert profile.backoff_base_seconds == 60
    assert profile.supports_tools is True
    assert profile.supports_vision is True
    assert profile.supports_thinking is True
    assert profile.max_context_tokens == 1_000_000
    assert profile.tier == "free"
    assert profile.free_tier_note == "15 RPM free • 1 M tokens/day • aistudio.google.com"


def test_ollama_profile():
    """Test Ollama (local) provider profile."""
    profile = PROVIDER_CATALOG["ollama"]

    assert profile.display_name == "Ollama (local)"
    assert profile.base_url == "http://localhost:11434"
    assert profile.transport == "anthropic_messages"
    assert profile.rpm_limit == 9_999  # Artificially high
    assert profile.tpm_limit is None
    assert profile.daily_token_limit is None
    assert profile.rpm_soft_threshold == 1.0  # Never back off
    assert profile.cooldown_seconds == 0
    assert profile.backoff_base_seconds == 0
    assert profile.supports_tools is True
    assert profile.supports_vision is False
    assert profile.supports_thinking is False
    assert profile.max_context_tokens == 32_768
    assert profile.tier == "local"
    assert profile.free_tier_note == "Unlimited • Runs on your machine • ollama.com"


def test_fireworks_profile():
    """Test Fireworks provider profile."""
    profile = PROVIDER_CATALOG["fireworks"]

    assert profile.display_name == "Fireworks AI"
    assert profile.base_url == "https://api.fireworks.ai/inference/v1/messages"
    assert profile.transport == "anthropic_messages"  # Different transport
    assert profile.rpm_limit == 600
    assert profile.tpm_limit is None
    assert profile.daily_token_limit is None
    assert profile.rpm_soft_threshold == 0.80
    assert profile.cooldown_seconds == 60
    assert profile.backoff_base_seconds == 60
    assert profile.supports_tools is True
    assert profile.supports_vision is False
    assert profile.supports_thinking is False
    assert profile.max_context_tokens == 131_072
    assert profile.tier == "free_credits"
    assert profile.free_tier_note == "Free credits on signup • fireworks.ai"


def test_get_profile_existing():
    """Test get_profile with existing provider."""
    profile = get_profile("nvidia_nim")
    assert profile is PROVIDER_CATALOG["nvidia_nim"]
    assert profile.display_name == "NVIDIA NIM"


def test_get_profile_unknown():
    """Test get_profile with unknown provider raises KeyError."""
    try:
        get_profile("unknown_provider")
        assert False, "Should have raised KeyError"
    except KeyError as e:
        assert "unknown_provider" in str(e)
        assert "Known providers:" in str(e)


def test_provider_catalog_values_are_frozen():
    """Test that ProviderProfile instances in catalog are frozen."""
    profile = PROVIDER_CATALOG["nvidia_nim"]

    try:
        profile.rpm_limit = 50
        assert False, "Should have raised FrozenInstanceError"
    except Exception:
        pass  # Expected


if __name__ == "__main__":
    test_provider_profile_creation()
    test_provider_profile_frozen()
    test_provider_catalog_has_required_providers()
    test_nvidia_nim_profile()
    test_gemini_profile()
    test_ollama_profile()
    test_fireworks_profile()
    test_get_profile_existing()
    test_get_profile_unknown()
    test_provider_catalog_values_are_frozen()
    print("All provider catalog tests passed!")