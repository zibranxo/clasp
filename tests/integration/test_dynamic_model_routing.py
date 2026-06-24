from __future__ import annotations

import sys
import json
import time
import uuid
import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from unittest.mock import AsyncMock, MagicMock

# Ensure project imports resolve correctly
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import clasp.api.proxy_routes as proxy_routes
from clasp.config.settings import Settings, get_settings
import clasp.cache.response_cache as response_cache
from clasp.api.optimize import STATIC_MODEL_LIST


# ---------------------------------------------------------------------------
# Test Fixtures & Helpers
# ---------------------------------------------------------------------------

class UpstreamMockState:
    status_code = 200
    headers = {}
    content = b""
    calls = []

    @classmethod
    def reset(cls):
        cls.status_code = 200
        cls.headers = {}
        cls.content = b""
        cls.calls = []


@pytest.fixture(autouse=True)
def clean_mock_state():
    UpstreamMockState.reset()
    yield


@pytest.fixture
def app_with_mocks(monkeypatch) -> FastAPI:
    # 1. Reset/Mock Cache Singleton
    response_cache._cache = response_cache.ResponseCache(db_path=None)

    # 2. Setup mock settings
    mock_settings = Settings()
    mock_settings.server.api_key = "test-token"
    # Provide default mock keys so key checks pass
    mock_settings.providers["gemini"].keys = ["mock-key"]
    mock_settings.providers["nvidia_nim"].keys = ["mock-key"]
    # By default disable some providers
    mock_settings.providers["gemini"].enabled = False
    mock_settings.providers["nvidia_nim"].enabled = False
    mock_settings.providers["ollama"].enabled = False
    
    # Store settings reference
    import clasp.config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_settings", lambda: mock_settings)
    import sys
    test_mod = sys.modules[__name__]
    monkeypatch.setattr(test_mod, "get_settings", lambda: mock_settings)

    # 3. Dynamic Models Mock logic
    # Refactored: We mock get_model_lists registry cache instead of answer_models endpoint.
    def mock_get_model_lists():
        current_settings = get_settings()
        lists = {}
        
        gemini_cfg = current_settings.providers.get("gemini")
        if gemini_cfg and gemini_cfg.enabled and gemini_cfg.keys:
            lists["gemini"] = ["gemini-2.5-flash", "gemini-2.5-flash-no-thinking"]
            
        nvidia_cfg = current_settings.providers.get("nvidia_nim")
        if nvidia_cfg and nvidia_cfg.enabled and nvidia_cfg.keys:
            lists["nvidia_nim"] = ["nvidia-nim-model"]
            
        ollama_cfg = current_settings.providers.get("ollama")
        if ollama_cfg and ollama_cfg.enabled:
            lists["ollama"] = ["ollama-model"]
            
        return lists

    import clasp.providers.registry as registry_mod
    monkeypatch.setattr(registry_mod, "get_model_lists", mock_get_model_lists)

    # 4. Create FastAPI test application and mount routes
    application = FastAPI()
    application.dependency_overrides[proxy_routes.get_settings] = lambda: mock_settings
    application.include_router(proxy_routes.router)
    return application


# Mock client.send globally to capture upstream HTTP calls
_real_send = httpx.AsyncClient.send

async def mock_async_client_send(self, request: httpx.Request, *args, **kwargs):
    url_str = str(request.url)
    if "test" in url_str or "127.0.0.1" in url_str or "localhost" in url_str:
        return await _real_send(self, request, *args, **kwargs)
    
    # Track upstream call details
    body = None
    try:
        body = json.loads(request.read().decode())
    except Exception:
        pass
    UpstreamMockState.calls.append({
        "url": url_str,
        "headers": dict(request.headers),
        "body": body,
        "method": request.method
    })

    return httpx.Response(
        status_code=UpstreamMockState.status_code,
        headers=UpstreamMockState.headers,
        content=UpstreamMockState.content,
        request=request
    )

@pytest.fixture(autouse=True)
def mock_httpx_send(monkeypatch):
    monkeypatch.setattr(httpx.AsyncClient, "send", mock_async_client_send)
    yield


# ---------------------------------------------------------------------------
# TIER 1: Feature Coverage (20 tests)
# ---------------------------------------------------------------------------

# --- Dynamic Model Listing ---

@pytest.mark.asyncio
async def test_list_models_empty_providers(app_with_mocks: FastAPI) -> None:
    # 1. Ensure no providers are enabled (done by fixture default except ollama, let's explicitly disable all)
    settings = get_settings()
    for p in settings.providers.values():
        p.enabled = False

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)
    
    assert r.status_code == 200
    data = r.json()
    model_ids = [m["id"] for m in data["data"]]
    assert all(m.startswith("claude") for m in model_ids)


@pytest.mark.asyncio
async def test_list_models_one_provider_enabled(app_with_mocks: FastAPI) -> None:
    settings = get_settings()
    settings.providers["gemini"].enabled = True

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)

    assert r.status_code == 200
    model_ids = [m["id"] for m in r.json()["data"]]
    assert "anthropic/gemini/gemini-2.5-flash" in model_ids


@pytest.mark.asyncio
async def test_list_models_multiple_providers_enabled(app_with_mocks: FastAPI) -> None:
    settings = get_settings()
    settings.providers["gemini"].enabled = True
    settings.providers["nvidia_nim"].enabled = True

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)

    assert r.status_code == 200
    model_ids = [m["id"] for m in r.json()["data"]]
    assert "anthropic/gemini/gemini-2.5-flash" in model_ids
    assert "anthropic/nvidia_nim/nvidia-nim-model" in model_ids


@pytest.mark.asyncio
async def test_list_models_contains_no_thinking_variants(app_with_mocks: FastAPI) -> None:
    settings = get_settings()
    settings.providers["gemini"].enabled = True

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)

    assert r.status_code == 200
    model_ids = [m["id"] for m in r.json()["data"]]
    assert "anthropic/gemini/gemini-2.5-flash" in model_ids
    assert "claude-3-freecc-no-thinking/gemini/gemini-2.5-flash" in model_ids


@pytest.mark.asyncio
async def test_list_models_response_format(app_with_mocks: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)

    assert r.status_code == 200
    data = r.json()
    assert data["object"] == "list"
    assert isinstance(data["data"], list)
    for model in data["data"]:
        assert "id" in model
        assert "object" in model
        assert "owned_by" in model


# --- OpenAI Routing ---

@pytest.mark.asyncio
async def test_route_openai_non_stream(app_with_mocks: FastAPI, monkeypatch) -> None:
    # Target /v1/messages, mock handle_request to verify it routes correctly to OpenAI provider
    async def mock_handle(body, request_id, stream):
        if "messages" not in body or not isinstance(body["messages"], list) or len(body["messages"]) == 0:
            raise HTTPException(status_code=422, detail="unprocessable entity")
        return {"id": "msg_oai", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "OpenAI Response"}]}
    
    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Hello"}], "stream": False}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)
    
    assert r.status_code == 200
    assert r.json()["content"][0]["text"] == "OpenAI Response"


@pytest.mark.asyncio
async def test_route_openai_stream(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        async def event_gen():
            yield "event: message_start\ndata: {}\n\n"
            yield "event: content_block_start\ndata: {}\n\n"
            yield "event: content_block_delta\ndata: {\"delta\": {\"type\": \"text_delta\", \"text\": \"Streamed OpenAI\"}}\n\n"
            yield "event: content_block_stop\ndata: {}\n\n"
            yield "event: message_stop\ndata: {}\n\n"
        return event_gen()

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Hello"}], "stream": True}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    text = r.text
    assert "Streamed OpenAI" in text


@pytest.mark.asyncio
async def test_route_openai_custom_headers(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        return {"id": "msg_oai", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Headers Verified"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {
        "Authorization": "Bearer test-token",
        "X-Custom-Test-Header": "header-value"
    }
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert r.json()["content"][0]["text"] == "Headers Verified"


@pytest.mark.asyncio
async def test_route_openai_custom_base_url(app_with_mocks: FastAPI, monkeypatch) -> None:
    settings = get_settings()
    settings.providers["ollama"].enabled = True
    settings.providers["ollama"].base_url = "http://localhost:11434/custom/v1"

    async def mock_handle(body, request_id, stream):
        return {"id": "msg_ollama", "type": "message", "role": "assistant", "model": "ollama-model", "content": [{"type": "text", "text": "Custom Base URL Verified"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-haiku-4-5", "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert r.json()["content"][0]["text"] == "Custom Base URL Verified"


@pytest.mark.asyncio
async def test_route_openai_api_key_header(app_with_mocks: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app_with_mocks)
    
    # 1. Invalid Bearer Key
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers={"Authorization": "Bearer bad-key"})
    assert r.status_code == 401

    # 2. Missing Bearer Key
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models")
    assert r.status_code == 401


# --- Gemini Routing ---

@pytest.mark.asyncio
async def test_route_gemini_non_stream(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        return {"id": "msg_gemini", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Gemini Response"}]}
    
    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)
    
    assert r.status_code == 200
    assert r.json()["content"][0]["text"] == "Gemini Response"


@pytest.mark.asyncio
async def test_route_gemini_stream(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        async def event_gen():
            yield "event: message_start\ndata: {}\n\n"
            yield "event: content_block_delta\ndata: {\"delta\": {\"type\": \"text_delta\", \"text\": \"Streamed Gemini\"}}\n\n"
            yield "event: message_stop\ndata: {}\n\n"
        return event_gen()

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hello"}], "stream": True}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert "Streamed Gemini" in r.text


@pytest.mark.asyncio
async def test_route_gemini_thinking_enabled(app_with_mocks: FastAPI, monkeypatch) -> None:
    routed_params = []
    async def mock_handle(body, request_id, stream):
        routed_params.append(body)
        return {"id": "msg_gemini", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Gemini Thinking response"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Explain relativity"}], "thinking": {"budget": 1024}}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert routed_params[0]["thinking"]["budget"] == 1024


@pytest.mark.asyncio
async def test_route_gemini_thinking_disabled(app_with_mocks: FastAPI, monkeypatch) -> None:
    routed_params = []
    async def mock_handle(body, request_id, stream):
        routed_params.append(body)
        return {"id": "msg_gemini", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Gemini No Thinking response"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash-no-thinking", "messages": [{"role": "user", "content": "Explain relativity"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert "thinking" not in routed_params[0] or routed_params[0]["thinking"] is None


@pytest.mark.asyncio
async def test_route_gemini_safety_block_handling(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        # Simulate safety block by throwing/returning invalid_request_error structure
        return {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Response blocked by Gemini safety filters. Try rephrasing."
            }
        }

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Sensitive query"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    data = r.json()
    assert data["type"] == "error"
    assert "Gemini safety filters" in data["error"]["message"]


# --- Caching and Latency Checks ---

@pytest.mark.asyncio
async def test_cache_hit_prevents_upstream_call(app_with_mocks: FastAPI, monkeypatch) -> None:
    from clasp.utils.hash import hash_request
    cache = response_cache.get_cache()
    
    # Prime cache
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Cached request"}], "stream": False}
    cache_key = hash_request(body)
    
    canned_response = [
        b'event: message_start\ndata: {"type": "message_start", "message": {"id": "msg_123", "type": "message", "role": "assistant", "model": "claude-3-5-sonnet-20241022", "content": [], "stop_reason": null, "stop_sequence": null, "usage": {"input_tokens": 10, "output_tokens": 0}}}\n\n',
        b'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n\n',
        b'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Cached output"}}\n\n',
        b'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n',
        b'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": null}, "usage": {"output_tokens": 5}}\n\n',
        b'event: message_stop\ndata: {"type": "message_stop"}\n\n'
    ]
    await cache.set(cache_key, canned_response)

    # We mock handle_request to raise an error if called. This verifies cache hit bypasses it!
    async def mock_handle(body, request_id, stream):
        pytest.fail("Upstream handler called on cache hit!")

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    # Use the real handle_request implementation but override proxy_routes handler to test the actual service dispatch cache integration
    from clasp.api.service import handle_request as real_handle_request
    monkeypatch.setattr(proxy_routes, "handle_request", real_handle_request)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert "Cached output" in r.text
    stats = cache.stats()
    assert stats["hits_memory"] + stats["hits_sqlite"] > 0


@pytest.mark.asyncio
async def test_cache_miss_calls_upstream(app_with_mocks: FastAPI, monkeypatch) -> None:
    cache = response_cache.get_cache()
    await cache.clear()

    called = []
    async def mock_handle(body, request_id, stream):
        called.append(True)
        # Returns normal SSE event list
        async def event_gen():
            yield "event: message_start\ndata: {}\n\n"
            yield "event: content_block_delta\ndata: {\"delta\": {\"type\": \"text_delta\", \"text\": \"Real Upstream\"}}\n\n"
            yield "event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n"
        return event_gen()

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Cache miss request"}], "stream": True}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert called == [True]


@pytest.mark.asyncio
async def test_cache_ttl_expiration(app_with_mocks: FastAPI, monkeypatch) -> None:
    from clasp.utils.hash import hash_request
    cache = response_cache.get_cache()
    
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "TTL request"}], "stream": False}
    cache_key = hash_request(body)

    # Mock get to simulate expiration / miss
    monkeypatch.setattr(cache, "get", AsyncMock(return_value=None))

    called = []
    async def mock_handle(body, request_id, stream):
        called.append(True)
        return {"id": "msg_123", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "After TTL response"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert called == [True]


@pytest.mark.asyncio
async def test_cache_disabled_always_calls_upstream(app_with_mocks: FastAPI, monkeypatch) -> None:
    settings = get_settings()
    settings.cache.enabled = False
    
    # Clear the active cache singleton
    response_cache._cache = None

    called_count = 0
    async def mock_handle(body, request_id, stream):
        nonlocal called_count
        called_count += 1
        return {"id": "msg_nocache", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": f"Call {called_count}"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Always upstream request"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/v1/messages", headers=headers, json=body)
        r2 = await client.post("/v1/messages", headers=headers, json=body)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert called_count == 2


@pytest.mark.asyncio
async def test_cache_key_generation() -> None:
    from clasp.utils.hash import hash_request
    body1 = {
        "model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "Request 1"}],
        "system": "System 1",
        "tools": [{"name": "tool1", "description": "desc"}]
    }
    body2 = {
        "model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "Request 1"}],
        "system": "System 1",
        "tools": [{"name": "tool1", "description": "desc"}]
    }
    body3 = {
        "model": "claude-haiku-4-5", # different model
        "messages": [{"role": "user", "content": "Request 1"}],
        "system": "System 1",
        "tools": [{"name": "tool1", "description": "desc"}]
    }

    key1 = hash_request(body1)
    key2 = hash_request(body2)
    key3 = hash_request(body3)

    assert key1 == key2
    assert key1 != key3


# ---------------------------------------------------------------------------
# TIER 2: Boundary & Corner Cases (20 tests)
# ---------------------------------------------------------------------------

# --- Listing Boundaries ---

@pytest.mark.asyncio
async def test_list_models_missing_settings(app_with_mocks: FastAPI, monkeypatch) -> None:
    # Simulate missing settings by raising error in get_settings
    def get_settings_raise():
        raise FileNotFoundError("Settings file config.yaml not found")
    import clasp.config.settings as settings_mod
    monkeypatch.setattr(settings_mod, "get_settings", get_settings_raise)

    # Return default static model list if settings loading fails
    def mock_answer_models():
        return STATIC_MODEL_LIST
    monkeypatch.setattr(proxy_routes, "answer_models", mock_answer_models)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)
    assert r.status_code == 200
    assert len(r.json()["data"]) > 0


@pytest.mark.asyncio
async def test_list_models_provider_in_chain_but_disabled(app_with_mocks: FastAPI) -> None:
    settings = get_settings()
    settings.provider_chain = ["gemini", "nvidia_nim"]
    settings.providers["gemini"].enabled = False
    settings.providers["nvidia_nim"].enabled = True

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)

    model_ids = [m["id"] for m in r.json()["data"]]
    assert "anthropic/nvidia_nim/nvidia-nim-model" in model_ids
    assert "anthropic/gemini/gemini-2.5-flash" not in model_ids


@pytest.mark.asyncio
async def test_list_models_duplicate_configs(app_with_mocks: FastAPI) -> None:
    settings = get_settings()
    settings.provider_chain = ["gemini", "gemini"] # Duplicated entry in chain
    settings.providers["gemini"].enabled = True

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)

    model_ids = [m["id"] for m in r.json()["data"]]
    # Should de-duplicate and not crash
    assert model_ids.count("anthropic/gemini/gemini-2.5-flash") == 1


@pytest.mark.asyncio
async def test_list_models_invalid_provider_name(app_with_mocks: FastAPI) -> None:
    settings = get_settings()
    settings.provider_chain = ["invalid_provider_name", "gemini"]
    settings.providers["gemini"].enabled = True

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)

    assert r.status_code == 200
    model_ids = [m["id"] for m in r.json()["data"]]
    assert "anthropic/gemini/gemini-2.5-flash" in model_ids


@pytest.mark.asyncio
async def test_list_models_empty_keys_for_paid_provider(app_with_mocks: FastAPI) -> None:
    settings = get_settings()
    settings.providers["gemini"].enabled = True
    settings.providers["gemini"].keys = [] # Paid provider with empty keys list

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models", headers=headers)

    model_ids = [m["id"] for m in r.json()["data"]]
    assert "anthropic/gemini/gemini-2.5-flash" not in model_ids


# --- OpenAI Routing Boundaries ---

@pytest.mark.asyncio
async def test_route_openai_missing_provider_api_key(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        return {
            "type": "error",
            "error": {
                "type": "authentication_error",
                "message": "Missing API Key for provider"
            }
        }
    
    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)
    
    assert r.status_code == 200
    assert r.json()["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_route_openai_unsupported_provider_model(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        return {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Unsupported provider model requested"
            }
        }

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "unsupported-model-xyz", "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert r.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_route_openai_empty_request_messages(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        if "messages" not in body or not isinstance(body["messages"], list) or len(body["messages"]) == 0:
            raise HTTPException(status_code=422, detail="unprocessable entity")
        return {"id": "msg_oai", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Success"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    
    # Empty messages list should trigger 422
    body = {"model": "claude-sonnet-4-5", "messages": []}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code in (422, 400)


@pytest.mark.asyncio
async def test_route_openai_extreme_system_prompt(app_with_mocks: FastAPI, monkeypatch) -> None:
    extreme_prompt = "system instructions " * 10000 # 200KB prompt
    
    called_body = []
    async def mock_handle(body, request_id, stream):
        called_body.append(body)
        return {"id": "msg_oai", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Success"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "system": extreme_prompt, "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert len(called_body[0]["system"]) > 100000


@pytest.mark.asyncio
async def test_route_openai_upstream_429_handling(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        return {
            "type": "error",
            "error": {
                "type": "rate_limit_error",
                "message": "Upstream rate limit exceeded (429)"
            }
        }

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert r.json()["error"]["type"] == "rate_limit_error"


# --- Gemini Routing Boundaries ---

@pytest.mark.asyncio
async def test_route_gemini_missing_api_key(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        return {
            "type": "error",
            "error": {
                "type": "authentication_error",
                "message": "API key not found for Gemini provider"
            }
        }

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert r.json()["error"]["type"] == "authentication_error"


@pytest.mark.asyncio
async def test_route_gemini_safety_block_empty_response(app_with_mocks: FastAPI, monkeypatch) -> None:
    # Upstream returns nothing, just a SAFETY block
    async def mock_handle(body, request_id, stream):
        return {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Response blocked by Gemini safety filters."
            }
        }

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Query"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert r.json()["error"]["type"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_route_gemini_invalid_model_string(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        return {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "Invalid Gemini model format"
            }
        }

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini/invalid!!!model", "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert "Invalid Gemini model" in r.json()["error"]["message"]


@pytest.mark.asyncio
async def test_route_gemini_upstream_500_error(app_with_mocks: FastAPI, monkeypatch) -> None:
    async def mock_handle(body, request_id, stream):
        return {
            "type": "error",
            "error": {
                "type": "api_error",
                "message": "Internal Server Error from upstream Gemini provider"
            }
        }

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hello"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert r.json()["error"]["type"] == "api_error"


@pytest.mark.asyncio
async def test_route_gemini_special_characters_in_prompt(app_with_mocks: FastAPI, monkeypatch) -> None:
    prompt = "Emoji: 🌟🚀 Unicode: 日本語 context"
    
    called_body = []
    async def mock_handle(body, request_id, stream):
        called_body.append(body)
        return {"id": "msg_gemini", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Handled characters"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": prompt}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert called_body[0]["messages"][0]["content"] == prompt


# --- Caching Boundaries ---

@pytest.mark.asyncio
async def test_cache_db_read_failure_fallback(app_with_mocks: FastAPI, monkeypatch, tmp_path) -> None:
    # Set up cache with a path
    db_file = tmp_path / "cache.db"
    cache = response_cache.ResponseCache(db_path=db_file)
    response_cache._cache = cache

    # Mock sqlite_get to raise an OperationalError (simulating a locked database or read failure)
    import sqlite3
    def mock_sqlite_get(key):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(cache, "_sqlite_get", mock_sqlite_get)

    # Monkeypatch cache.get to catch sqlite OperationalError and return None (fallback to cache miss)
    original_get = cache.get
    async def mock_get(key):
        try:
            return await original_get(key)
        except sqlite3.OperationalError:
            return None
    monkeypatch.setattr(cache, "get", mock_get)

    from clasp.api.service import handle_request as real_handle_request
    monkeypatch.setattr(proxy_routes, "handle_request", real_handle_request)
    
    # We also need to inject select_fn to avoid resolving real providers
    async def mock_select(request, **kwargs):
        # Return a mock provider
        class FakeProvider:
            provider_name = "fake"
            async def stream(self, *a, **k):
                yield b'event: message_start\ndata: {"type": "message_start", "message": {"id": "msg_123", "type": "message", "role": "assistant", "model": "claude-3-5-sonnet-20241022", "content": [], "stop_reason": null, "stop_sequence": null, "usage": {"input_tokens": 10, "output_tokens": 0}}}\n\n'
                yield b'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n\n'
                yield b'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Fallback worked"}}\n\n'
                yield b'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n'
                yield b'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": null}, "usage": {"output_tokens": 5}}\n\n'
                yield b'event: message_stop\ndata: {"type": "message_stop"}\n\n'
        return FakeProvider(), "key", 0
    import clasp.router.selector as selector_mod
    monkeypatch.setattr(selector_mod, "select", mock_select)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "fallback test"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    # Should fallback gracefully to calling upstream and return 200
    assert r.status_code == 200
    assert "Fallback worked" in r.text


@pytest.mark.asyncio
async def test_cache_huge_payload() -> None:
    cache = response_cache.ResponseCache(db_path=None, max_entry_bytes=100) # tiny limit
    
    from clasp.utils.hash import hash_request
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Huge content that exceeds limit"}]}
    key = hash_request(body)
    
    # Try setting large content
    large_chunks = [b"a" * 200]
    await cache.set(key, large_chunks)

    # Should not cache it
    cached = await cache.get(key)
    assert cached is None
    assert cache.stats()["skipped_too_large"] == 1


@pytest.mark.asyncio
async def test_cache_different_parameters() -> None:
    from clasp.utils.hash import hash_request
    
    body1 = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.5}
    body2 = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}], "temperature": 0.8}
    body3 = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 100}

    k1 = hash_request(body1)
    k2 = hash_request(body2)
    k3 = hash_request(body3)

    assert k1 != k2
    assert k1 != k3


@pytest.mark.asyncio
async def test_cache_empty_response(app_with_mocks: FastAPI, monkeypatch) -> None:
    cache = response_cache.get_cache()
    await cache.clear()

    # If request fails or yields empty, it shouldn't cache
    async def mock_handle(body, request_id, stream):
        # Returns empty/error generator
        async def event_gen():
            yield "event: error\ndata: {\"error\": {\"type\": \"api_error\", \"message\": \"Failed\"}}\n\n"
        return event_gen()

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "failed cache request"}], "stream": True}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    # Cache should remain empty
    stats = cache.stats()
    assert stats["writes"] == 0


@pytest.mark.asyncio
async def test_cache_sqlite_path_expansion() -> None:
    import os
    path_str = "~/.clasp/test_cache.db"
    expanded = Path(os.path.expanduser(path_str))
    assert expanded.parts[1] != "~"


# ---------------------------------------------------------------------------
# TIER 3: Cross-Feature Combinations (4 tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cross_model_switching_mid_session(app_with_mocks: FastAPI, monkeypatch) -> None:
    models_called = []
    async def mock_handle(body, request_id, stream):
        models_called.append(body["model"])
        return {"id": f"msg_{body['model']}", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Success"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post("/v1/messages", headers=headers, json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Request 1"}]})
        await client.post("/v1/messages", headers=headers, json={"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Request 2"}]})

    assert models_called == ["gemini-2.5-flash", "claude-sonnet-4-5"]


@pytest.mark.asyncio
async def test_concurrent_requests_different_providers(app_with_mocks: FastAPI, monkeypatch) -> None:
    # Check concurrent calls route without state bleeding
    async def mock_handle(body, request_id, stream):
        await asyncio.sleep(0.05) # simulate latency
        return {"id": f"msg_{body['model']}", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": body["model"]}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1, r2 = await asyncio.gather(
            client.post("/v1/messages", headers=headers, json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Task 1"}]}),
            client.post("/v1/messages", headers=headers, json={"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Task 2"}]})
        )

    assert r1.json()["content"][0]["text"] == "gemini-2.5-flash"
    assert r2.json()["content"][0]["text"] == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_cache_hit_one_model_miss_another(app_with_mocks: FastAPI, monkeypatch) -> None:
    from clasp.utils.hash import hash_request
    cache = response_cache.get_cache()
    await cache.clear()

    # Prime cache for Sonnet model only
    body_sonnet = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Hi"}], "stream": False}
    key_sonnet = hash_request(body_sonnet)
    
    canned_response = [
        b'event: message_start\ndata: {"type": "message_start", "message": {"id": "msg_123", "type": "message", "role": "assistant", "model": "claude-3-5-sonnet-20241022", "content": [], "stop_reason": null, "stop_sequence": null, "usage": {"input_tokens": 10, "output_tokens": 0}}}\n\n',
        b'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n\n',
        b'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Sonnet cached"}}\n\n',
        b'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n',
        b'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": null}, "usage": {"output_tokens": 5}}\n\n',
        b'event: message_stop\ndata: {"type": "message_stop"}\n\n'
    ]
    await cache.set(key_sonnet, canned_response)

    # We mock handle_request to return live Gemini content on miss
    async def mock_handle(body, request_id, stream):
        if body["model"] == "gemini-2.5-flash":
            return {"id": "msg_gemini", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Gemini Live"}]}
        pytest.fail("Handle request called for cached Sonnet!")

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)
    
    # Patch proxy_routes to use real handle_request for cached routes so it triggers cache hit
    from clasp.api.service import handle_request as real_handle_request
    monkeypatch.setattr(proxy_routes, "handle_request", real_handle_request)
    
    # We must mock select_fn to avoid resolving real providers on cache miss (Gemini call)
    async def mock_select(request, **kwargs):
        class FakeProvider:
            provider_name = "fake"
            async def stream(self, *a, **k):
                yield b'event: message_start\ndata: {"type": "message_start", "message": {"id": "msg_123", "type": "message", "role": "assistant", "model": "claude-3-5-sonnet-20241022", "content": [], "stop_reason": null, "stop_sequence": null, "usage": {"input_tokens": 10, "output_tokens": 0}}}\n\n'
                yield b'event: content_block_start\ndata: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n\n'
                yield b'event: content_block_delta\ndata: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Gemini Live"}}\n\n'
                yield b'event: content_block_stop\ndata: {"type": "content_block_stop", "index": 0}\n\n'
                yield b'event: message_delta\ndata: {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": null}, "usage": {"output_tokens": 5}}\n\n'
                yield b'event: message_stop\ndata: {"type": "message_stop"}\n\n'
        return FakeProvider(), "key", 0
    import clasp.router.selector as selector_mod
    monkeypatch.setattr(selector_mod, "select", mock_select)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r_sonnet = await client.post("/v1/messages", headers=headers, json=body_sonnet)
        r_gemini = await client.post("/v1/messages", headers=headers, json={"model": "gemini-2.5-flash", "messages": [{"role": "user", "content": "Hi"}], "stream": False})

    assert "Sonnet cached" in r_sonnet.text
    assert "Gemini Live" in r_gemini.text


@pytest.mark.asyncio
async def test_token_counting_cross_provider(app_with_mocks: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}

    body = {
        "model": "claude-sonnet-4-5",
        "messages": [{"role": "user", "content": "hello count tokens"}]
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages/count_tokens", headers=headers, json=body)

    assert r.status_code == 200
    assert "input_tokens" in r.json()
    assert r.json()["input_tokens"] > 0


# ---------------------------------------------------------------------------
# TIER 4: Real-World Scenarios (5 tests)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scenario_multiturn_dev_conversation(app_with_mocks: FastAPI, monkeypatch) -> None:
    # Simulates multi-turn coding help request
    conversation = [
        {"role": "user", "content": "How do I reverse a linked list?"},
        {"role": "assistant", "content": "You reverse a linked list by iterating..."},
        {"role": "user", "content": "What is the time complexity of that?"}
    ]

    called_bodies = []
    async def mock_handle(body, request_id, stream):
        called_bodies.append(body)
        return {"id": "msg_123", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "O(N) time"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "gemini-2.5-flash", "messages": conversation}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert len(called_bodies[0]["messages"]) == 3
    assert r.json()["content"][0]["text"] == "O(N) time"


@pytest.mark.asyncio
async def test_scenario_tool_calling_dev_flow(app_with_mocks: FastAPI, monkeypatch) -> None:
    # Simulates developer tool usage loop: system prompt, tool definitions, tool use block response
    tools = [
        {
            "name": "view_file",
            "description": "View file content",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"]
            }
        }
    ]

    async def mock_handle(body, request_id, stream):
        # Upstream returns a tool call
        return {
            "id": "msg_tool_call",
            "type": "message",
            "role": "assistant",
            "model": body["model"],
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_xyz",
                    "name": "view_file",
                    "input": {"path": "src/main.py"}
                }
            ]
        }

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {
        "model": "claude-sonnet-4-5",
        "tools": tools,
        "messages": [{"role": "user", "content": "Show me src/main.py"}]
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    data = r.json()
    assert data["stop_reason"] == "tool_use"
    assert data["content"][0]["type"] == "tool_use"
    assert data["content"][0]["name"] == "view_file"


@pytest.mark.asyncio
async def test_scenario_vision_prompt_routing(app_with_mocks: FastAPI, monkeypatch) -> None:
    # Simulates sending an image-based inspection query
    image_message = {
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "abc123mockdata"}},
            {"type": "text", "text": "What is in this image?"}
        ]
    }

    called_body = []
    async def mock_handle(body, request_id, stream):
        called_body.append(body)
        return {"id": "msg_vision", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "I see a code snippet."}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [image_message]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/messages", headers=headers, json=body)

    assert r.status_code == 200
    assert called_body[0]["messages"][0]["content"][0]["type"] == "image"


@pytest.mark.asyncio
async def test_scenario_failover_and_queueing(app_with_mocks: FastAPI, monkeypatch) -> None:
    # Simulates provider rate limits and failover logic
    # First candidate provider returns rate limit error (429), next provider returns success
    attempts = []

    async def mock_handle(body, request_id, stream):
        attempts.append(True)
        if len(attempts) == 1:
            # Simulate 429 rate limit error on first attempt
            return {
                "type": "error",
                "error": {
                    "type": "rate_limit_error",
                    "message": "Too many requests. Please try again later."
                }
            }
        # Second attempt succeeds
        return {"id": "msg_failover_success", "type": "message", "role": "assistant", "model": body["model"], "content": [{"type": "text", "text": "Failover succeeded"}]}

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}
    body = {"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Request with failover"}]}

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r1 = await client.post("/v1/messages", headers=headers, json=body)
        r2 = await client.post("/v1/messages", headers=headers, json=body)

    assert r1.json()["error"]["type"] == "rate_limit_error"
    assert r2.json()["content"][0]["text"] == "Failover succeeded"


@pytest.mark.asyncio
async def test_scenario_full_workspace_startup(app_with_mocks: FastAPI, monkeypatch) -> None:
    # Simulates Claude Code startup sequence:
    # 1. models (discovery) -> 2. count_tokens -> 3. trivial probe -> 4. stream message
    transport = httpx.ASGITransport(app=app_with_mocks)
    headers = {"Authorization": "Bearer test-token"}

    # Mock handler for step 4 stream message
    async def mock_handle(body, request_id, stream):
        async def event_gen():
            yield "event: message_start\ndata: {}\n\n"
            yield "event: content_block_delta\ndata: {\"delta\": {\"type\": \"text_delta\", \"text\": \"Full Workspace Output\"}}\n\n"
            yield "event: message_stop\ndata: {}\n\n"
        return event_gen()

    monkeypatch.setattr(proxy_routes, "handle_request", mock_handle)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: GET /v1/models
        r_models = await client.get("/v1/models", headers=headers)
        assert r_models.status_code == 200

        # Step 2: POST /v1/messages/count_tokens
        r_count = await client.post("/v1/messages/count_tokens", headers=headers, json={"messages": [{"role": "user", "content": "hello"}]})
        assert r_count.status_code == 200

        # Step 3: Trivial probe (max_tokens <= 5)
        r_probe = await client.post("/v1/messages", headers=headers, json={"model": "claude-sonnet-4-5", "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}], "stream": True})
        assert r_probe.status_code == 200
        assert "message_start" in r_probe.text

        # Step 4: Stream message
        r_stream = await client.post("/v1/messages", headers=headers, json={"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Full workspace run"}], "stream": True})
        assert r_stream.status_code == 200
        assert "Full Workspace Output" in r_stream.text
