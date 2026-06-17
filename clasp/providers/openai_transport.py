"""
clasp/providers/openai_transport.py

OpenAIChatTransport — a single, reusable BaseProvider implementation for
every provider in the catalog whose `transport` field is "openai_chat"
(NVIDIA NIM, Gemini's OpenAI-compat endpoint, Cerebras, Groq, OpenRouter,
Mistral, Together, Ollama, LM Studio, ...).

Composition over inheritance: concrete provider modules (e.g.
`providers/nvidia_nim.py`) construct an `OpenAIChatTransport` with that
provider's `base_url` and quirks (merge_system, whether it accepts
`stream_options.include_usage`, extra headers) rather than subclassing —
the translation logic itself (Anthropic <-> OpenAI Chat) is identical
across every such provider, so it lives here exactly once.

Three responsibilities, matching BaseProvider's abstract contract:
  stream()        Anthropic request -> OpenAI payload -> httpx streaming
                   POST -> SSEBuilder -> Anthropic SSE events.
  count_tokens()   Local estimate (placeholder pending
                   providers/common/token_counter.py, Sprint 1 step 10).
  list_models()    GET {base_url}/models, with a static-list override and
                   a short-lived in-memory cache so this never has to hit
                   upstream on every single /v1/models probe.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import AsyncIterator, Callable

import httpx
from loguru import logger

from clasp.providers.base import (
    BaseProvider,
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderTimeoutError,
)
from clasp.providers.common.message_converter import anthropic_to_openai
from clasp.providers.common.sse_builder import SSEBuilder


# --------------------------------------------------------------------------- #
# Small local helpers
# --------------------------------------------------------------------------- #


def _local_token_estimate(text: str) -> int:
    """
    Crude ~4-chars-per-token estimate, used as a placeholder until
    `providers/common/token_counter.py` (Sprint 1 step 10) lands with a
    real tiktoken-based count. Good enough to feed `message_start`'s
    `usage.input_tokens` and `ratelimit/bucket.py`'s pre-emptive TPM
    check; not good enough for billing-accuracy use cases.
    """
    return max(1, len(text) // 4)


def _parse_retry_after(value: str | None) -> float | None:
    """
    Parse a `Retry-After` header value, which per RFC 9110 is either an
    integer number of seconds or an HTTP-date. Returns seconds remaining
    (clamped to >= 0), or None if the header is absent/unparseable.
    """
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
# OpenAIChatTransport
# --------------------------------------------------------------------------- #


class OpenAIChatTransport(BaseProvider):
    def __init__(
        self,
        name: str,
        base_url: str,
        *,
        timeout_seconds: float = 60.0,
        merge_system: bool = False,
        merge_system_resolver: Callable[[str], bool] | None = None,
        send_stream_options: bool = True,
        extra_headers: dict[str, str] | None = None,
        models_endpoint: str | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        """
        Args:
            merge_system: Default merge-system-into-first-user-message
                behavior (see message_converter's `merge_system` flag),
                for providers/models that reject the `system` role
                outright — the NVIDIA NIM quirk plan.md calls out.
            merge_system_resolver: Optional per-model override — if
                given, called with the resolved model slug for each
                `stream()`/`count_tokens()` call instead of using the
                static `merge_system` flag. Lets a concrete provider
                (e.g. nvidia_nim.py) flip this per-model once
                `router/capability.py` exists, without changing this
                class's interface.
            send_stream_options: Whether to request
                `stream_options: {"include_usage": true}`. Most modern
                OpenAI-compatible APIs honor this; a handful of older or
                stricter ones reject unknown top-level fields — set False
                for those.
            static_models: If given, `list_models()` returns this list
                directly with no upstream call at all (useful for
                providers without a working `/models` endpoint, or to
                avoid an extra round-trip for ones whose catalog is fixed).
        """
        super().__init__(name, base_url, timeout_seconds=timeout_seconds)
        self.merge_system = merge_system
        self.merge_system_resolver = merge_system_resolver
        self.send_stream_options = send_stream_options
        self.extra_headers = extra_headers or {}
        self.models_endpoint = models_endpoint or f"{self.base_url}/models"
        self.static_models = static_models

        self._models_cache: list[str] | None = None
        self._models_cache_time: float = 0.0
        self._models_cache_ttl_seconds = models_cache_ttl_seconds

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @property
    def _chat_completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _build_headers(self, api_key: str | None) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        headers.update(self.extra_headers)
        return headers

    def _should_merge_system(self, model: str) -> bool:
        if self.merge_system_resolver is not None:
            return self.merge_system_resolver(model)
        return self.merge_system

    def _apply_provider_quirks(self, payload: dict, *, request: dict, model: str) -> dict:
        """
        Extension point for concrete provider subclasses (e.g.
        `providers/nvidia_nim.py`) to adjust the already-translated
        OpenAI-shaped payload before it's sent upstream — e.g. NIM's
        kimi-k2 models wanting an `extra_body.thinking` field instead of
        a standard one. No-op by default; override in a subclass rather
        than modifying this base implementation.
        """
        return payload

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
        payload = anthropic_to_openai(
            request,
            merge_system=self._should_merge_system(model),
            target_model=model,
        )
        payload = self._apply_provider_quirks(payload, request=request, model=model)
        payload["stream"] = True
        if self.send_stream_options:
            payload["stream_options"] = {"include_usage": True}

        headers = self._build_headers(api_key)
        estimated_input_tokens = _local_token_estimate(json.dumps(payload, default=str))
        builder = SSEBuilder(model=model, input_tokens=estimated_input_tokens)

        try:
            async with self.client.stream(
                "POST", self._chat_completions_url, json=payload, headers=headers
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

                async for raw_line in response.aiter_lines():
                    if not raw_line:
                        continue
                    for event in builder.parse_openai_sse_chunk(raw_line):
                        yield event
                    if builder.is_done:
                        break

        except httpx.TimeoutException as e:
            raise ProviderTimeoutError(f"{self.name}: timed out: {e}") from e
        except httpx.HTTPError as e:
            raise ProviderConnectionError(f"{self.name}: connection error: {e}") from e

        if not builder.is_done:
            # Upstream closed the connection without ever sending a
            # finish_reason chunk (dropped mid-response). Close out the
            # Anthropic-side stream so the client doesn't hang forever
            # waiting for a message_stop that was never coming.
            logger.warning(f"{self.name}: stream ended without finish_reason, finalizing")
            for event in builder.finalize():
                yield event

    # ------------------------------------------------------------------ #
    # count_tokens()
    # ------------------------------------------------------------------ #

    async def count_tokens(self, request: dict) -> int:
        """
        Placeholder pending `providers/common/token_counter.py` (Sprint 1
        step 10): rough chars/4 estimate over the raw Anthropic-shaped
        request JSON. Directionally correct, not billing-accurate.
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