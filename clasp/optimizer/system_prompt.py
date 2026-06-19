"""
clasp/optimizer/system_prompt.py
====================================
System prompt hash detection, a Gemini prefix-caching eligibility hint, and
trimming for small-context providers (plan.md §14 "Phase 6 — Payload
Optimization"; directory tree: "system_prompt.py — System prompt dedup,
compression, prefix caching."; CLAUDE.md task 1).

Three responsibilities, each independently usable:

1. ``hash_system_prompt()`` — a stable SHA-256 fingerprint of the system
   prompt's actual text content. Anthropic-native ``cache_control``
   annotations are stripped before hashing ("compression" in the directory
   tree's one-liner) since they're meaningless metadata for every provider
   in this catalog — none of them is the Anthropic API itself, so a
   ``cache_control: {"type": "ephemeral"}`` block Claude Code attached for
   *its own* prompt-caching with the real Anthropic API carries no signal
   for a request being rerouted to Gemini/NIM/Groq/etc. The hash is the
   building block for "is this the same system prompt as last time" —
   which is exactly what `should_prefer_implicit_cache()` (below) needs.

2. ``gemini_cache_hint()`` — a best-effort signal for whether *this*
   request is likely to land an implicit-cache hit on Gemini. This is
   deliberately just a hint, not an action: Gemini's OpenAI-compatible
   endpoint (the one `provider_catalog.py` configures for `gemini`) has no
   body field to "request" implicit caching — per Google's own docs,
   implicit caching is automatic and on by default for Gemini 2.5+ models,
   with no API call needed to enable it. (Google does also expose
   *explicit* caching — a separate stateful `CachedContent` resource with
   create/TTL/delete lifecycle management, reachable from the OpenAI-style
   endpoint via `extra_body.cached_content` — but that's a meaningfully
   bigger feature with its own resource lifecycle, not "a hint", so it's
   out of scope here.) What CLASP *can* act on: knowing a hit is likely
   means the system prompt's exact text/position shouldn't be needlessly
   churned for this provider, since implicit caching depends on byte-stable
   prefixes repeated within a short window.

   The eligibility heuristic mirrors Google's own published guidance:
   the system prompt must be at or above the model's minimum cacheable
   size (this implementation uses 1024 tokens for Flash-family slugs and
   2048 for everything else, per Google's 2.5-models announcement —
   thresholds Google has changed before and may change again, so treat
   these as best-effort defaults, not a guarantee), and ideally this isn't
   the first time this exact prompt has been seen (a first call always
   *writes* the cache; only a repeat within a short window can *hit* it).

3. ``trim_system_prompt()`` — for genuinely small-context providers (Groq,
   Mistral at 32k), Claude Code's system prompt (tool registry + project
   context) can be large enough to crowd out the actual conversation. This
   trims from the *middle*, preserving a prefix (core instructions, which
   tend to be the most load-bearing part) and a suffix (the most recently
   appended context, often the freshest/most relevant), with a marker
   noting what was cut — same spirit as `context_pruner.py`'s
   `keep_edges` strategy, applied to one text blob instead of a message
   list. Large-context providers (Gemini, NVIDIA NIM) are never trimmed —
   doing so would be pointless (they have the room) and actively harmful
   for Gemini specifically, since it would break the byte-stable prefix
   implicit caching depends on.

Integration note: nothing in this module is wired into `api/service.py`
yet — that's a separate "use the optimizer in the pipeline" step (plan.md
§8 stage [7]), not part of this turn's scope. `process_system_prompt()` is
the single entry point a future integration would call.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover — shim for test environments
    import logging as _logging

    class _Shim:
        _log = _logging.getLogger("clasp.system_prompt")

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

    logger = _Shim()  # type: ignore[assignment]

from clasp.providers.common.token_counter import estimate_tokens


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------

def extract_system_text(system: str | list[Any] | None) -> str:
    """
    Normalize Anthropic's ``system`` field — a plain string, or a list of
    content blocks (each optionally carrying an Anthropic-native
    ``cache_control`` annotation) — into one canonical string.

    ``cache_control`` blocks are deliberately ignored here: they're
    Claude-API-specific caching metadata, meaningless to every provider
    this proxy routes to, and would otherwise make two semantically
    identical system prompts hash differently just because Claude Code
    added/removed a caching breakpoint.
    """
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        parts: list[str] = []
        for block in system:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def hash_system_prompt(system: str | list[Any] | None) -> str | None:
    """
    Stable SHA-256 hex digest of the canonical system prompt text.

    Returns ``None`` (not a hash of an empty string) when there's no system
    prompt at all, so callers can distinguish "no system prompt" from
    "an empty/whitespace-only one" if that distinction ever matters.
    """
    text = extract_system_text(system)
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Recent-hash tracking (for the Gemini cache hint's "seen recently" signal)
# ---------------------------------------------------------------------------
# Deliberately simple: a bounded, module-level dict of {hash: last_seen
# monotonic time}. This is a single-process, in-memory heuristic — it does
# not need to be perfectly accurate (a restart or a second CLASP process
# just means a momentarily pessimistic "first time seen" guess, never an
# incorrect cache *action* since this module takes no caching action of its
# own) and does not need locking: every call site in this codebase is
# single-threaded asyncio, so there's no concurrent-mutation risk.

_MAX_TRACKED_HASHES = 1000
_last_seen: dict[str, float] = {}


def _record_seen(prompt_hash: str) -> float | None:
    """Record *prompt_hash* as seen now; return the *previous* seen time, if any."""
    previous = _last_seen.get(prompt_hash)
    _last_seen[prompt_hash] = time.monotonic()
    if len(_last_seen) > _MAX_TRACKED_HASHES:
        oldest_key = min(_last_seen, key=_last_seen.get)
        del _last_seen[oldest_key]
    return previous


def reset_recent_hash_tracking() -> None:
    """Clear all tracked hashes. Mainly useful for tests."""
    _last_seen.clear()


# ---------------------------------------------------------------------------
# Gemini implicit-cache eligibility hint
# ---------------------------------------------------------------------------

#: Google's documented minimum input size for an implicit-cache hit, by
#: model family. Flash-family slugs got a reduced threshold in Google's
#: 2.5-models implicit-caching announcement; everything else defaults to
#: the higher figure. Google has changed these numbers before — treat as
#: best-effort, not authoritative.
_GEMINI_MIN_CACHEABLE_TOKENS_FLASH = 1024
_GEMINI_MIN_CACHEABLE_TOKENS_DEFAULT = 2048

#: How recently the same prompt must have been seen to call a hit "likely".
#: Google doesn't publish an exact implicit-cache TTL (only explicit
#: caching's configurable TTL, which defaults to 60 minutes, is
#: documented) — this reuses that figure as a conservative proxy.
_RECENT_WINDOW_SECONDS = 60 * 60


@dataclass(frozen=True)
class GeminiCacheHint:
    """Best-effort signal for whether a request is likely to hit Gemini's implicit cache."""

    eligible_by_size: bool
    """System prompt is at/above the model's minimum cacheable token count."""
    seen_recently: bool
    """The same exact system prompt (by hash) was seen within the recent window."""
    likely_hit: bool
    """``eligible_by_size and seen_recently`` — convenience combination of the above."""
    min_cacheable_tokens: int
    """Threshold used for ``eligible_by_size``, for logging/debugging."""
    estimated_tokens: int
    """The system prompt's estimated token count, for logging/debugging."""


def gemini_cache_hint(system: str | list[Any] | None, model_slug: str) -> GeminiCacheHint:
    """
    Best-effort hint for whether *system* is likely to land a Gemini
    implicit-cache hit for *model_slug*. Pure read — records the prompt as
    "seen" for future calls' `seen_recently` checks as a side effect (the
    intended usage is one call per actual request sent to Gemini).

    Does not call any API and does not change the request in any way; see
    the module docstring for why there's nothing to "set" here at all.
    """
    estimated = estimate_tokens([], system=system)
    is_flash = "flash" in (model_slug or "").lower()
    min_tokens = _GEMINI_MIN_CACHEABLE_TOKENS_FLASH if is_flash else _GEMINI_MIN_CACHEABLE_TOKENS_DEFAULT
    eligible_by_size = estimated >= min_tokens

    prompt_hash = hash_system_prompt(system)
    seen_recently = False
    if prompt_hash is not None:
        previous = _record_seen(prompt_hash)
        if previous is not None:
            seen_recently = (time.monotonic() - previous) <= _RECENT_WINDOW_SECONDS

    hint = GeminiCacheHint(
        eligible_by_size=eligible_by_size,
        seen_recently=seen_recently,
        likely_hit=eligible_by_size and seen_recently,
        min_cacheable_tokens=min_tokens,
        estimated_tokens=estimated,
    )
    logger.debug("system_prompt: gemini cache hint", model_slug=model_slug, hint=hint)
    return hint


# ---------------------------------------------------------------------------
# Trimming for small-context providers
# ---------------------------------------------------------------------------

#: At most this fraction of a provider's context window is allowed to go to
#: the system prompt alone — the rest must remain for conversation history,
#: tool definitions, and the response itself.
_MAX_SYSTEM_PROMPT_FRACTION = 0.25

#: ...and never more than this many tokens regardless of how generous that
#: fraction would otherwise allow, so even a huge-context provider doesn't
#: let an unbounded system prompt through unchecked. (Large-context
#: providers like Gemini/NIM are excluded from trimming entirely by
#: `should_trim()` below, so this cap mainly bounds mid-sized providers —
#: e.g. Cerebras at 128k.)
_SYSTEM_PROMPT_HARD_CAP_TOKENS = 8000

#: Trimming is only even considered below this max_context_tokens. Above
#: it, a provider is treated as "large enough that the system prompt isn't
#: the bottleneck" — also true for Gemini specifically because trimming
#: would break the stable prefix implicit caching needs (see module
#: docstring), so excluding it here serves both reasons at once.
_SMALL_CONTEXT_THRESHOLD_TOKENS = 64_000

_TRIM_MARKER_TEMPLATE = (
    "\n\n[CLASP: system prompt trimmed — approximately {omitted} tokens omitted "
    "to fit this provider's context window]\n\n"
)


def system_prompt_budget(max_context_tokens: int) -> int:
    """Token budget allowed for the system prompt alone, given a provider's
    full context window — see the module-level constants above for the policy."""
    return min(_SYSTEM_PROMPT_HARD_CAP_TOKENS, int(max_context_tokens * _MAX_SYSTEM_PROMPT_FRACTION))


def should_trim(max_context_tokens: int) -> bool:
    """Whether a provider with this context window is small enough to even consider trimming."""
    return max_context_tokens < _SMALL_CONTEXT_THRESHOLD_TOKENS


def trim_system_prompt(system: str | list[Any] | None, max_context_tokens: int) -> tuple[str, bool]:
    """
    Trim *system* to fit `system_prompt_budget(max_context_tokens)`, if needed.

    Returns ``(text, was_trimmed)``. Returns the canonicalized (but
    untrimmed) text unchanged when the provider is large-context
    (`should_trim()` is False) or the prompt already fits the budget.

    Trims from the middle: keeps a prefix and a suffix of the *character*
    stream (not whole "messages" — there's no structure to preserve here,
    just one text blob), inserting a marker noting how much was cut. The
    prefix/suffix split is weighted slightly toward the prefix (60/40)
    since core instructions early in a system prompt tend to be more
    load-bearing than text appended later, but both ends are kept non-empty
    whenever there's a budget to keep them at all.
    """
    text = extract_system_text(system)
    if not text:
        return text, False

    if not should_trim(max_context_tokens):
        return text, False

    budget = system_prompt_budget(max_context_tokens)
    estimated = estimate_tokens([], system=text)
    if estimated <= budget:
        return text, False

    # Convert the token budget to a character budget using this text's own
    # observed chars-per-token ratio, so the trim is proportional rather
    # than relying on a second, possibly-different global heuristic.
    chars_per_token = len(text) / estimated if estimated > 0 else 4.0
    # Reserve room for the marker itself so the final trimmed text doesn't
    # creep back over budget once the marker text is added.
    marker_chars = len(_TRIM_MARKER_TEMPLATE) + 10
    char_budget = max(0, int(budget * chars_per_token) - marker_chars)

    prefix_chars = int(char_budget * 0.6)
    suffix_chars = char_budget - prefix_chars

    prefix = text[:prefix_chars]
    suffix = text[-suffix_chars:] if suffix_chars > 0 else ""
    omitted_chars = len(text) - len(prefix) - len(suffix)
    omitted_tokens_estimate = max(1, int(omitted_chars / chars_per_token))

    trimmed = prefix + _TRIM_MARKER_TEMPLATE.format(omitted=omitted_tokens_estimate) + suffix
    logger.debug(
        "system_prompt: trimmed for small-context provider",
        max_context_tokens=max_context_tokens, budget_tokens=budget,
        original_tokens=estimated, omitted_tokens_estimate=omitted_tokens_estimate,
    )
    return trimmed, True


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SystemPromptResult:
    """Result of processing a request's system prompt for one target provider."""

    text: str
    """Canonicalized (and possibly trimmed) system prompt text. Empty string
    if there was no system prompt at all."""
    system_hash: str | None
    """Hash of the *original, untrimmed* canonical text — stable regardless
    of trimming decisions, so dedup tracking reflects what the client
    actually sent, not a provider-specific derivative of it."""
    was_trimmed: bool
    gemini_hint: GeminiCacheHint | None
    """Populated only when ``provider_name == "gemini"``; ``None`` otherwise."""


def process_system_prompt(
    system: str | list[Any] | None,
    provider_name: str,
    model_slug: str,
    max_context_tokens: int,
) -> SystemPromptResult:
    """
    Single entry point combining hash detection, the Gemini cache hint, and
    small-context trimming for one (provider, model) target.

    Not yet called from anywhere in the live request pipeline — see the
    module docstring's integration note.
    """
    original_text = extract_system_text(system)
    system_hash = hash_system_prompt(system)

    gemini_hint: GeminiCacheHint | None = None
    if provider_name == "gemini":
        gemini_hint = gemini_cache_hint(system, model_slug)
        # Never trim for Gemini — see module docstring: trimming would
        # break the byte-stable prefix implicit caching depends on, and
        # Gemini's context window is large enough that there's no need to.
        final_text, was_trimmed = original_text, False
    else:
        final_text, was_trimmed = trim_system_prompt(system, max_context_tokens)

    return SystemPromptResult(
        text=final_text,
        system_hash=system_hash,
        was_trimmed=was_trimmed,
        gemini_hint=gemini_hint,
    )