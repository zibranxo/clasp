"""
tests/unit/test_service.py
==========================
Unit tests for clasp.api.service.
"""

from __future__ import annotations

import sys
import time
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import os

from clasp.api.service import (
    handle_request,
    _make_request_id,
    _no_provider_error_json,
    _no_provider_error_sse,
)
from clasp.api.detect import RequestType
from clasp.config.settings import Settings


def test_make_request_id():
    """Test request ID generation."""
    req_id1 = _make_request_id()
    req_id2 = _make_request_id()

    assert req_id1.startswith("req_")
    assert req_id2.startswith("req_")
    assert req_id1 != req_id2  # Should be different due to UUID
    assert len(req_id1) > 10  # Reasonable length


def test_no_provider_error_json():
    """Test no provider error JSON response."""
    request_id = "req_test123"
    error_json = _no_provider_error_json(request_id)

    assert isinstance(error_json, dict)
    assert error_json["type"] == "error"
    assert error_json["error"]["type"] == "overloaded_error"
    assert "No providers are currently available" in error_json["error"]["message"]
    assert "http://127.0.0.1:8082" in error_json["error"]["message"]


def test_no_provider_error_sse():
    """Test no provider error SSE response."""
    request_id = "req_test123"
    error_sse = _no_provider_error_sse(request_id)

    assert isinstance(error_sse, str)
    assert error_sse.startswith("event: error\ndata: ")
    assert error_sse.endswith("\n\n")

    # Check that it contains valid JSON
    import json
    json_str = error_sse[len("event: error\ndata: ") :-2]
    data = json.loads(json_str)
    assert data["type"] == "error"
    assert data["error"]["type"] == "overloaded_error"


def test_handle_request_no_provider():
    """Test handle_request when no providers are available."""
    body = {
        "model": "test",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    # Mock get_settings to return settings with no enabled providers
    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.host = "127.0.0.1"
    mock_settings.server.port = 8082
    mock_settings.server.api_key = "test"
    mock_settings.provider_chain = []
    mock_settings.providers = {}

    with patch("clasp.config.settings.get_settings", return_value=mock_settings):
        # Mock the registry to return None for first_available
        with patch("clasp.api.service._get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.first_available.return_value = None
            mock_get_registry.return_value = mock_registry

            # Test non-streaming
            result = asyncio.run(handle_request(body, stream=False))
            assert result == _no_provider_error_json("req_test123")  # Default request ID

            # Test streaming
            result_stream = asyncio.run(handle_request(body, stream=True))
            # Should be an async iterator
            assert hasattr(result_stream, "__aiter__")

            # Consume the stream
            chunks = []
            async for chunk in result_stream:
                chunks.append(chunk)

            assert len(chunks) == 1
            assert chunks[0] == _no_provider_error_sse("req_test123")


def test_handle_request_with_provider():
    """Test handle_request when a provider is available."""
    body = {
        "model": "test",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.host = "127.0.0.1"
    mock_settings.server.port = 8082
    mock_settings.server.api_key = "test"
    mock_settings.provider_chain = ["test-provider"]
    mock_providers = {"test-provider": MagicMock(enabled=True, keys=["key1"])}
    mock_settings.providers = mock_providers

    with patch("clasp.config.settings.get_settings", return_value=mock_settings):
        # Mock registry
        mock_provider = MagicMock()
        mock_provider.provider_name = "test-provider"
        mock_provider.stream = AsyncMock(return_value=AsyncMock())
        mock_provider.complete = AsyncMock(return_value={"type": "message", "role": "assistant", "content": []})

        mock_registry = MagicMock()
        mock_registry.first_available.return_value = mock_provider
        mock_registry.__iter__ = MagicMock(return_value=iter(["test-provider"]))
        mock_registry.__getitem__ = MagicMock(return_value=mock_provider)

        with patch("clasp.api.service._get_registry", return_value=mock_registry):
            # Mock the detect and classify_priority functions
            with patch("clasp.api.service.detect", return_value=RequestType.INTERACTIVE):
                with patch("clasp.api.service.classify_priority", return_value=0):

                    # Test non-streaming
                    result = asyncio.run(handle_request(body, stream=False))
                    # Should return the provider's response
                    mock_provider.complete.assert_called_once()
                    assert result == {"type": "message", "role": "assistant", "content": []}

                    # Reset mock
                    mock_provider.complete.reset_mock()

                    # Test streaming
                    mock_stream = AsyncMock()
                    mock_stream.__aiter__ = MagicMock(return_value=iter(["chunk1", "chunk2"]))
                    mock_provider.stream.return_value = mock_stream

                    result_stream = asyncio.run(handle_request(body, stream=True))
                    # Should return the stream
                    assert result_stream == mock_stream
                    mock_provider.stream.assert_called_once()


def test_handle_request_with_request_id():
    """Test handle_request respects provided request ID."""
    body = {
        "model": "test",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    custom_request_id = "custom-req-123"

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.host = "127.0.0.1"
    mock_settings.server.port = 8082
    mock_settings.server.api_key = "test"
    mock_settings.provider_chain = []
    mock_settings.providers = {}

    with patch("clasp.config.settings.get_settings", return_value=mock_settings):
        with patch("clasp.api.service._get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.first_available.return_value = None
            mock_get_registry.return_value = mock_registry

            # Test that custom request ID is used in error responses
            result = asyncio.run(handle_request(body, request_id=custom_request_id, stream=False))
            assert custom_request_id in str(result)  # Should appear in the JSON


def test_handle_request_stream_flag_from_body():
    """Test handle_request uses body['stream'] when stream parameter is None."""
    body = {
        "model": "test",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
        "stream": True,  # Explicitly set in body
    }

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.host = "127.0.0.1"
    mock_settings.server.port = 8082
    mock_settings.server.api_key = "test"
    mock_settings.provider_chain = []
    mock_settings.providers = {}

    with patch("clasp.config.settings.get_settings", return_value=mock_settings):
        with patch("clasp.api.service._get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.first_available.return_value = None
            mock_get_registry.return_value = mock_registry

            # Call with stream=None (should use body['stream'])
            result = asyncio.run(handle_request(body, stream=None))
            # Should be a streaming response since body['stream'] is True
            assert hasattr(result, "__aiter__")


def test_handle_request_priority_and_type_logging():
    """Test that request type and priority are determined and would be logged."""
    body = {
        "model": "test",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "Hello"}],
    }

    mock_settings = MagicMock(spec=Settings)
    mock_settings.server.host = "127.0.0.1"
    mock_settings.server.port = 8082
    mock_settings.server.api_key = "test"
    mock_settings.provider_chain = []
    mock_settings.providers = {}

    with patch("clasp.config.settings.get_settings", return_value=mock_settings):
        with patch("clasp.api.service._get_registry") as mock_get_registry:
            mock_registry = MagicMock()
            mock_registry.first_available.return_value = None
            mock_get_registry.return_value = mock_registry

            # Mock detect to return a specific type
            with patch("clasp.api.service.detect", return_value=RequestType.TOOL_USE):
                with patch("clasp.api.service.classify_priority", return_value=1) as mock_classify:
                    # Call handle_request
                    asyncio.run(handle_request(body, stream=False))
                    # Verify classify_priority was called with TOOL_USE
                    mock_classify.assert_called_once_with(RequestType.TOOL_USE)


if __name__ == "__main__":
    test_make_request_id()
    test_no_provider_error_json()
    test_no_provider_error_sse()
    test_handle_request_no_provider()
    test_handle_request_with_provider()
    test_handle_request_with_request_id()
    test_handle_request_stream_flag_from_body()
    test_handle_request_priority_and_type_logging()
    print("All service tests passed!")