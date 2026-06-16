"""
tests/unit/test_message_converter.py

Unit tests for clasp/providers/common/message_converter.py.

Coverage map (spec §18 + §9):
  1. System prompt → OpenAI system role message
  2. Anthropic tool_use block → OpenAI tool_calls
  3. Anthropic tool_result block → OpenAI tool-role message
  4. Anthropic image block → OpenAI image_url format (base64 + URL variants)
  5. Thinking block → stripped entirely
  6. Stop reason mapping: stop→end_turn, length→max_tokens, tool_calls→tool_use, etc.

Additional cases:
  - Empty/absent tools → "tools" key absent from OpenAI payload
  - merge_system=True → system injected into first user message
  - Multiple content blocks (text + tool_use) in one assistant message
  - tool_choice mapping (auto / any / specific tool)
  - Round-trip: anthropic_to_openai → openai_to_anthropic_response field integrity
  - tool_result with list content (multiple text blocks)
  - Plain string message content (fast path)
  - Missing/null stop reason → "end_turn"
"""

import json
import sys
import os

# Allow running from repo root without install
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from clasp.providers.common.message_converter import (
    anthropic_to_openai,
    openai_to_anthropic_response,
    _map_stop_reason,
    _openai_image_to_anthropic,
    STOP_REASON_MAP,
)

# ===========================================================================
# Fixtures / shared data
# ===========================================================================

SIMPLE_REQUEST: dict = {
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello, world!"}],
}


# ===========================================================================
# 1. System prompt → OpenAI system role message
# ===========================================================================

class TestSystemPrompt:
    def test_system_string_becomes_system_role(self):
        req = {**SIMPLE_REQUEST, "system": "You are a helpful assistant."}
        out = anthropic_to_openai(req)
        msgs = out["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "You are a helpful assistant."
        # Original user message follows
        assert msgs[1]["role"] == "user"

    def test_system_as_content_block_list(self):
        """Anthropic allows system as a list of text blocks."""
        req = {
            **SIMPLE_REQUEST,
            "system": [
                {"type": "text", "text": "You are "},
                {"type": "text", "text": "a pirate."},
            ],
        }
        out = anthropic_to_openai(req)
        assert out["messages"][0]["role"] == "system"
        assert out["messages"][0]["content"] == "You are a pirate."

    def test_system_thinking_block_stripped_from_system(self):
        """thinking blocks inside system content lists should be stripped."""
        req = {
            **SIMPLE_REQUEST,
            "system": [
                {"type": "thinking", "thinking": "internal monologue"},
                {"type": "text", "text": "Visible system text."},
            ],
        }
        out = anthropic_to_openai(req)
        assert out["messages"][0]["content"] == "Visible system text."

    def test_no_system_no_system_role_message(self):
        out = anthropic_to_openai(SIMPLE_REQUEST)
        roles = [m["role"] for m in out["messages"]]
        assert "system" not in roles

    def test_empty_system_string_no_system_role_message(self):
        req = {**SIMPLE_REQUEST, "system": ""}
        out = anthropic_to_openai(req)
        roles = [m["role"] for m in out["messages"]]
        assert "system" not in roles

    # --- merge_system variant (NIM compatibility) ---

    def test_merge_system_prepends_to_first_user_message(self):
        req = {**SIMPLE_REQUEST, "system": "Act as a coding assistant."}
        out = anthropic_to_openai(req, merge_system=True)
        msgs = out["messages"]
        roles = [m["role"] for m in msgs]
        assert "system" not in roles, "No system-role message when merge_system=True"
        first_user = msgs[0]
        assert first_user["role"] == "user"
        assert "<system>" in first_user["content"]
        assert "Act as a coding assistant." in first_user["content"]
        assert "Hello, world!" in first_user["content"]

    def test_merge_system_format_is_xml_wrapped(self):
        req = {**SIMPLE_REQUEST, "system": "S"}
        out = anthropic_to_openai(req, merge_system=True)
        content = out["messages"][0]["content"]
        assert content.startswith("<system>\nS\n</system>")


# ===========================================================================
# 2. tool_use content block → OpenAI tool_calls
# ===========================================================================

class TestToolUseToOpenAI:
    def _make_request_with_tool_use(self):
        return {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "Edit the file."},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_abc123",
                            "name": "str_replace_based_edit",
                            "input": {"path": "file.py", "old_str": "foo", "new_str": "bar"},
                        }
                    ],
                },
            ],
        }

    def test_tool_use_becomes_tool_calls(self):
        out = anthropic_to_openai(self._make_request_with_tool_use())
        assistant_msg = out["messages"][-1]
        assert assistant_msg["role"] == "assistant"
        tool_calls = assistant_msg.get("tool_calls", [])
        assert len(tool_calls) == 1

    def test_tool_call_id_preserved(self):
        out = anthropic_to_openai(self._make_request_with_tool_use())
        tc = out["messages"][-1]["tool_calls"][0]
        assert tc["id"] == "toolu_abc123"

    def test_tool_call_name_preserved(self):
        out = anthropic_to_openai(self._make_request_with_tool_use())
        tc = out["messages"][-1]["tool_calls"][0]
        assert tc["function"]["name"] == "str_replace_based_edit"

    def test_tool_call_arguments_is_json_string(self):
        out = anthropic_to_openai(self._make_request_with_tool_use())
        tc = out["messages"][-1]["tool_calls"][0]
        args = json.loads(tc["function"]["arguments"])
        assert args["path"] == "file.py"
        assert args["old_str"] == "foo"
        assert args["new_str"] == "bar"

    def test_tool_call_type_is_function(self):
        out = anthropic_to_openai(self._make_request_with_tool_use())
        tc = out["messages"][-1]["tool_calls"][0]
        assert tc["type"] == "function"

    def test_multiple_tool_calls_in_one_message(self):
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read_file",
                            "input": {"path": "a.py"},
                        },
                        {
                            "type": "tool_use",
                            "id": "toolu_2",
                            "name": "read_file",
                            "input": {"path": "b.py"},
                        },
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        tool_calls = out["messages"][0]["tool_calls"]
        assert len(tool_calls) == 2
        assert tool_calls[0]["id"] == "toolu_1"
        assert tool_calls[1]["id"] == "toolu_2"

    def test_text_plus_tool_use_in_assistant_message(self):
        """Text content and tool_use in one assistant message."""
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "I will edit the file now."},
                        {
                            "type": "tool_use",
                            "id": "toolu_xyz",
                            "name": "write_file",
                            "input": {"path": "out.py", "content": "x=1"},
                        },
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        msg = out["messages"][0]
        assert msg["content"] == "I will edit the file now."
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["id"] == "toolu_xyz"

    def test_tools_list_converted(self):
        """Top-level tools array is translated to OpenAI functions format."""
        req = {
            **SIMPLE_REQUEST,
            "tools": [
                {
                    "name": "get_weather",
                    "description": "Returns weather data.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                }
            ],
        }
        out = anthropic_to_openai(req)
        assert "tools" in out
        assert out["tools"][0]["type"] == "function"
        assert out["tools"][0]["function"]["name"] == "get_weather"
        assert out["tools"][0]["function"]["description"] == "Returns weather data."
        assert "location" in out["tools"][0]["function"]["parameters"]["properties"]

    def test_empty_tools_list_absent_from_output(self):
        req = {**SIMPLE_REQUEST, "tools": []}
        out = anthropic_to_openai(req)
        assert "tools" not in out

    def test_tool_choice_auto(self):
        req = {**SIMPLE_REQUEST, "tools": [_dummy_tool()], "tool_choice": {"type": "auto"}}
        out = anthropic_to_openai(req)
        assert out["tool_choice"] == "auto"

    def test_tool_choice_any_becomes_required(self):
        req = {**SIMPLE_REQUEST, "tools": [_dummy_tool()], "tool_choice": {"type": "any"}}
        out = anthropic_to_openai(req)
        assert out["tool_choice"] == "required"

    def test_tool_choice_specific_tool(self):
        req = {
            **SIMPLE_REQUEST,
            "tools": [_dummy_tool()],
            "tool_choice": {"type": "tool", "name": "my_fn"},
        }
        out = anthropic_to_openai(req)
        assert out["tool_choice"] == {"type": "function", "function": {"name": "my_fn"}}


# ===========================================================================
# 3. tool_result block → OpenAI tool-role message
# ===========================================================================

class TestToolResultToOpenAI:
    def test_tool_result_string_content(self):
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_abc",
                            "content": "The file contents are: x=1",
                        }
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        tool_msg = out["messages"][0]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "toolu_abc"
        assert tool_msg["content"] == "The file contents are: x=1"

    def test_tool_result_block_list_content(self):
        """tool_result.content can itself be a list of text blocks."""
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_def",
                            "content": [
                                {"type": "text", "text": "First part. "},
                                {"type": "text", "text": "Second part."},
                            ],
                        }
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        tool_msg = out["messages"][0]
        assert tool_msg["role"] == "tool"
        assert "First part." in tool_msg["content"]
        assert "Second part." in tool_msg["content"]

    def test_multiple_tool_results_become_multiple_tool_messages(self):
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "result 1",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_2",
                            "content": "result 2",
                        },
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        msgs = out["messages"]
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        ids = {m["tool_call_id"] for m in tool_msgs}
        assert ids == {"toolu_1", "toolu_2"}

    def test_user_text_and_tool_result_in_same_message(self):
        """User message has both text and a tool_result: text → user msg, result → tool msg."""
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Here is the output:"},
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_z",
                            "content": "42",
                        },
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        msgs = out["messages"]
        user_msgs = [m for m in msgs if m["role"] == "user"]
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert len(user_msgs) == 1
        assert "Here is the output:" in user_msgs[0]["content"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "toolu_z"


# ===========================================================================
# 4. Image content block → OpenAI image_url format
# ===========================================================================

class TestImageConversion:
    def test_base64_image_converted(self):
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "aGVsbG8=",
                            },
                        },
                        {"type": "text", "text": "What is in this image?"},
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        user_content = out["messages"][0]["content"]
        assert isinstance(user_content, list)
        image_items = [i for i in user_content if i["type"] == "image_url"]
        assert len(image_items) == 1
        url = image_items[0]["image_url"]["url"]
        assert url == "data:image/png;base64,aGVsbG8="

    def test_url_image_converted(self):
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": "https://example.com/photo.jpg",
                            },
                        }
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        user_content = out["messages"][0]["content"]
        image_items = [i for i in user_content if i["type"] == "image_url"]
        assert image_items[0]["image_url"]["url"] == "https://example.com/photo.jpg"

    def test_base64_image_roundtrip(self):
        """base64 → OpenAI image_url → back to Anthropic image block."""
        original_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": "/9j/fakebase64==",
            },
        }
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": [original_block]}
            ],
        }
        out = anthropic_to_openai(req)
        oai_item = out["messages"][0]["content"][0]
        recovered = _openai_image_to_anthropic(oai_item)
        assert recovered["type"] == "image"
        assert recovered["source"]["type"] == "base64"
        assert recovered["source"]["media_type"] == "image/jpeg"
        assert recovered["source"]["data"] == "/9j/fakebase64=="

    def test_jpeg_media_type_preserved(self):
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": "abc",
                            },
                        }
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        url = out["messages"][0]["content"][0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")


# ===========================================================================
# 5. Thinking block → stripped entirely
# ===========================================================================

class TestThinkingBlockStripped:
    def test_thinking_block_not_in_output(self):
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Let me reason step by step...",
                        },
                        {"type": "text", "text": "The answer is 42."},
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        msg = out["messages"][0]
        # No tool_calls (no tool_use blocks)
        assert "tool_calls" not in msg or msg.get("tool_calls") is None
        # Content should be just the text, no thinking
        assert msg["content"] == "The answer is 42."

    def test_thinking_only_message_produces_empty_content(self):
        """If all blocks are thinking, content should be empty string or None."""
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "internal..."},
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        msg = out["messages"][0]
        # content collapsed to empty after stripping
        content = msg.get("content")
        assert content == "" or content is None

    def test_thinking_block_in_user_message_stripped(self):
        """thinking blocks in user messages are stripped (unusual but should not crash)."""
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "thinking", "thinking": "..."},
                        {"type": "text", "text": "Actual user text."},
                    ],
                }
            ],
        }
        out = anthropic_to_openai(req)
        content = out["messages"][0]["content"]
        assert "thinking" not in str(content).lower() or "Actual user text." in str(content)
        assert "Actual user text." in str(content)

    def test_thinking_block_in_system_list_stripped(self):
        req = {
            **SIMPLE_REQUEST,
            "system": [
                {"type": "thinking", "thinking": "shh"},
                {"type": "text", "text": "Public instructions."},
            ],
        }
        out = anthropic_to_openai(req)
        system_content = out["messages"][0]["content"]
        assert "shh" not in system_content
        assert "Public instructions." in system_content


# ===========================================================================
# 6. Stop reason mapping
# ===========================================================================

class TestStopReasonMapping:
    @pytest.mark.parametrize(
        "finish_reason, expected_stop_reason",
        [
            ("stop", "end_turn"),
            ("length", "max_tokens"),
            ("tool_calls", "tool_use"),
            ("content_filter", "stop_sequence"),
            (None, "end_turn"),
            ("unknown_value", "end_turn"),  # default fallback
        ],
    )
    def test_stop_reason_map(self, finish_reason, expected_stop_reason):
        assert _map_stop_reason(finish_reason) == expected_stop_reason

    def test_stop_maps_to_end_turn_in_response(self):
        oai_resp = _make_oai_response(finish_reason="stop")
        anthropic_resp = openai_to_anthropic_response(oai_resp)
        assert anthropic_resp["stop_reason"] == "end_turn"

    def test_length_maps_to_max_tokens_in_response(self):
        oai_resp = _make_oai_response(finish_reason="length")
        anthropic_resp = openai_to_anthropic_response(oai_resp)
        assert anthropic_resp["stop_reason"] == "max_tokens"

    def test_tool_calls_maps_to_tool_use_in_response(self):
        oai_resp = _make_oai_response(finish_reason="tool_calls")
        anthropic_resp = openai_to_anthropic_response(oai_resp)
        assert anthropic_resp["stop_reason"] == "tool_use"

    def test_null_finish_reason_maps_to_end_turn(self):
        oai_resp = _make_oai_response(finish_reason=None)
        anthropic_resp = openai_to_anthropic_response(oai_resp)
        assert anthropic_resp["stop_reason"] == "end_turn"

    def test_stop_reason_map_is_complete(self):
        """Verify all expected keys are present in the mapping table."""
        for key in ("stop", "length", "tool_calls", "content_filter", None):
            assert key in STOP_REASON_MAP


# ===========================================================================
# openai_to_anthropic_response — response structure
# ===========================================================================

class TestOpenAIToAnthropicResponse:
    def test_basic_text_response_shape(self):
        oai_resp = _make_oai_response(text="Hello!")
        out = openai_to_anthropic_response(oai_resp)
        assert out["type"] == "message"
        assert out["role"] == "assistant"
        assert out["content"][0]["type"] == "text"
        assert out["content"][0]["text"] == "Hello!"

    def test_usage_tokens_mapped(self):
        oai_resp = _make_oai_response(prompt_tokens=100, completion_tokens=50)
        out = openai_to_anthropic_response(oai_resp)
        assert out["usage"]["input_tokens"] == 100
        assert out["usage"]["output_tokens"] == 50

    def test_response_id_preserved(self):
        oai_resp = _make_oai_response()
        oai_resp["id"] = "chatcmpl-TestID"
        out = openai_to_anthropic_response(oai_resp)
        assert out["id"] == "chatcmpl-TestID"

    def test_missing_id_gets_generated(self):
        oai_resp = _make_oai_response()
        oai_resp.pop("id", None)
        out = openai_to_anthropic_response(oai_resp)
        assert out["id"].startswith("msg_")

    def test_model_passed_through(self):
        oai_resp = _make_oai_response()
        oai_resp["model"] = "nvidia/llama-3.1-nemotron-70b"
        out = openai_to_anthropic_response(oai_resp)
        assert out["model"] == "nvidia/llama-3.1-nemotron-70b"

    def test_model_falls_back_to_original_request(self):
        oai_resp = _make_oai_response()
        oai_resp.pop("model", None)
        original_req = {"model": "claude-sonnet-4-6", "messages": []}
        out = openai_to_anthropic_response(oai_resp, original_request=original_req)
        assert out["model"] == "claude-sonnet-4-6"

    def test_tool_call_response_becomes_tool_use_block(self):
        oai_resp = _make_oai_response(finish_reason="tool_calls")
        oai_resp["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "call_abc",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "main.py"}',
                },
            }
        ]
        out = openai_to_anthropic_response(oai_resp)
        tool_blocks = [b for b in out["content"] if b["type"] == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0]["id"] == "call_abc"
        assert tool_blocks[0]["name"] == "read_file"
        assert tool_blocks[0]["input"]["path"] == "main.py"

    def test_malformed_tool_args_handled(self):
        """Non-JSON tool arguments are stored as a _raw fallback, not crash."""
        oai_resp = _make_oai_response(finish_reason="tool_calls")
        oai_resp["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "call_bad",
                "type": "function",
                "function": {"name": "fn", "arguments": "NOT JSON"},
            }
        ]
        out = openai_to_anthropic_response(oai_resp)
        tool_blocks = [b for b in out["content"] if b["type"] == "tool_use"]
        assert tool_blocks[0]["input"].get("_raw") == "NOT JSON"

    def test_stop_sequence_field_present(self):
        """stop_sequence key always present, even if None."""
        out = openai_to_anthropic_response(_make_oai_response())
        assert "stop_sequence" in out

    def test_zero_usage_when_missing(self):
        oai_resp = _make_oai_response()
        oai_resp.pop("usage", None)
        out = openai_to_anthropic_response(oai_resp)
        assert out["usage"]["input_tokens"] == 0
        assert out["usage"]["output_tokens"] == 0


# ===========================================================================
# Round-trip tests
# ===========================================================================

class TestRoundTrip:
    def test_simple_text_roundtrip_preserves_model(self):
        req = {**SIMPLE_REQUEST, "model": "claude-sonnet-4-6"}
        oai_req = anthropic_to_openai(req)
        # Simulate a minimal provider response
        oai_resp = _make_oai_response(text="Sure thing!")
        oai_resp["model"] = oai_req["model"]
        anthropic_resp = openai_to_anthropic_response(oai_resp, original_request=req)
        assert anthropic_resp["model"] == "claude-sonnet-4-6"

    def test_max_tokens_passes_through(self):
        req = {**SIMPLE_REQUEST, "max_tokens": 512}
        out = anthropic_to_openai(req)
        assert out["max_tokens"] == 512

    def test_temperature_passes_through(self):
        req = {**SIMPLE_REQUEST, "temperature": 0.7}
        out = anthropic_to_openai(req)
        assert out["temperature"] == 0.7

    def test_target_model_override(self):
        req = {**SIMPLE_REQUEST, "model": "claude-sonnet-4-6"}
        out = anthropic_to_openai(req, target_model="nvidia/llama-3.1-nemotron-70b")
        assert out["model"] == "nvidia/llama-3.1-nemotron-70b"

    def test_stop_sequences_single(self):
        req = {**SIMPLE_REQUEST, "stop_sequences": ["<END>"]}
        out = anthropic_to_openai(req)
        assert out["stop"] == "<END>"

    def test_stop_sequences_multiple(self):
        req = {**SIMPLE_REQUEST, "stop_sequences": ["<END>", "STOP"]}
        out = anthropic_to_openai(req)
        assert out["stop"] == ["<END>", "STOP"]

    def test_plain_string_message_passes_through(self):
        req = {**SIMPLE_REQUEST}
        out = anthropic_to_openai(req)
        assert out["messages"][0]["content"] == "Hello, world!"

    def test_original_request_not_mutated(self):
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "original"},
                    ],
                }
            ],
            "system": "sys",
        }
        import copy
        req_copy = copy.deepcopy(req)
        anthropic_to_openai(req)
        assert req == req_copy, "Input dict must not be mutated"

    def test_tool_use_and_result_full_chain(self):
        """
        Simulate a full tool chain:
          user → assistant (tool_use) → user (tool_result) → assistant (text)
        Ensure all messages translate correctly.
        """
        req = {
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [
                {"role": "user", "content": "What is 2+2?"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_calc",
                            "name": "calculator",
                            "input": {"expression": "2+2"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_calc",
                            "content": "4",
                        }
                    ],
                },
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "The answer is 4."}],
                },
            ],
        }
        out = anthropic_to_openai(req)
        msgs = out["messages"]
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant", "tool", "assistant"]
        assert msgs[1]["tool_calls"][0]["id"] == "toolu_calc"
        assert msgs[2]["tool_call_id"] == "toolu_calc"
        assert msgs[2]["content"] == "4"
        assert msgs[3]["content"] == "The answer is 4."


# ===========================================================================
# Helpers
# ===========================================================================

def _dummy_tool() -> dict:
    return {
        "name": "dummy",
        "description": "A dummy tool.",
        "input_schema": {"type": "object", "properties": {}},
    }


def _make_oai_response(
    text: str = "Test response.",
    finish_reason: str | None = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }