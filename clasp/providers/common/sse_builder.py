"""
clasp/providers/common/sse_builder.py
======================================
Converts OpenAI-format SSE chunks into Anthropic-format SSE event strings.

Public API
----------
    builder = SSEBuilder(model="gpt-4o", request_id="msg_abc123")
    events: list[str] = builder.process_chunk(openai_chunk: dict)
    tail:   list[str] = builder.flush()          # call once after the stream ends

One OpenAI chunk can produce 0, 1, or many Anthropic SSE events.  The builder
is stateful: it tracks whether the preamble (message_start) has been emitted,
which text/tool content blocks are open, and buffers per-tool JSON fragments
until a tool block is complete enough to forward as input_json_delta events.

Anthropic event ordering for text content
------------------------------------------
    message_start
    content_block_start  (index=N, type="text")
    content_block_delta  (index=N, type="text_delta", text="…")
    …                    (more deltas)
    content_block_stop   (index=N)
    message_delta        (stop_reason=…, usage.output_tokens=…)
    message_stop

Anthropic event ordering for tool_use content
----------------------------------------------
    message_start
    content_block_start  (index=N, type="tool_use", id="toolu_…", name="…")
    content_block_delta  (index=N, type="input_json_delta", partial_json="…")
    …                    (more deltas as JSON fragments arrive)
    content_block_stop   (index=N)
    message_delta        (stop_reason="tool_use", …)
    message_stop

Stop-reason mapping (OpenAI → Anthropic)
-----------------------------------------
    "stop"           → "end_turn"
    "length"         → "max_tokens"
    "tool_calls"     → "tool_use"
    "content_filter" → "stop_sequence"
    null / None      → "end_turn"

References: plan.md §9 "Critical Translation Details" and §9 "Tool call SSE
reconstruction".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Loguru shim — production code uses loguru; tests work without it.
# ---------------------------------------------------------------------------
try:
    from loguru import logger as _logger  # type: ignore[import-untyped]
except ModuleNotFoundError:  # pragma: no cover — shim for test environments
    import logging as _stdlib_logging

    class _LoguruShim:  # minimal subset needed by this module
        def __init__(self) -> None:
            self._log = _stdlib_logging.getLogger("clasp.sse_builder")

        def warning(self, msg: str, **kw: Any) -> None:  # noqa: ANN401
            self._log.warning(msg + ("  " + str(kw) if kw else ""))

        def debug(self, msg: str, **kw: Any) -> None:  # noqa: ANN401
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

    _logger = _LoguruShim()  # type: ignore[assignment]

logger = _logger


# ---------------------------------------------------------------------------
# Stop-reason translation
# ---------------------------------------------------------------------------

_STOP_REASON_MAP: dict[str | None, str] = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "stop_sequence",
    None: "end_turn",
}


def _map_stop_reason(openai_finish_reason: str | None) -> str:
    return _STOP_REASON_MAP.get(openai_finish_reason, "end_turn")


# ---------------------------------------------------------------------------
# Low-level SSE serialisation helpers
# ---------------------------------------------------------------------------

def _sse(event_type: str, payload: dict[str, Any]) -> str:
    """Serialise one Anthropic SSE event as a ready-to-send string."""
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# Tool-call buffer (one per tool index in the OpenAI stream)
# ---------------------------------------------------------------------------

@dataclass
class _ToolBuffer:
    """Accumulates one OpenAI streaming tool call into an Anthropic tool_use block."""

    index: int                   # OpenAI tool_calls array index
    tool_id: str = ""            # "call_abc…"  → maps to toolu_… id
    name: str = ""               # function name, arrives in first delta
    json_fragments: list[str] = field(default_factory=list)
    block_index: int = 0         # Anthropic content block index
    started: bool = False        # True after content_block_start emitted


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

class SSEBuilder:
    """
    Stateful OpenAI SSE → Anthropic SSE translator.

    Usage::

        builder = SSEBuilder(model="claude-3-5-sonnet-20241022",
                             request_id="msg_01abc")

        async for raw_line in upstream_response.aiter_lines():
            if not raw_line.startswith("data: "):
                continue
            data = raw_line[6:].strip()
            if data == "[DONE]":
                for ev in builder.flush():
                    yield ev
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            for ev in builder.process_chunk(chunk):
                yield ev
    """

    def __init__(
        self,
        model: str = "unknown",
        request_id: str = "",
        input_tokens: int = 0,
    ) -> None:
        self._model = model
        self._request_id = request_id or "msg_clasp_0"
        self._input_tokens = input_tokens

        # Preamble state
        self._message_started: bool = False

        # Text block state
        self._text_block_index: int | None = None  # None = no open text block

        # Tool call state: keyed by OpenAI tool_calls[i].index
        self._tool_buffers: dict[int, _ToolBuffer] = {}

        # Monotonically-increasing Anthropic content block index
        self._next_block_index: int = 0

        # Track how many output tokens we have seen across all chunks
        # (OpenAI sends cumulative usage only on the final chunk)
        self._output_tokens: int = 0

        # Whether the final tail (message_delta + message_stop) has been emitted
        self._tail_emitted: bool = False

    # ------------------------------------------------------------------
    # Primary entry point
    # ------------------------------------------------------------------

    def process_chunk(self, chunk: dict[str, Any]) -> list[str]:
        """
        Accept one parsed OpenAI chunk dict.  Return a list of zero or more
        Anthropic SSE event strings (each ending in ``\\n\\n``).

        Malformed chunks are logged and skipped; they never raise.
        """
        try:
            return self._process(chunk)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "sse_builder: skipping malformed chunk",
                error=str(exc),
                chunk=str(chunk)[:200],
            )
            return []

    def is_done(self) -> bool:
        """True after the final tail (message_delta + message_stop) has been emitted."""
        return self._tail_emitted

    def flush(self) -> list[str]:
        """
        Emit any events that were deferred until the stream ends.

        Call exactly once after the upstream ``[DONE]`` sentinel is received.
        Safe to call multiple times (subsequent calls return ``[]``).
        """
        if self._tail_emitted:
            return []

        events: list[str] = []

        # Close any open text block
        if self._text_block_index is not None:
            events.append(_sse("content_block_stop", {"type": "content_block_stop", "index": self._text_block_index}))
            self._text_block_index = None

        # Close any open tool blocks that weren't closed mid-stream
        for buf in self._tool_buffers.values():
            if buf.started:
                events.append(_sse("content_block_stop", {"type": "content_block_stop", "index": buf.block_index}))

        # message_delta + message_stop with default stop_reason when the stream
        # ended without an explicit finish_reason (e.g. provider omitted it)
        events.extend(self._emit_tail("end_turn"))

        self._tail_emitted = True
        return events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process(self, chunk: dict[str, Any]) -> list[str]:
        events: list[str] = []

        # ── preamble ────────────────────────────────────────────────
        if not self._message_started:
            events.append(self._emit_message_start())
            self._message_started = True

        choices: list[dict[str, Any]] = chunk.get("choices", [])

        # Some providers send usage-only chunks with no choices
        if not choices:
            self._absorb_usage(chunk)
            return events

        choice = choices[0]
        delta: dict[str, Any] = choice.get("delta", {})
        finish_reason: str | None = choice.get("finish_reason")

        # ── text content ─────────────────────────────────────────────
        text: str = delta.get("content") or ""
        if text:
            events.extend(self._handle_text_delta(text))

        # ── tool calls ───────────────────────────────────────────────
        tool_calls: list[dict[str, Any]] = delta.get("tool_calls") or []
        for tc in tool_calls:
            events.extend(self._handle_tool_call_delta(tc))

        # ── finish ───────────────────────────────────────────────────
        if finish_reason is not None:
            self._absorb_usage(chunk)
            events.extend(self._handle_finish(finish_reason))
            self._tail_emitted = True

        return events

    # ------------------------------------------------------------------
    # Text block helpers
    # ------------------------------------------------------------------

    def _handle_text_delta(self, text: str) -> list[str]:
        events: list[str] = []

        if self._text_block_index is None:
            # Open a new text content block
            idx = self._next_block_index
            self._next_block_index += 1
            self._text_block_index = idx
            events.append(
                _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            )

        events.append(
            _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": self._text_block_index,
                    "delta": {"type": "text_delta", "text": text},
                },
            )
        )
        return events

    # ------------------------------------------------------------------
    # Tool call helpers
    # ------------------------------------------------------------------

    def _handle_tool_call_delta(self, tc: dict[str, Any]) -> list[str]:
        """Handle one element from delta.tool_calls[]."""
        events: list[str] = []

        oai_index: int = tc.get("index", 0)

        if oai_index not in self._tool_buffers:
            self._tool_buffers[oai_index] = _ToolBuffer(index=oai_index)

        buf = self._tool_buffers[oai_index]

        # Harvest id / name (only on the first delta for this tool index)
        if tc_id := tc.get("id"):
            buf.tool_id = tc_id
        func: dict[str, Any] = tc.get("function") or {}
        if func_name := func.get("name"):
            buf.name = func_name

        # Emit content_block_start once we have id + name
        if not buf.started and buf.tool_id and buf.name:
            buf.block_index = self._next_block_index
            self._next_block_index += 1
            buf.started = True

            # Close any open text block first — can't have two blocks open
            if self._text_block_index is not None:
                events.append(
                    _sse("content_block_stop", {"type": "content_block_stop", "index": self._text_block_index})
                )
                self._text_block_index = None

            events.append(
                _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": buf.block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": buf.tool_id,
                            "name": buf.name,
                            "input": {},
                        },
                    },
                )
            )

        # Buffer + forward JSON fragments
        if partial_json := func.get("arguments"):
            buf.json_fragments.append(partial_json)
            if buf.started:
                events.append(
                    _sse(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": buf.block_index,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": partial_json,
                            },
                        },
                    )
                )

        return events

    # ------------------------------------------------------------------
    # Finish / tail helpers
    # ------------------------------------------------------------------

    def _handle_finish(self, finish_reason: str | None) -> list[str]:
        """Close open blocks and emit message_delta + message_stop."""
        events: list[str] = []

        # Close open text block
        if self._text_block_index is not None:
            events.append(
                _sse("content_block_stop", {"type": "content_block_stop", "index": self._text_block_index})
            )
            self._text_block_index = None

        # Close open tool blocks (in index order for determinism)
        for buf in sorted(self._tool_buffers.values(), key=lambda b: b.index):
            if buf.started:
                events.append(
                    _sse("content_block_stop", {"type": "content_block_stop", "index": buf.block_index})
                )

        stop_reason = _map_stop_reason(finish_reason)
        events.extend(self._emit_tail(stop_reason))
        return events

    def _emit_tail(self, stop_reason: str) -> list[str]:
        return [
            _sse(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": self._output_tokens},
                },
            ),
            _sse("message_stop", {"type": "message_stop"}),
        ]

    def _emit_message_start(self) -> str:
        return _sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": self._request_id,
                    "type": "message",
                    "role": "assistant",
                    "content": [],
                    "model": self._model,
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": self._input_tokens,
                        "output_tokens": 0,
                    },
                },
            },
        )

    def _absorb_usage(self, chunk: dict[str, Any]) -> None:
        """Extract output token count from the usage field if present."""
        usage = chunk.get("usage") or {}
        if ot := usage.get("completion_tokens"):
            self._output_tokens = ot


# ---------------------------------------------------------------------------
# Convenience function (functional wrapper, no state sharing)
# ---------------------------------------------------------------------------

def parse_openai_sse_chunk(
    chunk: dict[str, Any],
    *,
    builder: SSEBuilder,
) -> list[str]:
    """
    Functional entry point.  Pass a *pre-created* :class:`SSEBuilder` so state
    is shared across calls for the same stream.

    Example::

        builder = SSEBuilder(model="…", request_id="msg_…")
        for chunk in openai_chunks:
            for event_str in parse_openai_sse_chunk(chunk, builder=builder):
                yield event_str
        for event_str in builder.flush():
            yield event_str
    """
    return builder.process_chunk(chunk)
