"""
clasp/providers/anthropic_transport.py

AnthropicMessagesTransport — BaseProvider implementation for every catalog
entry whose `transport` field is "anthropic_messages" (currently just
Fireworks AI, whose base_url already points at a full
".../v1/messages"-style endpoint that natively speaks Anthropic's
Messages API and SSE protocol).

Unlike OpenAIChatTransport, there is no content-block translation step at
all: `message_converter.py` and `sse_builder.py` are both unused here.
The only two things this transport does on top of raw forwarding are:

  1. Swap the request's `model` field for the provider-resolved slug
     (the upstream still needs ITS model identifier, not whatever Claude
     tier name or alias the request arrived with).
  2. Speak the upstream's actual auth convention — real Anthropic-style
     endpoints use `x-api-key` + `anthropic-version`, not
     `Authorization: Bearer`, hence the "header shim" plan.md calls out.

Streaming forwards raw decoded text chunks via httpx's `aiter_text()`
rather than re-parsing into discrete SSE events the way SSEBuilder does
for OpenAIChatTransport — there's nothing to synthesize, and re-chunking
on line boundaries risks mangling the blank-line event terminators the
upstream already sends correctly. Whatever calls `stream()` just needs
to forward each yielded string to the client in order; the resulting
byte stream is then identical to what the upstream sent.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator

import httpx
from loguru import logger

from clasp.providers.base import (
    BaseProvider,
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderTimeoutError,
)


# --------------------------------------------------------------------------- #
# Small local helpers (intentionally not shared with openai_transport.py's
# copies — see that file's module docstring; pulling these into a common
# module is reasonable future cleanup, out of scope here)
# --------------------------------------------------------------------------- #


def _local_token_estimate(text: str) -> int:
    """
    Crude ~4-chars-per-token placeholder, same heuristic
    openai_transport.py uses, pending `providers/common/token_counter.py`
    (Sprint 1 step 10). Calling the real upstream `/count_tokens`
    endpoint would need an `api_key`, which `BaseProvider.count_tokens()`'s
    signature doesn't carry — left as a local-only estimate for now.
    """
    return max(1, len(text) // 4)


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a `Retry-After` header (RFC 9110: either delta-seconds or an
    HTTP-date). Returns seconds remaining (>= 0), or None if unparseable."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(delta, 0.0)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# AnthropicMessagesTransport
# --------------------------------------------------------------------------- #


class AnthropicMessagesTransport(BaseProvider):
    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        timeout_seconds: float = 60.0,
        anthropic_version: str = "2023-06-01",
        auth_header_name: str = "x-api-key",
        extra_headers: dict[str, str] | None = None,
        models_endpoint: str | None = None,
        count_tokens_endpoint: str | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        """
        Args:
            base_url: The FULL messages endpoint, not a root to append a
                path onto — e.g. Fireworks's catalog entry is
                "https://api.fireworks.ai/inference/v1/messages" already.
                This differs from OpenAIChatTransport, where base_url is
                a root and "/chat/completions" gets appended.
            auth_header_name: Defaults to "x-api-key" (real Anthropic's
                convention). If a future anthropic_messages provider
                instead wants `Authorization: Bearer <key>`, pass
                auth_header_name="Authorization" and the Bearer prefix
                is added automatically.
            count_tokens_endpoint: If the upstream exposes a real
                `/count_tokens` sibling endpoint (mirroring Anthropic's
                own API shape), this would be where to call it — not
                currently used (see `count_tokens()`'s docstring for why).
        """
        super().__init__(name, base_url, timeout_seconds=timeout_seconds)
        self.anthropic_version = anthropic_version
        self.auth_header_name = auth_header_name
        self.extra_headers = extra_headers or {}
        self.models_endpoint = models_endpoint or self._derive_sibling_endpoint(
            self.base_url, "models"
        )
        self.count_tokens_endpoint = count_tokens_endpoint or self._derive_sibling_endpoint(
            self.base_url, "count_tokens", relative_to_messages=True
        )
        self.static_models = static_models

        self._models_cache: list[str] | None = None
        self._models_cache_time: float = 0.0
        self._models_cache_ttl_seconds = models_cache_ttl_seconds

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _derive_sibling_endpoint(
        base_url: str, sibling: str, *, relative_to_messages: bool = False
    ) -> str:
        """
        Derive a sibling endpoint URL from a base_url that already points
        at the full ".../messages" path.

        relative_to_messages=True:  ".../v1/messages" -> ".../v1/messages/{sibling}"
            (matches Anthropic's real API: count_tokens lives under
            /v1/messages/count_tokens, a child of the messages endpoint.)
        relative_to_messages=False: ".../v1/messages" -> ".../v1/{sibling}"
            (matches Anthropic's real API: models lives at /v1/models, a
            sibling of /v1/messages, not a child of it.)
        """
        if relative_to_messages:
            return f"{base_url}/{sibling}"
        if base_url.endswith("/messages"):
            return base_url[: -len("/messages")] + f"/{sibling}"
        return base_url.rstrip("/") + f"/{sibling}"

    def _build_headers(self, api_key: str | None) -> dict[str, str]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "anthropic-version": self.anthropic_version,
        }
        if api_key:
            if self.auth_header_name.lower() == "authorization":
                headers[self.auth_header_name] = f"Bearer {api_key}"
            else:
                headers[self.auth_header_name] = api_key
        headers.update(self.extra_headers)
        return headers

    # ------------------------------------------------------------------ #
    # stream()
    # ------------------------------------------------------------------ #

    async def stream(
        self,
        request: dict,
        *,
        api_key: str,
        model: str,
    ) -> AsyncIterator[str]:
        payload = dict(request)
        payload["model"] = model
        payload["stream"] = True

        headers = self._build_headers(api_key)

        try:
            async with self.client.stream(
                "POST", self.base_url, json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    body_bytes = await response.aread()
                    body_text = body_bytes.decode("utf-8", errors="replace")
                    retry_after = (
                        _parse_retry_after(response.headers.get("retry-after"))
                        if response.status_code == 429
                        else None
                    )
                    raise ProviderHTTPError(
                        response.status_code,
                        f"{self.name}: upstream returned {response.status_code}",
                        retry_after=retry_after,
                        body=body_text,
                    )

                async for text_chunk in response.aiter_text():
                    if text_chunk:
                        yield text_chunk

        except httpx.TimeoutException as e:
            raise ProviderTimeoutError(f"{self.name}: timed out: {e}") from e
        except httpx.HTTPError as e:
            raise ProviderConnectionError(f"{self.name}: connection error: {e}") from e

    # ------------------------------------------------------------------ #
    # count_tokens()
    # ------------------------------------------------------------------ #

    async def count_tokens(self, request: dict) -> int:
        """
        Local-only placeholder estimate. The real upstream `/count_tokens`
        endpoint (derived above as `self.count_tokens_endpoint`, in case a
        future caller wants it) needs authentication, but
        `BaseProvider.count_tokens()`'s signature doesn't carry an
        `api_key` — swap this for a real call if that signature grows one,
        or once `providers/common/token_counter.py` (Sprint 1 step 10)
        lands.
        """
        return _local_token_estimate(json.dumps(request, default=str))

    # ------------------------------------------------------------------ #
    # list_models()
    # ------------------------------------------------------------------ #

    async def list_models(self, api_key: str | None = None) -> list[str]:
        if self.static_models is not None:
            return list(self.static_models)

        now = time.monotonic()
        if (
            self._models_cache is not None
            and (now - self._models_cache_time) < self._models_cache_ttl_seconds
        ):
            return self._models_cache

        try:
            response = await self.client.get(
                self.models_endpoint, headers=self._build_headers(api_key)
            )
            response.raise_for_status()
            data = response.json()
            models = [
                entry.get("id", "")
                for entry in data.get("data", [])
                if isinstance(entry, dict) and entry.get("id")
            ]
        except Exception as e:
            logger.warning(f"{self.name}: list_models() failed, using fallback: {e}")
            return self._models_cache or []

        self._models_cache = models
        self._models_cache_time = now
        return models