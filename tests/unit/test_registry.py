"""
tests/unit/test_registry.py
=============================
Unit tests for clasp/providers/registry.py.

Run with:
    python -m unittest tests.unit.test_registry -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.config.settings import Settings, ServerConfig, RoutingConfig, ProviderConfig
from clasp.providers import registry
from clasp.providers.base import BaseProvider
from clasp.providers.nvidia_nim import NvidiaProvider
from clasp.providers.openai_transport import OpenAIChatTransport
from clasp.providers.anthropic_transport import AnthropicMessagesTransport


def _settings(
    provider_chain: list[str],
    providers: dict[str, ProviderConfig],
) -> Settings:
    return Settings(
        server=ServerConfig(),
        routing=RoutingConfig(),
        provider_chain=provider_chain,
        providers=providers,
    )


class TestBuildRegistryBasic(unittest.TestCase):
    def test_enabled_provider_with_keys_is_registered(self):
        settings = _settings(
            provider_chain=["nvidia_nim"],
            providers={"nvidia_nim": ProviderConfig(enabled=True, keys=["nvapi-abc123"])},
        )
        reg = registry.build_registry(settings)
        self.assertIn("nvidia_nim", reg.all_enabled())
        self.assertIsInstance(reg.get("nvidia_nim"), NvidiaProvider)

    def test_disabled_provider_is_skipped(self):
        settings = _settings(
            provider_chain=["nvidia_nim"],
            providers={"nvidia_nim": ProviderConfig(enabled=False, keys=["nvapi-abc"])},
        )
        reg = registry.build_registry(settings)
        self.assertEqual(reg.all_enabled(), [])
        self.assertIsNone(reg.get("nvidia_nim"))

    def test_enabled_provider_without_keys_is_skipped(self):
        settings = _settings(
            provider_chain=["nvidia_nim"],
            providers={"nvidia_nim": ProviderConfig(enabled=True, keys=[])},
        )
        reg = registry.build_registry(settings)
        self.assertEqual(reg.all_enabled(), [])

    def test_provider_missing_from_config_is_skipped(self):
        """provider_chain references a name with no matching providers entry."""
        settings = _settings(
            provider_chain=["gemini"],
            providers={},  # no config at all for "gemini"
        )
        reg = registry.build_registry(settings)
        self.assertEqual(reg.all_enabled(), [])

    def test_local_provider_without_keys_is_still_registered(self):
        """Ollama (tier='local') doesn't need API keys."""
        settings = _settings(
            provider_chain=["ollama"],
            providers={"ollama": ProviderConfig(enabled=True, keys=[])},
        )
        reg = registry.build_registry(settings)
        self.assertIn("ollama", reg.all_enabled())


class TestRegistrationOrder(unittest.TestCase):
    def test_registration_order_matches_provider_chain(self):
        settings = _settings(
            provider_chain=["nvidia_nim", "gemini", "cerebras"],
            providers={
                "nvidia_nim": ProviderConfig(enabled=True, keys=["k1"]),
                "gemini": ProviderConfig(enabled=True, keys=["k2"]),
                "cerebras": ProviderConfig(enabled=True, keys=["k3"]),
            },
        )
        reg = registry.build_registry(settings)
        self.assertEqual(reg.all_enabled(), ["nvidia_nim", "gemini", "cerebras"])

    def test_disabled_providers_dont_break_order(self):
        settings = _settings(
            provider_chain=["nvidia_nim", "gemini", "cerebras"],
            providers={
                "nvidia_nim": ProviderConfig(enabled=True, keys=["k1"]),
                "gemini": ProviderConfig(enabled=False, keys=["k2"]),
                "cerebras": ProviderConfig(enabled=True, keys=["k3"]),
            },
        )
        reg = registry.build_registry(settings)
        self.assertEqual(reg.all_enabled(), ["nvidia_nim", "cerebras"])


class TestTransportClassResolution(unittest.TestCase):
    def test_nvidia_nim_uses_concrete_class(self):
        settings = _settings(
            provider_chain=["nvidia_nim"],
            providers={"nvidia_nim": ProviderConfig(enabled=True, keys=["k"])},
        )
        reg = registry.build_registry(settings)
        self.assertIsInstance(reg.get("nvidia_nim"), NvidiaProvider)

    def test_gemini_falls_back_to_openai_transport(self):
        settings = _settings(
            provider_chain=["gemini"],
            providers={"gemini": ProviderConfig(enabled=True, keys=["k"])},
        )
        reg = registry.build_registry(settings)
        self.assertIsInstance(reg.get("gemini"), OpenAIChatTransport)

    def test_fireworks_falls_back_to_anthropic_transport(self):
        settings = _settings(
            provider_chain=["fireworks"],
            providers={"fireworks": ProviderConfig(enabled=True, keys=["k"])},
        )
        reg = registry.build_registry(settings)
        self.assertIsInstance(reg.get("fireworks"), AnthropicMessagesTransport)

    def test_unknown_provider_name_skipped_without_crashing(self):
        """
        A provider name not in PROVIDER_CATALOG has no base_url to construct
        with, so build_registry must skip it gracefully (catch + log) rather
        than raising — the rest of the registry build must still succeed.
        """
        settings = _settings(
            provider_chain=["totally_made_up_provider", "nvidia_nim"],
            providers={
                "totally_made_up_provider": ProviderConfig(enabled=True, keys=["k"]),
                "nvidia_nim": ProviderConfig(enabled=True, keys=["k2"]),
            },
        )
        # Must not raise.
        reg = registry.build_registry(settings)
        self.assertNotIn("totally_made_up_provider", reg.all_enabled())
        # A later, valid provider in the chain must still register successfully.
        self.assertIn("nvidia_nim", reg.all_enabled())


class TestKeyPoolStub(unittest.TestCase):
    """Sprint 1: get_key_pool always returns None."""

    def test_get_key_pool_returns_none_for_registered_provider(self):
        settings = _settings(
            provider_chain=["nvidia_nim"],
            providers={"nvidia_nim": ProviderConfig(enabled=True, keys=["k"])},
        )
        registry.build_registry(settings)
        self.assertIsNone(registry.get_key_pool("nvidia_nim"))

    def test_get_key_pool_returns_none_for_unregistered_provider(self):
        settings = _settings(provider_chain=[], providers={})
        registry.build_registry(settings)
        self.assertIsNone(registry.get_key_pool("nonexistent"))


class TestModuleLevelHelpers(unittest.TestCase):
    def test_get_returns_none_when_not_registered(self):
        settings = _settings(provider_chain=[], providers={})
        registry.build_registry(settings)
        self.assertIsNone(registry.get("nvidia_nim"))

    def test_get_returns_instance_when_registered(self):
        settings = _settings(
            provider_chain=["nvidia_nim"],
            providers={"nvidia_nim": ProviderConfig(enabled=True, keys=["k"])},
        )
        registry.build_registry(settings)
        self.assertIsInstance(registry.get("nvidia_nim"), BaseProvider)

    def test_all_enabled_module_level_matches_instance_method(self):
        settings = _settings(
            provider_chain=["nvidia_nim", "gemini"],
            providers={
                "nvidia_nim": ProviderConfig(enabled=True, keys=["k1"]),
                "gemini": ProviderConfig(enabled=True, keys=["k2"]),
            },
        )
        reg = registry.build_registry(settings)
        self.assertEqual(registry.all_enabled(), reg.all_enabled())


class TestRebuild(unittest.TestCase):
    def test_rebuild_replaces_previous_registrations(self):
        settings_a = _settings(
            provider_chain=["nvidia_nim"],
            providers={"nvidia_nim": ProviderConfig(enabled=True, keys=["k1"])},
        )
        registry.build_registry(settings_a)
        self.assertIn("nvidia_nim", registry.all_enabled())

        settings_b = _settings(
            provider_chain=["gemini"],
            providers={"gemini": ProviderConfig(enabled=True, keys=["k2"])},
        )
        registry.rebuild(settings_b)

        self.assertNotIn("nvidia_nim", registry.all_enabled())
        self.assertIn("gemini", registry.all_enabled())

    def test_rebuild_with_empty_chain_clears_everything(self):
        settings_a = _settings(
            provider_chain=["nvidia_nim"],
            providers={"nvidia_nim": ProviderConfig(enabled=True, keys=["k1"])},
        )
        registry.build_registry(settings_a)
        self.assertGreater(len(registry.all_enabled()), 0)

        registry.rebuild(_settings(provider_chain=[], providers={}))
        self.assertEqual(registry.all_enabled(), [])


class TestProviderRegistryClassDirectly(unittest.TestCase):
    """Test the ProviderRegistry class API directly, not via module singleton."""

    def test_len_reflects_registered_count(self):
        reg = registry.ProviderRegistry()
        self.assertEqual(len(reg), 0)
        reg._register("nvidia_nim", NvidiaProvider())
        self.assertEqual(len(reg), 1)

    def test_clear_resets_state(self):
        reg = registry.ProviderRegistry()
        reg._register("nvidia_nim", NvidiaProvider())
        reg._clear()
        self.assertEqual(len(reg), 0)
        self.assertEqual(reg.all_enabled(), [])

    def test_register_same_name_twice_doesnt_duplicate_order_entry(self):
        reg = registry.ProviderRegistry()
        reg._register("nvidia_nim", NvidiaProvider())
        reg._register("nvidia_nim", NvidiaProvider())
        self.assertEqual(reg.all_enabled().count("nvidia_nim"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)