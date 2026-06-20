"""
tests/unit/test_detect.py
=========================
Unit tests for clasp.api.detect.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.api.detect import (
    RequestType,
    detect,
    classify_priority,
    LONG_CONTEXT_TOKEN_THRESHOLD,
    PROBE_MAX_TOKENS_CEILING,
    _estimate_tokens,
    _has_image_block,
    _is_background,
    _system_text,
    _first_user_text,
    _extract_text,
    _BACKGROUND_SYSTEM_PATTERNS,
)


def test_request_type_enum():
    """Test RequestType enum values."""
    assert RequestType.PROBE == "PROBE"
    assert RequestType.THINK == "THINK"
    assert RequestType.VISION == "VISION"
    assert RequestType.TOOL_USE == "TOOL_USE"
    assert RequestType.LONG_CONTEXT == "LONG_CONTEXT"
    assert RequestType.BACKGROUND == "BACKGROUND"
    assert RequestType.INTERACTIVE == "INTERACTIVE"

    # Test string comparison
    assert RequestType.PROBE == "PROBE"
    assert RequestType.INTERACTIVE != "PROBE"


def test_probe_detection():
    """Test PROBE request type detection."""
    # max_tokens <= 5
    body = {
        "model": "test",
        "max_tokens": 5,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    assert detect(body) == RequestType.PROBE

    body["max_tokens"] = 1
    assert detect(body) == RequestType.PROBE

    body["max_tokens"] = 0
    assert detect(body) == RequestType.PROBE

    # max_tokens > 5 should not be PROBE (unless other conditions)
    body["max_tokens"] = 6
    assert detect(body) != RequestType.PROBE


def test_think_detection():
    """Test THINK request type detection."""
    body = {
        "model": "test",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
        "thinking": {"type": "enabled"},
    }
    assert detect(body) == RequestType.THINK

    # thinking disabled should not trigger
    body["thinking"] = {"type": "disabled"}
    assert detect(body) != RequestType.THINK

    # missing thinking should not trigger
    del body["thinking"]
    assert detect(body) != RequestType.THINK

    # thinking with wrong type should not trigger
    body["thinking"] = {"type": "something-else"}
    assert detect(body) != RequestType.THINK


def test_vision_detection():
    """Test VISION request type detection."""
    body = {
        "model": "test",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    # No image
    assert detect(body) != RequestType.VISION

    # Add image block
    body["messages"][0]["content"] = [
        {"type": "text", "text": "Look at this image:"},
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": "base64data"},
        },
    ]
    assert detect(body) == RequestType.VISION

    # Image with URL source
    body["messages"][0]["content"] = [
        {"type": "text", "text": "Check this:"},
        {
            "type": "image",
            "source": {"type": "url", "url": "https://example.com/image.png"},
        },
    ]
    assert detect(body) == RequestType.VISION


def test_tool_use_detection():
    """Test TOOL_USE request type detection."""
    body = {
        "model": "test",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    # No tools
    assert detect(body) != RequestType.TOOL_USE

    # Empty tools list
    body["tools"] = []
    assert detect(body) != RequestType.TOOL_USE

    # Tools with content
    body["tools"] = [
        {
            "name": "test_tool",
            "description": "A test tool",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    assert detect(body) == RequestType.TOOL_USE

    # Remove tools
    del body["tools"]
    assert detect(body) != RequestType.TOOL_USE


def test_long_context_detection():
    """Test LONG_CONTEXT request type detection."""
    body = {
        "model": "test",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    # Normal length should not be LONG_CONTEXT
    assert detect(body) != RequestType.LONG_CONTEXT

    # Create a long message to exceed threshold
    long_text = "x" * (LONG_CONTEXT_TOKEN_THRESHOLD * 4 + 100)  # Roughly enough chars
    body["messages"][0]["content"] = long_text
    assert detect(body) == RequestType.LONG_CONTEXT

    # Reduce length
    body["messages"][0]["content"] = "x" * 100
    assert detect(body) != RequestType.LONG_CONTEXT


def test_background_detection():
    """Test BACKGROUND request type detection."""
    body = {
        "model": "test",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    # Normal content should not be BACKGROUND
    assert detect(body) != RequestType.BACKGROUND

    # Test system prompt patterns
    test_cases = [
        "Please index the file",
        "This is a background task",
        "Let's summarize this document",
        "I need to crawl the website",
        "Generate embeddings for these texts",
        "Index the codebase for search",
        "This is a clasp_background job",
        "task_type: background",
    ]

    for pattern in test_cases:
        body["system"] = pattern
        assert detect(body) == RequestType.BACKGROUND, f"Failed for pattern: {pattern}"
        del body["system"]

    # Test first user message patterns
    for pattern in test_cases:
        body["messages"][0]["content"] = pattern
        assert detect(body) == RequestType.BACKGROUND, f"Failed for pattern: {pattern}"
        body["messages"][0]["content"] = "Hello"

    # Test case insensitivity
    body["system"] = "PLeAsE InDeX tHe FiLe"
    assert detect(body) == RequestType.BACKGROUND
    del body["system"]

    # Test partial matches (should work due to regex patterns)
    body["system"] = "summarization"
    assert detect(body) == RequestType.BACKGROUND
    del body["system"]


def test_interactive_fallback():
    """Test that INTERACTIVE is the default."""
    body = {
        "model": "test",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello, how are you?"}],
    }

    # Not PROBE, THINK, VISION, TOOL_USE, LONG_CONTEXT, or BACKGROUND
    assert detect(body) == RequestType.INTERACTIVE

    # Change various fields to ensure it stays INTERACTIVE unless it matches other criteria
    body["max_tokens"] = 4  # Still not PROBE (need <=5, but let's check)
    assert detect(body) == RequestType.PROBE  # Actually this IS probe

    body["max_tokens"] = 6  # Back to not PROBE
    assert detect(body) == RequestType.INTERACTIVE

    body["thinking"] = {"type": "enabled"}
    assert detect(body) == RequestType.THINK  # This IS think
    del body["thinking"]

    body["messages"][0]["content"] = [
        {"type": "text", "text": "Text"},
        {"type": "image", "source": {"type": "base64", "data": "data"}},
    ]
    assert detect(body) == RequestType.VISION  # This IS vision
    body["messages"][0]["content"] = "Hello"

    body["tools"] = [{"name": "test"}]
    assert detect(body) == RequestType.TOOL_USE  # This IS tool use
    del body["tools"]

    long_text = "x" * (LONG_CONTEXT_TOKEN_THRESHOLD * 4 + 100)
    body["messages"][0]["content"] = long_text
    assert detect(body) == RequestType.LONG_CONTEXT  # This IS long context
    body["messages"][0]["content"] = "Hello"

    body["system"] = "background task"
    assert detect(body) == RequestType.BACKGROUND  # This IS background
    del body["system"]

    # Should be back to INTERACTIVE
    assert detect(body) == RequestType.INTERACTIVE


def test_classify_priority():
    """Test priority classification."""
    # Priority 0: PROBE, INTERACTIVE, THINK, VISION, LONG_CONTEXT
    assert classify_priority(RequestType.PROBE) == 0
    assert classify_priority(RequestType.INTERACTIVE) == 0
    assert classify_priority(RequestType.THINK) == 0
    assert classify_priority(RequestType.VISION) == 0
    assert classify_priority(RequestType.LONG_CONTEXT) == 0

    # Priority 1: TOOL_USE
    assert classify_priority(RequestType.TOOL_USE) == 1

    # Priority 2: BACKGROUND
    assert classify_priority(RequestType.BACKGROUND) == 2

    # Test with unknown type (should default to 0)
    class FakeType:
        value = "FAKE"

    assert classify_priority(FakeType()) == 0


def test_estimate_tokens():
    """Test token estimation function."""
    # Empty body
    assert _estimate_tokens({}) == 0

    # Simple text
    body = {"messages": [{"role": "user", "content": "Hello world"}]}
    tokens = _estimate_tokens(body)
    assert isinstance(tokens, int)
    assert tokens > 0

    # With system
    body["system"] = "You are a helpful assistant"
    tokens_with_system = _estimate_tokens(body)
    assert tokens_with_system > tokens

    # With multiple messages
    body["messages"].append({"role": "assistant", "content": "I am doing well"})
    tokens_more = _estimate_tokens(body)
    assert tokens_more > tokens_with_system

    # Test that it's reasonable (roughly 4 chars per token)
    text = "This is a test sentence for token estimation."
    approx_tokens = len(text) // 4
    body = {"messages": [{"role": "user", "content": text}]}
    actual_tokens = _estimate_tokens(body)
    # Should be in the ballpark (within 50%)
    assert actual_tokens > 0
    assert actual_tokens > approx_tokens * 0.5
    assert actual_tokens < approx_tokens * 2.0


def test_has_image_block():
    """Test image block detection."""
    # No images
    body = {
        "messages": [
            {"role": "user", "content": "Hello world"},
            {"role": "assistant", "content": "Hi there"},
        ]
    }
    assert _has_image_block(body) is False

    # Image in first message
    body["messages"][0]["content"] = [
        {"type": "text", "text": "Look:"},
        {"type": "image", "source": {"type": "base64", "data": "data"}},
    ]
    assert _has_image_block(body) is True

    # Image in second message
    body["messages"][0]["content"] = "Hello"
    body["messages"][1]["content"] = [
        {"type": "text", "text": "Picture:"},
        {"type": "image", "source": {"type": "url", "url": "http://example.com/img.jpg"}},
    ]
    assert _has_image_block(body) is True

    # No images again
    body["messages"][1]["content"] = "Just text"
    assert _has_image_block(body) is False


def test_system_text():
    """Test system text extraction."""
    # No system
    body = {}
    assert _system_text(body) == ""

    # System as string
    body["system"] = "You are helpful"
    assert _system_text(body) == "You are helpful"

    # System as list of blocks
    body["system"] = [
        {"type": "text", "text": "Part 1"},
        {"type": "text", "text": "Part 2"},
        {"type": "image", "source": {"type": "base64", "data": "data"}},  # Should be ignored
    ]
    assert _system_text(body) == "Part 1Part 2"

    # System with non-text blocks (should ignore non-text)
    body["system"] = [
        {"type": "text", "text": "Start"},
        {"type": "image", "source": {"type": "base64", "data": "data"}},
        {"type": "text", "text": "End"},
    ]
    assert _system_text(body) == "StartEnd"


def test_first_user_text():
    """Test first user message text extraction."""
    # No messages
    body = {}
    assert _first_user_text(body) == ""

    # No user messages
    body["messages"] = [{"role": "assistant", "content": "Hello"}]
    assert _first_user_text(body) == ""

    # First message is user
    body["messages"][0]["role"] = "user"
    body["messages"][0]["content"] = "Hello from user"
    assert _first_user_text(body) == "Hello from user"

    # First message is not user, second is
    body["messages"][0]["role"] = "system"
    body["messages"][0]["content"] = "System message"
    body["messages"][1]["role"] = "user"
    body["messages"][1]["content"] = "Hello from second user"
    assert _first_user_text(body) == "Hello from second user"

    # User message with complex content
    body["messages"][1]["content"] = [
        {"type": "text", "text": "Part 1"},
        {"type": "text", "text": "Part 2"},
        {"type": "image", "source": {"type": "base64", "data": "data"}},
    ]
    assert _first_user_text(body) == "Part 1Part 2"


def test_extract_text():
    """Test text extraction from content blocks."""
    # String content
    assert _extract_text("Hello world") == "Hello world"

    # Empty string
    assert _extract_text("") == ""

    # Text block
    assert _extract_text({"type": "text", "text": "Extract this"}) == "Extract this"

    # Text block with empty text
    assert _extract_text({"type": "text", "text": ""}) == ""

    # Non-text block
    assert _extract_text({"type": "image", "source": {"type": "base64", "data": "data"}}) == ""

    # Unknown block type
    assert _extract_text({"type": "unknown", "text": "should be ignored"}) == ""

    # Non-dict
    assert _extract_text(["not", "a", "dict"]) == ""
    assert _extract_text(123) == ""
    assert _extract_text(None) == ""


def test_background_system_patterns():
    """Test that background system patterns are compiled regexes."""
    for pattern in _BACKGROUND_SYSTEM_PATTERNS:
        assert hasattr(pattern, "search")
        # Test that it's actually a compiled regex
        import re
        assert isinstance(pattern, re.Pattern)


if __name__ == "__main__":
    test_request_type_enum()
    test_probe_detection()
    test_think_detection()
    test_vision_detection()
    test_tool_use_detection()
    test_long_context_detection()
    test_background_detection()
    test_interactive_fallback()
    test_classify_priority()
    test_estimate_tokens()
    test_has_image_block()
    test_system_text()
    test_first_user_text()
    test_extract_text()
    test_background_system_patterns()
    print("All detect tests passed!")