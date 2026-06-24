"""
tests/unit/test_selector.py
Rewritten for Sprint 2 architectures.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.ratelimit.bucket import TokenBucket
from clasp.ratelimit.cooldown import CooldownManager
from clasp.ratelimit.key_pool import KeyPool
from clasp.router.model_map import resolve_model
from clasp.router.selector import select, AnthropicRequest
from clasp.router.types import SelectorConfig, ProviderEnableConfig
from clasp.config.settings import Settings, RoutingConfig, ModelRoutes, ByTypeRoutes
from clasp.providers.registry import ProviderRegistry
from clasp.config.provider_catalog import ProviderProfile
from clasp.api.detect import RequestType

def run(coro):
    return asyncio.run(coro)

# ---------------------------------------------------------------------------
# TokenBucket tests
# ---------------------------------------------------------------------------
class TestTokenBucket(unittest.TestCase):
    def test_full_bucket_can_consume(self):
        b = TokenBucket(rpm_limit=10, tpm_limit=None)
        self.assertTrue(run(b.can_consume(0)))

    def test_consume_actual_corrects_tpm_upward(self):
        b = TokenBucket(rpm_limit=40, tpm_limit=10_000)
        run(b.consume(estimated_tokens=100))
        run(b.consume_actual(actual=300, estimated=100))
        self.assertAlmostEqual(b.tpm_tokens, 10_000 - 300, delta=5)

    def test_consume_actual_refunds_tpm(self):
        b = TokenBucket(rpm_limit=40, tpm_limit=10_000)
        run(b.consume(estimated_tokens=500))
        run(b.consume_actual(actual=100, estimated=500))
        self.assertAlmostEqual(b.tpm_tokens, 10_000 - 100, delta=5)

# ---------------------------------------------------------------------------
# model_map tests
# ---------------------------------------------------------------------------
class TestModelMap(unittest.TestCase):
    def test_exact_provider_prefix_match(self):
        models = {"opus": "nvidia_nim/moonshotai/kimi-k2-thinking"}
        slug = resolve_model(AnthropicRequest.from_body({"model": "claude-opus-4-5"}), "nvidia_nim", Settings(routing=RoutingConfig(models=ModelRoutes(**models))))
        self.assertEqual(slug, "moonshotai/kimi-k2-thinking")

# ---------------------------------------------------------------------------
# AnthropicRequest tests
# ---------------------------------------------------------------------------
class TestAnthropicRequest(unittest.TestCase):
    def test_detects_tools_list(self):
        rv = AnthropicRequest.from_body({"model": "x", "messages": [], "tools": [{"name": "bash"}]})
        self.assertTrue(rv.needs_tools)

    def test_detects_thinking_from_type(self):
        rv = AnthropicRequest.from_body({"model": "x", "messages": [], "thinking": {"type": "enabled"}})
        self.assertEqual(rv.type, RequestType.THINK)

# ---------------------------------------------------------------------------
# KeyPool tests
# ---------------------------------------------------------------------------
class TestKeyPool(unittest.TestCase):
    def test_skips_cooling_keys(self):
        cd = CooldownManager()
        profile = ProviderProfile(display_name="nim", base_url="", transport="openai_chat", rpm_limit=40, tpm_limit=None, daily_token_limit=None, rpm_soft_threshold=0.8, cooldown_seconds=60, backoff_base_seconds=2, supports_tools=True, supports_vision=True, supports_thinking=True, max_context_tokens=100000, tier="free", free_tier_note="")
        pool = KeyPool("nim", ["k1", "k2"], profile, cooldown_tracker=cd)
        cd.on_429("nim", 0, "60")
        res = run(pool.pick_key())
        self.assertIsNotNone(res)
        self.assertEqual(res[1], 1)

# ---------------------------------------------------------------------------
# Selector tests
# ---------------------------------------------------------------------------
class FakeProvider:
    def __init__(self, name="fake"):
        self.name = name

class TestSelector(unittest.TestCase):
    def test_select_basic(self):
        req = AnthropicRequest.from_body({"model": "claude-3-5-sonnet-20241022", "messages": []})
        config = SelectorConfig(provider_chain=["nim"], providers={"nim": ProviderEnableConfig(enabled=True)})
        
        registry = ProviderRegistry()
        registry._providers = {"nim": FakeProvider("nim")}
        
        profile = ProviderProfile(display_name="nim", base_url="", transport="openai_chat", rpm_limit=40, tpm_limit=None, daily_token_limit=None, rpm_soft_threshold=0.8, cooldown_seconds=60, backoff_base_seconds=2, supports_tools=True, supports_vision=True, supports_thinking=True, max_context_tokens=100000, tier="free", free_tier_note="")
        pool = KeyPool("nim", ["k1"], profile, cooldown_tracker=CooldownManager())
        registry._key_pools = {"nim": pool}
        
        settings = Settings(routing=RoutingConfig(models=ModelRoutes(sonnet="nim/llama-3.1-nemotron-70b-instruct")))
        
        res = run(select(req, config=config, registry=registry, settings=settings))
        self.assertIsNotNone(res)
        self.assertEqual(res[0].name, "nim")

    def test_select_restricts_to_target_provider_prefixed_model(self):
        req = AnthropicRequest.from_body({"model": "anthropic/gemini/gemini-1.5-pro", "messages": []})
        config = SelectorConfig(
            provider_chain=["nim", "gemini"],
            providers={
                "nim": ProviderEnableConfig(enabled=True),
                "gemini": ProviderEnableConfig(enabled=True),
            }
        )
        
        registry = ProviderRegistry()
        registry._providers = {
            "nim": FakeProvider("nim"),
            "gemini": FakeProvider("gemini"),
        }
        
        profile = ProviderProfile(display_name="x", base_url="", transport="openai_chat", rpm_limit=40, tpm_limit=None, daily_token_limit=None, rpm_soft_threshold=0.8, cooldown_seconds=60, backoff_base_seconds=2, supports_tools=True, supports_vision=True, supports_thinking=True, max_context_tokens=100000, tier="free", free_tier_note="")
        registry._key_pools = {
            "nim": KeyPool("nim", ["k1"], profile, cooldown_tracker=CooldownManager()),
            "gemini": KeyPool("gemini", ["k2"], profile, cooldown_tracker=CooldownManager()),
        }
        
        settings = Settings()
        res = run(select(req, config=config, registry=registry, settings=settings))
        self.assertIsNotNone(res)
        self.assertEqual(res[0].name, "gemini")


if __name__ == '__main__':
    unittest.main()