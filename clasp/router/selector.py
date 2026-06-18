"""
clasp/router/selector.py
============================
`select()` — walk the provider chain, check capability + key-pool capacity,
return a ready-to-use `(provider, api_key, key_index)` (plan.md §13 "Phase 5
— Multi-Provider Chain & Smart Routing", "Provider Selector
(`router/selector.py`)").

This is a faithful implementation of the pseudocode given in plan.md §13,
adapted to call the real functions built alongside it this turn:
`model_map.resolve_model()`, `capability.get()`, and
`registry.get_key_pool()` (now wired to real `KeyPool` instances rather
than the Sprint-1 `None` stub — see `providers/registry.py`).

`AnthropicRequest`
--------------------
Referenced throughout plan.md (`request.type`, `request.needs_tools`,
`request.needs_vision`, `request.estimated_tokens`, `request.priority`,
`request.model`) but never given an explicit dataclass definition or file
location anywhere in the spec. It's defined here because this module is its
primary consumer per the `select()` signature plan.md gives
(`async def select(request: AnthropicRequest, ...)`). `api/service.py`
constructs one per incoming request (via `api/detect.py` + `token_counter`)
and imports the type from here; later phases (response cache, absorber/
queue) that also reference `AnthropicRequest` in plan.md should import it
from here too, rather than each defining their own.

References: plan.md §13 "Provider Selector (`router/selector.py`)" (the
            `select()` algorithm below mirrors that pseudocode line-for-
            line, with real module calls substituted in), §11 (priority
            levels), §8 [4] (detect → estimate tokens → select).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover — shim for test environments
    import logging as _logging

    class _Shim:
        _log = _logging.getLogger("clasp.selector")

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

        def info(self, msg: str, **kw: Any) -> None:
            self._log.info(msg + ("  " + str(kw) if kw else ""))

    logger = _Shim()  # type: ignore[assignment]

from clasp.api.detect import RequestType
from clasp.router import capability, model_map
from clasp.providers import registry

if TYPE_CHECKING:
    from clasp.providers.base import BaseProvider


# ---------------------------------------------------------------------------
# AnthropicRequest
# ---------------------------------------------------------------------------

@dataclass
class AnthropicRequest:
    """
    A classified, pre-flight-estimated request ready for provider selection.

    Built by `api/service.py` from the raw request body via `api/detect.py`
    (for `type`/`needs_tools`/`needs_vision`/`priority`) and
    `providers/common/token_counter.py` (for `estimated_tokens`).

    Fields
    ------
    type:
        Classification from `api/detect.py`'s `detect()`.
    model:
        The Claude model name the client actually requested (e.g.
        ``"claude-sonnet-4-5-20250929"``) — used by `model_map.resolve_model()`
        for tier-keyword matching.
    body:
        The raw, original Anthropic-format request body. Carried through so
        `provider.stream()` has the full payload to translate, without the
        selector needing to know provider-specific transport details.
    needs_tools / needs_vision:
        Independent capability gates (see `api/detect.py` module docstring
        for why these are separate from `type`).
    estimated_tokens:
        Pre-flight token estimate (`token_counter.estimate_request_tokens()`),
        used both for the `max_context_tokens` capability check and as the
        TPM cost passed to `KeyPool.pick_key()`.
    priority:
        Queue priority (`api/detect.py`'s `priority_for()`) — not consumed
        by `select()` itself, but carried here since plan.md's queue
        manager (`queue/manager.py`, a later phase) reads it directly off
        this same object.
    request_id:
        Optional correlation id for logging, set by `api/service.py`.
    """

    type: RequestType
    model: str
    body: dict[str, Any] = field(default_factory=dict)
    needs_tools: bool = False
    needs_vision: bool = False
    estimated_tokens: int = 0
    priority: int = 0
    request_id: str | None = None


# ---------------------------------------------------------------------------
# select()
# ---------------------------------------------------------------------------

async def select(
    request: AnthropicRequest,
    exclude: set[str] | None = None,
) -> tuple["BaseProvider", str, int] | None:
    """
    Walk the provider chain and return the first
    ``(provider, api_key, key_index)`` that can serve *request* right now.

    Candidate order
    ------------------
    `settings.routing.by_type` is checked for an override matching
    `request.type` — if present (and not in *exclude*), that provider is
    tried *first*, then the rest of `provider_chain` in its configured
    order as fallback. Without an override, `provider_chain` is tried as-is.

    For each candidate, in order:

    1. Skip if excluded, disabled, or not registered (`registry.get()`).
    2. Resolve a model slug via `model_map.resolve_model()` — skip if this
       provider isn't configured to serve this request's tier/type at all.
    3. Capability gate via `capability.get()` — skip if the request needs
       tools/vision this provider+model doesn't support, or if the
       estimated input would exceed 90% of its context window.
    4. Key availability via `registry.get_key_pool().pick_key()` — skip if
       every key for this provider is currently rate-limited/cooling/
       circuit-open.

    The first candidate that survives all four checks wins. Returns
    ``None`` if no candidate in the entire chain can serve the request.

    Parameters
    ----------
    exclude:
        Provider names to skip outright — used by callers retrying after a
        provider-side failure mid-stream, to avoid immediately re-selecting
        the same provider that just failed.
    """
    from clasp.config.settings import get_settings

    settings = get_settings()
    exclude = exclude or set()

    # Build candidate order. by_type override goes first, if not excluded.
    by_type = model_map._as_dict(getattr(settings.routing, "by_type", None))
    override_value = by_type.get(request.type.value) or ""
    override_name = override_value.split("/")[0] if override_value else ""
    if override_name and override_name not in exclude:
        candidates = [override_name] + [
            p for p in settings.provider_chain if p != override_name
        ]
    else:
        candidates = list(settings.provider_chain)

    logger.debug("selector: evaluating candidates", request_id=request.request_id,
                type=request.type.value, candidates=candidates, exclude=sorted(exclude))

    for provider_name in candidates:
        if provider_name in exclude:
            continue

        cfg = settings.providers.get(provider_name)
        if not cfg or not cfg.enabled:
            logger.debug("selector: skip (disabled/unconfigured)", provider=provider_name)
            continue

        provider = registry.get(provider_name)
        if not provider:
            logger.debug("selector: skip (not registered)", provider=provider_name)
            continue

        # Model resolution
        model_slug = model_map.resolve_model(request, provider_name, settings)
        if not model_slug:
            logger.debug("selector: skip (no model mapping for this tier/type)",
                         provider=provider_name, requested_model=request.model)
            continue

        # Capability check
        caps = capability.get(provider_name, model_slug, settings)
        if request.needs_tools and not caps.supports_tools:
            logger.debug("selector: skip (needs tools, unsupported)", provider=provider_name,
                         model_slug=model_slug)
            continue
        if request.needs_vision and not caps.supports_vision:
            logger.debug("selector: skip (needs vision, unsupported)", provider=provider_name,
                         model_slug=model_slug)
            continue
        if request.estimated_tokens > caps.max_context_tokens * 0.9:
            logger.debug("selector: skip (exceeds 90% of context window)", provider=provider_name,
                         estimated_tokens=request.estimated_tokens,
                         max_context_tokens=caps.max_context_tokens)
            continue

        # Key availability
        key_pool = registry.get_key_pool(provider_name)
        if key_pool is None:
            logger.debug("selector: skip (no key pool registered)", provider=provider_name)
            continue

        result = await key_pool.pick_key(request.estimated_tokens)
        if result:
            api_key, key_idx = result
            logger.info("selector: selected", request_id=request.request_id,
                       provider=provider_name, model_slug=model_slug, key_index=key_idx,
                       type=request.type.value)
            return provider, api_key, key_idx

        logger.debug("selector: skip (no healthy key available)", provider=provider_name)

    logger.info("selector: no provider available", request_id=request.request_id,
               type=request.type.value, candidates=candidates, exclude=sorted(exclude))
    return None