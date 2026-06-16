"""
clasp/providers/common/message_converter.py

Bidirectional translation between Anthropic Messages API and OpenAI Chat Completions API.

Public surface:
  anthropic_to_openai(request: dict, *, merge_system: bool = False) -> dict
  openai_to_anthropic_response(response: dict, original_request: dict | None = None) -> dict

Design contract:
  - Pure functions — no I/O, no global state.
  - Raise ValueError on unrecoverable malformed input.
  - Strip thinking blocks for all providers (non-thinking providers can't handle them).
  - merge_system=True: system prompt is prepended to the first user message inside
    <system>…</system> tags (required for NVIDIA NIM models that reject the system role).
"""

from __future__ import annotations

import copy
import json
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# Stop-reason mapping
# ---------------------------------------------------------------------------

#: OpenAI finish_reason → Anthropic stop_reason
STOP_REASON_MAP: dict[str | None, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "stop_sequence",
    None: "end_turn",
}


def _map_stop_reason(finish_reason: str | None) -> str:
    return STOP_REASON_MAP.get(finish_reason, "end_turn")


# ---------------------------------------------------------------------------
# Helpers: Anthropic content block → text string
# ---------------------------------------------------------------------------

def _block_to_text(block: Any) -> str:
    """Extract plain text from an Anthropic content block or raw string."""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        btype = block.get("type", "")
        if btype == "text":
            return block.get("text", "")
        if btype == "thinking":
            return ""          # always stripped
        if btype in ("tool_use", "tool_result", "image"):
            return ""          # handled separately
    return ""


def _blocks_to_text(content: Any) -> str:
    """Collapse a list of content blocks into a single plain string (for system prompt)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(_block_to_text(b) for b in content)
    return str(content)


# ---------------------------------------------------------------------------
# Anthropic → OpenAI: messages
# ---------------------------------------------------------------------------

def _convert_anthropic_message_to_openai(
    msg: dict,
) -> list[dict]:
    """
    Convert a single Anthropic message dict to one or more OpenAI message dicts.

    Anthropic role "user" or "assistant" may contain a list of mixed content blocks:
      - text         → plain text (accumulated per message)
      - image        → OpenAI image_url content item
      - tool_use     → assistant tool_calls (on assistant messages)
      - tool_result  → tool role message (on user messages — one per result block)
      - thinking     → STRIPPED entirely

    Returns a list because one Anthropic message can expand into multiple OpenAI messages
    (e.g. multiple tool_result blocks each become their own tool-role message).
    """
    role: str = msg.get("role", "user")
    content: Any = msg.get("content", "")

    # Fast path: plain string content
    if isinstance(content, str):
        return [{"role": role, "content": content}]

    if not isinstance(content, list):
        return [{"role": role, "content": str(content)}]

    # ------------------------------------------------------------------ #
    # ASSISTANT message — may contain text + tool_use blocks              #
    # ------------------------------------------------------------------ #
    if role == "assistant":
        text_parts: list[dict] = []
        tool_calls: list[dict] = []

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")

            if btype == "text":
                text_parts.append({"type": "text", "text": block.get("text", "")})

            elif btype == "tool_use":
                # Anthropic tool_use → OpenAI tool_calls entry
                tool_input = block.get("input", {})
                tool_calls.append(
                    {
                        "id": block.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": (
                                json.dumps(tool_input)
                                if isinstance(tool_input, dict)
                                else str(tool_input)
                            ),
                        },
                    }
                )

            elif btype == "thinking":
                pass  # strip

            # image / other blocks inside assistant messages — skip
            # (unusual; not generated by Claude Code in practice)

        # Build the assistant message
        oai_msg: dict = {"role": "assistant"}

        if text_parts:
            # If only text and no tool calls, use a simple string for compatibility
            if not tool_calls:
                oai_msg["content"] = "".join(p["text"] for p in text_parts)
            else:
                oai_msg["content"] = "".join(p["text"] for p in text_parts) or None
        else:
            oai_msg["content"] = None  # content must be null when tool_calls present

        if tool_calls:
            oai_msg["tool_calls"] = tool_calls

        return [oai_msg]

    # ------------------------------------------------------------------ #
    # USER message — may contain text + image + tool_result blocks        #
    # ------------------------------------------------------------------ #
    # tool_result blocks each become their own {"role":"tool"} message.
    # Text and image blocks form a single {"role":"user"} message.

    user_content_items: list[dict] = []
    tool_messages: list[dict] = []

    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")

        if btype == "text":
            user_content_items.append({"type": "text", "text": block.get("text", "")})

        elif btype == "image":
            user_content_items.append(_convert_image_block(block))

        elif btype == "tool_result":
            # Each tool_result becomes a separate tool-role message
            tool_messages.append(_convert_tool_result_block(block))

        elif btype == "thinking":
            pass  # strip

        # unknown block types are silently skipped

    result: list[dict] = []

    # Emit user message if there is any non-tool content
    if user_content_items:
        # If all items are plain text, collapse to a string for wider compatibility
        if all(item["type"] == "text" for item in user_content_items):
            result.append(
                {
                    "role": "user",
                    "content": "".join(item["text"] for item in user_content_items),
                }
            )
        else:
            result.append({"role": "user", "content": user_content_items})

    result.extend(tool_messages)
    return result


def _convert_image_block(block: dict) -> dict:
    """
    Anthropic image content block → OpenAI image_url content item.

    Anthropic format:
      {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "..."}}
      {"type": "image", "source": {"type": "url", "url": "https://..."}}

    OpenAI format:
      {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
      {"type": "image_url", "image_url": {"url": "https://..."}}
    """
    source = block.get("source", {})
    src_type = source.get("type", "")

    if src_type == "base64":
        media_type = source.get("media_type", "image/jpeg")
        data = source.get("data", "")
        url = f"data:{media_type};base64,{data}"
    elif src_type == "url":
        url = source.get("url", "")
    else:
        # Fallback — treat as URL
        url = source.get("url", source.get("data", ""))

    detail = block.get("detail", "auto")
    image_url_obj: dict[str, Any] = {"url": url}
    if detail and detail != "auto":
        image_url_obj["detail"] = detail

    return {"type": "image_url", "image_url": image_url_obj}


def _convert_tool_result_block(block: dict) -> dict:
    """
    Anthropic tool_result content block (inside a user message) → OpenAI tool-role message.

    Anthropic:
      {"type": "tool_result", "tool_use_id": "toolu_xxx", "content": "..."}

    OpenAI:
      {"role": "tool", "tool_call_id": "toolu_xxx", "content": "..."}
    """
    raw_content = block.get("content", "")

    if isinstance(raw_content, list):
        # Content can itself be a list of text blocks
        content_str = "".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in raw_content
        )
    elif isinstance(raw_content, dict):
        content_str = raw_content.get("text", json.dumps(raw_content))
    else:
        content_str = str(raw_content)

    return {
        "role": "tool",
        "tool_call_id": block.get("tool_use_id", ""),
        "content": content_str,
    }


# ---------------------------------------------------------------------------
# Anthropic → OpenAI: tools
# ---------------------------------------------------------------------------

def _convert_tools(anthropic_tools: list[dict]) -> list[dict]:
    """
    Anthropic tools list → OpenAI functions-as-tools list.

    Anthropic:
      {"name": "str_replace", "description": "...", "input_schema": {JSON Schema}}

    OpenAI:
      {"type": "function", "function": {"name": "...", "description": "...", "parameters": {…}}}
    """
    oai_tools = []
    for tool in anthropic_tools:
        func: dict = {
            "name": tool.get("name", ""),
        }
        if "description" in tool:
            func["description"] = tool["description"]
        # Anthropic uses "input_schema"; OpenAI uses "parameters"
        schema = tool.get("input_schema") or tool.get("parameters") or {}
        func["parameters"] = schema

        oai_tools.append({"type": "function", "function": func})
    return oai_tools


def _convert_tool_choice(anthropic_choice: Any) -> Any:
    """
    Map Anthropic tool_choice to OpenAI tool_choice.

      Anthropic                     OpenAI
      ──────────────────────────    ──────────────────────────
      {"type": "auto"}              "auto"
      {"type": "any"}               "required"
      {"type": "none"}              "none"
      {"type": "tool",              {"type": "function",
       "name": "my_fn"}             "function": {"name": "my_fn"}}
    """
    if anthropic_choice is None:
        return None
    if isinstance(anthropic_choice, str):
        # Already in a simple form (unusual but tolerate)
        return anthropic_choice
    if isinstance(anthropic_choice, dict):
        tc_type = anthropic_choice.get("type", "auto")
        if tc_type == "auto":
            return "auto"
        if tc_type == "any":
            return "required"
        if tc_type == "none":
            return "none"
        if tc_type == "tool":
            return {"type": "function", "function": {"name": anthropic_choice.get("name", "")}}
    return "auto"


# ---------------------------------------------------------------------------
# Public: Anthropic request → OpenAI request
# ---------------------------------------------------------------------------

def anthropic_to_openai(
    request: dict,
    *,
    merge_system: bool = False,
    target_model: str | None = None,
) -> dict:
    """
    Translate an Anthropic Messages API request dict to an OpenAI Chat Completions dict.

    Args:
        request:      The raw Anthropic request body (already parsed from JSON).
        merge_system: If True, the system prompt is prepended to the first user message
                      inside <system>…</system> tags instead of a system-role message.
                      Required for NVIDIA NIM models that reject the system role.
        target_model: Override the model name in the output (e.g. the NIM model string).
                      If None, the original model name is used unchanged.

    Returns:
        A dict ready to be serialised and sent to an OpenAI-compatible endpoint.
    """
    request = copy.deepcopy(request)

    # ── System prompt ──────────────────────────────────────────────────── #
    system_text: str | None = None
    raw_system = request.get("system")
    if raw_system:
        system_text = _blocks_to_text(raw_system).strip() or None

    # ── Messages ───────────────────────────────────────────────────────── #
    anthropic_messages: list[dict] = request.get("messages", [])
    oai_messages: list[dict] = []

    if system_text and not merge_system:
        oai_messages.append({"role": "system", "content": system_text})

    for i, msg in enumerate(anthropic_messages):
        converted = _convert_anthropic_message_to_openai(msg)

        # merge_system: inject system text into the first user message
        if merge_system and system_text and i == 0:
            first = converted[0] if converted else None
            if first and first.get("role") == "user":
                existing = first.get("content", "")
                if isinstance(existing, str):
                    first["content"] = (
                        f"<system>\n{system_text}\n</system>\n\n{existing}"
                    )
                elif isinstance(existing, list):
                    first["content"] = [
                        {"type": "text", "text": f"<system>\n{system_text}\n</system>\n\n"}
                    ] + existing
            # If first message isn't user (unusual), prepend a synthetic user message
            elif system_text:
                converted.insert(
                    0,
                    {
                        "role": "user",
                        "content": f"<system>\n{system_text}\n</system>",
                    },
                )
            system_text = None  # consumed

        oai_messages.extend(converted)

    # ── Build output payload ────────────────────────────────────────────── #
    out: dict = {
        "model": target_model or request.get("model", ""),
        "messages": oai_messages,
    }

    # Sampling parameters — only include when present
    for field in ("temperature", "top_p", "max_tokens"):
        if field in request:
            out[field] = request[field]

    # stream
    if "stream" in request:
        out["stream"] = request["stream"]

    # top_k → not supported by standard OpenAI; skip
    # (individual provider classes can re-add it if supported)

    # stop sequences
    if "stop_sequences" in request:
        sequences = request["stop_sequences"]
        out["stop"] = sequences[0] if len(sequences) == 1 else sequences

    # tools
    raw_tools: list[dict] | None = request.get("tools")
    if raw_tools:
        out["tools"] = _convert_tools(raw_tools)
        tool_choice = request.get("tool_choice")
        if tool_choice is not None:
            out["tool_choice"] = _convert_tool_choice(tool_choice)

    # metadata — drop (provider-specific; not forwarded)

    return out


# ---------------------------------------------------------------------------
# Public: OpenAI response → Anthropic response
# ---------------------------------------------------------------------------

def openai_to_anthropic_response(
    response: dict,
    original_request: dict | None = None,
) -> dict:
    """
    Translate a non-streaming OpenAI Chat Completions response to an Anthropic
    Messages API response.

    Args:
        response:         Parsed OpenAI response body.
        original_request: The original Anthropic request (used to echo model name
                          and to infer the request id when absent).

    Returns:
        A dict matching the Anthropic Messages API response schema.
    """
    choice = _first_choice(response)
    message = choice.get("message", {}) if choice else {}
    finish_reason = choice.get("finish_reason") if choice else None

    # ── Content blocks ─────────────────────────────────────────────────── #
    content_blocks: list[dict] = []

    raw_content = message.get("content")
    if raw_content:
        content_blocks.append({"type": "text", "text": raw_content})

    tool_calls: list[dict] = message.get("tool_calls") or []
    for tc in tool_calls:
        fn = tc.get("function", {})
        raw_args = fn.get("arguments", "{}")
        try:
            parsed_input = json.loads(raw_args)
        except (json.JSONDecodeError, TypeError):
            parsed_input = {"_raw": raw_args}

        content_blocks.append(
            {
                "type": "tool_use",
                "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                "name": fn.get("name", ""),
                "input": parsed_input,
            }
        )

    # ── Usage ──────────────────────────────────────────────────────────── #
    usage_raw: dict = response.get("usage") or {}
    usage: dict = {
        "input_tokens": usage_raw.get("prompt_tokens", 0),
        "output_tokens": usage_raw.get("completion_tokens", 0),
    }

    # ── Model ──────────────────────────────────────────────────────────── #
    model: str = response.get("model", "")
    if not model and original_request:
        model = original_request.get("model", "")

    # ── ID ─────────────────────────────────────────────────────────────── #
    response_id: str = response.get("id") or f"msg_{uuid.uuid4().hex[:20]}"

    # ── Stop reason ────────────────────────────────────────────────────── #
    stop_reason = _map_stop_reason(finish_reason)

    return {
        "id": response_id,
        "type": "message",
        "role": "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_choice(response: dict) -> dict | None:
    choices = response.get("choices")
    if choices and isinstance(choices, list):
        return choices[0]
    return None


# ---------------------------------------------------------------------------
# OpenAI image_url → Anthropic image block  (for round-trip / testing)
# ---------------------------------------------------------------------------

def _openai_image_to_anthropic(item: dict) -> dict:
    """
    Inverse of _convert_image_block. Used in tests and for providers that echo
    back image_url items we need to re-convert.
    """
    image_url_obj = item.get("image_url", {})
    url: str = image_url_obj.get("url", "")

    if url.startswith("data:"):
        # data:<media_type>;base64,<data>
        header, _, data = url.partition(",")
        media_type = header.split(";")[0][len("data:"):]
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": data},
        }
    else:
        return {
            "type": "image",
            "source": {"type": "url", "url": url},
        }