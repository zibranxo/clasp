"""
clasp/api/service.py

Minimal Sprint-1 orchestration layer.

Responsibility
--------------
Sits between ``proxy_routes.py`` (FastAPI request/response) and the provider
layer.  For Sprint 1 the pipeline is deliberately thin:

    detect(body)  →  pick first enabled provider  →  provider.stream()

Later sprints will slot in:
  - Sprint 2: full selector (rate-limit checks, capability filtering)
  - Sprint 3: 429 absorber + priority queue
  - Sprint 5: optimizer + cache read/write

The function signatures are stable; callers won't need to change.

Public API
----------
    from clasp.api.service import handle_request

    # Non-streaming: returns the complete Anthropic response dict.
    response = await handle_request(body, request_id="req_abc123", stream=False)

    # Streaming: yields raw SSE lines (already in Anthropic format).
    async for line in handle_request(body, request_id="req_abc123", stream=True):
        yield line
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger

from clasp.api.detect import RequestType, classify_priority, detect
from clasp.config.settings import get_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request_id() -> str:
    """Generate a ``req_<uuid4>`` request identifier."""
    return f"req_{uuid.uuid4().hex}"


def _get_registry():
    """
    Lazy import of the provider registry to avoid circular imports at module
    load time (registry imports settings, settings is already loaded here).
    """
    from clasp.providers.registry import get_registry  # noqa: PLC0415

    return get_registry()


# ---------------------------------------------------------------------------
# No-provider error response builders
# ---------------------------------------------------------------------------

def _no_provider_error_json(request_id: str) -> dict[str, Any]:
    """Anthropic-format error response when no provider is available."""
    return {
        "type": "error",
        "error": {
            "type": "overloaded_error",
            "message": (
                "No providers are currently available. "
                "All configured providers are either disabled, cooling down, "
                "or no API keys have been added. "
                "Check the CLASP web UI at http://127.0.0.1:8082 to add keys."
            ),
        },
    }


def _no_provider_error_sse(request_id: str) -> str:
    """SSE-formatted error event for the no-provider case."""
    import json  # noqa: PLC0415

    payload = json.dumps(_no_provider_error_json(request_id))
    return f"event: error\ndata: {payload}\n\n"


# ---------------------------------------------------------------------------
# Core service function
# ---------------------------------------------------------------------------

async def handle_request(
    body: dict[str, Any],
    *,
    request_id: str | None = None,
    stream: bool | None = None,
) -> dict[str, Any] | AsyncIterator[str]:
    """
    Orchestrate a single ``POST /v1/messages`` request.

    Parameters
    ----------
    body:
        Validated Anthropic Messages API request dict.
    request_id:
        Optional pre-generated request ID.  A new one is minted if omitted.
    stream:
        Whether to stream.  Falls back to ``body["stream"]`` if ``None``.

    Returns
    -------
    For non-streaming:  ``dict`` — complete Anthropic response.
    For streaming:      ``AsyncIterator[str]`` — SSE lines in Anthropic format.
    """
    if request_id is None:
        request_id = _make_request_id()

    if stream is None:
        stream = bool(body.get("stream", False))

    # ── Classify ────────────────────────────────────────────────────────────
    req_type: RequestType = detect(body)
    priority: int = classify_priority(req_type)

    logger.info(
        "request",
        request_id=request_id,
        type=req_type.value,
        priority=priority,
        model=body.get("model", "unknown"),
        stream=stream,
    )

    # ── Select provider (Sprint 1: first enabled) ───────────────────────────
    registry = _get_registry()
    provider = registry.first_available()

    if provider is None:
        logger.warning("no provider available", request_id=request_id)
        if stream:
            return _stream_no_provider_error(request_id)
        return _no_provider_error_json(request_id)

    logger.info(
        "provider selected",
        request_id=request_id,
        provider=provider.provider_name,
    )

    # ── Dispatch ────────────────────────────────────────────────────────────
    t0 = time.monotonic()

    if stream:
        return _stream_with_logging(
            provider=provider,
            body=body,
            request_id=request_id,
            req_type=req_type,
            t0=t0,
        )
    else:
        return await _non_stream_with_logging(
            provider=provider,
            body=body,
            request_id=request_id,
            req_type=req_type,
            t0=t0,
        )


# ---------------------------------------------------------------------------
# Streaming wrapper
# ---------------------------------------------------------------------------

async def _stream_no_provider_error(request_id: str) -> AsyncIterator[str]:
    """Yield a single SSE error event and stop."""
    yield _no_provider_error_sse(request_id)


async def _stream_with_logging(
    *,
    provider: Any,
    body: dict[str, Any],
    request_id: str,
    req_type: RequestType,
    t0: float,
) -> AsyncIterator[str]:
    """
    Wrap ``provider.stream()`` so we can log outcome + latency once the
    generator is exhausted (or on error).
    """
    outcome = "ok"
    try:
        async for chunk in provider.stream(body):
            yield chunk
    except Exception as exc:  # noqa: BLE001
        outcome = f"error:{type(exc).__name__}"
        logger.error(
            "stream error",
            request_id=request_id,
            provider=provider.provider_name,
            error=str(exc),
        )
        import json  # noqa: PLC0415

        error_payload = json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": f"Provider error: {exc}",
                },
            }
        )
        yield f"event: error\ndata: {error_payload}\n\n"
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "request_complete",
            request_id=request_id,
            type=req_type.value,
            provider=provider.provider_name,
            latency_ms=latency_ms,
            stream=True,
            outcome=outcome,
        )


# ---------------------------------------------------------------------------
# Non-streaming wrapper
# ---------------------------------------------------------------------------

async def _non_stream_with_logging(
    *,
    provider: Any,
    body: dict[str, Any],
    request_id: str,
    req_type: RequestType,
    t0: float,
) -> dict[str, Any]:
    """Call ``provider.complete()`` and log the result."""
    outcome = "ok"
    try:
        response = await provider.complete(body)
        return response
    except Exception as exc:  # noqa: BLE001
        outcome = f"error:{type(exc).__name__}"
        logger.error(
            "complete error",
            request_id=request_id,
            provider=provider.provider_name,
            error=str(exc),
        )
        return _no_provider_error_json(request_id)
    finally:
        latency_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "request_complete",
            request_id=request_id,
            type=req_type.value,
            provider=provider.provider_name,
            latency_ms=latency_ms,
            stream=False,
            outcome=outcome,
        )