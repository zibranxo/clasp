"""
clasp/router/selector.py

`select()` walks the provider chain and returns the first provider/key that
can actually serve a request right now — or `None` if every candidate is
disabled, circuit-broken, cooling down, or out of rate-limit headroom.

Sprint-3 scope note
--------------------
This implements the core walk-the-chain + by_type-override + health-check
algorithm from plan.md §13, but deliberately omits the capability-matching
(`router/capability.py`), model-slug resolution (`router/model_map.py`), and
multi-key rotation (`ratelimit/key_pool.py`) pieces — those are Sprint 4/5
deliverables that don't exist yet. Until then, every provider is assumed
capable of serving every request, and only a single key (index 0) per
provider is considered. The seams (`registry.get_keys()`,
`registry.get_bucket()`) are already shaped so that swapping in a real
`KeyPool` later won't require changing this function's control flow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from clasp.ratelimit.cooldown import CooldownManager, get_cooldown_manager
from clasp.router.types import AnthropicRequest, ProviderEnableConfig, SelectorConfig

if TYPE_CHECKING:
    from clasp.providers.base import BaseProvider
    from clasp.providers.registry import ProviderRegistry


async def select(
    request: AnthropicRequest,
    *,
    exclude: set[str] | None = None,
    config: SelectorConfig | None = None,
    registry: "ProviderRegistry | None" = None,
    cooldown_mgr: CooldownManager | None = None,
) -> tuple["BaseProvider", str, int] | None:
    """
    Pick the best available (provider, api_key, key_index) for *request*.

    Parameters
    ----------
    request:
        The classified request being routed.
    exclude:
        Provider names to skip outright (e.g. the provider that just 429'd,
        when this is called from the absorber's immediate-failover branch).
    config:
        Routing configuration. If omitted, falls back to a permissive
        default built purely from whatever is registered in *registry*
        (every registered provider, in registry order, treated as
        enabled) — see `_default_config()`. This keeps `select()` callable
        with zero pydantic dependency when no config is supplied, which
        matters because `providers.base.BaseProvider.stream()` calls
        `queue.absorber.on_upstream_429()` — and therefore this function —
        with no injected config at all, matching plan.md §11's literal
        `provider.stream(request, key=key, key_index=key_idx)` call shape.
    registry:
        Provider registry to query. Defaults to the process-wide singleton.
    cooldown_mgr:
        Cooldown tracker to consult. Defaults to the process-wide singleton.

    Returns
    -------
    `(provider, api_key, key_index)` on success, or `None` if no provider
    in the chain can currently serve the request.
    """
    if registry is None:
        from clasp.providers.registry import get_registry  # noqa: PLC0415

        registry = get_registry()
    if config is None:
        config = _default_config(registry)
    if cooldown_mgr is None:
        cooldown_mgr = get_cooldown_manager()

    exclude = exclude or set()

    # ── Build candidate order: by_type override goes first ─────────────────
    override_slug = config.by_type.get(request.type.value.lower(), "") or ""
    override_name = override_slug.split("/")[0] if override_slug else ""

    if override_name and override_name not in exclude:
        candidates = [override_name] + [
            p for p in config.provider_chain if p != override_name
        ]
    else:
        candidates = list(config.provider_chain)

    for provider_name in candidates:
        if provider_name in exclude:
            continue

        cfg = config.providers.get(provider_name)
        if not cfg or not cfg.enabled:
            continue

        provider = registry.get(provider_name)
        if not provider:
            continue

        # Capability check — deferred to Sprint 5 (router/capability.py,
        # router/model_map.py). Every registered provider is assumed capable
        # for now.

        # Health check: circuit breaker.
        cb = registry.get_circuit_breaker(provider_name)
        if cb is not None and not cb.is_closed():
            logger.debug(
                "selector: skipping provider, circuit open",
                provider=provider_name,
            )
            continue

        # Key availability — Sprint-3 simplification: single key, index 0.
        # Multi-key rotation (ratelimit/key_pool.py) lands in Sprint 4.
        keys = registry.get_keys(provider_name)
        if not keys:
            continue
        key_idx = 0
        api_key = keys[key_idx]

        if cooldown_mgr.is_cooling(provider_name, key_idx):
            logger.debug(
                "selector: skipping provider, key cooling",
                provider=provider_name,
                key_index=key_idx,
            )
            continue

        bucket = registry.get_bucket(provider_name)
        if bucket is not None:
            if not await bucket.can_consume(request.estimated_tokens):
                logger.debug(
                    "selector: skipping provider, bucket exhausted",
                    provider=provider_name,
                )
                continue
            await bucket.consume(request.estimated_tokens)

        logger.debug(
            "selector: selected provider",
            provider=provider_name,
            key_index=key_idx,
        )
        return provider, api_key, key_idx

    return None


def _default_config(registry: "ProviderRegistry") -> SelectorConfig:
    """
    Build a permissive default `SelectorConfig` purely from what's
    registered in *registry*: every registered provider, in registry
    (insertion) order, treated as enabled. No by-type overrides.

    This is what `select()` falls back to when no explicit `config` is
    supplied — which happens whenever `providers.base.BaseProvider.stream()`
    calls through to the absorber on a 429, since that call path passes no
    config at all. Production code that *does* have a real `Settings`
    object available (server.py's `build_selector_config()`, and eventually
    `api/service.py` per plan.md §20 step 42) should build and pass a real
    `SelectorConfig` instead of relying on this fallback.
    """
    names = registry.provider_names()
    return SelectorConfig(
        provider_chain=names,
        providers={name: ProviderEnableConfig(enabled=True) for name in names},
    )