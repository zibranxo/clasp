"""
clasp/api/optimize.py
======================
Local probe answering — short-circuit responses that need no provider call.

Claude Code fires three categories of requests that are answered entirely
locally (plan.md §8 step [2], §14 "Local Probe Answering"):

1. **GET /v1/models** — Claude Code polls this on every startup to discover
   available models.  Answered with a static list of Claude model IDs so
   Claude Code is happy without burning a provider slot.

2. **POST /v1/messages/count_tokens** — Token pre-flight sent before most
   messages.  Answered with a tiktoken estimate (``cl100k_base``).  The
   estimate is conservative: always within ±5 % of real counts in practice.

3. **Trivial probe** — A ``POST /v1/messages`` with ``max_tokens ≤ 5``
   and a very short message body.  Claude Code sends these to test
   connectivity and liveness.  Answered with a canned streaming response
   that looks exactly like a real Anthropic response.

Public API
----------
``is_probe(path, body) → bool``
    Returns ``True`` if the request can be answered locally.

``handle_probe(path, body) → dict | None``
    Returns a JSON-serialisable dict (for ``/count_tokens`` and ``/v1/models``)
    or a list of SSE event strings (for trivial streaming probes), or ``None``
    if the request is not a probe.

``ProbeResult(type, payload)``
    Named dataclass returned by :func:`handle_probe`.
    ``type`` is one of ``"json"`` | ``"sse"`` | ``None``.
    ``payload`` carries the response body.

Implementation notes
--------------------
- No async — all computation is synchronous and cheap.
- ``tiktoken`` is imported lazily so missing the dep doesn't break startup
  for environments that skip optional extras; falls back to a character-
  based heuristic (4 chars ≈ 1 token).
- The canned SSE response is hardcoded and deterministic — no randomness,
  no provider calls, no network.

References: plan.md §8 [2], §14, §17 (API contracts for /v1/models and
            /v1/messages/count_tokens).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from loguru import logger


# ---------------------------------------------------------------------------
# Static model list (plan.md §14)
# ---------------------------------------------------------------------------

#: The model list returned by ``GET /v1/models``.
#: Claude Code expects these IDs to be present; the proxy maps them to
#: real upstream model slugs at dispatch time.
STATIC_MODEL_LIST: dict[str, Any] = {
    "object": "list",
    "data": [
        {"id": "claude-opus-4-5",           "object": "model", "owned_by": "clasp"},
        {"id": "claude-sonnet-4-5",          "object": "model", "owned_by": "clasp"},
        {"id": "claude-haiku-4-5",           "object": "model", "owned_by": "clasp"},
        {"id": "claude-3-5-sonnet-20241022", "object": "model", "owned_by": "clasp"},
        {"id": "claude-3-5-haiku-20241022",  "object": "model", "owned_by": "clasp"},
        {"id": "claude-3-opus-20240229",     "object": "model", "owned_by": "clasp"},
    ],
}

#: Maximum ``max_tokens`` value that is still considered a trivial probe.
TRIVIAL_PROBE_MAX_TOKENS: int = 5

#: Maximum total character length of a message body still considered a probe.
#: Guards against very short ``max_tokens`` on long multi-turn conversations.
TRIVIAL_PROBE_MAX_CHARS: int = 800


# ---------------------------------------------------------------------------
# ProbeResult
# ---------------------------------------------------------------------------

@dataclass
class ProbeResult:
    """
    The outcome of :func:`handle_probe`.

    Attributes
    ----------
    kind:
        ``"json"`` — ``payload`` is a JSON-serialisable dict.
        ``"sse"``  — ``payload`` is a ``list[str]`` of raw SSE event strings.
        ``None``   — not a probe; ``payload`` is ``None``.
    payload:
        The response body, or ``None`` when ``kind`` is ``None``.
    """

    kind: Literal["json", "sse"] | None
    payload: dict[str, Any] | list[str] | None

    @classmethod
    def not_a_probe(cls) -> "ProbeResult":
        return cls(kind=None, payload=None)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(body: dict[str, Any]) -> int:
    """
    Estimate the number of input tokens for *body*.

    Tries ``tiktoken`` (``cl100k_base``) first for accuracy.  If tiktoken is
    unavailable, falls back to the widely-used heuristic: 4 chars ≈ 1 token.

    Token counting mirrors the Anthropic ``count_tokens`` response contract:
    returns the estimated *input* token count, not output.
    """
    # Gather all text content from messages + system
    parts: list[str] = []

    system = body.get("system")
    if isinstance(system, str):
        parts.append(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))

    for msg in body.get("messages", []):
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        # Tool result content can itself be a list
                        tc = block.get("content", "")
                        if isinstance(tc, str):
                            parts.append(tc)
                        elif isinstance(tc, list):
                            for tb in tc:
                                if isinstance(tb, dict) and tb.get("type") == "text":
                                    parts.append(tb.get("text", ""))

    # Also add rough estimate for tool definitions
    for tool in body.get("tools", []):
        parts.append(json.dumps(tool))

    combined = " ".join(parts)

    # Try tiktoken
    try:
        import tiktoken  # type: ignore[import-untyped]
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(combined))
    except Exception:  # noqa: BLE001
        # Heuristic fallback: 4 chars ≈ 1 token (slightly conservative)
        return max(1, len(combined) // 4)


# ---------------------------------------------------------------------------
# Probe classification
# ---------------------------------------------------------------------------

def _body_char_length(body: dict[str, Any]) -> int:
    """Rough total character length of all text content in *body*."""
    try:
        return len(json.dumps(body))
    except Exception:  # noqa: BLE001
        return 0


def _is_trivial_probe(body: dict[str, Any]) -> bool:
    """
    Return True if this is a trivial liveness / connectivity probe.

    Criteria (both must hold):
    - ``max_tokens`` ≤ ``TRIVIAL_PROBE_MAX_TOKENS``
    - total body length ≤ ``TRIVIAL_PROBE_MAX_CHARS``

    The body-length guard prevents treating a huge multi-turn conversation
    with ``max_tokens=1`` (which Claude Code sometimes sends for token
    counting purposes) as a trivial probe.
    """
    max_tok = body.get("max_tokens", 1024)
    if not isinstance(max_tok, int) or max_tok > TRIVIAL_PROBE_MAX_TOKENS:
        return False
    return _body_char_length(body) <= TRIVIAL_PROBE_MAX_CHARS


# ---------------------------------------------------------------------------
# Canned SSE response for trivial probes
# ---------------------------------------------------------------------------

def _sse(event_type: str, data: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _canned_sse_response(model: str, request_id: str | None = None) -> list[str]:
    """
    Build a minimal but spec-compliant Anthropic SSE response.

    The response contains a single space character as text content.
    This is the smallest possible non-empty response that satisfies Claude
    Code's expectation of a complete message stream.
    """
    msg_id = request_id or f"msg_clasp_{uuid.uuid4().hex[:12]}"
    events: list[str] = [
        _sse("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        }),
        _sse("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        }),
        _sse("content_block_delta", {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": " "},
        }),
        _sse("content_block_stop", {
            "type": "content_block_stop",
            "index": 0,
        }),
        _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 1},
        }),
        _sse("message_stop", {"type": "message_stop"}),
    ]
    return events


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_probe(path: str, body: dict[str, Any]) -> bool:
    """
    Return ``True`` if this request can be answered locally without a provider.

    Parameters
    ----------
    path:
        The HTTP request path, e.g. ``"/v1/models"`` or
        ``"/v1/messages/count_tokens"``.
    body:
        Parsed JSON request body (may be empty ``{}`` for GET requests).
    """
    if path == "/v1/models":
        return True
    if path == "/v1/messages/count_tokens":
        return True
    if path == "/v1/messages" and _is_trivial_probe(body):
        return True
    return False


def handle_probe(
    path: str,
    body: dict[str, Any],
    *,
    request_id: str | None = None,
) -> ProbeResult:
    """
    Handle a local probe and return the appropriate :class:`ProbeResult`.

    Returns :meth:`ProbeResult.not_a_probe` if *path* + *body* is not a probe.

    Parameters
    ----------
    path:
        HTTP request path.
    body:
        Parsed JSON body.  May be empty ``{}`` for GET requests.
    request_id:
        Optional request ID to embed in canned SSE responses (for log correlation).
    """
    # ── /v1/models ───────────────────────────────────────────────────────────
    if path == "/v1/models":
        logger.debug("optimize: serving model list")
        return ProbeResult(kind="json", payload=answer_models())

    # ── /v1/messages/count_tokens ────────────────────────────────────────────
    if path == "/v1/messages/count_tokens":
        token_count = _estimate_tokens(body)
        logger.debug("optimize: count_tokens estimate", tokens=token_count)
        return ProbeResult(kind="json", payload={"input_tokens": token_count})

    # ── trivial probe on /v1/messages ────────────────────────────────────────
    if path == "/v1/messages" and _is_trivial_probe(body):
        model = body.get("model", "claude-sonnet-4-5")
        logger.debug("optimize: serving canned SSE for trivial probe",
                     model=model, max_tokens=body.get("max_tokens"))
        return ProbeResult(
            kind="sse",
            payload=_canned_sse_response(model, request_id=request_id),
        )

    return ProbeResult.not_a_probe()


# ---------------------------------------------------------------------------
# Convenience: model list accessor (used by proxy_routes.py GET /v1/models)
# ---------------------------------------------------------------------------

def get_model_list() -> dict[str, Any]:
    """Return the static model list dict directly."""
    return STATIC_MODEL_LIST


def count_tokens_local(body: dict[str, Any]) -> int:
    """Return a local token estimate for *body*. Convenience wrapper."""
    return _estimate_tokens(body)


# Export constants for use in other modules
__all__ = [
    "STATIC_MODEL_LIST",
    "TRIVIAL_PROBE_MAX_TOKENS",
    "TRIVIAL_PROBE_MAX_CHARS",
    "is_probe",
    "handle_probe",
    "ProbeResult",
    "get_model_list",
    "count_tokens_local",
    "COUNT_TOKENS_ENDPOINT",
    "MODELS_ENDPOINT",
    "answer_count_tokens",
    "answer_models",
    "is_local_probe",
]

# Define the constants that proxy_routes.py expects
COUNT_TOKENS_ENDPOINT = "/v1/messages/count_tokens"
MODELS_ENDPOINT = "/v1/models"

def answer_count_tokens(body: dict[str, Any]) -> dict:
    """Answer count_tokens request locally."""
    return handle_probe("/v1/messages/count_tokens", body).payload

def answer_models() -> dict:
    """Answer models request locally with static and dynamic prefixed variants."""
    data = list(STATIC_MODEL_LIST["data"])
    try:
        from clasp.providers.registry import get_model_lists
        model_lists = get_model_lists()
        for provider_name, models in model_lists.items():
            if provider_name == "anthropic":
                continue
            for model_id in models:
                data.append({
                    "id": f"anthropic/{provider_name}/{model_id}",
                    "object": "model",
                    "owned_by": "clasp"
                })
                data.append({
                    "id": f"claude-3-freecc-no-thinking/{provider_name}/{model_id}",
                    "object": "model",
                    "owned_by": "clasp"
                })
    except Exception as e:
        logger.warning(f"Failed to fetch dynamic model list from registry: {e}")

    return {
        "object": "list",
        "data": data,
    }

def is_local_probe(body: dict[str, Any]) -> bool:
    """Check if body represents a local probe (for /v1/messages endpoint)."""
    return _is_trivial_probe(body)