"""
tests/integration/test_provider_chain.py

Coverage (as specified):
  - disabled provider is skipped in the chain
  - cooling provider is skipped without being tried
  - capability mismatch (vision needed, provider lacks it) is skipped

Exercises router/selector.py + providers/registry.py + ratelimit/key_pool.py
+ ratelimit/cooldown.py together — "disabled" is represented the same way
registry.py already represents it (no keys configured -> registry.get()
returns None), not a separate enabled/disabled flag, since nothing in
this codebase has one yet.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "_stubs"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.config.provider_catalog import ProviderProfile
from clasp.providers.registry import ProviderRegistry
from clasp.ratelimit.cooldown import CooldownTracker
from clasp.router.selector import select


def _run(coro):
    return asyncio.run(coro)


def _make_profile(supports_vision: bool = False, supports_tools: bool = True) -> ProviderProfile:
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
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        supports_thinking=False,
        max_context_tokens=100_000,
        tier="free",
        free_tier_note="test",
    )


SIMPLE_REQUEST = {"model": "x", "messages": [{"role": "user", "content": "hi"}]}

VISION_REQUEST = {
    "model": "x",
    "messages": [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "url", "url": "https://example.com/cat.png"}},
                {"type": "text", "text": "what is this?"},
            ],
        }
    ],
}


def test_disabled_provider_is_skipped():
    """'alpha' has no keys configured -- registry.py never instantiates it,
    which is how 'disabled' shows up everywhere else in this codebase too."""
    catalog = {"alpha": _make_profile(), "beta": _make_profile()}
    registry = ProviderRegistry()
    registry.initialize(
        {"alpha": [], "beta": ["key-beta-0"]},
        catalog=catalog,
        cooldown_tracker=CooldownTracker(),
    )

    result = _run(select(SIMPLE_REQUEST, provider_chain=["alpha", "beta"], registry=registry, catalog=catalog))

    assert result is not None
    provider, key, _ = result
    assert provider.name == "beta"
    assert key == "key-beta-0"


def test_cooling_provider_is_skipped_without_being_tried():
    catalog = {"alpha": _make_profile(), "beta": _make_profile()}
    tracker = CooldownTracker()
    registry = ProviderRegistry()
    registry.initialize(
        {"alpha": ["key-alpha-0"], "beta": ["key-beta-0"]},
        catalog=catalog,
        cooldown_tracker=tracker,
    )
    tracker.on_429("alpha", 0, retry_after_header="120")  # alpha's only key is cooling

    result = _run(select(SIMPLE_REQUEST, provider_chain=["alpha", "beta"], registry=registry, catalog=catalog))

    assert result is not None
    provider, key, _ = result
    assert provider.name == "beta"
    assert key == "key-beta-0"


def test_capability_mismatch_vision_is_skipped():
    catalog = {
        "no_vision": _make_profile(supports_vision=False),
        "has_vision": _make_profile(supports_vision=True),
    }
    registry = ProviderRegistry()
    registry.initialize(
        {"no_vision": ["key-0"], "has_vision": ["key-1"]},
        catalog=catalog,
        cooldown_tracker=CooldownTracker(),
    )

    result = _run(
        select(VISION_REQUEST, provider_chain=["no_vision", "has_vision"], registry=registry, catalog=catalog)
    )

    assert result is not None
    provider, key, _ = result
    assert provider.name == "has_vision"
    assert key == "key-1"


def test_vision_request_against_only_non_vision_providers_returns_none():
    """Sanity check on the above: if NOTHING in the chain supports vision,
    selection correctly fails rather than silently picking an incompatible
    provider."""
    catalog = {"no_vision": _make_profile(supports_vision=False)}
    registry = ProviderRegistry()
    registry.initialize({"no_vision": ["key-0"]}, catalog=catalog, cooldown_tracker=CooldownTracker())

    result = _run(select(VISION_REQUEST, provider_chain=["no_vision"], registry=registry, catalog=catalog))
    assert result is None


def test_all_providers_unavailable_returns_none():
    catalog = {"alpha": _make_profile()}
    tracker = CooldownTracker()
    registry = ProviderRegistry()
    registry.initialize({"alpha": ["key-0"]}, catalog=catalog, cooldown_tracker=tracker)
    tracker.on_429("alpha", 0, retry_after_header="120")

    result = _run(select(SIMPLE_REQUEST, provider_chain=["alpha"], registry=registry, catalog=catalog))
    assert result is None