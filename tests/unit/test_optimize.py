"""
tests/unit/test_optimize.py
=============================
Unit tests for clasp/api/optimize.py.

Run with:
    python -m unittest tests.unit.test_optimize -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.api.optimize import (
    is_probe,
    handle_probe,
    ProbeResult,
    STATIC_MODEL_LIST,
    TRIVIAL_PROBE_MAX_TOKENS,
    count_tokens_local,
    get_model_list,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg_body(text: str, max_tokens: int = 1024, model: str = "claude-sonnet-4-5") -> dict:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": text}],
    }


def _parse_sse_events(raw: list[str]) -> list[dict]:
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


# ---------------------------------------------------------------------------
# is_probe()
# ---------------------------------------------------------------------------

class TestIsProbeModelsEndpoint(unittest.TestCase):
    def test_v1_models_is_always_a_probe(self):
        self.assertTrue(is_probe("/v1/models", {}))


class TestIsProbeCountTokens(unittest.TestCase):
    def test_count_tokens_is_always_a_probe(self):
        self.assertTrue(is_probe("/v1/messages/count_tokens", _msg_body("hello")))

    def test_count_tokens_with_empty_body_is_still_a_probe(self):
        self.assertTrue(is_probe("/v1/messages/count_tokens", {}))


class TestIsProbeTrivialPing(unittest.TestCase):
    def test_low_max_tokens_short_body_is_probe(self):
        body = _msg_body("hi", max_tokens=1)
        self.assertTrue(is_probe("/v1/messages", body))

    def test_max_tokens_at_threshold_is_probe(self):
        body = _msg_body("hi", max_tokens=TRIVIAL_PROBE_MAX_TOKENS)
        self.assertTrue(is_probe("/v1/messages", body))

    def test_max_tokens_above_threshold_is_not_probe(self):
        body = _msg_body("hi", max_tokens=TRIVIAL_PROBE_MAX_TOKENS + 1)
        self.assertFalse(is_probe("/v1/messages", body))

    def test_normal_max_tokens_is_not_probe(self):
        body = _msg_body("Write me a function to reverse a string", max_tokens=1024)
        self.assertFalse(is_probe("/v1/messages", body))

    def test_low_max_tokens_but_huge_body_is_not_probe(self):
        """A long multi-turn conversation with max_tokens=1 is a real
        count-tokens-style call, not a liveness probe."""
        long_text = "x" * 5000
        body = _msg_body(long_text, max_tokens=1)
        self.assertFalse(is_probe("/v1/messages", body))

    def test_missing_max_tokens_defaults_to_not_probe(self):
        body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}]}
        self.assertFalse(is_probe("/v1/messages", body))

    def test_non_int_max_tokens_is_not_probe(self):
        body = _msg_body("hi", max_tokens=1)
        body["max_tokens"] = "not-a-number"
        self.assertFalse(is_probe("/v1/messages", body))


class TestIsProbeUnrelatedPaths(unittest.TestCase):
    def test_unrelated_path_is_never_a_probe(self):
        self.assertFalse(is_probe("/internal/status", {}))

    def test_v1_messages_with_normal_request_is_not_probe(self):
        body = _msg_body("Refactor this code please", max_tokens=4096)
        self.assertFalse(is_probe("/v1/messages", body))


# ---------------------------------------------------------------------------
# handle_probe() — /v1/models
# ---------------------------------------------------------------------------

class TestHandleProbeModels(unittest.TestCase):
    def test_returns_json_kind(self):
        result = handle_probe("/v1/models", {})
        self.assertEqual(result.kind, "json")

    def test_returns_static_model_list(self):
        result = handle_probe("/v1/models", {})
        self.assertEqual(result.payload, STATIC_MODEL_LIST)

    def test_model_list_contains_expected_ids(self):
        result = handle_probe("/v1/models", {})
        ids = [m["id"] for m in result.payload["data"]]
        self.assertIn("claude-sonnet-4-5", ids)
        self.assertIn("claude-opus-4-5", ids)
        self.assertIn("claude-haiku-4-5", ids)

    def test_get_model_list_matches_handle_probe(self):
        self.assertEqual(get_model_list(), handle_probe("/v1/models", {}).payload)


# ---------------------------------------------------------------------------
# handle_probe() — count_tokens
# ---------------------------------------------------------------------------

class TestHandleProbeCountTokens(unittest.TestCase):
    def test_returns_json_kind(self):
        result = handle_probe("/v1/messages/count_tokens", _msg_body("hello world"))
        self.assertEqual(result.kind, "json")

    def test_payload_has_input_tokens_key(self):
        result = handle_probe("/v1/messages/count_tokens", _msg_body("hello world"))
        self.assertIn("input_tokens", result.payload)
        self.assertIsInstance(result.payload["input_tokens"], int)

    def test_longer_text_yields_more_tokens(self):
        short = handle_probe("/v1/messages/count_tokens", _msg_body("hi"))
        long = handle_probe(
            "/v1/messages/count_tokens",
            _msg_body("hello " * 500),
        )
        self.assertGreater(long.payload["input_tokens"], short.payload["input_tokens"])

    def test_empty_message_yields_nonzero_minimum(self):
        result = handle_probe("/v1/messages/count_tokens", {"messages": []})
        self.assertGreaterEqual(result.payload["input_tokens"], 0)

    def test_system_prompt_counted(self):
        body = {
            "system": "You are a helpful assistant.",
            "messages": [{"role": "user", "content": "hi"}],
        }
        result = handle_probe("/v1/messages/count_tokens", body)
        self.assertGreater(result.payload["input_tokens"], 0)

    def test_system_as_block_list_counted(self):
        body = {
            "system": [{"type": "text", "text": "You are a helpful assistant with a long prompt describing many rules."}],
            "messages": [{"role": "user", "content": "hi"}],
        }
        result = handle_probe("/v1/messages/count_tokens", body)
        self.assertGreater(result.payload["input_tokens"], 5)

    def test_multi_block_content_counted(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Look at this code"},
                        {"type": "text", "text": "and tell me what's wrong with it"},
                    ],
                }
            ]
        }
        result = handle_probe("/v1/messages/count_tokens", body)
        self.assertGreater(result.payload["input_tokens"], 0)

    def test_tool_result_content_counted(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "content": [{"type": "text", "text": "file contents here " * 20}],
                        }
                    ],
                }
            ]
        }
        result = handle_probe("/v1/messages/count_tokens", body)
        self.assertGreater(result.payload["input_tokens"], 5)

    def test_tools_definitions_contribute_to_count(self):
        body_no_tools = _msg_body("hi")
        body_with_tools = dict(body_no_tools)
        body_with_tools["tools"] = [
            {
                "name": "read_file",
                "description": "Reads a file from disk given a path argument",
                "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }
        ]
        no_tools = handle_probe("/v1/messages/count_tokens", body_no_tools)
        with_tools = handle_probe("/v1/messages/count_tokens", body_with_tools)
        self.assertGreater(
            with_tools.payload["input_tokens"], no_tools.payload["input_tokens"]
        )

    def test_count_tokens_local_helper_matches_handle_probe(self):
        body = _msg_body("consistency check")
        via_probe = handle_probe("/v1/messages/count_tokens", body).payload["input_tokens"]
        via_helper = count_tokens_local(body)
        self.assertEqual(via_probe, via_helper)


# ---------------------------------------------------------------------------
# handle_probe() — trivial SSE probe
# ---------------------------------------------------------------------------

class TestHandleProbeTrivialSSE(unittest.TestCase):
    def test_returns_sse_kind(self):
        body = _msg_body("hi", max_tokens=1)
        result = handle_probe("/v1/messages", body)
        self.assertEqual(result.kind, "sse")

    def test_payload_is_list_of_strings(self):
        body = _msg_body("hi", max_tokens=1)
        result = handle_probe("/v1/messages", body)
        self.assertIsInstance(result.payload, list)
        for ev in result.payload:
            self.assertIsInstance(ev, str)

    def test_sse_sequence_has_required_event_types(self):
        body = _msg_body("hi", max_tokens=1)
        result = handle_probe("/v1/messages", body)
        events = _parse_sse_events(result.payload)
        types = [e["_event"] for e in events]
        for required in (
            "message_start", "content_block_start", "content_block_delta",
            "content_block_stop", "message_delta", "message_stop",
        ):
            self.assertIn(required, types)

    def test_sse_event_order(self):
        body = _msg_body("hi", max_tokens=1)
        result = handle_probe("/v1/messages", body)
        events = _parse_sse_events(result.payload)
        types = [e["_event"] for e in events]
        self.assertEqual(types, [
            "message_start", "content_block_start", "content_block_delta",
            "content_block_stop", "message_delta", "message_stop",
        ])

    def test_message_start_carries_requested_model(self):
        body = _msg_body("hi", max_tokens=1, model="claude-haiku-4-5")
        result = handle_probe("/v1/messages", body)
        events = _parse_sse_events(result.payload)
        start = next(e for e in events if e["_event"] == "message_start")
        self.assertEqual(start["message"]["model"], "claude-haiku-4-5")

    def test_stop_reason_is_end_turn(self):
        body = _msg_body("hi", max_tokens=1)
        result = handle_probe("/v1/messages", body)
        events = _parse_sse_events(result.payload)
        delta = next(e for e in events if e["_event"] == "message_delta")
        self.assertEqual(delta["delta"]["stop_reason"], "end_turn")

    def test_custom_request_id_is_embedded(self):
        body = _msg_body("hi", max_tokens=1)
        result = handle_probe("/v1/messages", body, request_id="msg_custom_123")
        events = _parse_sse_events(result.payload)
        start = next(e for e in events if e["_event"] == "message_start")
        self.assertEqual(start["message"]["id"], "msg_custom_123")

    def test_default_request_id_has_clasp_prefix(self):
        body = _msg_body("hi", max_tokens=1)
        result = handle_probe("/v1/messages", body)
        events = _parse_sse_events(result.payload)
        start = next(e for e in events if e["_event"] == "message_start")
        self.assertTrue(start["message"]["id"].startswith("msg_clasp_"))

    def test_each_event_string_ends_with_double_newline(self):
        body = _msg_body("hi", max_tokens=1)
        result = handle_probe("/v1/messages", body)
        for ev in result.payload:
            self.assertTrue(ev.endswith("\n\n"))


# ---------------------------------------------------------------------------
# handle_probe() — not a probe
# ---------------------------------------------------------------------------

class TestHandleProbeNotAProbe(unittest.TestCase):
    def test_normal_message_returns_not_a_probe(self):
        body = _msg_body("Write a long essay about distributed systems", max_tokens=4096)
        result = handle_probe("/v1/messages", body)
        self.assertEqual(result, ProbeResult.not_a_probe())

    def test_kind_is_none(self):
        body = _msg_body("a real request", max_tokens=2048)
        result = handle_probe("/v1/messages", body)
        self.assertIsNone(result.kind)

    def test_payload_is_none(self):
        body = _msg_body("a real request", max_tokens=2048)
        result = handle_probe("/v1/messages", body)
        self.assertIsNone(result.payload)

    def test_unrelated_path_returns_not_a_probe(self):
        result = handle_probe("/internal/status", {})
        self.assertIsNone(result.kind)
        self.assertIsNone(result.payload)


# ---------------------------------------------------------------------------
# ProbeResult dataclass
# ---------------------------------------------------------------------------

class TestProbeResultDataclass(unittest.TestCase):
    def test_not_a_probe_factory(self):
        r = ProbeResult.not_a_probe()
        self.assertIsNone(r.kind)
        self.assertIsNone(r.payload)

    def test_equality_between_two_not_a_probe_instances(self):
        self.assertEqual(ProbeResult.not_a_probe(), ProbeResult.not_a_probe())


if __name__ == "__main__":
    unittest.main(verbosity=2)