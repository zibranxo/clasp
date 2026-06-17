"""
clasp/providers/common/error_mapper.py
=========================================
HTTP error → internal error type classification (plan.md §9 step 11,
§20 step 11).

Every provider speaks a slightly different error dialect: OpenAI-compatible
providers nest `{"error": {"type": ..., "code": ..., "message": ...}}`,
Gemini uses `{"error": {"code": int, "status": "RESOURCE_EXHAUSTED", ...}}`
and sometimes signals trouble via `finish_reason` on an otherwise-200
response rather than an HTTP error at all. `classify_error()` normalizes
all of that into one small :class:`ErrorType` enum that the rest of the
proxy — circuit breaker, absorber, response translation — can reason about
without knowing which provider it's talking to (P1 — transparency).

This module deliberately does **not** decide retry/failover policy itself;
it only classifies. The one exception, `is_retryable()`, encodes a single
hard-won rule from plan.md ("Gemini Safety Blocks Are Not Rate Limits"):
content-filter style rejections must never trigger a different-provider
retry, because the content itself is the problem, not the provider.

Public API
----------
``ErrorType``
    Enum: ``RATE_LIMIT``, ``OVERLOADED``, ``SERVER_ERROR``, ``TIMEOUT``,
    ``AUTH_ERROR``, ``PERMISSION_ERROR``, ``NOT_FOUND``, ``INVALID_REQUEST``,
    ``CONTENT_FILTER``, ``UNKNOWN``.

``classify_error(status, body=None) → ErrorType``
    Primary entry point. ``status`` is the HTTP status code, or ``None`` for
    connection-level failures (timeouts, connection refused) that never got
    an HTTP response at all. ``body`` is the parsed JSON error body (dict),
    a raw string, or ``None``.

``is_retryable(error_type) → bool``
    Whether the absorber should attempt failover to a different provider for
    this error type, vs. surfacing it directly to Claude Code.

``to_anthropic_error_type(error_type) → str``
    Maps an internal :class:`ErrorType` to the Anthropic API's own error
    `type` string (the value that goes in
    ``{"error": {"type": "...", ...}}``).

``build_anthropic_error(error_type, message) → dict``
    Builds a complete Anthropic-format error response body, ready to be
    serialised and sent back to Claude Code (either as a JSON error response
    or wrapped in an SSE `error` event by `queue/sse_hold.py`).

References: plan.md §9 step 11, §20 step 11, "Critical Translation Details"
            → Gemini Safety Blocks Are Not Rate Limits, request flow [8].
"""

from __future__ import annotations

from enum import Enum
from typing import Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover — shim for test environments
    import logging as _logging

    class _Shim:
        _log = _logging.getLogger("clasp.error_mapper")

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

        def warning(self, msg: str, **kw: Any) -> None:
            self._log.warning(msg + ("  " + str(kw) if kw else ""))

    logger = _Shim()  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# ErrorType
# ---------------------------------------------------------------------------

class ErrorType(str, Enum):
    """
    Internal error classification, provider-agnostic.

    Subclasses ``str`` so instances compare equal to their value and
    serialise cleanly (``json.dumps`` works without a custom encoder).
    """

    RATE_LIMIT = "rate_limit"            # 429 — quota/RPM/TPM exceeded
    OVERLOADED = "overloaded"            # 503/529 — provider temporarily saturated
    SERVER_ERROR = "server_error"        # other 5xx — provider-side fault
    TIMEOUT = "timeout"                  # no HTTP response at all (connect/read timeout)
    AUTH_ERROR = "auth_error"            # 401 — bad/expired API key
    PERMISSION_ERROR = "permission_error"  # 403 — key valid but lacks access
    NOT_FOUND = "not_found"              # 404 — unknown model/endpoint
    INVALID_REQUEST = "invalid_request"  # 400/422 — malformed request
    CONTENT_FILTER = "content_filter"    # safety/moderation block (e.g. Gemini SAFETY)
    UNKNOWN = "unknown"                  # anything we couldn't classify


# ---------------------------------------------------------------------------
# Status-code → ErrorType (the primary signal)
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[int, ErrorType] = {
    400: ErrorType.INVALID_REQUEST,
    401: ErrorType.AUTH_ERROR,
    403: ErrorType.PERMISSION_ERROR,
    404: ErrorType.NOT_FOUND,
    408: ErrorType.TIMEOUT,
    409: ErrorType.INVALID_REQUEST,
    413: ErrorType.INVALID_REQUEST,
    422: ErrorType.INVALID_REQUEST,
    429: ErrorType.RATE_LIMIT,
    500: ErrorType.SERVER_ERROR,
    502: ErrorType.SERVER_ERROR,
    503: ErrorType.OVERLOADED,
    504: ErrorType.TIMEOUT,
    529: ErrorType.OVERLOADED,  # Anthropic's own "overloaded" status
}

#: finish_reason / blockReason values (Gemini, and similar fields from other
#: providers) that indicate a content/safety block rather than a real error,
#: even when the HTTP status itself was 200.
_CONTENT_FILTER_FINISH_REASONS = {
    "SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST",
    "content_filter", "CONTENT_FILTER",
}


# ---------------------------------------------------------------------------
# Body inspection helpers
# ---------------------------------------------------------------------------

def _body_as_dict(body: Any) -> dict[str, Any]:
    """Best-effort coercion of *body* to a dict for uniform inspection."""
    if isinstance(body, dict):
        return body
    if isinstance(body, str):
        try:
            import json
            parsed = json.loads(body)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _detect_content_filter(body_dict: dict[str, Any]) -> bool:
    """
    Look for provider-specific signals that this is a content/safety block,
    not a genuine error or rate limit. Checked regardless of HTTP status,
    since Gemini reports SAFETY blocks on an otherwise-200 response.
    """
    # Gemini: candidates[].finishReason, or top-level promptFeedback.blockReason
    candidates = body_dict.get("candidates")
    if isinstance(candidates, list):
        for c in candidates:
            if isinstance(c, dict) and c.get("finishReason") in _CONTENT_FILTER_FINISH_REASONS:
                return True

    prompt_feedback = body_dict.get("promptFeedback")
    if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
        return True

    # OpenAI-compatible: choices[].finish_reason == "content_filter"
    choices = body_dict.get("choices")
    if isinstance(choices, list):
        for c in choices:
            if isinstance(c, dict) and c.get("finish_reason") in _CONTENT_FILTER_FINISH_REASONS:
                return True

    # Direct finish_reason at top level (some providers flatten this)
    if body_dict.get("finish_reason") in _CONTENT_FILTER_FINISH_REASONS:
        return True

    return False


def _detect_error_type_from_body_type_field(body_dict: dict[str, Any]) -> ErrorType | None:
    """
    OpenAI/Anthropic-style error bodies nest a ``type`` string under
    ``error``: ``{"error": {"type": "invalid_request_error", ...}}``.
    If present and recognisable, it's a strong, provider-confirmed signal —
    stronger than guessing from the status code alone.
    """
    error_obj = body_dict.get("error")
    if not isinstance(error_obj, dict):
        return None

    type_str = str(error_obj.get("type", "")).lower()

    if "rate_limit" in type_str or "quota" in type_str or "resource_exhausted" in type_str:
        return ErrorType.RATE_LIMIT
    if "overloaded" in type_str:
        return ErrorType.OVERLOADED
    if "authentication" in type_str or "invalid_api_key" in type_str:
        return ErrorType.AUTH_ERROR
    if "permission" in type_str:
        return ErrorType.PERMISSION_ERROR
    if "not_found" in type_str:
        return ErrorType.NOT_FOUND
    if "invalid_request" in type_str or "invalid_argument" in type_str:
        return ErrorType.INVALID_REQUEST
    if "content_filter" in type_str or "safety" in type_str:
        return ErrorType.CONTENT_FILTER
    if "server_error" in type_str or "internal" in type_str:
        return ErrorType.SERVER_ERROR

    # Gemini nests status string instead: {"error": {"status": "RESOURCE_EXHAUSTED"}}
    status_str = str(error_obj.get("status", "")).upper()
    if status_str == "RESOURCE_EXHAUSTED":
        return ErrorType.RATE_LIMIT
    if status_str == "UNAVAILABLE":
        return ErrorType.OVERLOADED
    if status_str in {"PERMISSION_DENIED"}:
        return ErrorType.PERMISSION_ERROR
    if status_str in {"UNAUTHENTICATED"}:
        return ErrorType.AUTH_ERROR
    if status_str in {"NOT_FOUND"}:
        return ErrorType.NOT_FOUND
    if status_str in {"INVALID_ARGUMENT", "FAILED_PRECONDITION"}:
        return ErrorType.INVALID_REQUEST

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_error(status: int | None, body: Any = None) -> ErrorType:
    """
    Classify an upstream provider error into a provider-agnostic
    :class:`ErrorType`.

    Parameters
    ----------
    status:
        HTTP status code returned by the provider, or ``None`` if the
        request never got an HTTP response at all (connection refused,
        DNS failure, read/connect timeout raised by the HTTP client).
    body:
        The response body — a parsed JSON dict, a raw string, or ``None``.
        Inspected for provider-specific error signals that refine or
        override the status-code-based guess.

    Returns
    -------
    ErrorType
        Best-effort classification. Defaults to :attr:`ErrorType.UNKNOWN`
        when nothing matches.

    Notes
    -----
    Content-filter detection runs *first* and can override any status code,
    including 200 — Gemini reports safety blocks on successful HTTP
    responses via ``finish_reason``/``blockReason`` rather than an error
    status (see plan.md "Gemini Safety Blocks Are Not Rate Limits").
    """
    body_dict = _body_as_dict(body)

    # Content-filter blocks take priority — they can hide behind a 200.
    if _detect_content_filter(body_dict):
        logger.debug("error_mapper: classified as content_filter", status=status)
        return ErrorType.CONTENT_FILTER

    # No HTTP response at all → connection-level timeout/failure.
    if status is None:
        logger.debug("error_mapper: classified as timeout (no status)")
        return ErrorType.TIMEOUT

    # Body's own declared error type/status, when present, is more reliable
    # than guessing purely from the HTTP status code.
    from_body = _detect_error_type_from_body_type_field(body_dict)
    if from_body is not None:
        logger.debug("error_mapper: classified from body", status=status, error_type=from_body.value)
        return from_body

    # Fall back to the HTTP status code.
    if status in _STATUS_MAP:
        result = _STATUS_MAP[status]
        logger.debug("error_mapper: classified from status", status=status, error_type=result.value)
        return result

    if 500 <= status < 600:
        logger.debug("error_mapper: unmapped 5xx, defaulting to server_error", status=status)
        return ErrorType.SERVER_ERROR

    logger.warning("error_mapper: could not classify error", status=status)
    return ErrorType.UNKNOWN


#: ErrorTypes for which failing over to a different provider makes sense.
#: Content/auth/permission/invalid-request errors are about the *request*
#: or *key*, not the provider's availability — retrying elsewhere either
#: can't help (content filter — plan.md is explicit: "Do not retry on a
#: different provider") or would just repeat the same failure.
_RETRYABLE_TYPES = frozenset({
    ErrorType.RATE_LIMIT,
    ErrorType.OVERLOADED,
    ErrorType.SERVER_ERROR,
    ErrorType.TIMEOUT,
})


def is_retryable(error_type: ErrorType) -> bool:
    """
    Whether the absorber/selector should attempt failover to a different
    provider for this error type.

    ``True``  → RATE_LIMIT, OVERLOADED, SERVER_ERROR, TIMEOUT (provider-side,
                another provider might succeed).
    ``False`` → AUTH_ERROR, PERMISSION_ERROR, NOT_FOUND, INVALID_REQUEST,
                CONTENT_FILTER, UNKNOWN (request/key/content issue — a
                different provider won't fix it, and for CONTENT_FILTER
                specifically, trying elsewhere may just repeat the block).
    """
    return error_type in _RETRYABLE_TYPES


#: ErrorType → Anthropic API's own `error.type` string.
#: See https://docs.claude.com (Errors) for the canonical list.
_ANTHROPIC_ERROR_TYPE_MAP: dict[ErrorType, str] = {
    ErrorType.RATE_LIMIT: "rate_limit_error",
    ErrorType.OVERLOADED: "overloaded_error",
    ErrorType.SERVER_ERROR: "api_error",
    ErrorType.TIMEOUT: "api_error",
    ErrorType.AUTH_ERROR: "authentication_error",
    ErrorType.PERMISSION_ERROR: "permission_error",
    ErrorType.NOT_FOUND: "not_found_error",
    ErrorType.INVALID_REQUEST: "invalid_request_error",
    # Per plan.md: Gemini SAFETY blocks map to invalid_request_error, since
    # Claude Code has no native concept of a "content_filter" error type.
    ErrorType.CONTENT_FILTER: "invalid_request_error",
    ErrorType.UNKNOWN: "api_error",
}


def to_anthropic_error_type(error_type: ErrorType) -> str:
    """Map an internal :class:`ErrorType` to the Anthropic API's `error.type` string."""
    return _ANTHROPIC_ERROR_TYPE_MAP.get(error_type, "api_error")


def build_anthropic_error(error_type: ErrorType, message: str) -> dict[str, Any]:
    """
    Build a complete Anthropic-format error body.

    Returns
    -------
    dict
        ``{"type": "error", "error": {"type": "<anthropic_type>", "message": message}}``,
        ready to ``json.dumps`` directly into an HTTP error response or an
        SSE ``error`` event payload.
    """
    return {
        "type": "error",
        "error": {
            "type": to_anthropic_error_type(error_type),
            "message": message,
        },
    }