"""
clasp/providers/base.py

Abstract base class every concrete provider transport (NVIDIA NIM, Gemini,
Cerebras, etc.) inherits from.

The critical integration point: `stream()` is the public method callers
invoke (selector.py, queue/manager.py's drain_task, queue/absorber.py's own
immediate-failover branch). It wraps the subclass-provided `_stream_raw()`
and, the moment that raises `UpstreamRateLimitError` (a 429 from upstream),
hands off to the 429 absorber instead of letting the error reach the
caller. This is what plan.md §19's "Mid-Stream 429" edge case and the P2
design principle ("Never propagate 429") both describe — Claude Code should
never see a raw 429, whether it happens before the first byte or mid-stream.

Concrete transports only need to implement `_stream_raw()` and raise
`UpstreamRateLimitError(retry_after=...)` when the upstream HTTP response is
a 429. Everything else (translation to Anthropic SSE format, etc.) happens
inside `_stream_raw()` in the real openai_transport.py / anthropic_transport.py
subclasses — out of scope for this file.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clasp.router.types import AnthropicRequest


class UpstreamRateLimitError(Exception):
    """
    Raised by a concrete provider's `_stream_raw()` when the upstream HTTP
    response is a 429. Carries the raw `Retry-After` header value (if any)
    so `cooldown.on_429()` can parse it.
    """

    def __init__(self, retry_after: str | None = None) -> None:
        self.retry_after = retry_after
        super().__init__(f"upstream 429 (retry_after={retry_after!r})")


class ProviderConnectionError(Exception):
    """Raised when a provider cannot be reached at all (network error, DNS failure, etc.)."""

    def __init__(self, message: str = "Provider connection failed") -> None:
        super().__init__(message)


class ProviderHTTPError(Exception):
    """
    Raised for non-429 HTTP error responses from the upstream provider
    (4xx other than 429, 5xx, unexpected status codes).
    """

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}" if message else f"HTTP {status_code}")


class ProviderTimeoutError(Exception):
    """Raised when the upstream provider request times out."""

    def __init__(self, message: str = "Provider request timed out") -> None:
        super().__init__(message)


class BaseProvider(ABC):
    """
    Abstract base for all provider transports.

    Subclasses must set `provider_name` and implement `_stream_raw()`.
    """

    provider_name: str

    def __init__(self, name: str, base_url: str, timeout_seconds: float = 60.0) -> None:
        import httpx
        self.name = name
        self.provider_name = name
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout_seconds)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def list_models(self, api_key: str | None = None) -> list[str]:
        """List available models from the provider."""
        return []

    @abstractmethod
    def _stream_raw(
        self,
        request: "AnthropicRequest",
        key: str,
        key_index: int,
    ) -> AsyncIterator[str]:
        """
        Make the actual upstream call and yield Anthropic-format SSE chunks.

        Must raise `UpstreamRateLimitError` (not return/yield an error chunk)
        when the upstream response is a 429, so `stream()` below can route
        it through the absorber instead of letting it leak to the caller.
        """
        raise NotImplementedError

    async def stream(
        self,
        request: "AnthropicRequest",
        key: str,
        key_index: int,
    ) -> AsyncIterator[str]:
        """
        Public streaming entrypoint. Never raises `UpstreamRateLimitError` —
        a 429 from `_stream_raw()` is caught here and handed to
        `queue.absorber.on_upstream_429()`, whose output (an immediate
        failover response, or a queued-and-held response, or — only as a
        last resort — a single Anthropic-shaped `overloaded_error` SSE
        event) is yielded in its place.
        """
        try:
            async for chunk in self._stream_raw(request, key, key_index):
                yield chunk
        except UpstreamRateLimitError as exc:
            # Lazy import: queue.absorber imports router.selector, which in
            # turn would otherwise create an import cycle with providers.base
            # at module-load time (selector needs to type-check against
            # BaseProvider; base needs absorber only inside this branch).
            from clasp.queue.absorber import on_upstream_429  # noqa: PLC0415

            async for chunk in on_upstream_429(
                request=request,
                failed_provider=self.provider_name,
                failed_key_index=key_index,
                retry_after_header=exc.retry_after,
            ):
                yield chunk