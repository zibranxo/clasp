"""
tests/unit/test_sse_builder.py
================================
Unit tests for clasp/providers/common/sse_builder.py.

Run with either:
    python -m pytest tests/unit/test_sse_builder.py -v
    python -m unittest tests.unit.test_sse_builder -v
"""

from __future__ import annotations

import json
import sys
import os
import unittest

# ---------------------------------------------------------------------------
# Make sure the project root is importable regardless of cwd.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.providers.common.sse_builder import SSEBuilder, _map_stop_reason, parse_openai_sse_chunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_events(raw: list[str]) -> list[dict]:
    """Parse a list of SSE event strings into dicts for easy assertion."""
    parsed = []
    for ev_str in raw:
        lines = ev_str.strip().split("\n")
        event_type = None
        data_str = None
        for line in lines:
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data_str = line[6:]
        if data_str:
            obj = json.loads(data_str)
            obj["_event"] = event_type
            parsed.append(obj)
    return parsed


def _make_text_chunk(
    text: str,
    finish_reason: str | None = None,
    output_tokens: int | None = None,
) -> dict:
    """Build a minimal OpenAI text-delta chunk."""
    chunk: dict = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
    }
    if output_tokens is not None:
        chunk["usage"] = {"completion_tokens": output_tokens, "prompt_tokens": 10, "total_tokens": output_tokens + 10}
    return chunk


def _make_tool_chunk(
    oai_index: int,
    tool_id: str = "",
    name: str = "",
    arguments: str = "",
    finish_reason: str | None = None,
) -> dict:
    """Build a minimal OpenAI tool-call delta chunk."""
    tc: dict = {"index": oai_index}
    if tool_id:
        tc["id"] = tool_id
    func: dict = {}
    if name:
        func["name"] = name
    if arguments:
        func["arguments"] = arguments
    if func:
        tc["function"] = func
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [tc]},
                "finish_reason": finish_reason,
            }
        ],
    }


def _collect(builder: SSEBuilder, chunks: list[dict]) -> list[dict]:
    """Run all chunks through builder + flush, return parsed events."""
    raw: list[str] = []
    for ch in chunks:
        raw.extend(builder.process_chunk(ch))
    raw.extend(builder.flush())
    return _parse_events(raw)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStopReasonMapping(unittest.TestCase):
    def test_stop_maps_to_end_turn(self):
        self.assertEqual(_map_stop_reason("stop"), "end_turn")

    def test_length_maps_to_max_tokens(self):
        self.assertEqual(_map_stop_reason("length"), "max_tokens")

    def test_tool_calls_maps_to_tool_use(self):
        self.assertEqual(_map_stop_reason("tool_calls"), "tool_use")

    def test_content_filter_maps_to_stop_sequence(self):
        self.assertEqual(_map_stop_reason("content_filter"), "stop_sequence")

    def test_none_maps_to_end_turn(self):
        self.assertEqual(_map_stop_reason(None), "end_turn")

    def test_unknown_maps_to_end_turn(self):
        self.assertEqual(_map_stop_reason("some_future_reason"), "end_turn")


class TestMessageStart(unittest.TestCase):
    """message_start is always the very first event."""

    def test_message_start_emitted_on_first_chunk(self):
        builder = SSEBuilder(model="test-model", request_id="msg_001")
        events = _collect(builder, [_make_text_chunk("Hello")])
        self.assertEqual(events[0]["_event"], "message_start")
        self.assertEqual(events[0]["type"], "message_start")

    def test_message_start_before_content_block_start(self):
        builder = SSEBuilder(model="test-model", request_id="msg_002")
        events = _collect(builder, [_make_text_chunk("Hello")])
        types = [e["_event"] for e in events]
        msg_start_idx = types.index("message_start")
        cb_start_idx = types.index("content_block_start")
        self.assertLess(msg_start_idx, cb_start_idx)

    def test_message_start_contains_model(self):
        builder = SSEBuilder(model="my-model", request_id="msg_003")
        events = _collect(builder, [_make_text_chunk("Hi")])
        self.assertEqual(events[0]["message"]["model"], "my-model")

    def test_message_start_contains_request_id(self):
        builder = SSEBuilder(model="m", request_id="msg_xyz")
        events = _collect(builder, [_make_text_chunk("Hi")])
        self.assertEqual(events[0]["message"]["id"], "msg_xyz")

    def test_message_start_emitted_exactly_once(self):
        builder = SSEBuilder(model="m", request_id="msg_once")
        events = _collect(
            builder,
            [_make_text_chunk("Hello"), _make_text_chunk(" world")],
        )
        starts = [e for e in events if e["_event"] == "message_start"]
        self.assertEqual(len(starts), 1)


class TestTextDelta(unittest.TestCase):
    """Text delta chunks → content_block_start → delta(s) → stop."""

    def test_single_text_delta(self):
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(builder, [_make_text_chunk("Hello")])
        event_types = [e["_event"] for e in events]
        self.assertIn("content_block_start", event_types)
        self.assertIn("content_block_delta", event_types)

    def test_text_delta_has_correct_type(self):
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(builder, [_make_text_chunk("World")])
        deltas = [e for e in events if e["_event"] == "content_block_delta"]
        self.assertTrue(len(deltas) >= 1)
        self.assertEqual(deltas[0]["delta"]["type"], "text_delta")
        self.assertEqual(deltas[0]["delta"]["text"], "World")

    def test_multiple_text_chunks_open_block_once(self):
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(
            builder,
            [_make_text_chunk("Hello"), _make_text_chunk(" world")],
        )
        starts = [e for e in events if e["_event"] == "content_block_start"]
        # Only one text block should be opened
        self.assertEqual(len(starts), 1)

    def test_text_content_block_start_type_is_text(self):
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(builder, [_make_text_chunk("Hi")])
        start = next(e for e in events if e["_event"] == "content_block_start")
        self.assertEqual(start["content_block"]["type"], "text")

    def test_content_block_stop_emitted_after_text(self):
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(builder, [_make_text_chunk("Hi")])
        event_types = [e["_event"] for e in events]
        self.assertIn("content_block_stop", event_types)

    def test_full_sequence_order(self):
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(builder, [_make_text_chunk("Hi")])
        types = [e["_event"] for e in events]
        # Must appear in this relative order
        expected_order = [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]
        indices = [types.index(t) for t in expected_order]
        self.assertEqual(indices, sorted(indices))

    def test_delta_index_matches_block_start_index(self):
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(builder, [_make_text_chunk("Hi")])
        start = next(e for e in events if e["_event"] == "content_block_start")
        delta = next(e for e in events if e["_event"] == "content_block_delta")
        self.assertEqual(start["index"], delta["index"])


class TestFinishReason(unittest.TestCase):
    """finish_reason → message_delta.stop_reason + message_stop."""

    def _stop_reason_for(self, finish_reason: str | None) -> str:
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(
            builder,
            [_make_text_chunk("Hi", finish_reason=finish_reason)],
        )
        msg_delta = next(e for e in events if e["_event"] == "message_delta")
        return msg_delta["delta"]["stop_reason"]

    def test_stop_finish_reason(self):
        self.assertEqual(self._stop_reason_for("stop"), "end_turn")

    def test_length_finish_reason(self):
        self.assertEqual(self._stop_reason_for("length"), "max_tokens")

    def test_tool_calls_finish_reason(self):
        self.assertEqual(self._stop_reason_for("tool_calls"), "tool_use")

    def test_content_filter_finish_reason(self):
        self.assertEqual(self._stop_reason_for("content_filter"), "stop_sequence")

    def test_message_stop_always_follows_message_delta(self):
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(builder, [_make_text_chunk("Hi", finish_reason="stop")])
        types = [e["_event"] for e in events]
        delta_idx = types.index("message_delta")
        stop_idx = types.index("message_stop")
        self.assertLess(delta_idx, stop_idx)

    def test_message_stop_event_type(self):
        builder = SSEBuilder(model="m", request_id="r")
        events = _collect(builder, [_make_text_chunk("Hi", finish_reason="stop")])
        stop = next(e for e in events if e["_event"] == "message_stop")
        self.assertEqual(stop["type"], "message_stop")


class TestOutputTokenTracking(unittest.TestCase):
    def test_output_tokens_reflected_in_message_delta(self):
        builder = SSEBuilder(model="m", request_id="r", input_tokens=50)
        events = _collect(
            builder,
            [_make_text_chunk("Hello", finish_reason="stop", output_tokens=12)],
        )
        msg_delta = next(e for e in events if e["_event"] == "message_delta")
        self.assertEqual(msg_delta["usage"]["output_tokens"], 12)


class TestToolCallStream(unittest.TestCase):
    """Tool call delta sequence → content_block_start(tool_use) → input_json_delta(s) → stop."""

    def _tool_stream(self) -> list[dict]:
        """A realistic two-fragment tool call stream."""
        builder = SSEBuilder(model="m", request_id="r")
        chunks = [
            # First delta: id + name
            _make_tool_chunk(0, tool_id="call_abc123", name="read_file", arguments=""),
            # Second delta: first JSON fragment
            _make_tool_chunk(0, arguments='{"path":'),
            # Third delta: second JSON fragment
            _make_tool_chunk(0, arguments='"file.py"}'),
            # Finish
            _make_tool_chunk(0, finish_reason="tool_calls"),
        ]
        return _collect(builder, chunks)

    def test_tool_use_block_start_emitted(self):
        events = self._tool_stream()
        starts = [e for e in events if e["_event"] == "content_block_start"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["content_block"]["type"], "tool_use")

    def test_tool_use_block_has_id_and_name(self):
        events = self._tool_stream()
        start = next(e for e in events if e["_event"] == "content_block_start")
        self.assertEqual(start["content_block"]["id"], "call_abc123")
        self.assertEqual(start["content_block"]["name"], "read_file")

    def test_input_json_deltas_emitted(self):
        events = self._tool_stream()
        deltas = [e for e in events if e["_event"] == "content_block_delta"]
        self.assertGreaterEqual(len(deltas), 1)
        for d in deltas:
            self.assertEqual(d["delta"]["type"], "input_json_delta")

    def test_input_json_fragments_are_correct(self):
        events = self._tool_stream()
        deltas = [e for e in events if e["_event"] == "content_block_delta"]
        combined = "".join(d["delta"]["partial_json"] for d in deltas)
        self.assertEqual(combined, '{"path":"file.py"}')

    def test_tool_use_stop_reason_is_tool_use(self):
        events = self._tool_stream()
        msg_delta = next(e for e in events if e["_event"] == "message_delta")
        self.assertEqual(msg_delta["delta"]["stop_reason"], "tool_use")

    def test_content_block_stop_emitted_for_tool(self):
        events = self._tool_stream()
        event_types = [e["_event"] for e in events]
        self.assertIn("content_block_stop", event_types)

    def test_tool_sequence_order(self):
        events = self._tool_stream()
        types = [e["_event"] for e in events]
        expected_order = [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ]
        indices = [types.index(t) for t in expected_order]
        self.assertEqual(indices, sorted(indices))

    def test_empty_arguments_not_emitted_as_delta(self):
        """First delta carries id+name but empty arguments → no input_json_delta yet."""
        builder = SSEBuilder(model="m", request_id="r")
        # Only the id+name delta, no arguments
        raw = builder.process_chunk(_make_tool_chunk(0, tool_id="call_x", name="fn"))
        events = _parse_events(raw)
        deltas = [e for e in events if e["_event"] == "content_block_delta"]
        self.assertEqual(len(deltas), 0)


class TestParallelToolCalls(unittest.TestCase):
    """Two tool calls in the same stream → two separate content blocks."""

    def _parallel_stream(self) -> list[dict]:
        builder = SSEBuilder(model="m", request_id="r")
        chunks = [
            _make_tool_chunk(0, tool_id="call_a", name="tool_a", arguments=""),
            _make_tool_chunk(1, tool_id="call_b", name="tool_b", arguments=""),
            _make_tool_chunk(0, arguments='{"x":1}'),
            _make_tool_chunk(1, arguments='{"y":2}'),
            _make_tool_chunk(0, finish_reason="tool_calls"),
        ]
        return _collect(builder, chunks)

    def test_two_tool_block_starts(self):
        events = self._parallel_stream()
        starts = [e for e in events if e["_event"] == "content_block_start"]
        self.assertEqual(len(starts), 2)

    def test_two_tool_block_stops(self):
        events = self._parallel_stream()
        stops = [e for e in events if e["_event"] == "content_block_stop"]
        self.assertEqual(len(stops), 2)

    def test_different_block_indices(self):
        events = self._parallel_stream()
        starts = [e for e in events if e["_event"] == "content_block_start"]
        indices = [e["index"] for e in starts]
        self.assertEqual(len(set(indices)), 2)


class TestMalformedChunks(unittest.TestCase):
    """Malformed chunks must be silently skipped — no crash, no exception."""

    def test_none_choices_returns_empty(self):
        builder = SSEBuilder(model="m", request_id="r")
        result = builder.process_chunk({"choices": None, "usage": {}})
        self.assertIsInstance(result, list)

    def test_completely_empty_chunk(self):
        builder = SSEBuilder(model="m", request_id="r")
        result = builder.process_chunk({})
        self.assertIsInstance(result, list)

    def test_missing_delta_in_choice(self):
        builder = SSEBuilder(model="m", request_id="r")
        chunk = {"choices": [{"index": 0, "finish_reason": None}]}
        result = builder.process_chunk(chunk)
        self.assertIsInstance(result, list)

    def test_garbage_data_doesnt_raise(self):
        builder = SSEBuilder(model="m", request_id="r")
        # Should not raise even if internal code path errors
        result = builder.process_chunk({"choices": [{"delta": "not-a-dict"}]})
        self.assertIsInstance(result, list)


class TestFlush(unittest.TestCase):
    """flush() closes dangling blocks and is idempotent."""

    def test_flush_after_stream_without_finish_reason(self):
        """Provider omits finish_reason — flush() still emits tail events."""
        builder = SSEBuilder(model="m", request_id="r")
        builder.process_chunk(_make_text_chunk("partial"))
        tail = _parse_events(builder.flush())
        types = [e["_event"] for e in tail]
        self.assertIn("content_block_stop", types)
        self.assertIn("message_delta", types)
        self.assertIn("message_stop", types)

    def test_flush_is_idempotent(self):
        builder = SSEBuilder(model="m", request_id="r")
        builder.process_chunk(_make_text_chunk("Hello"))
        first = builder.flush()
        second = builder.flush()
        self.assertEqual(second, [])

    def test_flush_after_normal_finish_returns_empty(self):
        builder = SSEBuilder(model="m", request_id="r")
        chunks = [_make_text_chunk("Hi", finish_reason="stop")]
        _collect(builder, chunks)  # already flushes inside _collect
        # Call flush explicitly again — should be a no-op
        extra = builder.flush()
        self.assertEqual(extra, [])


class TestFunctionalWrapper(unittest.TestCase):
    """parse_openai_sse_chunk() convenience function."""

    def test_wrapper_returns_same_events(self):
        builder = SSEBuilder(model="m", request_id="r")
        chunk = _make_text_chunk("Hello")
        result = parse_openai_sse_chunk(chunk, builder=builder)
        self.assertIsInstance(result, list)
        events = _parse_events(result)
        types = [e["_event"] for e in events]
        self.assertIn("message_start", types)


class TestSSEFormat(unittest.TestCase):
    """Verify the raw SSE string format (event: / data: / blank line)."""

    def test_sse_strings_end_with_double_newline(self):
        builder = SSEBuilder(model="m", request_id="r")
        raw = builder.process_chunk(_make_text_chunk("Hi"))
        for ev in raw:
            self.assertTrue(ev.endswith("\n\n"), repr(ev))

    def test_sse_strings_contain_event_line(self):
        builder = SSEBuilder(model="m", request_id="r")
        raw = builder.process_chunk(_make_text_chunk("Hi"))
        for ev in raw:
            self.assertIn("event: ", ev)

    def test_sse_strings_contain_data_line(self):
        builder = SSEBuilder(model="m", request_id="r")
        raw = builder.process_chunk(_make_text_chunk("Hi"))
        for ev in raw:
            self.assertIn("data: ", ev)

    def test_data_lines_are_valid_json(self):
        builder = SSEBuilder(model="m", request_id="r")
        raw = builder.process_chunk(_make_text_chunk("Hi"))
        for ev in raw:
            for line in ev.split("\n"):
                if line.startswith("data: "):
                    json.loads(line[6:])  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
