"""
clasp/router/selector.py

`select()` walks the provider chain and returns the first provider/key that
can actually serve a request right now — or `None` if every candidate is
disabled, circuit-broken, cooling down, or out of rate-limit headroom.

Sprint 2 implementation
------------------------
Implements the full Section 13 selection algorithm:

  1. Build candidate order: by_type override first, then provider_chain.
  2. For each candidate provider:
     a. Skip if excluded or disabled in config.
     b. Skip if not registered in the registry.
     c. Resolve the model slug via model_map.resolve() — skip if this
        provider is not configured to serve this request's tier/type.
     d. Check capability flags via capability.get() — skip if the request
        needs tools/vision/thinking that this provider/model can't handle.
     e. Call key_pool.pick_key() — atomically checks cooldown, circuit
        breaker, and token bucket, then deducts if OK.  Skip provider if
        no key is available.
  3. Return (provider_instance, api_key, key_index) on first match.
  4. Return None if the whole chain is exhausted.

The `registry` and `config` parameters are injectable for testing; in
production both default to their process-wide singletons.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from clasp.ratelimit.cooldown import CooldownManager, get_cooldown_manager
from clasp.router import capability as capability_module
from clasp.router import model_map
from clasp.router.types import AnthropicRequest, ProviderEnableConfig, SelectorConfig

if TYPE_CHECKING:
    from clasp.config.settings import Settings
    from clasp.providers.base import BaseProvider
    from clasp.providers.registry import ProviderRegistry


async def select(
    request: AnthropicRequest,
    *,
    exclude: set[str] | None = None,
    config: SelectorConfig | None = None,
    registry: "ProviderRegistry | None" = None,
    cooldown_mgr: CooldownManager | None = None,
    settings: "Settings | None" = None,
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
        enabled).
    registry:
        Provider registry to query. Defaults to the process-wide singleton.
    cooldown_mgr:
        Cooldown tracker to consult. Defaults to the process-wide singleton.
        (Passed through for test injection; KeyPool.pick_key() already
        consults its own internal cooldown manager, so this parameter is
        kept for backward compatibility with the absorber call shape.)
    settings:
        Settings object for capability / model_map resolution.  If omitted,
        get_settings() is called lazily.

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
    if settings is None:
        try:
            from clasp.config.settings import get_settings  # noqa: PLC0415

            settings = get_settings()
        except Exception:  # noqa: BLE001
            settings = None

    exclude = exclude or set()

    # ── Build candidate order: by_type override goes first ─────────────────
    requested_model = (request.body.get("model") or "") if isinstance(request.body, dict) else (getattr(request, "model", "") or "")
    target_provider = None
    if requested_model:
        from clasp.router.model_map import decode_gateway_model_id
        decoded = decode_gateway_model_id(requested_model)
        if decoded:
            target_provider, _, _ = decoded

    if target_provider:
        if target_provider in exclude:
            candidates = []
        else:
            candidates = [target_provider]
    else:
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

        key_pool = registry.get_key_pool(provider_name)
        if not key_pool:
            continue

        # ── Model resolution ────────────────────────────────────────────
        # Determine the concrete model slug this provider should be asked
        # for.  Returns None if the provider isn't configured for this
        # request's tier/type (e.g. no entry in routing.models for this tier).
        if settings is not None:
            model_slug = model_map.resolve_model(request, provider_name, settings)
        else:
            # No settings available — skip model resolution; assume the
            # provider can serve any request (same as Sprint 1 behavior).
            model_slug = None

        # A None model_slug means this provider has no configured mapping
        # for this request's tier — skip it and try the next candidate.
        # Exception: if settings is None we already set model_slug to None
        # deliberately (fallback mode), so we allow it through.
        if settings is not None and model_slug is None:
            logger.debug(
                "selector: skipping provider, no model mapping for request",
                provider=provider_name,
                request_type=request.type.value,
            )
            continue

        # ── Capability check ────────────────────────────────────────────
        if settings is not None:
            cap = capability_module.get(provider_name, model_slug, settings)
            if request.needs_tools and not cap.supports_tools:
                logger.debug(
                    "selector: skipping provider, no tool support",
                    provider=provider_name,
                    model_slug=model_slug,
                )
                continue
            if request.needs_vision and not cap.supports_vision:
                logger.debug(
                    "selector: skipping provider, no vision support",
                    provider=provider_name,
                    model_slug=model_slug,
                )
                continue

        # ── Key selection (cooldown + circuit breaker + bucket, atomic) ─
        result = await key_pool.pick_key(request.estimated_tokens)
        if result is None:
            logger.debug(
                "selector: skipping provider, no available key",
                provider=provider_name,
            )
            continue

        api_key, key_index = result

        logger.debug(
            "selector: selected provider",
            provider=provider_name,
            key_index=key_index,
            model_slug=model_slug,
        )
        return provider, api_key, key_index

    return None


def _default_config(registry: "ProviderRegistry") -> SelectorConfig:
    """
    Build a permissive default `SelectorConfig` purely from what's
    registered in *registry*: every registered provider, in registry
    (insertion) order, treated as enabled. No by-type overrides.

    This is what `select()` falls back to when no explicit `config` is
    supplied — which happens whenever `providers.base.BaseProvider.stream()`
    calls through to the absorber on a 429, since that call path passes no
    config at all.
    """
    names = registry.all_enabled()
    return SelectorConfig(
        provider_chain=names,
        providers={name: ProviderEnableConfig(enabled=True) for name in names},
    )