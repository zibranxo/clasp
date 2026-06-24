"""
clasp/api/proxy_routes.py
=========================
FastAPI router that exposes the Anthropic Messages API surface to Claude Code.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from clasp.api.optimize import (
    COUNT_TOKENS_ENDPOINT,
    MODELS_ENDPOINT,
    answer_count_tokens,
    answer_models,
    is_local_probe,
)
from clasp.api.service import handle_request
from clasp.config.settings import get_settings, Settings
from clasp.core.openai_responses import OpenAIResponsesAdapter

router = APIRouter()

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def get_handle_request_fn():
    return handle_request

def get_is_local_probe_fn():
    return is_local_probe

def _verify_bearer(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """
    FastAPI dependency: reject non-loopback requests with the wrong Bearer token.

    Returns ``None`` on success (FastAPI discards the return value of Depends).
    Raises ``HTTPException(401)`` on failure.
    """
    
    expected = settings.server.api_key

    auth_header: str = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        _raise_401("Missing Authorization header")

    token = auth_header[7:].strip()  # strip "Bearer "
    if token != expected:
        _raise_401("Invalid API key")


def _raise_401(message: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "type": "error",
            "error": {
                "type": "authentication_error",
                "message": message,
            },
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# GET /health  (no auth — used by `clasp server` startup polling)
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> JSONResponse:
    """Liveness probe.  Always returns 200 with ``{"status": "ok"}``."""
    return JSONResponse({"status": "ok", "version": _VERSION})


# ---------------------------------------------------------------------------
# GET /v1/models
# ---------------------------------------------------------------------------


@router.get("/v1/models", dependencies=[Depends(_verify_bearer)])
async def list_models() -> JSONResponse:
    """
    Return the static model list.  Claude Code calls this on every startup.
    Answered locally — zero provider calls.
    """
    return JSONResponse(answer_models())


# ---------------------------------------------------------------------------
# POST /v1/messages/count_tokens
# ---------------------------------------------------------------------------


@router.post("/v1/messages/count_tokens", dependencies=[Depends(_verify_bearer)])
async def count_tokens(request: Request) -> JSONResponse:
    """
    Estimate token count locally via tiktoken.
    Answered locally — zero provider calls.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": str(exc)}},
        )

    if not body.get("messages"):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "messages: list cannot be empty",
                },
            },
        )

    try:
        return JSONResponse(answer_count_tokens(body))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": f"Token estimation failed: {exc}",
                },
            },
        )


# ---------------------------------------------------------------------------
# POST /v1/messages  (main proxy endpoint)
# ---------------------------------------------------------------------------


@router.post("/v1/messages", dependencies=[Depends(_verify_bearer)])
async def messages(
    request: Request,
    is_local_probe_fn = Depends(get_is_local_probe_fn),
    handle_request_fn = Depends(get_handle_request_fn)
):
    """
    Main proxy endpoint.

    1. Parse + validate the request body.
    2. Generate a ``request_id`` for tracing.
    3. Short-circuit trivial probes locally (``optimize.is_local_probe``).
    4. Delegate to ``service.handle_request`` for everything else.
    5. Return a ``StreamingResponse`` (SSE) or plain ``JSONResponse``.
    """
    # ── Parse body ───────────────────────────────────────────────────────────
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": f"Invalid JSON body: {exc}",
                },
            },
        )

    if not body.get("messages"):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "messages: list cannot be empty",
                },
            },
        )

    # ── Request ID ───────────────────────────────────────────────────────────
    # Honour an existing ``X-Request-Id`` header from Claude Code, or mint one.
    request_id: str = (
        request.headers.get("X-Request-Id")
        or request.headers.get("x-request-id")
        or f"req_{uuid.uuid4().hex}"
    )

    want_stream: bool = bool(body.get("stream", False))

    logger.debug(
        "incoming request",
        request_id=request_id,
        model=body.get("model", "unknown"),
        stream=want_stream,
        max_tokens=body.get("max_tokens"),
    )

    # ── Local probe short-circuit ────────────────────────────────────────────
    # optimize.is_local_probe() covers max_tokens ≤ 5 / trivial pings.
    # (count_tokens + /v1/models are already handled by their own routes above.)
    if is_local_probe(body):
        local_resp = _local_probe_response(body, request_id, want_stream)
        if want_stream:
            return StreamingResponse(
                local_resp,
                media_type="text/event-stream",
                headers=_sse_headers(request_id),
            )
        return JSONResponse(local_resp)

    # ── Delegate to service ─────────────────────────────────────────────────
    result = await handle_request_fn(body, request_id=request_id, stream=want_stream)

    if want_stream:
        # result is an AsyncIterator[str]
        return StreamingResponse(
            _wrap_async_iter(result),  # type: ignore[arg-type]
            media_type="text/event-stream",
            headers=_sse_headers(request_id),
        )

    # result is a dict
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# POST /v1/responses  (OpenAI Responses proxy endpoint)
# ---------------------------------------------------------------------------


@router.post("/v1/responses", dependencies=[Depends(_verify_bearer)])
async def responses(
    request: Request,
    handle_request_fn = Depends(get_handle_request_fn)
):
    """
    OpenAI Responses-compatible proxy endpoint.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": f"Invalid JSON body: {exc}",
                },
            },
        ) from exc

    # Ensure stream=True is set (we don't support non-streaming)
    if body.get("stream") is not True:
        adapter = OpenAIResponsesAdapter()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=adapter.error_payload(
                message="CLASP /v1/responses supports streaming only; set stream=true.",
                error_type="invalid_request_error",
            ),
        )

    try:
        adapter = OpenAIResponsesAdapter()
        anthropic_payload = adapter.to_anthropic_payload(body)
    except OpenAIResponsesAdapter.ConversionError as exc:
        adapter = OpenAIResponsesAdapter()
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=adapter.error_payload(
                message=str(exc),
                error_type="invalid_request_error",
            ),
        )

    request_id: str = (
        request.headers.get("X-Request-Id")
        or request.headers.get("x-request-id")
        or f"req_{uuid.uuid4().hex}"
    )

    # Delegate translated payload to handle_request
    result_stream = await handle_request_fn(
        anthropic_payload,
        request_id=request_id,
        stream=True,
    )

    sse_generator = adapter.iter_sse_from_anthropic(result_stream, body)
    
    headers = dict(adapter.sse_headers)
    headers["X-Request-Id"] = request_id

    return StreamingResponse(
        _wrap_async_iter(sse_generator),
        media_type="text/event-stream",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sse_headers(request_id: str) -> dict[str, str]:
    return {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-Request-Id": request_id,
    }


async def _wrap_async_iter(ait: AsyncIterator[str]) -> AsyncIterator[bytes]:
    """Encode each SSE string line to UTF-8 bytes for StreamingResponse."""
    async for chunk in ait:
        yield chunk.encode("utf-8")


@router.post("/stop", dependencies=[Depends(_verify_bearer)])
async def stop_cli(request: Request):
    """Stop all CLI sessions and pending tasks."""
    workflow = getattr(request.app.state, "messaging_workflow", None)
    if not workflow:
        # Fallback if messaging not initialized
        cli_manager = getattr(request.app.state, "cli_manager", None)
        if cli_manager:
            await cli_manager.stop_all()
            logger.info("STOP_CLI: source=cli_manager cancelled_count=N/A")
            return {"status": "stopped", "source": "cli_manager"}
        raise HTTPException(status_code=503, detail="Messaging system not initialized")

    count = await workflow.stop_all_tasks()
    from clasp.core.trace import trace_event
    trace_event(
        stage="ingress",
        event="api.cli.stop_via_messaging_workflow",
        source="api",
        cancelled_nodes=count,
    )
    logger.info("STOP_CLI: source=messaging_workflow cancelled_count={}", count)
    return {"status": "stopped", "cancelled_count": count}


def _local_probe_response(
    body: dict[str, Any],
    request_id: str,
    stream: bool,
) -> Any:
    """
    Return a minimal canned response for trivial probes (max_tokens ≤ 5).

    For streaming, returns an async generator; for non-streaming, a dict.
    The response is a valid Anthropic API shape so Claude Code parses it
    cleanly without knowing it was answered locally.
    """
    import json  # noqa: PLC0415

    model = body.get("model", "claude-sonnet-4-5")
    canned: dict[str, Any] = {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": ""}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }

    if not stream:
        return canned

    async def _gen() -> AsyncIterator[str]:
        # Anthropic SSE shape: message_start → content_block_start →
        # content_block_stop → message_delta → message_stop
        events = [
            ("message_start",       json.dumps({"type": "message_start", "message": canned})),
            ("content_block_start", json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})),
            ("content_block_delta", json.dumps({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " "}})),
            ("content_block_stop",  json.dumps({"type": "content_block_stop", "index": 0})),
            ("message_delta",       json.dumps({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 1}})),
            ("message_stop",        json.dumps({"type": "message_stop"})),
        ]
        for event_type, data in events:
            yield f"event: {event_type}\ndata: {data}\n\n"

    return _gen()


# Export constants for use in other modules if needed
__all__ = ["router"]