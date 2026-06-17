"""
clasp/api/detect.py

Classify an incoming Anthropic Messages API request body into a ``RequestType``
so downstream routing can pick the best provider/model.

Classification order (first match wins, highest-priority first):

  1. PROBE         — max_tokens ≤ 5, or a trivially empty body.
  2. THINK         — ``thinking: {type: "enabled"}`` block present.
  3. VISION        — any content block has ``type == "image"``.
  4. TOOL_USE      — ``tools`` list is non-empty.
  5. LONG_CONTEXT  — estimated prompt tokens > LONG_CONTEXT_TOKEN_THRESHOLD (50 000).
  6. BACKGROUND    — system prompt or first message contains a background-task marker.
  7. INTERACTIVE    — everything else (default).

Priority for the queue (lower number = higher priority):

  INTERACTIVE = 0
  TOOL_USE    = 1
  BACKGROUND  = 2
  PROBE / THINK / VISION / LONG_CONTEXT inherit INTERACTIVE (0) unless overridden.

Public API
----------
    from clasp.api.detect import RequestType, detect, classify_priority

    req_type = detect(body)          # body is the parsed request dict
    priority  = classify_priority(req_type)
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

LONG_CONTEXT_TOKEN_THRESHOLD: int = 50_000
"""Estimated token count above which a request is classified LONG_CONTEXT."""

PROBE_MAX_TOKENS_CEILING: int = 5
"""max_tokens at or below this → PROBE (trivial ping / model-list warmup)."""

# ---------------------------------------------------------------------------
# Background-task detection patterns
# ---------------------------------------------------------------------------

# Substrings / regex patterns found in system prompts or the first user
# message that indicate a background / batch workload.  All matched
# case-insensitively.
_BACKGROUND_SYSTEM_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bfile\s+index", re.IGNORECASE),
    re.compile(r"\bbackground\s+task", re.IGNORECASE),
    re.compile(r"\bbatch\s+process", re.IGNORECASE),
    re.compile(r"\bsummariz", re.IGNORECASE),           # summarize / summarizing / summarization
    re.compile(r"\bindex\s+codebase", re.IGNORECASE),
    re.compile(r"\bcrawl\b", re.IGNORECASE),
    re.compile(r"\bembedding", re.IGNORECASE),
    re.compile(r"\bclasp[_\-]background", re.IGNORECASE),  # explicit opt-in marker
    re.compile(r"\btask_type\s*[:=]\s*background", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# RequestType
# ---------------------------------------------------------------------------

class RequestType(str, Enum):
    """
    Enum that doubles as a plain string so it serialises cleanly in JSON logs.

    >>> RequestType.INTERACTIVE == "INTERACTIVE"
    True
    """

    PROBE        = "PROBE"
    THINK        = "THINK"
    VISION       = "VISION"
    TOOL_USE     = "TOOL_USE"
    LONG_CONTEXT = "LONG_CONTEXT"
    BACKGROUND   = "BACKGROUND"
    INTERACTIVE  = "INTERACTIVE"


# Queue priority mapping (lower = higher urgency)
_PRIORITY: dict[RequestType, int] = {
    RequestType.PROBE:        0,
    RequestType.INTERACTIVE:  0,
    RequestType.THINK:        0,
    RequestType.VISION:       0,
    RequestType.LONG_CONTEXT: 0,
    RequestType.TOOL_USE:     1,
    RequestType.BACKGROUND:   2,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_text(block: Any) -> str:
    """Pull plain text out of a content block (string or dict)."""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        if block.get("type") == "text":
            return block.get("text", "")
    return ""


def _system_text(body: dict[str, Any]) -> str:
    """Return the system prompt as a flat string (handles string + list forms)."""
    system = body.get("system", "")
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return " ".join(_extract_text(b) for b in system)
    return ""


def _first_user_text(body: dict[str, Any]) -> str:
    """Return the text content of the first user message."""
    messages: list[Any] = body.get("messages", [])
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(_extract_text(b) for b in content)
    return ""


def _has_image_block(body: dict[str, Any]) -> bool:
    """Return True if any message contains an image content block."""
    for msg in body.get("messages", []):
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    return True
    return False


def _estimate_tokens(body: dict[str, Any]) -> int:
    """
    Fast, dependency-free token estimate using the 4-chars-per-token heuristic.

    Falls back to tiktoken if available for better accuracy, but never blocks
    on import failure.  The rough estimate is sufficient for routing decisions.
    """
    # Try tiktoken first (will be available once pyproject.toml is installed).
    try:
        import tiktoken  # noqa: PLC0415

        enc = tiktoken.get_encoding("cl100k_base")
        total = 0

        # System prompt
        total += len(enc.encode(_system_text(body)))

        # Messages
        for msg in body.get("messages", []):
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                for block in content:
                    text = _extract_text(block)
                    if text:
                        total += len(enc.encode(text))
        return total

    except Exception:  # noqa: BLE001
        # Fallback: ~4 chars per token
        total_chars = len(_system_text(body))
        for msg in body.get("messages", []):
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    total_chars += len(_extract_text(block))
        return max(1, total_chars // 4)


def _is_background(body: dict[str, Any]) -> bool:
    """Check system prompt and first user message for background-task signals."""
    haystack = (_system_text(body) + " " + _first_user_text(body)).strip()
    if not haystack:
        return False
    return any(pat.search(haystack) for pat in _BACKGROUND_SYSTEM_PATTERNS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect(body: dict[str, Any]) -> RequestType:
    """
    Classify *body* into a ``RequestType``.  Classification is purely
    synchronous and CPU-bound (no I/O).

    Parameters
    ----------
    body:
        Parsed Anthropic Messages API request dict (already validated by
        proxy_routes before this is called).

    Returns
    -------
    RequestType
        The inferred request type.
    """
    # ── 1. PROBE ────────────────────────────────────────────────────────────
    max_tokens: int = body.get("max_tokens", 1024)
    if max_tokens <= PROBE_MAX_TOKENS_CEILING:
        logger.debug("detect → PROBE", max_tokens=max_tokens)
        return RequestType.PROBE

    # ── 2. THINK ────────────────────────────────────────────────────────────
    thinking = body.get("thinking")
    if isinstance(thinking, dict) and thinking.get("type") == "enabled":
        logger.debug("detect → THINK")
        return RequestType.THINK

    # ── 3. VISION ───────────────────────────────────────────────────────────
    if _has_image_block(body):
        logger.debug("detect → VISION")
        return RequestType.VISION

    # ── 4. TOOL_USE ─────────────────────────────────────────────────────────
    tools = body.get("tools")
    if tools and isinstance(tools, list) and len(tools) > 0:
        logger.debug("detect → TOOL_USE", tool_count=len(tools))
        return RequestType.TOOL_USE

    # ── 5. LONG_CONTEXT ─────────────────────────────────────────────────────
    estimated = _estimate_tokens(body)
    if estimated > LONG_CONTEXT_TOKEN_THRESHOLD:
        logger.debug("detect → LONG_CONTEXT", estimated_tokens=estimated)
        return RequestType.LONG_CONTEXT

    # ── 6. BACKGROUND ───────────────────────────────────────────────────────
    if _is_background(body):
        logger.debug("detect → BACKGROUND")
        return RequestType.BACKGROUND

    # ── 7. INTERACTIVE (default) ────────────────────────────────────────────
    logger.debug("detect → INTERACTIVE", estimated_tokens=estimated)
    return RequestType.INTERACTIVE


def classify_priority(req_type: RequestType) -> int:
    """
    Return the queue priority integer for *req_type*.

    Lower values = higher urgency.

      0 → INTERACTIVE, PROBE, THINK, VISION, LONG_CONTEXT
      1 → TOOL_USE
      2 → BACKGROUND
    """
    return _PRIORITY.get(req_type, 0)