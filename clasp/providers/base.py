"""
clasp/providers/base.py

BaseProvider — the abstract contract every concrete provider transport
(OpenAIChatTransport, AnthropicMessagesTransport, and ultimately each
named provider such as NvidiaNIMProvider) implements.

Three abstract responsibilities:
  - stream()        Translate an Anthropic-format request, call the
                     upstream API, and yield ready-to-write Anthropic SSE
                     event strings ("event: ...\\ndata: ...\\n\\n").
  - count_tokens()  Return an estimated input-token count for an
                     Anthropic-format request, without calling upstream.
  - list_models()   Return the model slugs this provider currently
                     exposes (answers GET /v1/models locally and feeds
                     the Models panel in the web UI).

BaseProvider also owns the shared httpx.AsyncClient lifecycle — one
persistent connection pool per provider instance, created lazily and
closed via `aclose()` — and defines the small family of exceptions that
ratelimit/circuit_breaker.py and queue/absorber.py react to, so the rest
of the proxy never has to know which HTTP client a provider uses
internally.

Design note: one BaseProvider instance is shared across every API key
configured for that provider (see `providers/registry.py`). The key
itself is passed per-call into `stream()`, not bound at construction
time, since `ratelimit/key_pool.py` rotates keys independently of which
provider they belong to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

import httpx


# --------------------------------------------------------------------------- #
# Provider-level exceptions
# --------------------------------------------------------------------------- #


class ProviderError(Exception):
    """Base class for every error a provider can raise from `stream()`,
    `count_tokens()`, or `list_models()`. Callers (selector, absorber,
    circuit breaker) catch this family rather than raw httpx exceptions.
    """


class ProviderHTTPError(ProviderError):
    """Upstream returned a non-2xx HTTP response.

    `retry_after` is populated from a parsed `Retry-After` header (seconds)
    when present, so `ratelimit/cooldown.py` can schedule recovery without
    re-parsing the header itself. `status_code` is what
    `ratelimit/circuit_breaker.py` and `queue/absorber.py` branch on
    (429 → absorber; 5xx → circuit_breaker.record_error()).
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        retry_after: float | None = None,
        body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.body = body

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ProviderHTTPError(status_code={self.status_code!r}, "
            f"retry_after={self.retry_after!r})"
        )


class ProviderTimeoutError(ProviderError):
    """Upstream did not respond within the configured timeout.
    `circuit_breaker.record_timeout()` reacts to this distinctly from a
    generic HTTP error, since flaky-but-reachable upstreams behave
    differently from genuinely down ones.
    """


class ProviderConnectionError(ProviderError):
    """Could not establish or maintain a connection to upstream (DNS
    failure, connection reset, TLS error, etc.) — kept distinct from
    `ProviderTimeoutError` because circuit_breaker.py may want to weight
    these failure modes differently.
    """


# --------------------------------------------------------------------------- #
# BaseProvider
# --------------------------------------------------------------------------- #


class BaseProvider(ABC):
    """
    One instance per configured provider, living for the whole process
    lifetime inside `providers/registry.py`.
    """

    #: Catalog name this provider is registered under (e.g. "nvidia_nim").
    #: Set by subclasses; used in log lines and health/status endpoints.
    name: str

    #: Upstream base URL, e.g. "https://integrate.api.nvidia.com/v1".
    base_url: str

    def __init__(self, name: str, base_url: str, *, timeout_seconds: float = 60.0) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ #
    # Shared httpx client lifecycle
    # ------------------------------------------------------------------ #

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazily-created, reused across requests for connection pooling.

        Concrete providers should call this rather than constructing their
        own `httpx.AsyncClient`, so `aclose()` can clean up deterministically
        on server shutdown and so connection pooling is actually shared
        across requests for the same provider.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    async def aclose(self) -> None:
        """Close the underlying connection pool. Called once per provider
        from `server.py`'s shutdown handler."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "BaseProvider":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ #
    # Abstract contract
    # ------------------------------------------------------------------ #

    @abstractmethod
    def stream(
        self,
        request: dict,
        *,
        api_key: str,
        model: str,
    ) -> AsyncIterator[str]:
        """
        Translate `request` (Anthropic Messages API shape) into this
        provider's wire format, open a streaming connection upstream using
        `api_key`, and yield fully-formatted Anthropic SSE event strings
        (each already terminated, e.g. via
        `providers.common.sse_builder.format_sse_event`) as they become
        available.

        `model` is the already-resolved provider-specific model slug
        (e.g. "moonshotai/kimi-k2-thinking" for nvidia_nim), produced by
        `router/model_map.resolve()` — implementations should not need to
        know about Claude tier names like "opus" or "sonnet".

        Concrete subclasses implement this as an `async def` async
        generator (using `yield`); the abstract declaration here is a
        plain method so the ABC doesn't force a particular sync/async
        generator shape on overrides — only that the name and return
        type are honored.

        Raises:
            ProviderHTTPError        on any non-2xx upstream response.
            ProviderTimeoutError     on read/connect timeout.
            ProviderConnectionError  on transport-level connection failure.
        """
        raise NotImplementedError

    @abstractmethod
    async def count_tokens(self, request: dict) -> int:
        """
        Return an estimated input-token count for `request` (Anthropic
        Messages API shape), without making any upstream call. Used to
        answer `POST /v1/messages/count_tokens` locally (see
        `api/optimize.py`) and to feed `ratelimit/bucket.py`'s pre-emptive
        TPM check before a request is dispatched.
        """
        ...

    @abstractmethod
    async def list_models(self) -> list[str]:
        """
        Return the model slugs this provider currently exposes. Used to
        answer `GET /v1/models` locally and to populate the Models panel
        in the web UI. Implementations may call upstream's `/models`
        endpoint and cache the result, or return a static list from the
        provider catalog — either is acceptable as long as the call does
        not block the request path (cache aggressively).
        """
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, base_url={self.base_url!r})"