from clasp.router import model_map
import pytest

@pytest.fixture(autouse=True)
def _patch_resolve_model(monkeypatch):
    monkeypatch.setattr(model_map, 'resolve_model', lambda *a, **k: 'test-model')
"""
tests/integration/test_key_rotation.py

Coverage (as specified):
  - 2 keys on one provider, key[0] fills up, key[1] takes over
  - 429 on key[0] puts it in cooldown, key[1] handles next request

Same registry.py + selector.py + key_pool.py + cooldown.py stack as
test_provider_chain.py, but exercising key-level rotation within a
single provider rather than chain-level provider skipping.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "_stubs"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.config.provider_catalog import ProviderProfile
from clasp.providers.registry import ProviderRegistry
from clasp.ratelimit.cooldown import CooldownManager
from clasp.router.selector import select
from clasp.router.types import SelectorConfig, ProviderEnableConfig, AnthropicRequest, RequestType


def _run(coro):
    return asyncio.run(coro)


def _make_profile() -> ProviderProfile:
    return ProviderProfile(
        display_name="Test Provider",
        base_url="https://example.com/v1",
        transport="openai_chat",
        rpm_limit=40,
        tpm_limit=None,
        daily_token_limit=None,
        rpm_soft_threshold=0.80,
        cooldown_seconds=5,
        backoff_base_seconds=5,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=False,
        max_context_tokens=100_000,
        tier="free",
        free_tier_note="test",
    )


SIMPLE_REQUEST = AnthropicRequest(body={"model": "claude", "messages": [{"role": "user", "content": "hi"}]}, type=RequestType.INTERACTIVE, priority=0, estimated_tokens=10)


def test_key_zero_fills_up_key_one_takes_over():
    catalog = {"provider_a": _make_profile()}
    from clasp.ratelimit.key_pool import KeyPool
    registry = ProviderRegistry()
    pool = KeyPool("provider_a", ["key-0", "key-1"], catalog["provider_a"], cooldown_tracker=CooldownManager())
    registry._register("provider_a", "dummy_provider", pool)
    registry._registration_order.append("provider_a")
    registry.get_key_pool("provider_a").buckets[0].rpm_tokens = 0.0  # key 0 exhausted

    result = _run(select(SIMPLE_REQUEST, config=SelectorConfig(provider_chain=["provider_a"], providers={"provider_a": ProviderEnableConfig(enabled=True)}), registry=registry))

    assert result is not None
    _, key, key_index = result
    assert key == "key-1"
    assert key_index == 1


def test_429_on_key_zero_puts_it_in_cooldown_key_one_handles_next_request():
    catalog = {"provider_a": _make_profile()}
    from clasp.ratelimit.key_pool import KeyPool
    registry = ProviderRegistry()
    pool = KeyPool("provider_a", ["key-0", "key-1"], catalog["provider_a"], cooldown_tracker=CooldownManager())
    registry._register("provider_a", "dummy_provider", pool)
    registry._registration_order.append("provider_a")
    key_pool = registry.get_key_pool("provider_a")

    # Round-robin starts at index 0: the first request lands on key-0.
    first = _run(select(SIMPLE_REQUEST, config=SelectorConfig(provider_chain=["provider_a"], providers={"provider_a": ProviderEnableConfig(enabled=True)}), registry=registry))
    assert first is not None
    _, first_key, first_index = first
    assert (first_key, first_index) == ("key-0", 0)

    # That request comes back as a 429.
    key_pool.record_429(first_index, retry_after_header="120")

    # Next request: round-robin's own position would point at key-1 next
    # anyway, so a third request (wrapping back to index 0) proves this is
    # the cooldown check working, not coincidental round-robin ordering.
    second = _run(select(SIMPLE_REQUEST, config=SelectorConfig(provider_chain=["provider_a"], providers={"provider_a": ProviderEnableConfig(enabled=True)}), registry=registry))
    assert second is not None
    _, second_key, second_index = second
    assert (second_key, second_index) == ("key-1", 1)

    third = _run(select(SIMPLE_REQUEST, config=SelectorConfig(provider_chain=["provider_a"], providers={"provider_a": ProviderEnableConfig(enabled=True)}), registry=registry))
    assert third is not None
    _, third_key, third_index = third
    assert (third_key, third_index) == ("key-1", 1)  # key-0 still cooling, skipped again