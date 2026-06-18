"""
clasp/providers/gemini.py

GeminiProvider — Google Gemini AI Studio via its OpenAI-compatible
endpoint. Same pattern as nvidia_nim.py: a thin OpenAIChatTransport
subclass supplying this provider's base_url and quirks.

plan.md's Edge Cases section is explicit that `finish_reason: "SAFETY"`
is a content block, not a rate limit, and must not be confused with one
— the only piece actually implemented here, via OpenAIChatTransport's
`error_finish_reasons` hook (added alongside this file specifically for
this case): instead of the normal message_delta/message_stop close-out,
SAFETY produces a single Anthropic `invalid_request_error` event. "Do
not retry on a different provider" (plan.md) isn't enforced here because
there's nothing to enforce it ON yet — failover decisions live in
queue/absorber.py (Phase 3) and router/selector.py (Phase 5), neither of
which exists in this codebase yet.

The catalog's "prefix caching" note refers to Gemini's native context
caching (CachedContent resources, created and referenced ahead of a
generation call) — that's a separate cache-management subsystem, not a
per-request payload tweak, and nothing in plan.md gives a spec for one.
Not implemented; flagging it here rather than guessing at one.

`daily_token_limit` (1,000,000/day per the catalog) is also not enforced
by any code today — `TokenBucket` only tracks RPM/TPM per minute, and
`KeyPool`/`persistence.py`'s `daily_counters` plumbing exists but nothing
populates or checks it yet. Same gap, not specific to Gemini.
"""

from __future__ import annotations

from clasp.providers.openai_transport import OpenAIChatTransport

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

SAFETY_BLOCK_MESSAGE = "Response blocked by Gemini safety filters. Try rephrasing."


class GeminiProvider(OpenAIChatTransport):
    def __init__(
        self,
        *,
        base_url: str = GEMINI_BASE_URL,
        timeout_seconds: float = 60.0,
        send_stream_options: bool = True,
        extra_headers: dict[str, str] | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        super().__init__(
            "gemini",
            base_url,
            timeout_seconds=timeout_seconds,
            send_stream_options=send_stream_options,
            extra_headers=extra_headers,
            static_models=static_models,
            models_cache_ttl_seconds=models_cache_ttl_seconds,
            error_finish_reasons={"SAFETY": SAFETY_BLOCK_MESSAGE},
        )