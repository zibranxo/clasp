"""
clasp/optimizer/payload_filter.py
=====================================
Per-provider field blocklist + strip logic (plan.md §14 "Phase 6 — Payload
Optimization", "Payload Filter (`optimizer/payload_filter.py`)"; CLAUDE.md
task 3).

Some Anthropic-format request body fields aren't recognized by every
provider's chat-completions API — e.g. `thinking` (extended-thinking
config) isn't a Groq/Cerebras/Mistral concept, and a provider's strict
schema validation can reject the whole request over one field it doesn't
recognize, rather than just ignoring it. `BLOCKLIST` is copied verbatim
from plan.md §14; `strip_blocked_fields()` removes exactly those top-level
fields for a given provider, silently (the request still succeeds) but
loudly in the logs (DEBUG-level, so an operator debugging "why didn't
thinking work on Groq" can find out without it cluttering INFO+ output).

One field in the spec, `betas`, is unusual: in raw Anthropic API usage,
beta opt-ins are normally sent as the `anthropic-beta` HTTP *header*, not a
request body field. plan.md's own `BLOCKLIST` snippet lists it alongside
body fields like `thinking`/`tool_choice` though, so it's implemented here
exactly as specified — as a top-level body key — on the assumption that
whatever upstream stage assembles CLASP's internal request representation
may fold it in as a body field before this filter runs. If your actual
pipeline keeps it as a header instead, this module simply never finds the
key and is a no-op for it; header stripping would need its own pass
elsewhere.

Not wired into `api/service.py` yet — see `system_prompt.py`'s module
docstring for the same integration note; it applies here too.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


#: Per-provider list of top-level request body fields to strip before
#: sending. Verbatim from plan.md §14. A provider not listed here at all
#: (e.g. fireworks, openrouter, mistral's siblings together/ollama/lm_studio)
#: defaults to permissive (no stripping) via `blocked_fields_for()`'s
#: `.get(provider_name, [])` fallback — the same permissive default
#: plan.md states explicitly for gemini/nvidia_nim.
BLOCKLIST: dict[str, list[str]] = {
    "groq": ["thinking", "betas"],
    "cerebras": ["thinking"],
    "mistral": ["thinking", "tool_choice"],
    "gemini": [],       # Permissive
    "nvidia_nim": [],   # Permissive
}


def blocked_fields_for(provider_name: str) -> list[str]:
    """Fields stripped for *provider_name*. Empty list (not an error) for any unlisted provider."""
    return BLOCKLIST.get(provider_name, [])


def strip_blocked_fields(body: dict[str, Any], provider_name: str) -> dict[str, Any]:
    """
    Return a *new* dict — a shallow copy of *body* with every field in
    ``blocked_fields_for(provider_name)`` removed. Never mutates *body*.

    A no-op (returns a shallow copy with nothing removed) for providers
    with an empty or absent blocklist entry.
    """
    blocked = blocked_fields_for(provider_name)
    if not blocked:
        return dict(body)

    stripped_fields = [field for field in blocked if field in body]
    if not stripped_fields:
        return dict(body)

    result = {k: v for k, v in body.items() if k not in blocked}
    logger.debug("payload_filter: stripped fields", provider=provider_name, fields=stripped_fields)
    return result