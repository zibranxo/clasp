"""
clasp/api/service.py
Top-level request orchestration — the spine of the request flow (plan §8):

    proxy_routes.py   → validate Bearer token, parse body
    optimize.py       → short-circuit probes (/v1/models, count_tokens, pings)
    response_cache.py → [STEP 3] memory LRU hit → return; SQLite hit → return
    detect.py         → classify INTERACTIVE | BACKGROUND | LONG_CONTEXT | ...
    selector.py       → walk provider_chain, check bucket/CB health, pick key
    optimizer/        → [STEP 9, pre-dispatch] system prompt dedup,
                          context pruning, payload filter
    provider.stream()  → translate, stream, translate SSE back
    response_cache.py → [STEP 9, post-dispatch] store on success

This module owns exactly those two integration points:

    Step 3 — cache check BEFORE the optimizer/selector pipeline runs at all.
             A cache hit short-circuits everything: no detect(), no select(),
             no optimizer, no provider call.

    Step 9 — the request-shaping optimizer passes (system_prompt dedup,
             context_pruner, payload_filter) run immediately before dispatch,
             i.e. AFTER routing has already picked a provider/model.
             The successful, fully-collected response is written to cache
             right after.

dispatch() is the single entry point proxy_routes.py calls for non-streaming
requests; dispatch_stream() is used for streaming requests. Both share the
same cache-check → route → optimize → call → cache-write pipeline so the
caching behavior is identical regardless of the `stream` flag (the cache key
already excludes `stream` — see utils/hash.py).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, AsyncGenerator

try:
    from loguru import logger as _logger
    _LOG = True
except ImportError:
    _LOG = False

from clasp.cache.response_cache import get_cache
from clasp.utils.hash import hash_request

from clasp.providers.common.error_mapper import ErrorType, build_anthropic_error
from clasp.providers.common.token_counter import estimate_request_tokens
from clasp.config.settings import get_settings

try:
    from clasp.api.detect import detect as _default_detect
except ImportError:  # pragma: no cover
    _default_detect = None  # type: ignore[assignment]

from clasp.router.types import AnthropicRequest


# ---------------------------------------------------------------------------
# Public entry points (called by proxy_routes.py)
# ---------------------------------------------------------------------------

async def dispatch_stream(
    request: dict[str, Any],
    *,
    request_id: str | None = None,
    # Injected dependencies — default to the real implementations at call
    # time so this module stays unit-testable without a running server.
    detect_fn=None,
    select_fn=None,
    optimize_fn=None,
) -> AsyncGenerator[bytes, None]:
    """
    Handle a streaming ``/v1/messages`` request end-to-end.

    Yields raw Anthropic-formatted SSE bytes. On a cache hit, yields the
    exact bytes that were cached (so the client sees an identical stream
    to the original response, including all event boundaries).
    """
    model = request.get("model", "")
    if model:
        from clasp.router.model_map import decode_gateway_model_id
        decoded = decode_gateway_model_id(model)
        if decoded:
            _, _, thinking_enabled = decoded
            if not thinking_enabled:
                request.pop("thinking", None)

    request_id = request_id or _make_request_id()
    cache = get_cache()
    cache_key = hash_request(request)

    if _LOG:
        _logger.debug(
            "service: request start",
            request_id=request_id,
            stream=True,
            model=request.get("model"),
            cache_key=cache_key[:12],
        )

    # ── STEP 3: cache check BEFORE the optimizer/selector pipeline ────────
    if cache is not None:
        cached_chunks = await cache.get(cache_key)
        if cached_chunks is not None:
            if _LOG:
                _logger.info(
                    "cache hit — skipping provider dispatch entirely",
                    request_id=request_id,
                    cache_key=cache_key[:12],
                    model=request.get("model"),
                )
            for chunk in cached_chunks:
                yield chunk
            return
        if _LOG:
            _logger.debug(
                "cache miss",
                request_id=request_id,
                cache_key=cache_key[:12],
            )

    try:
        from clasp.config.settings import get_settings  # noqa: PLC0415
        settings = get_settings()
    except Exception:
        settings = None

    if settings is not None:
        from clasp.api.optimization_handlers import try_optimizations  # noqa: PLC0415
        opt_response = try_optimizations(request, settings)
        if opt_response is not None:
            def _sse_event(event_type: str, data: dict) -> bytes:
                return f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode("utf-8")

            # 1. message_start
            yield _sse_event("message_start", {
                "type": "message_start",
                "message": {
                    "id": opt_response["id"],
                    "type": "message",
                    "role": "assistant",
                    "model": opt_response["model"],
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": opt_response["usage"]["input_tokens"],
                        "output_tokens": 0,
                    }
                }
            })

            # 2. content block(s)
            for idx, block in enumerate(opt_response["content"]):
                yield _sse_event("content_block_start", {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": block["type"],
                        "text": ""
                    }
                })

                yield _sse_event("content_block_delta", {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {
                        "type": "text_delta",
                        "text": block["text"]
                    }
                })

                yield _sse_event("content_block_stop", {
                    "type": "content_block_stop",
                    "index": idx
                })

            # 3. message_delta
            yield _sse_event("message_delta", {
                "type": "message_delta",
                "delta": {
                    "stop_reason": opt_response["stop_reason"],
                    "stop_sequence": opt_response.get("stop_sequence")
                },
                "usage": {
                    "output_tokens": opt_response["usage"]["output_tokens"]
                }
            })

            # 4. message_stop
            yield _sse_event("message_stop", {
                "type": "message_stop"
            })
            return

    if settings is not None and settings.enable_web_server_tools:
        from clasp.api.web_tools.request import is_web_server_tool_request
        if is_web_server_tool_request(request):
            from clasp.api.web_tools.egress import WebFetchEgressPolicy
            from clasp.api.web_tools.streaming import stream_web_server_tool_response

            egress = WebFetchEgressPolicy(
                allow_private_network_targets=settings.web_fetch_allow_private_networks,
                allowed_schemes=settings.web_fetch_allowed_scheme_set(),
            )
            input_tokens = estimate_request_tokens(request)

            async for chunk in stream_web_server_tool_response(
                request,
                input_tokens=input_tokens,
                web_fetch_egress=egress,
                verbose_client_errors=False,
            ):
                yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk
            return

    # ── classify + route (only reached on a cache miss) ───────────────────
    if select_fn is None:
        from clasp.router.selector import select as select_fn  # type: ignore[import]

    try:
        # Build an AnthropicRequest from the raw body so selector gets the
        # classified + enriched request shape it expects.
        anthropic_request = AnthropicRequest.from_body(request)
        selection = await select_fn(anthropic_request)
    except Exception as exc:  # noqa: BLE001
        if _LOG:
            _logger.error(
                "service: classify/select failed",
                request_id=request_id,
                model=request.get("model"),
                error=str(exc),
            )
        yield _error_event(
            ErrorType.SERVER_ERROR,
            "Request classification/routing failed.",
        )
        return

    if selection is None:
        if _LOG:
            _logger.warning(
                "service: no provider available",
                request_id=request_id,
                model=request.get("model"),
            )
        yield _error_event(
            ErrorType.OVERLOADED,
            "No provider currently available for this request.",
        )
        return

    provider, api_key, key_index = selection

    # ── Local server tool validation for OpenAI-compatible providers ─────────
    provider_name = getattr(provider, "provider_name", "")
    from clasp.config.provider_catalog import PROVIDER_CATALOG
    profile = PROVIDER_CATALOG.get(provider_name)
    is_openai_chat = profile is not None and profile.transport == "openai_chat"
    if is_openai_chat:
        from clasp.api.web_tools.request import openai_chat_upstream_server_tool_error
        web_tools_enabled = settings.enable_web_server_tools if settings is not None else False
        tool_err = openai_chat_upstream_server_tool_error(request, web_tools_enabled=web_tools_enabled)
        if tool_err is not None:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=400,
                detail={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": tool_err,
                    },
                },
            )
    # Retrieve key_pool for outcome feedback after dispatch.
    from clasp.providers.registry import get_registry  # noqa: PLC0415
    _kp = get_registry().get_key_pool(getattr(provider, "provider_name", ""))

    # Update request model to the resolved model slug
    try:
        from clasp.config.settings import get_settings  # noqa: PLC0415
        settings = get_settings()
    except Exception:
        settings = None

    if settings is not None:
        from clasp.router.model_map import resolve_model  # noqa: PLC0415
        model_slug = resolve_model(anthropic_request, getattr(provider, "provider_name", ""), settings)
        if model_slug:
            anthropic_request.body["model"] = model_slug

    # ── STEP 9 (pre-dispatch): request-shaping optimizer passes ────────
    if optimize_fn is None:
        try:
            from clasp.optimizer import optimize as optimize_fn  # type: ignore[import]
        except ImportError:  # pragma: no cover
            optimize_fn = None  # type: ignore[assignment]

    if optimize_fn is not None:
        try:
            optimized = await _maybe_await(
                optimize_fn(anthropic_request.body, provider_name=getattr(provider, "provider_name", None))
            )
            if isinstance(optimized, dict):
                # Optimizer returned a modified body — patch it back onto the request.
                from dataclasses import replace as _replace  # noqa: PLC0415
                anthropic_request = _replace(anthropic_request, body=optimized)
        except Exception as exc:  # noqa: BLE001
            if _LOG:
                _logger.error(
                    "service: optimize failed (continuing with original body)",
                    request_id=request_id,
                    provider=getattr(provider, "provider_name", None),
                    error=str(exc),
                )
            # Optimization is best-effort — don't abort the request on failure.

    # ── dispatch + collect (so we can write a complete entry to cache) ───
    collected: list[bytes] = []
    success = False
    actual_tokens: int = 0
    estimated_tokens: int = anthropic_request.estimated_tokens

    try:
        async for chunk in provider.stream(
            anthropic_request, key=api_key, key_index=key_index
        ):
            raw = chunk if isinstance(chunk, bytes) else chunk.encode()
            collected.append(raw)
            yield raw
        success = True
    except Exception as exc:  # noqa: BLE001
        if _LOG:
            _logger.error(
                "service: provider stream failed",
                request_id=request_id,
                provider=getattr(provider, "provider_name", None),
                key_index=key_index,
                error=str(exc),
            )
        err = _error_event(
            ErrorType.SERVER_ERROR,
            "Upstream provider error.",
        )
        collected.append(err)
        yield err
    finally:
        # Outcome feedback — keeps circuit breaker and TPM bucket in sync.
        if _kp is not None:
            if success:
                _kp.record_success(key_index)
                # TPM reconciliation: try to parse actual token usage from the
                # collected SSE stream so consume_actual() can correct the
                # pre-flight estimate.  Falls back silently if unparseable.
                try:
                    actual_tokens = _parse_actual_tokens(collected)
                    if actual_tokens > 0:
                        await _kp.buckets[key_index].consume_actual(
                            actual=actual_tokens, estimated=estimated_tokens
                        )
                except Exception:  # noqa: BLE001
                    pass
            else:
                _kp.record_error(key_index)
        if _LOG:
            _logger.info(
                "service: request complete",
                request_id=request_id,
                provider=getattr(provider, "provider_name", None),
                key_index=key_index,
                stream=True,
                success=success,
            )

    # ── STEP 9 (post-dispatch): cache the complete, successful response ──
    if cache is not None and collected and success:
        await cache.set(cache_key, collected)
        if _LOG:
            _logger.debug(
                "cache write",
                request_id=request_id,
                cache_key=cache_key[:12],
                chunks=len(collected),
                provider=getattr(provider, "name", None),
            )


async def dispatch(
    request: dict[str, Any],
    *,
    request_id: str | None = None,
    detect_fn=None,
    select_fn=None,
    optimize_fn=None,
) -> dict[str, Any]:
    """
    Handle a non-streaming ``/v1/messages`` request.

    Internally reuses ``dispatch_stream`` and reassembles the SSE event
    sequence into a single Anthropic Messages response object, so caching
    behavior is identical between streaming and non-streaming calls for the
    same logical request (the cache key excludes the ``stream`` field).
    """
    request_id = request_id or _make_request_id()
    t0 = time.monotonic()

    try:
        from clasp.config.settings import get_settings  # noqa: PLC0415
        settings = get_settings()
    except Exception:
        settings = None

    if settings is not None:
        from clasp.api.optimization_handlers import try_optimizations  # noqa: PLC0415
        opt_response = try_optimizations(request, settings)
        if opt_response is not None:
            if _LOG:
                _logger.info(
                    "service: optimization short-circuit (non-stream)",
                    request_id=request_id,
                    model=request.get("model"),
                )
            return opt_response

    chunks: list[bytes] = []
    async for chunk in dispatch_stream(
        request,
        request_id=request_id,
        detect_fn=detect_fn,
        select_fn=select_fn,
        optimize_fn=optimize_fn,
    ):
        chunks.append(chunk)

    message = _assemble_message(chunks)

    if _LOG:
        _logger.info(
            "service: request complete",
            request_id=request_id,
            model=request.get("model"),
            stream=False,
            latency_s=round(time.monotonic() - t0, 3),
            result_type=message.get("type"),
        )

    return message


async def handle_request(
    body: dict[str, Any],
    *,
    request_id: str | None = None,
    stream: bool | None = None,
    detect_fn=None,
    select_fn=None,
    optimize_fn=None,
) -> dict[str, Any] | AsyncGenerator[bytes, None]:
    """
    Compatibility wrapper for the newer orchestration entry point.

    Keeps the v1 cache-first pipeline as the source of truth.
    """
    request_id = request_id or _make_request_id()
    if stream is None:
        stream = bool(body.get("stream", False))

    if stream:
        return dispatch_stream(
            body,
            request_id=request_id,
            detect_fn=detect_fn,
            select_fn=select_fn,
            optimize_fn=optimize_fn,
        )

    return await dispatch(
        body,
        request_id=request_id,
        detect_fn=detect_fn,
        select_fn=select_fn,
        optimize_fn=optimize_fn,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_request_id() -> str:
    """Generate a stable ``req_<uuid4>`` request identifier."""
    return f"req_{uuid.uuid4().hex}"


def _provider_name(provider: Any) -> str:
    """Best-effort provider label for logs."""
    return getattr(
        provider,
        "name",
        getattr(provider, "provider_name", type(provider).__name__),
    )


def _build_routing_request(
    request: dict[str, Any],
    *,
    request_id: str,
    request_type: Any,
) -> dict[str, Any]:
    """
    Legacy helper kept for backward compat with any code that still calls it.
    The main dispatch path now uses AnthropicRequest.from_body() instead.
    """
    enriched_request = dict(request)
    enriched_request["request_id"] = request_id
    enriched_request["request_type"] = request_type
    enriched_request.setdefault("type", request_type)
    enriched_request["estimated_tokens"] = estimate_request_tokens(request)
    return enriched_request


def _error_event(error_type: ErrorType, message: str) -> bytes:
    """One Anthropic-format ``event: error`` SSE block."""
    payload = build_anthropic_error(error_type, message)
    return f"event: error\ndata: {json.dumps(payload)}\n\n".encode()


def _parse_actual_tokens(chunks: list[bytes]) -> int:
    """
    Scan collected SSE chunks for a ``message_delta`` event with a
    ``usage.output_tokens`` field and return the count, or 0 if not found.
    """
    import json as _json

    for raw in chunks:
        text = raw.decode(errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                event = _json.loads(line[5:].strip())
            except _json.JSONDecodeError:
                continue
            if event.get("type") == "message_delta":
                usage = event.get("usage", {})
                tokens = usage.get("output_tokens")
                if isinstance(tokens, int) and tokens > 0:
                    return tokens
    return 0


async def _maybe_await(value: Any) -> Any:
    """Allow detect_fn/optimize_fn to be sync or async callables."""
    if hasattr(value, "__await__"):
        return await value
    return value


def _assemble_message(chunks: list[bytes]) -> dict[str, Any]:
    """
    Reconstruct a single Anthropic Messages API response object from a
    sequence of SSE event chunks (message_start, content_block_*,
    message_delta, message_stop).

    If an ``event: error`` chunk is present, return the parsed error payload
    directly so non-streaming callers get a structured error response.
    """
    import json as _json

    message: dict[str, Any] = {
        "id": "",
        "type": "message",
        "role": "assistant",
        "content": [],
        "model": "",
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    blocks: dict[int, dict[str, Any]] = {}

    for raw in chunks:
        text = raw.decode(errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue

            payload_str = line[5:].strip()
            try:
                event = _json.loads(payload_str)
            except _json.JSONDecodeError:
                continue

            etype = event.get("type")
            if etype == "error":
                return event

            if etype == "message_start":
                msg = event.get("message", {})
                message["id"] = msg.get("id", message["id"])
                message["model"] = msg.get("model", message["model"])
                usage = msg.get("usage", {})
                message["usage"]["input_tokens"] = usage.get(
                    "input_tokens", message["usage"]["input_tokens"]
                )

            elif etype == "content_block_start":
                idx = event.get("index", 0)
                blocks[idx] = dict(event.get("content_block", {"type": "text", "text": ""}))

            elif etype == "content_block_delta":
                idx = event.get("index", 0)
                delta = event.get("delta", {})
                block = blocks.setdefault(idx, {"type": "text", "text": ""})

                if delta.get("type") == "text_delta":
                    block["text"] = block.get("text", "") + delta.get("text", "")
                elif delta.get("type") == "input_json_delta":
                    block["input_json_partial"] = (
                        block.get("input_json_partial", "")
                        + delta.get("partial_json", "")
                    )

            elif etype == "message_delta":
                delta = event.get("delta", {})
                if "stop_reason" in delta:
                    message["stop_reason"] = delta["stop_reason"]
                if "stop_sequence" in delta:
                    message["stop_sequence"] = delta["stop_sequence"]
                usage = event.get("usage", {})
                if "output_tokens" in usage:
                    message["usage"]["output_tokens"] = usage["output_tokens"]
            # message_stop carries no additional fields we need.

    message["content"] = [blocks[i] for i in sorted(blocks)]
    return message