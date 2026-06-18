"""
clasp/router/model_map.py
============================
`resolve_model()` — Claude tier (opus/sonnet/haiku/fable/default) → concrete
provider/model slug (plan.md §13 "Model Resolution (`router/model_map.py`)").

Matching order (first hit wins), **all evaluated against the specific
`provider_name` the caller is asking about** — this function answers
"if I'm considering *this* provider for *this* request, what model slug
should I ask it for?", not "what's the single best model overall":

1. **by_type override** — `settings.routing.by_type[request.type.value]`,
   e.g. `by_type.think = "nvidia_nim/moonshotai/kimi-k2-thinking"`. Only
   four of the seven `RequestType` values typically have an entry in the
   example config (`think`, `long_context`, `background`, `vision`) — the
   others simply fall through to step 2.
2. **Tier keyword in model name** — the Claude model name the client
   actually requested (e.g. `"claude-sonnet-4-5-20250929"`) is checked for
   each configured tier keyword as a case-insensitive substring.
   `claude-fable-*` matching "fable" is the example plan.md gives, but this
   works generically for any tier key present in `routing.models` (so a
   user can add custom tiers without code changes) — the canonical four
   (`opus`, `sonnet`, `haiku`, `fable`) are checked first in that fixed
   order for predictability, then any other custom keys in whatever order
   the config dict provides them.
3. **default** — `settings.routing.models["default"]`.

At every step, the resolved `"provider/slug"` string's provider half must
equal *provider_name* for that step to count as a hit — per plan.md: "Returns
the model slug portion for the given provider, **or None if provider doesn't
match**." This means a provider not explicitly wired up for this request's
tier (in `by_type`, the matched tier, or `default`) is correctly reported as
unable to serve this request — `router/selector.py` then moves on to the
next candidate in `provider_chain`. (Real cross-provider failover for a
given Claude tier therefore depends on the user's `config.yaml` actually
pointing more than one provider at the same tier/default slot — this
function implements the matching algorithm exactly as specified, it doesn't
invent additional cross-provider equivalence on its own.)

References: plan.md §13 "Model Resolution (`router/model_map.py`)",
            §6 (`routing.models` / `routing.by_type` config schema).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover — shim for test environments
    import logging as _logging

    class _Shim:
        _log = _logging.getLogger("clasp.model_map")

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

    logger = _Shim()  # type: ignore[assignment]

if TYPE_CHECKING:
    from clasp.config.settings import Settings
    from clasp.router.selector import AnthropicRequest

#: Canonical tier keys checked first, in this fixed order, for predictable
#: matching when a model name happens to contain more than one tier keyword.
#: Any additional custom keys in `routing.models` (besides "default") are
#: checked afterward, in whatever order the config dict provides.
_CANONICAL_TIER_ORDER: tuple[str, ...] = ("opus", "sonnet", "haiku", "fable")


def _as_dict(value: Any) -> dict[str, str]:
    """
    Normalize *value* to a plain ``dict[str, str]``.

    `settings.routing.models` / `settings.routing.by_type` may be a plain
    dict (as in lightweight/test `Settings` stubs) or a typed Pydantic
    model with fixed fields like ``ModelRoutes``/``ByTypeRoutes`` (the real
    `config/settings.py`). Both need to support `.get()` and key iteration
    here, so a typed model is converted via `.model_dump()` rather than
    assumed to already behave like a dict.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    return dict(value)


def _split_provider_slug(value: str) -> tuple[str, str]:
    """Split a `"provider/model/slug/with/segments"` config string into
    `(provider, slug)`. Only the first `/` is significant — model slugs
    routinely contain their own `/` (e.g. `"moonshotai/kimi-k2-thinking"`)."""
    provider, _, slug = value.partition("/")
    return provider, slug


def _tier_keys_in_order(routing_models: dict[str, str]) -> list[str]:
    """All tier keys to check (excluding "default"), canonical ones first."""
    keys = [k for k in _CANONICAL_TIER_ORDER if k in routing_models]
    keys += [k for k in routing_models if k != "default" and k not in _CANONICAL_TIER_ORDER]
    return keys


def resolve_model(
    request: "AnthropicRequest",
    provider_name: str,
    settings: "Settings",
) -> str | None:
    """
    Resolve the model slug *provider_name* should be asked for, to serve
    *request*. Returns ``None`` if this provider isn't configured to serve
    this request's type/tier at all.

    See module docstring for the full three-step matching order.
    """
    routing_models: dict[str, str] = _as_dict(getattr(settings.routing, "models", None))
    by_type: dict[str, str] = _as_dict(getattr(settings.routing, "by_type", None))

    # 1. by_type override
    type_key = getattr(request.type, "value", request.type)
    override_value = by_type.get(type_key)
    if override_value:
        prov, slug = _split_provider_slug(override_value)
        if prov == provider_name and slug:
            logger.debug("model_map: resolved via by_type override",
                         provider=provider_name, type=type_key, slug=slug)
            return slug

    # 2. tier keyword in requested model name
    requested_model = (request.model or "").lower()
    if requested_model:
        for tier in _tier_keys_in_order(routing_models):
            if tier in requested_model:
                mapped = routing_models.get(tier)
                if mapped:
                    prov, slug = _split_provider_slug(mapped)
                    if prov == provider_name and slug:
                        logger.debug("model_map: resolved via tier match",
                                     provider=provider_name, tier=tier, slug=slug)
                        return slug
                # Tier matched the model name but this provider isn't the
                # one configured for it — don't keep checking other tiers;
                # the model name's tier is unambiguous once matched.
                break

    # 3. default
    default_mapped = routing_models.get("default")
    if default_mapped:
        prov, slug = _split_provider_slug(default_mapped)
        if prov == provider_name and slug:
            logger.debug("model_map: resolved via default", provider=provider_name, slug=slug)
            return slug

    logger.debug("model_map: no mapping for provider", provider=provider_name,
                requested_model=request.model)
    return None


#: Alias matching the function name used in plan.md §13's selector.py
#: pseudocode (`model_map.resolve(...)`), so either spelling works.
resolve = resolve_model