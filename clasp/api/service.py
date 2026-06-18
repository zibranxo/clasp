"""
clasp/api/service.py

Merged orchestration layer for ``/v1/messages``.

Keeps Version 1 as the core pipeline:
    trivial probe short-circuit → detect/classify → build AnthropicRequest
    → selector-based provider routing → streamed SSE output

Backports Version 2’s missing behavior:
    - stream=False support
    - provider.complete(...) non-streaming path
    - unified logging / latency tracking for both modes
    - request-id helper
    - structured error handling for both stream and non-stream
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover — shim for test environments
    import logging as _logging

    class _Shim:
        _log = _logging.getLogger("clasp.service")

        def info(self, msg: str, **kw: Any) -> None:
            self._log.info(msg + ("  " + str(kw) if kw else ""))

        def warning(self, msg: str, **kw: Any) -> None:
            self._log.warning(msg + ("  " + str(kw) if kw else ""))

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

        def error(self, msg: str, **kw: Any) -> None:
            self._log.error(msg + ("  " + str(kw) if kw else ""))

    logger = _Shim()  # type: ignore[assignment]

from clasp.api import optimize
from clasp.api.detect import detect, needs_tools, needs_vision, priority_for
from clasp.providers.common.error_mapper import ErrorType, build_anthropic_error
from clasp.providers.common.token_counter import estimate_request_tokens
from clasp.router import selector


def _make_request_id() -> str:
    """Generate a stable ``req_<uuid4>`` request identifier."""
    return f"req_{uuid.uuid4().hex}"


def _provider_name(provider: Any) -> str:
    """Best-effort provider label for logs."""
    return getattr(provider, "name", getattr(provider, "provider_name", type(provider).__name__))


def build_anthropic_request(
    body: dict[str, Any],
    request_id: str | None = None,
) -> selector.AnthropicRequest:
    """
    Classify and pre-flight-estimate *body* into a ready-to-route
    ``AnthropicRequest`` — the object ``router/selector.py`` consumes.
    """
    request_type = detect(body)
    estimated = estimate_request_tokens(body)
    return selector.AnthropicRequest(
        type=request_type,
        model=body.get("model", ""),
        body=body,
        needs_tools=needs_tools(body),
        needs_vision=needs_vision(body),
        estimated_tokens=estimated,
        priority=priority_for(request_type),
        request_id=request_id,
    )


def _error_sse(error_type: ErrorType, message: str) -> str:
    """One Anthropic-format ``event: error`` SSE block."""
    payload = build_anthropic_error(error_type, message)
    return f"event: error\ndata: {json.dumps(payload)}\n\n"


def _no_provider_error_json(message: str) -> dict[str, Any]:
    """Anthropic-format JSON error response for non-streaming no-provider cases."""
    return build_anthropic_error(ErrorType.OVERLOADED, message)


async def _stream_no_provider_error(error_type: ErrorType, message: str) -> AsyncIterator[str]:
    """Yield a single SSE error event and stop."""
    yield _error_sse(error_type, message)


async def _stream_with_logging(
    *,
    provider: Any,
    api_key: str,
    key_index: int,
    body: dict[str, Any],
    request_id: str,
    request_type: str,
    t0: float,
) -> AsyncIterator[str]:
    """
    Wrap ``provider.stream()`` so we can log outcome + latency once the
    generator is exhausted (or on error).
    """
    outcome = "ok"
    try:
        async for chunk in provider.stream(body, api_key, key_index):
            yield chunk
    except Exception as exc:  # noqa: BLE001
        outcome = f"error:{type(exc).__name__}"
        logger.error(
            "service: provider stream failed",
            request_id=request_id,
            provider=_provider_name(provider),
            key_index=key_index,
            error=str(exc),
        )
        yield _error_sse(ErrorType.SERVER_ERROR, f"Upstream provider error: {exc}")
    finally:
        logger.info(
            "service: request complete",
            request_id=request_id,
            provider=_provider_name(provider),
            key_index=key_index,
            latency_s=round(time.monotonic() - t0, 3),
            stream=True,
            outcome=outcome,
            type=request_type,
        )


async def _non_stream_with_logging(
    *,
    provider: Any,
    api_key: str,
    key_index: int,
    body: dict[str, Any],
    request_id: str,
    request_type: str,
    t0: float,
) -> dict[str, Any]:
    """
    Call ``provider.complete()`` and log the result.
    """
    outcome = "ok"
    try:
        response = await provider.complete(body, api_key, key_index)
        return response
    except Exception as exc:  # noqa: BLE001
        outcome = f"error:{type(exc).__name__}"
        logger.error(
            "service: provider complete failed",
            request_id=request_id,
            provider=_provider_name(provider),
            key_index=key_index,
            error=str(exc),
        )
        return _no_provider_error_json(f"Upstream provider error: {exc}")
    finally:
        logger.info(
            "service: request complete",
            request_id=request_id,
            provider=_provider_name(provider),
            key_index=key_index,
            latency_s=round(time.monotonic() - t0, 3),
            stream=False,
            outcome=outcome,
            type=request_type,
        )


async def handle_request(
    body: dict[str, Any],
    *,
    request_id: str | None = None,
    stream: bool | None = None,
) -> dict[str, Any] | AsyncIterator[str]:
    """
    Run the full ``/v1/messages`` pipeline for one request.

    Returns:
        - non-streaming: Anthropic JSON dict
        - streaming: async iterator yielding SSE strings
    """
    request_id = request_id or _make_request_id()
    if stream is None:
        stream = bool(body.get("stream", False))

    # Trivial-probe short-circuit (max_tokens<=5, tiny body) — answered
    # locally with a canned response, no provider call at all.
    if optimize.is_probe("/v1/messages", body):
        result = optimize.handle_probe("/v1/messages", body, request_id=request_id)
        logger.debug("service: trivial probe answered locally", request_id=request_id)

        if stream:
            async def _probe_stream() -> AsyncIterator[str]:
                for line in result.payload:
                    yield line

            return _probe_stream()

        # Best-effort non-streaming fallback for probe requests.
        # If the probe helper already produced a JSON-like object, return it.
        # Otherwise, preserve the payload locally rather than routing upstream.
        if hasattr(result, "response") and isinstance(result.response, dict):
            return result.response
        return {
            "type": "message",
            "id": request_id,
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "\n".join(getattr(result, "payload", [])),
                }
            ],
        }

    request = build_anthropic_request(body, request_id=request_id)
    logger.info(
        "service: request classified",
        request_id=request_id,
        type=request.type.value,
        estimated_tokens=request.estimated_tokens,
        needs_tools=request.needs_tools,
        needs_vision=request.needs_vision,
        priority=request.priority,
        stream=stream,
    )

    selection = await selector.select(request)
    if selection is None:
        logger.warning(
            "service: no provider available — failing fast (no queue/absorber yet)",
            request_id=request_id,
            type=request.type.value,
        )
        message = (
            "All configured providers are currently rate-limited or unavailable. "
            "Please retry shortly."
        )
        if stream:
            return _stream_no_provider_error(ErrorType.OVERLOADED, message)
        return _no_provider_error_json(message)

    provider, api_key, key_index = selection
    start = time.monotonic()

    if not stream:
        return await _non_stream_with_logging(
            provider=provider,
            api_key=api_key,
            key_index=key_index,
            body=request.body,
            request_id=request_id,
            request_type=request.type.value,
            t0=start,
        )

    return _stream_with_logging(
        provider=provider,
        api_key=api_key,
        key_index=key_index,
        body=request.body,
        request_id=request_id,
        request_type=request.type.value,
        t0=start,
    )