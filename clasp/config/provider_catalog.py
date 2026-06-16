"""
clasp/config/provider_catalog.py

Static catalog of every supported provider.  User config overlays these defaults
(e.g. to override rpm_limit for a personal account with higher quotas).

The catalog is read-only at runtime; it is never mutated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# ---------------------------------------------------------------------------
# ProviderProfile
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderProfile:
    """Immutable description of one upstream AI provider."""

    display_name: str
    """Human-readable name shown in the web UI."""

    base_url: str
    """Root URL for API calls.  No trailing slash."""

    transport: Literal["openai_chat", "anthropic_messages"]
    """Wire protocol to use when talking to this provider."""

    # ── Rate-limit defaults ────────────────────────────────────────────────
    rpm_limit: int
    """Maximum requests per minute on the free tier (hard ceiling)."""

    tpm_limit: int | None
    """Maximum tokens per minute, or None if not enforced."""

    daily_token_limit: int | None
    """Maximum tokens per day, or None if not enforced."""

    rpm_soft_threshold: float
    """Fraction of rpm_limit at which pre-emptive backpressure kicks in.
    0.80 means: start routing away when 80 % of quota is consumed."""

    cooldown_seconds: int
    """Seconds to wait after a 429 before retrying this provider."""

    backoff_base_seconds: int
    """Base for exponential back-off on repeated failures."""

    # ── Capabilities ──────────────────────────────────────────────────────
    supports_tools: bool
    """Whether the provider/model family reliably handles function/tool calls."""

    supports_vision: bool
    """Whether image content blocks are accepted."""

    supports_thinking: bool
    """Whether extended-thinking / chain-of-thought is supported."""

    max_context_tokens: int
    """Maximum combined input + output tokens per request."""

    # ── Metadata ──────────────────────────────────────────────────────────
    tier: Literal["free", "free_credits", "local", "paid"]
    """Pricing category:
      free         — permanently free, no credit card required.
      free_credits — free signup credits that eventually run out.
      local        — runs on the user's own machine (unlimited).
      paid         — always costs money.
    """

    free_tier_note: str
    """One-line blurb shown under the provider name in the Providers panel."""


# ---------------------------------------------------------------------------
# PROVIDER_CATALOG
# ---------------------------------------------------------------------------

PROVIDER_CATALOG: dict[str, ProviderProfile] = {

    # ── Cloud free-tier providers ──────────────────────────────────────────

    "nvidia_nim": ProviderProfile(
        display_name="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        transport="openai_chat",
        rpm_limit=40,
        tpm_limit=None,
        daily_token_limit=None,
        rpm_soft_threshold=0.80,
        cooldown_seconds=60,
        backoff_base_seconds=60,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=True,
        max_context_tokens=128_000,
        tier="free",
        free_tier_note="40 RPM free • build.nvidia.com",
    ),

    "gemini": ProviderProfile(
        display_name="Google Gemini AI Studio",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        transport="openai_chat",
        rpm_limit=15,
        tpm_limit=1_000_000,
        daily_token_limit=1_000_000,
        rpm_soft_threshold=0.80,
        cooldown_seconds=60,
        backoff_base_seconds=60,
        supports_tools=True,
        supports_vision=True,
        supports_thinking=True,
        max_context_tokens=1_000_000,
        tier="free",
        free_tier_note="15 RPM free • 1 M tokens/day • aistudio.google.com",
    ),

    "cerebras": ProviderProfile(
        display_name="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        transport="openai_chat",
        rpm_limit=30,
        tpm_limit=60_000,
        daily_token_limit=None,
        rpm_soft_threshold=0.80,
        cooldown_seconds=60,
        backoff_base_seconds=30,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=True,
        max_context_tokens=128_000,
        tier="free",
        free_tier_note="30 RPM free • Ultra-fast inference",
    ),

    "groq": ProviderProfile(
        display_name="Groq",
        base_url="https://api.groq.com/openai/v1",
        transport="openai_chat",
        rpm_limit=30,
        tpm_limit=6_000,
        daily_token_limit=None,
        rpm_soft_threshold=0.80,
        cooldown_seconds=60,
        backoff_base_seconds=30,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=False,
        max_context_tokens=32_768,
        tier="free",
        free_tier_note="30 RPM free • console.groq.com",
    ),

    "openrouter": ProviderProfile(
        display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        transport="openai_chat",
        rpm_limit=20,
        tpm_limit=None,
        daily_token_limit=None,
        rpm_soft_threshold=0.80,
        cooldown_seconds=60,
        backoff_base_seconds=60,
        supports_tools=True,
        supports_vision=True,
        supports_thinking=True,
        max_context_tokens=128_000,
        tier="free",
        free_tier_note="Free models (use :free suffix) • openrouter.ai",
    ),

    "mistral": ProviderProfile(
        display_name="Mistral AI",
        base_url="https://api.mistral.ai/v1",
        transport="openai_chat",
        rpm_limit=30,
        tpm_limit=None,
        daily_token_limit=None,
        rpm_soft_threshold=0.80,
        cooldown_seconds=60,
        backoff_base_seconds=60,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=False,
        max_context_tokens=32_768,
        tier="free",
        free_tier_note="Free experiment plan • console.mistral.ai",
    ),

    # ── Free-credits providers (signup bonus, exhaustible) ─────────────────

    "fireworks": ProviderProfile(
        display_name="Fireworks AI",
        # Fireworks exposes an Anthropic-compatible endpoint directly.
        base_url="https://api.fireworks.ai/inference/v1/messages",
        transport="anthropic_messages",
        rpm_limit=600,
        tpm_limit=None,
        daily_token_limit=None,
        rpm_soft_threshold=0.80,
        cooldown_seconds=60,
        backoff_base_seconds=60,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=False,
        max_context_tokens=131_072,
        tier="free_credits",
        free_tier_note="Free credits on signup • fireworks.ai",
    ),

    "together": ProviderProfile(
        display_name="together.ai",
        base_url="https://api.together.xyz/v1",
        transport="openai_chat",
        rpm_limit=60,
        tpm_limit=None,
        daily_token_limit=None,
        rpm_soft_threshold=0.80,
        cooldown_seconds=60,
        backoff_base_seconds=60,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=False,
        max_context_tokens=32_768,
        tier="free_credits",
        free_tier_note="Free credits on signup • together.ai",
    ),

    # ── Local / self-hosted providers (unlimited, no API key) ─────────────

    "ollama": ProviderProfile(
        display_name="Ollama (local)",
        base_url="http://localhost:11434",
        transport="openai_chat",
        # Artificially high so the rate limiter never blocks local requests.
        rpm_limit=9_999,
        tpm_limit=None,
        daily_token_limit=None,
        # Never back off — it's local hardware.
        rpm_soft_threshold=1.0,
        cooldown_seconds=0,
        backoff_base_seconds=0,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=False,
        max_context_tokens=32_768,
        tier="local",
        free_tier_note="Unlimited • Runs on your machine • ollama.com",
    ),

    "lm_studio": ProviderProfile(
        display_name="LM Studio (local)",
        base_url="http://localhost:1234",
        transport="openai_chat",
        rpm_limit=9_999,
        tpm_limit=None,
        daily_token_limit=None,
        rpm_soft_threshold=1.0,
        cooldown_seconds=0,
        backoff_base_seconds=0,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=False,
        max_context_tokens=32_768,
        tier="local",
        free_tier_note="Unlimited • Runs on your machine • lmstudio.ai",
    ),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_profile(provider_name: str) -> ProviderProfile:
    """Return the catalog entry for *provider_name*, raising KeyError if unknown."""
    try:
        return PROVIDER_CATALOG[provider_name]
    except KeyError:
        known = ", ".join(sorted(PROVIDER_CATALOG))
        raise KeyError(
            f"Unknown provider {provider_name!r}. Known providers: {known}"
        ) from None