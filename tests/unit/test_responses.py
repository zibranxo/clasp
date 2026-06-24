import os
import sys
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Make sure clasp is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from clasp.api.proxy_routes import router, get_handle_request_fn
from clasp.config.settings import get_settings
from clasp.core.openai_responses.codex_catalog import (
    build_codex_model_catalog,
    update_codex_model_catalog,
)


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(router)
    
    mock_settings = MagicMock()
    mock_settings.server.api_key = "test-key"
    
    app.dependency_overrides[get_settings] = lambda: mock_settings
    return app


@pytest.fixture
def authorized_client(test_app):
    client = TestClient(test_app)
    client.headers.update({"Authorization": "Bearer test-key"})
    return client


def test_responses_requires_auth(test_app):
    client = TestClient(test_app)
    # No Auth header
    response = client.post("/v1/responses", json={"stream": True})
    assert response.status_code == 401
    
    # Invalid API key
    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer bad-key"},
        json={"stream": True}
    )
    assert response.status_code == 401


def test_responses_requires_stream_true(authorized_client):
    # stream = False
    response = authorized_client.post(
        "/v1/responses",
        json={"model": "nvidia_nim/meta/llama-3", "input": "hello", "stream": False}
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert "supports streaming only" in payload["error"]["message"]

    # stream omitted
    response = authorized_client.post(
        "/v1/responses",
        json={"model": "nvidia_nim/meta/llama-3", "input": "hello"}
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert "supports streaming only" in payload["error"]["message"]


@pytest.mark.anyio
async def test_responses_successful_translation_and_streaming(test_app, authorized_client):
    async def mock_handle_request_fn(payload, request_id, stream):
        assert payload["model"] == "nvidia_nim/meta/llama-3"
        assert payload["messages"][0]["content"] == "hello"
        assert stream is True
        
        async def _gen():
            # Yield Anthropic SSE chunks
            events = [
                b"event: message_start\ndata: {\"type\": \"message_start\", \"message\": {\"id\": \"msg_1\", \"type\": \"message\", \"role\": \"assistant\", \"content\": [], \"model\": \"meta/llama-3\", \"stop_reason\": null, \"stop_sequence\": null, \"usage\": {\"input_tokens\": 10, \"output_tokens\": 0}}}\n\n",
                b"event: content_block_start\ndata: {\"type\": \"content_block_start\", \"index\": 0, \"content_block\": {\"type\": \"text\", \"text\": \"\"}}\n\n",
                b"event: content_block_delta\ndata: {\"type\": \"content_block_delta\", \"index\": 0, \"delta\": {\"type\": \"text_delta\", \"text\": \"Hello world\"}}\n\n",
                b"event: content_block_stop\ndata: {\"type\": \"content_block_stop\", \"index\": 0}\n\n",
                b"event: message_delta\ndata: {\"type\": \"message_delta\", \"delta\": {\"stop_reason\": \"end_turn\", \"stop_sequence\": null}, \"usage\": {\"output_tokens\": 5}}\n\n",
                b"event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
            ]
            for event in events:
                yield event
        return _gen()

    test_app.dependency_overrides[get_handle_request_fn] = lambda: mock_handle_request_fn
    
    try:
        response = authorized_client.post(
            "/v1/responses",
            json={
                "model": "nvidia_nim/meta/llama-3",
                "input": "hello",
                "stream": True
            }
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        
        lines = [line for line in response.iter_lines() if line]
        
        event_types = []
        data_payloads = []
        for line in lines:
            line_decoded = line.decode("utf-8") if isinstance(line, bytes) else line
            if line_decoded.startswith("event:"):
                event_types.append(line_decoded.split("event:")[1].strip())
            elif line_decoded.startswith("data:"):
                data_payloads.append(json.loads(line_decoded.split("data:")[1].strip()))
        
        failed_event = [d for d in data_payloads if d.get("type") == "response.failed"]
        if failed_event:
            raise AssertionError(f"Request failed response details: {failed_event[0]['response']}")

        assert "response.created" in event_types
        assert "response.output_text.delta" in event_types
        assert "response.completed" in event_types
        
        completed_event = [d for d in data_payloads if d.get("type") == "response.completed"][0]
        resp = completed_event["response"]
        assert resp["status"] == "completed"
        assert resp["output"][0]["content"][0]["text"] == "Hello world"
    finally:
        test_app.dependency_overrides.pop(get_handle_request_fn, None)


def test_build_codex_model_catalog():
    models_response = {
        "object": "list",
        "data": [
            {"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"},
            {"id": "anthropic/nvidia_nim/meta/llama-3", "display_name": "Llama 3"},
            {"id": "claude-3-freecc-no-thinking/nvidia_nim/meta/llama-3", "display_name": "Llama 3 (No Thinking)"}
        ]
    }
    
    catalog = build_codex_model_catalog(models_response)
    assert "models" in catalog
    model_slugs = [m["slug"] for m in catalog["models"]]
    
    assert "nvidia_nim/meta/llama-3" in model_slugs
    assert "claude-3-freecc-no-thinking/nvidia_nim/meta/llama-3" not in model_slugs


def test_update_codex_model_catalog(tmp_path):
    models_response = {
        "object": "list",
        "data": [
            {"id": "anthropic/nvidia_nim/meta/llama-3", "display_name": "Llama 3"}
        ]
    }
    
    with patch("pathlib.Path.home", return_value=tmp_path):
        update_codex_model_catalog(models_response)
        
        catalog_file = tmp_path / ".fcc" / "codex-model-catalog.json"
        assert catalog_file.exists()
        
        content = json.loads(catalog_file.read_text(encoding="utf-8"))
        assert "models" in content
        assert content["models"][0]["slug"] == "nvidia_nim/meta/llama-3"
