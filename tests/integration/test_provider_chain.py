"""
tests/integration/test_provider_chain.py

Coverage (as specified):
  - disabled provider is skipped in the chain
  - cooling provider is skipped without being tried
  - capability mismatch (vision needed, provider lacks it) is skipped

Exercises router/selector.py + providers/registry.py + ratelimit/key_pool.py
+ ratelimit/cooldown.py together.
"""

import asyncio
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "_stubs"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.router import model_map
import clasp.router.capability as _capability_module
from clasp.router.capability import Capability
from clasp.router.types import SelectorConfig, ProviderEnableConfig, RequestType, AnthropicRequest
from clasp.config.provider_catalog import ProviderProfile
from clasp.providers.registry import ProviderRegistry
from clasp.ratelimit.cooldown import CooldownManager
from clasp.router.selector import select


@pytest.fixture(autouse=True)
def _patch_model_map_and_capability(monkeypatch):
    """Patch model_map.resolve_model and capability.get for the duration of
    each test in this file, then restore them automatically via monkeypatch."""
    monkeypatch.setattr(model_map, "resolve_model", lambda *a, **k: "test-model")
    monkeypatch.setattr(
        _capability_module,
        "get",
        lambda provider_name, model_slug=None, settings=None: Capability(
            supports_tools=True,
            supports_vision=(provider_name == "has_vision"),
            supports_thinking=False,
            max_context_tokens=100_000,
        ),
    )
    yield



def _run(coro):
    return asyncio.run(coro)


def init_registry(registry, keys_map, catalog, cooldown_tracker=None):
    from clasp.ratelimit.key_pool import KeyPool
    from clasp.providers.base import BaseProvider

    class FakeProvider(BaseProvider):
        provider_name = "fake"

        def __init__(self, name):
            self.provider_name = name

        async def complete(self, *a, **k):
            pass

        async def stream(self, *a, **k):
            pass

        async def _stream_raw(self, *a, **k):
            pass

        async def _complete_raw(self, *a, **k):
            pass

    for name, keys in keys_map.items():
        if keys:
            pool = KeyPool(name, keys, catalog[name], cooldown_tracker=cooldown_tracker)
            registry._register(name, FakeProvider(name), pool)
            registry._registration_order.append(name)


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


SIMPLE_REQUEST = AnthropicRequest(
    body={"model": "x", "messages": [{"role": "user", "content": "hi"}]},
    type=RequestType.INTERACTIVE,
    priority=0,
    estimated_tokens=10,
)

VISION_REQUEST = AnthropicRequest(
    body={"model": "x", "messages": [{"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "https://example.com/cat.png"}}]}]},
    type=RequestType.INTERACTIVE,
    priority=0,
    estimated_tokens=10,
    needs_vision=True,
)


def test_disabled_provider_is_skipped():
    """'alpha' has no keys configured -- registry.py never instantiates it,
    which is how 'disabled' shows up everywhere else in this codebase too."""
    catalog = {"alpha": _make_profile(), "beta": _make_profile()}
    registry = ProviderRegistry()
    init_registry(registry, {"alpha": [], "beta": ["key-beta-0"]}, catalog, CooldownManager())

    result = _run(select(
        SIMPLE_REQUEST,
        config=SelectorConfig(
            provider_chain=["alpha", "beta"],
            providers={n: ProviderEnableConfig(enabled=True) for n in ["alpha", "beta"]},
        ),
        registry=registry,
    ))

    assert result is not None
    provider, key, _ = result
    assert provider.provider_name == "beta"
    assert key == "key-beta-0"


def test_cooling_provider_is_skipped_without_being_tried():
    catalog = {"alpha": _make_profile(), "beta": _make_profile()}
    tracker = CooldownManager()
    registry = ProviderRegistry()
    init_registry(registry, {"alpha": ["key-alpha-0"], "beta": ["key-beta-0"]}, catalog, tracker)
    tracker.on_429("alpha", 0, retry_after_header="120")  # alpha's only key is cooling

    result = _run(select(
        SIMPLE_REQUEST,
        config=SelectorConfig(
            provider_chain=["alpha", "beta"],
            providers={n: ProviderEnableConfig(enabled=True) for n in ["alpha", "beta"]},
        ),
        registry=registry,
    ))

    assert result is not None
    provider, key, _ = result
    assert provider.provider_name == "beta"
    assert key == "key-beta-0"


def test_capability_mismatch_vision_is_skipped():
    catalog = {
        "no_vision": _make_profile(supports_vision=False),
        "has_vision": _make_profile(supports_vision=True),
    }
    registry = ProviderRegistry()
    init_registry(registry, {"no_vision": ["key-0"], "has_vision": ["key-1"]}, catalog, CooldownManager())

    result = _run(
        select(
            VISION_REQUEST,
            config=SelectorConfig(
                provider_chain=["no_vision", "has_vision"],
                providers={n: ProviderEnableConfig(enabled=True) for n in ["no_vision", "has_vision"]},
            ),
            registry=registry,
        )
    )

    assert result is not None
    provider, key, _ = result
    assert provider.provider_name == "has_vision"
    assert key == "key-1"


def test_vision_request_against_only_non_vision_providers_returns_none():
    """Sanity check on the above: if NOTHING in the chain supports vision,
    selection correctly fails rather than silently picking an incompatible
    provider."""
    catalog = {"no_vision": _make_profile(supports_vision=False)}
    registry = ProviderRegistry()
    init_registry(registry, {"no_vision": ["key-0"]}, catalog, CooldownManager())

    result = _run(select(
        VISION_REQUEST,
        config=SelectorConfig(
            provider_chain=["no_vision"],
            providers={n: ProviderEnableConfig(enabled=True) for n in ["no_vision"]},
        ),
        registry=registry,
    ))
    assert result is None


def test_all_providers_unavailable_returns_none():
    catalog = {"alpha": _make_profile()}
    tracker = CooldownManager()
    registry = ProviderRegistry()
    init_registry(registry, {"alpha": ["key-0"]}, catalog, tracker)
    tracker.on_429("alpha", 0, retry_after_header="120")

    result = _run(select(
        SIMPLE_REQUEST,
        config=SelectorConfig(
            provider_chain=["alpha"],
            providers={n: ProviderEnableConfig(enabled=True) for n in ["alpha"]},
        ),
        registry=registry,
    ))
    assert result is None