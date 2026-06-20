"""
tests/unit/test_selector.py
Unit tests for clasp/router/selector.py, clasp/router/model_map.py,
and clasp/ratelimit/bucket.py.

Run with:
    python3 -m unittest tests/unit/test_selector.py -v

Pure stdlib — no pytest required.

Coverage:
  TokenBucket
    - can_consume: returns True when bucket full
    - can_consume: False when RPM saturated
    - can_consume: False above soft threshold
    - can_consume: False when TPM insufficient
    - consume: debits RPM and TPM tokens
    - consume_actual: corrects TPM after real usage
    - seconds_until_available: correct estimate

  model_map.resolve()
    - exact provider/model prefix match
    - no-prefix slug accepted by any provider
    - wrong provider prefix → None
    - opus/sonnet/haiku tier keyword match
    - fable wildcard match
    - by_type_model_slug override wins
    - default fallback
    - unrecognised model with no default → None

  selector.select()
    - returns first healthy provider in chain
    - skips excluded provider
    - skips disabled provider
    - skips provider with no model mapping
    - skips provider that lacks tools capability
    - skips provider that lacks vision capability
    - skips provider that lacks thinking capability
    - skips provider when context window exceeded
    - skips provider when all keys cooling
    - skips provider when circuit breaker open
    - by_type override moves matching provider to front
    - least-loaded strategy reorders by RPM usage
    - cost-aware strategy reorders by tier
    - returns None when all providers exhausted
    - KeyPool round-robin across multiple keys
    - KeyPool skips saturated bucket key, uses next
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.ratelimit.bucket import TokenBucket
from clasp.ratelimit.cooldown import CooldownManager
from clasp.ratelimit.key_pool import KeyPool
from clasp.router.model_map import resolve_model
from clasp.router.selector import select


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class FakeProvider:
    def __init__(self, name="fake"):
        self.name = name

    async def stream(self, request, *, key, key_index=0, **kw):
        if False:
            yield  # make it an async generator


def _make_chain(*names):
    return list(names)


def _make_instances(*names):
    return {n: FakeProvider(n) for n in names}


def _make_keys(*entries):
    """entries: (name, [key1, key2, ...])"""
    return dict(entries)


def _make_caps(**kwargs):
    """
    Build a capabilities dict for a provider.
    Defaults match a fully-capable provider.
    """
    return {
        "supports_tools": kwargs.get("supports_tools", True),
        "supports_vision": kwargs.get("supports_vision", True),
        "supports_thinking": kwargs.get("supports_thinking", True),
        "max_context_tokens": kwargs.get("max_context_tokens", 200_000),
        "tier": kwargs.get("tier", "free"),
        "rpm_limit": kwargs.get("rpm_limit", 40),
        "tpm_limit": kwargs.get("tpm_limit", None),
        "rpm_soft_threshold": kwargs.get("rpm_soft_threshold", 0.80),
    }


def _make_pool(provider_name, keys, rpm=40, tpm=None, soft=0.80):
    from clasp.config.provider_catalog import ProviderProfile
    profile = ProviderProfile(
        display_name=provider_name,
        base_url="https://example.com/v1",
        transport="openai_chat",
        rpm_limit=rpm,
        tpm_limit=tpm,
        daily_token_limit=None,
        rpm_soft_threshold=soft,
        cooldown_seconds=5,
        backoff_base_seconds=5,
        supports_tools=True,
        supports_vision=True,
        supports_thinking=True,
        max_context_tokens=200_000,
        tier="free",
        free_tier_note="test",
    )
    return KeyPool(provider_name, keys, profile, cooldown_tracker=CooldownManager())


# ---------------------------------------------------------------------------
# TokenBucket tests
# ---------------------------------------------------------------------------

class TestTokenBucket(unittest.TestCase):

    def test_full_bucket_can_consume(self):
        b = TokenBucket(rpm_limit=10, tpm_limit=None)
        self.assertTrue(run(b.can_consume(0)))

    def test_saturated_rpm_cannot_consume(self):
        b = TokenBucket(rpm_limit=10, tpm_limit=None, soft_threshold=0.80)
        # Drain to exactly the soft threshold (80% of 10 = 8 consumed)
        b.rpm_tokens = 10 * (1 - 0.80) - 0.001  # just over 80% consumed
        self.assertFalse(run(b.can_consume(0)))

    def test_below_soft_threshold_can_consume(self):
        b = TokenBucket(rpm_limit=100, tpm_limit=None, soft_threshold=0.80)
        # 50% consumed — still below threshold
        b.rpm_tokens = 50.0
        self.assertTrue(run(b.can_consume(0)))

    def test_tpm_insufficient_cannot_consume(self):
        b = TokenBucket(rpm_limit=40, tpm_limit=1000)
        b.tpm_tokens = 5.0  # almost empty
        self.assertFalse(run(b.can_consume(estimated_tokens=100)))

    def test_tpm_sufficient_can_consume(self):
        b = TokenBucket(rpm_limit=40, tpm_limit=1000)
        b.tpm_tokens = 500.0
        self.assertTrue(run(b.can_consume(estimated_tokens=100)))

    def test_consume_debits_rpm(self):
        b = TokenBucket(rpm_limit=10, tpm_limit=None)
        run(b.consume(0))
        self.assertEqual(b.rpm_used, 1)

    def test_consume_debits_tpm(self):
        b = TokenBucket(rpm_limit=40, tpm_limit=1000)
        run(b.consume(estimated_tokens=200))
        self.assertAlmostEqual(b.tpm_tokens, 800.0, delta=1.0)

    def test_consume_actual_corrects_tpm_upward(self):
        b = TokenBucket(rpm_limit=40, tpm_limit=10_000)
        run(b.consume(estimated_tokens=100))
        # Actual was 300 — should deduct additional 200
        run(b.consume_actual(actual_tokens=300, estimated_tokens=100))
        self.assertAlmostEqual(b.tpm_tokens, 10_000 - 300, delta=5)

    def test_consume_actual_refunds_tpm(self):
        b = TokenBucket(rpm_limit=40, tpm_limit=10_000)
        run(b.consume(estimated_tokens=500))
        # Actual was only 100 — refund 400
        run(b.consume_actual(actual_tokens=100, estimated_tokens=500))
        self.assertAlmostEqual(b.tpm_tokens, 10_000 - 100, delta=5)

    def test_seconds_until_available(self):
        # Drain bucket fully so there IS a real wait time.
        b = TokenBucket(rpm_limit=60, tpm_limit=None)
        b.rpm_tokens = 0.0   # fully empty
        # deficit = max(0, 1 - 0) = 1; refill_rate = 60/60 = 1.0 t/s → 1.0s
        secs = b.seconds_until_available()
        self.assertAlmostEqual(secs, 1.0, delta=0.05)
        # Full bucket → no wait
        b2 = TokenBucket(rpm_limit=60, tpm_limit=None)
        self.assertAlmostEqual(b2.seconds_until_available(), 0.0, delta=0.01)

    def test_tpm_none_never_blocks_on_tokens(self):
        b = TokenBucket(rpm_limit=40, tpm_limit=None)
        # Even requesting millions of tokens should not block when tpm is None
        self.assertTrue(run(b.can_consume(estimated_tokens=10_000_000)))


# ---------------------------------------------------------------------------
# model_map tests
# ---------------------------------------------------------------------------

ROUTING_MODELS = {
    "opus":   "nvidia_nim/moonshotai/kimi-k2-thinking",
    "sonnet": "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct",
    "haiku":  "cerebras/llama3.1-8b",
    "fable":  "gemini/gemini-2.5-flash",
    "default": "nvidia_nim/nvidia/llama-3.1-nemotron-70b-instruct",
}


class TestModelMap(unittest.TestCase):

    def test_exact_provider_prefix_match(self):
        slug = resolve_model("claude-opus-4-5", "nvidia_nim", ROUTING_MODELS)
        self.assertEqual(slug, "moonshotai/kimi-k2-thinking")

    def test_wrong_provider_prefix_returns_none(self):
        # "haiku" tier is pinned to cerebras
        slug = resolve_model("claude-haiku-4-5", "nvidia_nim", ROUTING_MODELS)
        self.assertIsNone(slug)

    def test_cerebras_gets_haiku_model(self):
        slug = resolve_model("claude-haiku-4-5", "cerebras", ROUTING_MODELS)
        self.assertEqual(slug, "llama3.1-8b")

    def test_fable_wildcard_match(self):
        # Any model containing "fable" → fable tier
        slug = resolve_model("claude-fable-something-new", "gemini", ROUTING_MODELS)
        self.assertEqual(slug, "gemini-2.5-flash")

    def test_no_prefix_slug_accepted_by_any_provider(self):
        # A config value without "/" → any provider can use it
        models = {"default": "some-generic-model"}
        slug = resolve_model("claude-anything", "groq", models)
        self.assertEqual(slug, "some-generic-model")

    def test_by_type_override_wins_over_tier_keyword(self):
        # Even if model name says "haiku", by_type override to gemini wins
        slug = resolve_model(
            "claude-haiku-4-5", "gemini", ROUTING_MODELS,
            by_type_model_slug="gemini/gemini-2.5-flash",
        )
        self.assertEqual(slug, "gemini-2.5-flash")

    def test_by_type_override_wrong_provider_returns_none(self):
        slug = resolve_model(
            "claude-haiku-4-5", "groq", ROUTING_MODELS,
            by_type_model_slug="gemini/gemini-2.5-flash",
        )
        self.assertIsNone(slug)

    def test_default_fallback_for_unknown_model(self):
        slug = resolve_model("claude-unknown-model-xyz", "nvidia_nim", ROUTING_MODELS)
        self.assertEqual(slug, "nvidia/llama-3.1-nemotron-70b-instruct")

    def test_no_default_returns_none(self):
        models = {"opus": "nvidia_nim/kimi"}
        slug = resolve_model("claude-unknown-xyz", "nvidia_nim", models)
        self.assertIsNone(slug)

    def test_sonnet_keyword_in_model_name(self):
        slug = resolve_model("claude-3-5-sonnet-20241022", "nvidia_nim", ROUTING_MODELS)
        self.assertEqual(slug, "nvidia/llama-3.1-nemotron-70b-instruct")

    def test_parse_by_type_entry_with_slash(self):
        provider, slug = parse_by_type_entry("nvidia_nim/kimi-k2-thinking")
        self.assertEqual(provider, "nvidia_nim")
        self.assertEqual(slug, "kimi-k2-thinking")

    def test_parse_by_type_entry_no_slash(self):
        provider, slug = parse_by_type_entry("kimi-k2-thinking")
        self.assertIsNone(provider)
        self.assertEqual(slug, "kimi-k2-thinking")

    def test_parse_by_type_entry_multi_slash(self):
        # provider/org/model — split on first slash only
        provider, slug = parse_by_type_entry("nvidia_nim/moonshotai/kimi-k2")
        self.assertEqual(provider, "nvidia_nim")
        self.assertEqual(slug, "moonshotai/kimi-k2")


# ---------------------------------------------------------------------------
# selector.select() tests
# ---------------------------------------------------------------------------

class TestSelectorBasic(unittest.TestCase):
    """Core routing — priority-chain strategy."""

    def _make_request(self, **kwargs):
        base = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "hi"}],
            "request_type": "INTERACTIVE",
            "estimated_tokens": 100,
        }
        base.update(kwargs)
        return base

    def _select(self, request, **kwargs):
        return run(select(request, **kwargs))

    def test_returns_first_healthy_provider(self):
        request = self._make_request()
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["nim", "gemini"],
            provider_instances=_make_instances("nim", "gemini"),
            provider_keys=_make_keys(("nim", ["k1"]), ("gemini", ["k2"])),
            provider_capabilities={"nim": _make_caps(), "gemini": _make_caps()},
            routing_models={"default": "some-model"},
            enabled_providers={"nim", "gemini"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, key, idx = result
        self.assertEqual(provider.name, "nim")
        self.assertEqual(key, "k1")
        self.assertEqual(idx, 0)

    def test_skips_excluded_provider(self):
        request = self._make_request()
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            exclude={"nim"},
            provider_chain=["nim", "gemini"],
            provider_instances=_make_instances("nim", "gemini"),
            provider_keys=_make_keys(("nim", ["k1"]), ("gemini", ["k2"])),
            provider_capabilities={"nim": _make_caps(), "gemini": _make_caps()},
            routing_models={"default": "some-model"},
            enabled_providers={"nim", "gemini"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "gemini")

    def test_skips_disabled_provider(self):
        request = self._make_request()
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["nim", "gemini"],
            provider_instances=_make_instances("nim", "gemini"),
            provider_keys=_make_keys(("nim", ["k1"]), ("gemini", ["k2"])),
            provider_capabilities={"nim": _make_caps(), "gemini": _make_caps()},
            routing_models={"default": "some-model"},
            enabled_providers={"gemini"},   # nim disabled
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "gemini")

    def test_returns_none_when_all_exhausted(self):
        request = self._make_request()
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["nim"],
            provider_instances=_make_instances("nim"),
            provider_keys=_make_keys(("nim", ["k1"])),
            provider_capabilities={"nim": _make_caps()},
            routing_models={"default": "some-model"},
            enabled_providers=set(),   # none enabled
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNone(result)

    def test_skips_provider_not_in_registry(self):
        request = self._make_request()
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["missing", "gemini"],
            provider_instances=_make_instances("gemini"),  # "missing" absent
            provider_keys=_make_keys(("missing", ["k1"]), ("gemini", ["k2"])),
            provider_capabilities={"missing": _make_caps(), "gemini": _make_caps()},
            routing_models={"default": "some-model"},
            enabled_providers={"missing", "gemini"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "gemini")


class TestSelectorModelMap(unittest.TestCase):
    """Model slug resolution gates — provider skipped when no slug resolves."""

    def _select(self, request, **kwargs):
        return run(select(request, **kwargs))

    def test_skips_provider_with_no_model_mapping(self):
        # "haiku" tier is pinned to cerebras; nim has no haiku entry
        models = {
            "haiku": "cerebras/llama3.1-8b",
            "default": "cerebras/llama3.1-8b",  # also cerebras
        }
        request = {"model": "claude-haiku-4-5", "messages": [], "estimated_tokens": 50}
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["nim", "cerebras"],
            provider_instances=_make_instances("nim", "cerebras"),
            provider_keys=_make_keys(("nim", ["k1"]), ("cerebras", ["k2"])),
            provider_capabilities={"nim": _make_caps(), "cerebras": _make_caps()},
            routing_models=models,
            enabled_providers={"nim", "cerebras"},
            cooldown_store=cd, cb_store=cb,
        )
        # nim has no valid mapping, cerebras should be selected
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "cerebras")


class TestSelectorCapabilities(unittest.TestCase):
    """Capability checks gate provider selection."""

    def _select(self, request, **kwargs):
        return run(select(request, **kwargs))

    def test_skips_provider_lacking_tools(self):
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [],
            "tools": [{"name": "bash"}],
            "estimated_tokens": 100,
        }
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["no_tools_prov", "tools_prov"],
            provider_instances=_make_instances("no_tools_prov", "tools_prov"),
            provider_keys=_make_keys(("no_tools_prov", ["k1"]), ("tools_prov", ["k2"])),
            provider_capabilities={
                "no_tools_prov": _make_caps(supports_tools=False),
                "tools_prov": _make_caps(supports_tools=True),
            },
            routing_models={"default": "some-model"},
            enabled_providers={"no_tools_prov", "tools_prov"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "tools_prov")

    def test_skips_provider_lacking_vision(self):
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [
                {"role": "user", "content": [{"type": "image", "source": {}}]}
            ],
            "estimated_tokens": 500,
        }
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["blind", "seeing"],
            provider_instances=_make_instances("blind", "seeing"),
            provider_keys=_make_keys(("blind", ["k1"]), ("seeing", ["k2"])),
            provider_capabilities={
                "blind": _make_caps(supports_vision=False),
                "seeing": _make_caps(supports_vision=True),
            },
            routing_models={"default": "some-model"},
            enabled_providers={"blind", "seeing"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "seeing")

    def test_skips_provider_lacking_thinking(self):
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [],
            "type": "THINK",
            "thinking": {"type": "enabled", "budget_tokens": 5000},
            "estimated_tokens": 200,
        }
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["no_think", "thinker"],
            provider_instances=_make_instances("no_think", "thinker"),
            provider_keys=_make_keys(("no_think", ["k1"]), ("thinker", ["k2"])),
            provider_capabilities={
                "no_think": _make_caps(supports_thinking=False),
                "thinker": _make_caps(supports_thinking=True),
            },
            routing_models={"default": "some-model"},
            enabled_providers={"no_think", "thinker"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "thinker")

    def test_skips_provider_when_context_window_exceeded(self):
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [],
            "estimated_tokens": 30_000,  # > 90% of 32k
        }
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["small_ctx", "big_ctx"],
            provider_instances=_make_instances("small_ctx", "big_ctx"),
            provider_keys=_make_keys(("small_ctx", ["k1"]), ("big_ctx", ["k2"])),
            provider_capabilities={
                "small_ctx": _make_caps(max_context_tokens=32_768),  # 90% = 29,491
                "big_ctx": _make_caps(max_context_tokens=200_000),
            },
            routing_models={"default": "some-model"},
            enabled_providers={"small_ctx", "big_ctx"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "big_ctx")


class TestSelectorCooldownAndCB(unittest.TestCase):
    """Key-level health checks."""

    def _select(self, request, **kwargs):
        return run(select(request, **kwargs))

    def test_skips_cooling_key_uses_next(self):
        request = {"model": "claude-3-5-sonnet-20241022", "messages": [], "estimated_tokens": 0}
        cd = CooldownStore()
        cb = CircuitBreakerStore()
        # Put key 0 of "nim" in cooldown
        cd.on_429("nim", 0, "60")
        result = self._select(
            request,
            provider_chain=["nim"],
            provider_instances=_make_instances("nim"),
            provider_keys=_make_keys(("nim", ["k0", "k1"])),
            provider_capabilities={"nim": _make_caps()},
            routing_models={"default": "some-model"},
            enabled_providers={"nim"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        _, _, idx = result
        self.assertEqual(idx, 1)  # k1 used, not k0

    def test_all_keys_cooling_skips_provider(self):
        request = {"model": "claude-3-5-sonnet-20241022", "messages": [], "estimated_tokens": 0}
        cd = CooldownStore()
        cb = CircuitBreakerStore()
        cd.on_429("nim", 0, "60")
        cd.on_429("nim", 1, "60")
        result = self._select(
            request,
            provider_chain=["nim", "gemini"],
            provider_instances=_make_instances("nim", "gemini"),
            provider_keys=_make_keys(("nim", ["k0", "k1"]), ("gemini", ["k2"])),
            provider_capabilities={"nim": _make_caps(), "gemini": _make_caps()},
            routing_models={"default": "some-model"},
            enabled_providers={"nim", "gemini"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "gemini")

    def test_skips_open_circuit_breaker(self):
        request = {"model": "claude-3-5-sonnet-20241022", "messages": [], "estimated_tokens": 0}
        cd = CooldownStore()
        cb = CircuitBreakerStore()
        # Trip CB for nim key 0 (3 consecutive 429s)
        breaker = cb.get("nim", 0)
        for _ in range(3):
            breaker.record_429()
        self.assertFalse(breaker.is_closed())

        result = self._select(
            request,
            provider_chain=["nim", "gemini"],
            provider_instances=_make_instances("nim", "gemini"),
            provider_keys=_make_keys(("nim", ["k0"]), ("gemini", ["k1"])),
            provider_capabilities={"nim": _make_caps(), "gemini": _make_caps()},
            routing_models={"default": "some-model"},
            enabled_providers={"nim", "gemini"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "gemini")


class TestSelectorByType(unittest.TestCase):
    """by_type override reorders candidate list."""

    def _select(self, request, **kwargs):
        return run(select(request, **kwargs))

    def test_by_type_moves_provider_to_front(self):
        # Default chain: nim → gemini; THINK override → gemini first
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [],
            "type": "THINK",
            "thinking": {"type": "enabled"},
            "estimated_tokens": 100,
        }
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["nim", "gemini"],
            provider_instances=_make_instances("nim", "gemini"),
            provider_keys=_make_keys(("nim", ["k1"]), ("gemini", ["k2"])),
            provider_capabilities={
                "nim": _make_caps(supports_thinking=False),
                "gemini": _make_caps(supports_thinking=True),
            },
            routing_models={"default": "some-model"},
            routing_by_type={"THINK": "gemini/gemini-2.5-pro"},
            enabled_providers={"nim", "gemini"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "gemini")

    def test_by_type_excluded_falls_back_to_chain(self):
        # by_type says gemini for THINK, but gemini is excluded → use nim
        request = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [],
            "type": "THINK",
            "thinking": {"type": "enabled"},
            "estimated_tokens": 100,
        }
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            exclude={"gemini"},
            provider_chain=["nim", "gemini"],
            provider_instances=_make_instances("nim", "gemini"),
            provider_keys=_make_keys(("nim", ["k1"]), ("gemini", ["k2"])),
            provider_capabilities={
                "nim": _make_caps(supports_thinking=True),
                "gemini": _make_caps(supports_thinking=True),
            },
            routing_models={"default": "some-model"},
            routing_by_type={"THINK": "gemini/gemini-2.5-pro"},
            enabled_providers={"nim", "gemini"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "nim")


class TestSelectorStrategies(unittest.TestCase):
    """Routing strategy variants."""

    def _select(self, request, **kwargs):
        return run(select(request, **kwargs))

    def test_least_loaded_picks_emptier_provider(self):
        request = {"model": "claude-3-5-sonnet-20241022", "messages": [], "estimated_tokens": 0}
        cd, cb = CooldownStore(), CircuitBreakerStore()

        # nim pool: saturate it (80%+ consumed)
        nim_pool = _make_pool("nim", ["k1"], rpm=10)
        nim_pool.buckets[0].rpm_tokens = 1.9  # 81% consumed → above soft threshold
        gemini_pool = _make_pool("gemini", ["k2"], rpm=40)
        # gemini pool: fresh (0% consumed)

        result = self._select(
            request,
            provider_chain=["nim", "gemini"],
            provider_instances=_make_instances("nim", "gemini"),
            provider_keys=_make_keys(("nim", ["k1"]), ("gemini", ["k2"])),
            provider_capabilities={"nim": _make_caps(), "gemini": _make_caps()},
            routing_models={"default": "some-model"},
            key_pools={"nim": nim_pool, "gemini": gemini_pool},
            routing_strategy="least-loaded",
            enabled_providers={"nim", "gemini"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "gemini")

    def test_cost_aware_picks_free_over_paid(self):
        request = {"model": "claude-3-5-sonnet-20241022", "messages": [], "estimated_tokens": 0}
        cd, cb = CooldownStore(), CircuitBreakerStore()
        result = self._select(
            request,
            provider_chain=["paid_prov", "free_prov"],
            provider_instances=_make_instances("paid_prov", "free_prov"),
            provider_keys=_make_keys(("paid_prov", ["k1"]), ("free_prov", ["k2"])),
            provider_capabilities={
                "paid_prov": _make_caps(tier="paid"),
                "free_prov": _make_caps(tier="free"),
            },
            routing_models={"default": "some-model"},
            routing_strategy="cost-aware",
            enabled_providers={"paid_prov", "free_prov"},
            cooldown_store=cd, cb_store=cb,
        )
        self.assertIsNotNone(result)
        provider, _, _ = result
        self.assertEqual(provider.name, "free_prov")


class TestKeyPoolRoundRobin(unittest.TestCase):
    """KeyPool correctly rotates across multiple keys."""

    def test_round_robin_across_healthy_keys(self):
        cd, cb = CooldownStore(), CircuitBreakerStore()
        pool = _make_pool("nim", ["k0", "k1", "k2"], rpm=100)
        results = []
        for _ in range(3):
            r = run(pool.pick_key(cooldown_store=cd, cb_store=cb))
            self.assertIsNotNone(r)
            results.append(r[1])  # key_index
        self.assertEqual(results, [0, 1, 2])

    def test_skips_saturated_key_bucket(self):
        cd, cb = CooldownStore(), CircuitBreakerStore()
        pool = _make_pool("nim", ["k0", "k1"], rpm=10, soft=0.80)
        # Saturate k0's bucket
        pool.buckets[0].rpm_tokens = 1.0  # exactly at soft boundary — blocks
        result = run(pool.pick_key(cooldown_store=cd, cb_store=cb))
        self.assertIsNotNone(result)
        _, idx = result
        self.assertEqual(idx, 1)  # k1 selected

    def test_returns_none_when_all_keys_cooling(self):
        cd = CooldownStore()
        cb = CircuitBreakerStore()
        pool = _make_pool("nim", ["k0", "k1"], rpm=40)
        cd.on_429("nim", 0, "60")
        cd.on_429("nim", 1, "60")
        result = run(pool.pick_key(cooldown_store=cd, cb_store=cb))
        self.assertIsNone(result)

    def test_health_summary_reflects_cooling_keys(self):
        cd = CooldownStore()
        cb = CircuitBreakerStore()
        pool = _make_pool("nim", ["k0", "k1", "k2"], rpm=40)
        cd.on_429("nim", 1, "60")
        summary = pool.health_summary(cooldown_store=cd, cb_store=cb)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["healthy"], 2)
        self.assertTrue(summary["keys"][1]["cooling"])


class TestRequestView(unittest.TestCase):
    """RequestView correctly parses dict and detects vision/tools/thinking."""

    def test_detects_tools_list(self):
        rv = RequestView({"model": "x", "messages": [], "tools": [{"name": "bash"}]})
        self.assertTrue(rv.needs_tools)

    def test_empty_tools_list_not_needs_tools(self):
        rv = RequestView({"model": "x", "messages": [], "tools": []})
        self.assertFalse(rv.needs_tools)

    def test_detects_vision_in_content_blocks(self):
        rv = RequestView({
            "model": "x",
            "messages": [{"role": "user", "content": [{"type": "image", "source": {}}]}],
        })
        self.assertTrue(rv.needs_vision)

    def test_no_vision_for_text_only(self):
        rv = RequestView({
            "model": "x",
            "messages": [{"role": "user", "content": "just text"}],
        })
        self.assertFalse(rv.needs_vision)

    def test_detects_thinking_from_type(self):
        rv = RequestView({"model": "x", "messages": [], "type": "THINK"})
        self.assertTrue(rv.needs_thinking)

    def test_detects_thinking_from_field(self):
        rv = RequestView({"model": "x", "messages": [], "thinking": {"type": "enabled"}})
        self.assertTrue(rv.needs_thinking)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)