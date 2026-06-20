"""
clasp/router/capability.py
=============================
Per-model capability flags — `capability.get(provider_name, model_slug) →
Capability` (plan.md §13 `router/selector.py` pseudocode; directory
description: "Per-model capability flags: tools, vision, thinking,
max_context_tokens. Built from catalog + user overrides.").

Three layers, each able to override the one before it:

1. **Catalog default** — `PROVIDER_CATALOG[provider_name]` (plan.md §7).
   Provider-wide defaults: e.g. NVIDIA NIM as a whole supports tools and
   thinking but not vision.
2. **User config override** — `settings.providers[provider_name].supports_*`
   / `.max_context_tokens` (plan.md §6: "# Uncomment to override catalog
   default."). Still provider-wide, but lets a user correct the catalog if
   it's wrong for their specific deployment.
3. **Model-slug quirk override** — `MODEL_CAPABILITY_OVERRIDES`, matched by
   substring against *model_slug*. A single provider often hosts many
   different underlying models with different real capabilities (e.g. NIM
   hosts both `kimi-k2-thinking`, which genuinely supports extended
   thinking, and smaller Llama models that don't) — this is the layer that
   makes `capability.get()` actually use its `model_slug` parameter instead
   of just being a provider lookup in disguise. See plan.md's "NVIDIA NIM
   Model Quirks" note, which explicitly calls out the kimi-k2 thinking
   parameter difference and says to make it "per-model configurable in
   `capability.py`" — this table is that configuration point.

If *provider_name* isn't in the catalog at all, `get()` returns a
conservative all-`False` / small-context `Capability` rather than raising,
since an unknown provider should never accidentally look more capable than
it is.

References: plan.md §7 (ProviderProfile/PROVIDER_CATALOG), §13 (selector.py
            calls `capability.get(provider_name, model_slug)`), "NVIDIA NIM
            Model Quirks" (model-specific thinking parameter differences).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover — shim for test environments
    import logging as _logging

    class _Shim:
        _log = _logging.getLogger("clasp.capability")

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

        def warning(self, msg: str, **kw: Any) -> None:
            self._log.warning(msg + ("  " + str(kw) if kw else ""))

    logger = _Shim()  # type: ignore[assignment]

from clasp.config.provider_catalog import PROVIDER_CATALOG

if TYPE_CHECKING:
    from clasp.config.settings import Settings


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Capability:
    """Resolved capability flags for one (provider, model_slug) pair."""

    supports_tools: bool
    supports_vision: bool
    supports_thinking: bool
    max_context_tokens: int


#: Conservative fallback for a provider/model we know nothing about.
_UNKNOWN_CAPABILITY = Capability(
    supports_tools=False,
    supports_vision=False,
    supports_thinking=False,
    max_context_tokens=4096,
)


# ---------------------------------------------------------------------------
# Model-slug quirk overrides
# ---------------------------------------------------------------------------
# Keyed by a case-insensitive substring matched against `model_slug`. First
# match wins (dict iteration order = declaration order below). Only fields
# that actually differ from the provider's catalog default need to be
# listed — anything omitted falls through to the provider-level value.
#
# See plan.md "NVIDIA NIM Model Quirks": kimi-k2 / kimi-k2-thinking use a
# different extended-thinking parameter shape than other NIM-hosted models,
# but they DO genuinely support it, unlike most of the rest of NIM's catalog.
# ---------------------------------------------------------------------------

MODEL_CAPABILITY_OVERRIDES: dict[str, dict[str, Any]] = {
    "kimi-k2-thinking": {"supports_thinking": True, "max_context_tokens": 256_000},
    "kimi-k2": {"supports_thinking": True},
    "llama-3.1-nemotron-70b": {"supports_thinking": False},
    "gemini-2.5-flash": {"supports_vision": True, "supports_thinking": True},
    "gemini-2.5-pro": {"supports_vision": True, "supports_thinking": True, "max_context_tokens": 2_000_000},
    "llama3.1-8b": {"supports_thinking": False, "max_context_tokens": 8192},
}


def _model_overrides_for(model_slug: str | None) -> dict[str, Any]:
    """Return the first matching quirk-override dict for *model_slug*, or {}."""
    if not model_slug:
        return {}
    lowered = model_slug.lower()
    for pattern, overrides in MODEL_CAPABILITY_OVERRIDES.items():
        if pattern in lowered:
            return overrides
    return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get(
    provider_name: str,
    model_slug: str | None = None,
    settings: "Settings | None" = None,
) -> Capability:
    """
    Resolve capability flags for *provider_name* (+ optionally *model_slug*).

    Layering, each overriding the previous: catalog default → user config
    override (`settings.providers[provider_name]`) → model-slug quirk
    override (`MODEL_CAPABILITY_OVERRIDES`).

    Parameters
    ----------
    provider_name:
        Catalog key, e.g. ``"nvidia_nim"``.
    model_slug:
        The concrete model slug being considered for this provider (e.g.
        ``"moonshotai/kimi-k2-thinking"``). Optional — if omitted, only the
        catalog/user-override layers apply.
    settings:
        Loaded `Settings`. If omitted, `get_settings()` is called lazily
        (imported here, not at module load, to avoid a hard dependency for
        callers that already have a `Settings` instance in hand — e.g.
        tests constructing one directly).

    Returns
    -------
    Capability
        Always returns a value — never raises for an unknown provider.
    """
    profile = PROVIDER_CATALOG.get(provider_name)
    if profile is None:
        logger.warning("capability: unknown provider, returning conservative defaults",
                       provider=provider_name)
        return _UNKNOWN_CAPABILITY

    supports_tools = profile.supports_tools
    supports_vision = profile.supports_vision
    supports_thinking = profile.supports_thinking
    max_context_tokens = profile.max_context_tokens

    # Layer 2 (applied first): model-slug quirk override.
    # These are built-in adjustments for models whose real capabilities differ
    # from the provider-level catalog default (e.g. kimi-k2-thinking on NIM).
    overrides = _model_overrides_for(model_slug)
    supports_tools = overrides.get("supports_tools", supports_tools)
    supports_vision = overrides.get("supports_vision", supports_vision)
    supports_thinking = overrides.get("supports_thinking", supports_thinking)
    max_context_tokens = overrides.get("max_context_tokens", max_context_tokens)

    # Layer 3 (applied last, wins): user config override (provider-wide).
    # An explicit user correction always takes precedence over any built-in
    # quirk table entry — the user knows their deployment better than we do.
    if settings is None:
        from clasp.config.settings import get_settings
        settings = get_settings()

    cfg = settings.providers.get(provider_name)
    if cfg is not None:
        if cfg.supports_tools is not None:
            supports_tools = cfg.supports_tools
        if cfg.supports_vision is not None:
            supports_vision = cfg.supports_vision
        if cfg.supports_thinking is not None:
            supports_thinking = cfg.supports_thinking
        if cfg.max_context_tokens is not None:
            max_context_tokens = cfg.max_context_tokens

    result = Capability(
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        supports_thinking=supports_thinking,
        max_context_tokens=max_context_tokens,
    )
    logger.debug("capability: resolved", provider=provider_name, model_slug=model_slug,
                result=result)
    return result