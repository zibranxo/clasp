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

try:
    from clasp.api.detect import classify as _default_detect  # v1 style
except ImportError:  # pragma: no cover
    from clasp.api.detect import detect as _default_detect  # type: ignore

from clasp.api.detect import needs_tools, needs_vision, priority_for


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

    # ── classify + route (only reached on a cache miss) ───────────────────
    if detect_fn is None:
        detect_fn = _default_detect
    if select_fn is None:
        from clasp.router.selector import select as select_fn  # type: ignore[import]

    try:
        request_type = await _maybe_await(detect_fn(request))
        request_type_value = getattr(request_type, "value", request_type)

        enriched_request = _build_routing_request(
            request,
            request_id=request_id,
            request_type=request_type_value,
        )

        selection = await select_fn(enriched_request)
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
            f"Request classification/routing failed: {exc}",
        )
        return

    if selection is None:
        if _LOG:
            _logger.warning(
                "service: no provider available",
                request_id=request_id,
                model=request.get("model"),
                type=enriched_request.get("request_type"),
            )
        yield _error_event(
            ErrorType.OVERLOADED,
            "No provider currently available for this request.",
        )
        return

    provider, api_key, key_index = selection

    # ── STEP 9 (pre-dispatch): request-shaping optimizer passes ──────────
    if optimize_fn is None:
        from clasp.optimizer import optimize as optimize_fn  # type: ignore[import]

    try:
        optimized_request = await _maybe_await(
            optimize_fn(enriched_request, provider_name=getattr(provider, "name", None))
        )
    except Exception as exc:  # noqa: BLE001
        if _LOG:
            _logger.error(
                "service: optimize failed",
                request_id=request_id,
                provider=getattr(provider, "name", None),
                error=str(exc),
            )
        yield _error_event(
            ErrorType.SERVER_ERROR,
            f"Request optimization failed: {exc}",
        )
        return

    # ── dispatch + collect (so we can write a complete entry to cache) ───
    collected: list[bytes] = []
    success = False

    try:
        async for chunk in provider.stream(
            optimized_request, key=api_key, key_index=key_index
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
                provider=getattr(provider, "name", None),
                key_index=key_index,
                error=str(exc),
            )
        err = _error_event(
            ErrorType.SERVER_ERROR,
            f"Upstream provider error: {exc}",
        )
        collected.append(err)
        yield err
    finally:
        if _LOG:
            _logger.info(
                "service: request complete",
                request_id=request_id,
                provider=getattr(provider, "name", None),
                key_index=key_index,
                stream=True,
                success=success,
                type=enriched_request.get("request_type"),
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
) -> dict[str, Any] | AsyncGenerator[bytes, None]:
    """
    Compatibility wrapper for the newer orchestration entry point.

    Keeps the v1 cache-first pipeline as the source of truth.
    """
    request_id = request_id or _make_request_id()
    if stream is None:
        stream = bool(body.get("stream", False))

    if stream:
        return dispatch_stream(body, request_id=request_id)

    return await dispatch(body, request_id=request_id)


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
    Add routing metadata from v2 without changing the v1 routing contract.
    """
    enriched_request = dict(request)
    enriched_request["request_id"] = request_id
    enriched_request["request_type"] = request_type
    enriched_request.setdefault("type", request_type)
    enriched_request["estimated_tokens"] = estimate_request_tokens(request)
    enriched_request["needs_tools"] = needs_tools(request)
    enriched_request["needs_vision"] = needs_vision(request)
    enriched_request["priority"] = priority_for(request_type)
    return enriched_request


def _error_event(error_type: ErrorType, message: str) -> bytes:
    """One Anthropic-format ``event: error`` SSE block."""
    payload = build_anthropic_error(error_type, message)
    return f"event: error\ndata: {json.dumps(payload)}\n\n".encode()


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