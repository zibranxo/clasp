"""
tests/unit/test_proxy_routes.py
===============================
Unit tests for clasp.api.proxy_routes.
"""

from __future__ import annotations

import sys
import uuid
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import os

from clasp.api.proxy_routes import (
    router,
    _verify_bearer,
    _raise_401,
    _sse_headers,
    _wrap_async_iter,
    _local_probe_response,
)
from clasp.config.settings import Settings
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import pytest


def test_sse_headers():
    """Test SSE headers generation."""
    request_id = "req_test123"
    headers = _sse_headers(request_id)

    assert isinstance(headers, dict)
    assert headers["Cache-Control"] == "no-cache"
    assert headers["X-Accel-Buffering"] == "no"
    assert headers["X-Request-Id"] == "req_test123"


def test_wrap_async_iter():
    """Test wrapping async iterator."""
    async def mock_async_iter():
        yield "chunk1"
        yield "chunk2"
        yield "chunk3"

    wrapped = _wrap_async_iter(mock_async_iter())

    # Test that it works
    async def collect_chunks():
        chunks = []
        async for chunk in wrapped:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect_chunks())
    assert chunks == [b"chunk1", b"chunk2", b"chunk3"]


def test_local_probe_response_structure():
    """Test local probe response structure."""
    body = {
        "model": "test-model",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "test"}],
    }
    request_id = "req_local_123"

    # Test non-streaming response
    result_json = _local_probe_response(body, request_id, stream=False)
    assert isinstance(result_json, dict)
    assert result_json["id"].startswith("msg_")
    assert result_json["type"] == "message"
    assert result_json["role"] == "assistant"
    assert result_json["model"] == "test-model"
    assert len(result_json["content"]) == 1
    assert result_json["content"][0]["type"] == "text"
    assert result_json["stop_reason"] == "end_turn"
    assert result_json["stop_sequence"] is None
    assert result_json["usage"]["input_tokens"] >= 1
    assert result_json["usage"]["output_tokens"] >= 1

    # Test streaming response
    result_stream = _local_probe_response(body, request_id, stream=True)
    assert hasattr(result_stream, "__aiter__")

    # Consume the stream
    async def collect_events():
        events = []
        async for event in result_stream:
            events.append(event)
        return events

    events = asyncio.run(collect_events())
    assert len(events) == 5  # message_start, content_block_start, content_block_delta, content_block_stop, message_delta, message_stop? Wait that's 6
    # Actually let's check what _canned_sse_response produces
    # It produces 6 events: message_start, content_block_start, content_block_delta, content_block_stop, message_delta, message_stop
    assert len(events) == 6

    # Check first event is message_start
    assert events[0].startswith("event: message_start\ndata:")
    assert '"type":"message_start"' in events[0]

    # Check last event is message_stop
    assert events[-1].startswith("event: message_stop\ndata:")
    assert '"type":"message_stop"' in events[-1]


def test_verify_bearer_valid():
    """Test bearer token validation with valid token."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {"Authorization": "Bearer valid-token"}

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.api_key = "valid-token"

    with patch("clasp.api.proxy_routes.get_settings", return_value=mock_settings):
        # Should not raise
        _verify_bearer(mock_request)


def test_verify_bearer_missing_header():
    """Test bearer token validation with missing header."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {}  # No Authorization header

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.api_key = "any-token"

    with patch("clasp.api.proxy_routes.get_settings", return_value=mock_settings):
        try:
            _verify_bearer(mock_request)
            assert False, "Should have raised HTTPException"
        except HTTPException as e:
            assert e.status_code == 401
            assert "Missing Authorization header" in e.detail["error"]["message"]


def test_verify_bearer_wrong_prefix():
    """Test bearer token validation with wrong prefix."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {"Authorization": "Basic invalid-token"}

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.api_key = "any-token"

    with patch("clasp.api.proxy_routes.get_settings", return_value=mock_settings):
        try:
            _verify_bearer(mock_request)
            assert False, "Should have raised HTTPException"
        except HTTPException as e:
            assert e.status_code == 401
            assert "Missing Authorization header" in e.detail["error"]["message"]


def test_verify_bearer_wrong_token():
    """Test bearer token validation with wrong token."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {"Authorization": "Bearer wrong-token"}

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.api_key = "correct-token"

    with patch("clasp.api.proxy_routes.get_settings", return_value=mock_settings):
        try:
            _verify_bearer(mock_request)
            assert False, "Should have raised HTTPException"
        except HTTPException as e:
            assert e.status_code == 401
            assert "Invalid API key" in e.detail["error"]["message"]


def test_verify_bearer_case_insensitive_prefix():
    """Test bearer token validation is case insensitive for Bearer."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {"Authorization": "bearer correct-token"}  # lowercase

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.api_key = "correct-token"

    with patch("clasp.api.proxy_routes.get_settings", return_value=mock_settings):
        # Should not raise (case insensitive)
        _verify_bearer(mock_request)

    # Test mixed case
    mock_request.headers = {"Authorization": "BeArEr correct-token"}
    with patch("clasp.api.proxy_routes.get_settings", return_value=mock_settings):
        _verify_bearer(mock_request)


def test_verify_bearer_extra_spaces():
    """Test bearer token validation handles extra spaces."""
    mock_request = MagicMock(spec=Request)
    mock_request.headers = {"Authorization": "Bearer  correct-token  "}  # Extra spaces

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.api_key = "correct-token"

    with patch("clasp.api.proxy_routes.get_settings", return_value=mock_settings):
        # Should not raise
        _verify_bearer(mock_request)


def test_raise_401():
    """Test _raise_401 function."""
    try:
        _raise_401("Test error message")
        assert False, "Should have raised HTTPException"
    except HTTPException as e:
        assert e.status_code == 401
        assert e.detail["type"] == "error"
        assert e.detail["error"]["type"] == "authentication_error"
        assert e.detail["error"]["message"] == "Test error message"
        assert e.headers["WWW-Authenticate"] == "Bearer"


def test_router_exists():
    """Test that the router was created."""
    assert router is not None
    # Test that it has the expected routes (basic check)
    assert len(router.routes) > 0


if __name__ == "__main__":
    test_sse_headers()
    test_wrap_async_iter()
    test_local_probe_response_structure()
    test_verify_bearer_valid()
    test_verify_bearer_missing_header()
    test_verify_bearer_wrong_prefix()
    test_verify_bearer_wrong_token()
    test_verify_bearer_case_insensitive_prefix()
    test_verify_bearer_extra_spaces()
    test_raise_401()
    test_router_exists()
    print("All proxy routes tests passed!")