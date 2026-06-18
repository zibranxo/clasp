from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT.parent
sys.modules.pop("clasp", None)
sys.path = [p for p in sys.path if Path(p).resolve() != PARENT.resolve()]
sys.path.insert(0, str(ROOT))

import clasp.api.proxy_routes as proxy_routes


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    settings = SimpleNamespace(server=SimpleNamespace(api_key="freecc"))
    monkeypatch.setattr(proxy_routes, "get_settings", lambda: settings)

    app = FastAPI()
    app.include_router(proxy_routes.router)
    return app


@pytest.mark.asyncio
async def test_models_requires_bearer_auth(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_models_and_count_tokens_are_answered_locally(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer freecc"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        models = await client.get("/v1/models", headers=headers)
        count = await client.post(
            "/v1/messages/count_tokens",
            headers=headers,
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert models.status_code == 200
    assert models.json()["object"] == "list"
    assert count.status_code == 200
    assert count.json()["input_tokens"] >= 1


@pytest.mark.asyncio
async def test_messages_trivial_probe_json(app: FastAPI) -> None:
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer freecc"}
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1,
        "messages": [{"role": "user", "content": "ping"}],
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        non_stream = await client.post("/v1/messages", headers=headers, json=body)

    assert non_stream.status_code == 200
    assert non_stream.json()["type"] == "message"


@pytest.mark.asyncio
async def test_messages_non_probe_delegates_to_service_non_stream(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []

    async def _fake_handle_request(body, *, request_id=None, stream=None):
        calls.append({"body": body, "request_id": request_id, "stream": stream})
        return {"type": "message", "role": "assistant", "content": [{"type": "text", "text": "ok"}]}

    monkeypatch.setattr(proxy_routes, "is_local_probe", lambda _body: False)
    monkeypatch.setattr(proxy_routes, "handle_request", _fake_handle_request)

    transport = httpx.ASGITransport(app=app)
    headers = {
        "Authorization": "Bearer freecc",
        "X-Request-Id": "req_integration_123",
    }
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 64,
        "messages": [{"role": "user", "content": "hello"}],
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/messages", headers=headers, json=body)

    assert resp.status_code == 200
    assert resp.json()["role"] == "assistant"
    assert calls[0]["request_id"] == "req_integration_123"
    assert calls[0]["stream"] is False


@pytest.mark.asyncio
async def test_messages_non_probe_delegates_to_service_stream(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_stream():
        payload = json.dumps({"type": "message_start"})
        yield f"event: message_start\\ndata: {payload}\\n\\n"
        payload2 = json.dumps({"type": "message_stop"})
        yield f"event: message_stop\\ndata: {payload2}\\n\\n"

    async def _fake_handle_request(_body, *, request_id=None, stream=None):
        assert stream is True
        return _fake_stream()

    monkeypatch.setattr(proxy_routes, "is_local_probe", lambda _body: False)
    monkeypatch.setattr(proxy_routes, "handle_request", _fake_handle_request)

    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": "Bearer freecc"}
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 128,
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("POST", "/v1/messages", headers=headers, json=body) as resp:
            streamed = "".join([chunk async for chunk in resp.aiter_text()])
            request_id = resp.headers.get("X-Request-Id")

    assert resp.status_code == 200
    assert request_id is not None
    assert "event: message_start" in streamed
    assert "event: message_stop" in streamed
