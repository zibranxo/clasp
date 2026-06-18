"""
tests/unit/test_model_map.py
================================
Tests for `clasp/router/model_map.py`'s `resolve_model()`. Covers the
three-step matching order from plan.md §13 (by_type override → tier
keyword → default), the "provider mismatch returns None" rule, the
`claude-fable-*` example from the spec, and the `resolve` alias.
"""

import unittest

from clasp.router.model_map import resolve_model, resolve
from clasp.router.selector import AnthropicRequest
from clasp.api.detect import RequestType
from clasp.config.settings import Settings, RoutingConfig


def _req(model="claude-sonnet-4-5-20250929", request_type=RequestType.INTERACTIVE):
    return AnthropicRequest(type=request_type, model=model)


def _settings(models=None, by_type=None):
    return Settings(routing=RoutingConfig(
        models=models or {}, by_type=by_type or {},
    ))


class TestByTypeOverride(unittest.TestCase):
    def test_by_type_override_wins_for_matching_provider(self):
        settings = _settings(by_type={"think": "nvidia_nim/moonshotai/kimi-k2-thinking"})
        request = _req(request_type=RequestType.THINK)
        self.assertEqual(
            resolve_model(request, "nvidia_nim", settings),
            "moonshotai/kimi-k2-thinking",
        )

    def test_by_type_override_returns_none_for_non_matching_provider_with_no_fallback(self):
        settings = _settings(by_type={"think": "nvidia_nim/moonshotai/kimi-k2-thinking"})
        request = _req(model="claude-haiku-4-5", request_type=RequestType.THINK)
        # gemini isn't the by_type target, and the model name doesn't match
        # any configured tier, and there's no "default" entry either.
        self.assertIsNone(resolve_model(request, "gemini", settings))

    def test_by_type_override_falls_through_to_tier_for_other_providers(self):
        # think -> nvidia_nim, but if we ask about cerebras and the model
        # name itself matches a tier mapped to cerebras, that should still work.
        settings = _settings(
            models={"haiku": "cerebras/llama3.1-8b"},
            by_type={"think": "nvidia_nim/moonshotai/kimi-k2-thinking"},
        )
        request = _req(model="claude-haiku-4-5", request_type=RequestType.THINK)
        self.assertEqual(resolve_model(request, "cerebras", settings), "llama3.1-8b")

    def test_no_by_type_entry_for_this_type_falls_through(self):
        settings = _settings(
            models={"default": "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct"},
            by_type={"vision": "gemini/models/gemini-2.5-flash"},
        )
        # TOOL_USE has no by_type entry in this config -> falls through to default.
        request = _req(model="claude-sonnet-4-5", request_type=RequestType.TOOL_USE)
        self.assertEqual(
            resolve_model(request, "nvidia_nim", settings),
            "nvidia/llama-3.1-nemotron-70b-instruct",
        )


class TestTierKeywordMatch(unittest.TestCase):
    def test_opus_keyword_match(self):
        settings = _settings(models={"opus": "nvidia_nim/moonshotai/kimi-k2-thinking"})
        request = _req(model="claude-opus-4-5-20250929")
        self.assertEqual(resolve_model(request, "nvidia_nim", settings), "moonshotai/kimi-k2-thinking")

    def test_sonnet_keyword_match(self):
        settings = _settings(models={"sonnet": "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct"})
        request = _req(model="claude-sonnet-4-5-20250929")
        self.assertEqual(
            resolve_model(request, "nvidia_nim", settings),
            "nvidia/llama-3.1-nemotron-70b-instruct",
        )

    def test_haiku_keyword_match(self):
        settings = _settings(models={"haiku": "cerebras/llama3.1-8b"})
        request = _req(model="claude-haiku-4-5")
        self.assertEqual(resolve_model(request, "cerebras", settings), "llama3.1-8b")

    def test_fable_matches_any_model_name_containing_fable(self):
        # plan.md's explicit example: "claude-fable-* matches any model name
        # containing the string 'fable'".
        settings = _settings(models={"fable": "gemini/models/gemini-2.5-flash"})
        request = _req(model="claude-fable-2099-some-future-name")
        self.assertEqual(resolve_model(request, "gemini", settings), "models/gemini-2.5-flash")

    def test_tier_match_wrong_provider_returns_none_without_default(self):
        settings = _settings(models={"sonnet": "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct"})
        request = _req(model="claude-sonnet-4-5")
        self.assertIsNone(resolve_model(request, "gemini", settings))

    def test_canonical_tiers_checked_before_custom_tiers(self):
        # A custom tier key happens to also appear as a substring; canonical
        # tiers (opus/sonnet/haiku/fable) are still checked first per the
        # documented fixed order.
        settings = _settings(models={
            "sonnet": "nvidia_nim/some-sonnet-model",
            "custom_tier": "gemini/some-other-model",
        })
        request = _req(model="claude-sonnet-4-5")
        self.assertEqual(resolve_model(request, "nvidia_nim", settings), "some-sonnet-model")


class TestDefaultFallback(unittest.TestCase):
    def test_unmatched_model_name_uses_default(self):
        settings = _settings(models={"default": "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct"})
        request = _req(model="some-totally-custom-model-name")
        self.assertEqual(
            resolve_model(request, "nvidia_nim", settings),
            "nvidia/llama-3.1-nemotron-70b-instruct",
        )

    def test_default_for_wrong_provider_returns_none(self):
        settings = _settings(models={"default": "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct"})
        request = _req(model="some-totally-custom-model-name")
        self.assertIsNone(resolve_model(request, "gemini", settings))

    def test_no_mapping_at_all_returns_none(self):
        settings = _settings(models={})
        request = _req(model="claude-sonnet-4-5")
        self.assertIsNone(resolve_model(request, "nvidia_nim", settings))


class TestProviderSlugSplitting(unittest.TestCase):
    def test_model_slug_with_internal_slashes_preserved(self):
        # Only the FIRST "/" should split provider from slug.
        settings = _settings(models={"opus": "nvidia_nim/moonshotai/kimi-k2-thinking"})
        request = _req(model="claude-opus-4-5")
        self.assertEqual(resolve_model(request, "nvidia_nim", settings), "moonshotai/kimi-k2-thinking")

    def test_deeply_nested_slug_path(self):
        settings = _settings(by_type={"vision": "openrouter/google/gemini-2.5-flash-preview:free"})
        request = _req(model="claude-sonnet-4-5", request_type=RequestType.VISION)
        self.assertEqual(
            resolve_model(request, "openrouter", settings),
            "google/gemini-2.5-flash-preview:free",
        )


class TestResolveAlias(unittest.TestCase):
    def test_resolve_alias_matches_resolve_model(self):
        settings = _settings(models={"haiku": "cerebras/llama3.1-8b"})
        request = _req(model="claude-haiku-4-5")
        self.assertEqual(
            resolve(request, "cerebras", settings),
            resolve_model(request, "cerebras", settings),
        )
        self.assertIs(resolve, resolve_model)


if __name__ == "__main__":
    unittest.main()