"""
clasp/providers/common/token_counter.py
=========================================
Local token estimation — `estimate_tokens(messages) → int` (plan.md §9 step 10,
§20 step 10).

Used by the selector (`router/selector.py`) *before* a request is dispatched,
to ask `key_pool.pick_key(estimated_tokens)` whether a bucket has enough TPM
headroom (P3 — pre-emptive limiting, see CLAUDE.md "Key design decisions").
Accuracy only needs to be approximate: the real, authoritative count comes
back from the provider's response (`response.usage.output_tokens`) and is
fed into `bucket.consume_actual()` afterwards.

Strategy
--------
1. Prefer `tiktoken` with the `cl100k_base` encoding — closest open
   approximation to Claude's own tokenizer for English/code text.
2. If `tiktoken` is not installed (or fails for any reason), fall back to the
   widely-used heuristic: ~4 characters ≈ 1 token.
3. Image content blocks have no extractable text — they're estimated with a
   flat per-image constant (`IMAGE_TOKEN_ESTIMATE`), deliberately biased
   high. Undercounting risks a provider-side 429; overcounting only costs a
   little wasted TPM headroom, so we round up.

Public API
----------
``estimate_tokens(messages, *, system=None, tools=None) → int``
    Primary entry point. ``messages`` is a list of Anthropic-format message
    dicts (``{"role": ..., "content": ...}``). ``system`` and ``tools`` are
    optional extras so the same function can estimate an entire request, not
    just the message list, without a second call.

``estimate_request_tokens(body) → int``
    Convenience wrapper: pulls ``messages``, ``system``, and ``tools`` out of
    a full Anthropic ``/v1/messages`` request body and calls
    :func:`estimate_tokens`.

References: plan.md §9 step 10, §20 step 10.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from loguru import logger


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

#: tiktoken encoding used as the closest open stand-in for Claude's tokenizer.
_ENCODING_NAME = "cl100k_base"

#: Fallback heuristic when tiktoken is unavailable: chars per token.
_FALLBACK_CHARS_PER_TOKEN = 4

#: Flat token estimate per image content block (deliberately on the high
#: side — see module docstring on why overcounting is the safer failure mode).
IMAGE_TOKEN_ESTIMATE = 1600


# ---------------------------------------------------------------------------
# Encoding cache
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _get_encoding() -> Any | None:
    """
    Return a cached tiktoken encoding object, or ``None`` if tiktoken is
    unavailable. Cached because loading an encoding is relatively expensive
    and this is called on every request.
    """
    try:
        import tiktoken  # type: ignore[import-untyped]
        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "token_counter: tiktoken unavailable, using char-based fallback",
            error=str(exc),
        )
        return None


def _count_text(text: str) -> int:
    """Token-count a single string, using tiktoken if available."""
    if not text:
        return 0
    enc = _get_encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("token_counter: tiktoken encode failed, falling back",
                          error=str(exc))
    return max(1, len(text) // _FALLBACK_CHARS_PER_TOKEN)


# ---------------------------------------------------------------------------
# Content extraction helpers
# ---------------------------------------------------------------------------

def _extract_text_from_content(content: Any) -> tuple[list[str], int]:
    """
    Walk an Anthropic ``content`` value (string or list of content blocks)
    and return ``(text_fragments, image_count)``.

    Handles: plain strings, ``text`` blocks, ``tool_use`` (input serialised
    to its repr — cheap, no need for exact JSON), ``tool_result`` (whose
    ``content`` can itself be a string or nested list of blocks), and
    ``image`` blocks (counted, not text-extracted).
    """
    fragments: list[str] = []
    image_count = 0

    if isinstance(content, str):
        fragments.append(content)
        return fragments, image_count

    if not isinstance(content, list):
        return fragments, image_count

    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")

        if block_type == "text":
            fragments.append(block.get("text", ""))

        elif block_type == "image":
            image_count += 1

        elif block_type == "tool_use":
            # Name + serialised input contribute a small but real token cost.
            fragments.append(str(block.get("name", "")))
            fragments.append(str(block.get("input", "")))

        elif block_type == "tool_result":
            nested = block.get("content", "")
            nested_fragments, nested_images = _extract_text_from_content(nested)
            fragments.extend(nested_fragments)
            image_count += nested_images

        elif block_type == "thinking":
            fragments.append(block.get("thinking", ""))

    return fragments, image_count


def _extract_text_from_system(system: Any) -> list[str]:
    """Anthropic ``system`` can be a plain string or a list of text blocks."""
    if isinstance(system, str):
        return [system]
    if isinstance(system, list):
        out: list[str] = []
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                out.append(block.get("text", ""))
            elif isinstance(block, str):
                out.append(block)
        return out
    return []


def _extract_text_from_tools(tools: list[dict[str, Any]] | None) -> list[str]:
    """Tool definitions (name, description, schema) all consume real tokens."""
    if not tools:
        return []
    out: list[str] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        out.append(str(tool.get("name", "")))
        out.append(str(tool.get("description", "")))
        out.append(str(tool.get("input_schema", "")))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def estimate_tokens(
    messages: list[dict[str, Any]],
    *,
    system: str | list[Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """
    Estimate the input token count for a list of Anthropic-format messages.

    Parameters
    ----------
    messages:
        List of ``{"role": "user"|"assistant", "content": ...}`` dicts.
        ``content`` may be a plain string or a list of content blocks
        (text, image, tool_use, tool_result, thinking).
    system:
        Optional system prompt — string or list of text blocks. Counted
        alongside messages since it consumes real input tokens too.
    tools:
        Optional tool definitions. Counted because large tool schemas
        (common with Claude Code) can be a significant fraction of input.

    Returns
    -------
    int
        Estimated input token count. Never negative; at least 0 for a
        completely empty request.
    """
    if not isinstance(messages, list):
        logger.warning("token_counter: messages is not a list, returning 0",
                       got_type=type(messages).__name__)
        return 0

    text_fragments: list[str] = []
    image_count = 0

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        fragments, images = _extract_text_from_content(content)
        text_fragments.extend(fragments)
        image_count += images

    text_fragments.extend(_extract_text_from_system(system))
    text_fragments.extend(_extract_text_from_tools(tools))

    combined = " ".join(f for f in text_fragments if f)
    text_tokens = _count_text(combined)
    image_tokens = image_count * IMAGE_TOKEN_ESTIMATE

    total = text_tokens + image_tokens
    logger.debug("token_counter: estimate complete",
                 text_tokens=text_tokens, image_tokens=image_tokens,
                 images=image_count, total=total)
    return total


def estimate_request_tokens(body: dict[str, Any]) -> int:
    """
    Convenience wrapper: estimate tokens for a full Anthropic request body.

    Extracts ``messages``, ``system``, and ``tools`` from *body* and
    delegates to :func:`estimate_tokens`.
    """
    return estimate_tokens(
        body.get("messages", []),
        system=body.get("system"),
        tools=body.get("tools"),
    )