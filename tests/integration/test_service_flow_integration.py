from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT.parent
sys.modules.pop("clasp", None)
sys.path = [p for p in sys.path if Path(p).resolve() != PARENT.resolve()]
sys.path.insert(0, str(ROOT))

import clasp.api.service as service
from clasp.api.detect import RequestType
from clasp.router.capability import Capability
from clasp.router.selector import select as _real_select, _default_config
from clasp.router import model_map as model_map_mod
from clasp.router import capability as capability_mod
import clasp.router.selector as selector_mod


class _FakeProvider:
    provider_name = "fake_provider"

    def __init__(self, *, fail_stream: bool = False):
        self.fail_stream = fail_stream

    async def complete(self, request, **kwargs):
        return {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
        }

    async def stream(self, request, **kwargs):
        if self.fail_stream:
            raise RuntimeError("boom")
        yield b"event: message_start\ndata: {}\n\n"
        yield b"event: message_stop\ndata: {}\n\n"

class _FakeKeyPool:
    async def pick_key(self, tokens):
        return "key-0", 0

class _FakeRegistry:
    def __init__(self, provider):
        self._provider = provider

    def all_enabled(self):
        return ["fake_provider"] if self._provider else []

    def get(self, name):
        return self._provider

    def get_key_pool(self, name):
        if self._provider is None:
            return None
        return _FakeKeyPool()


@pytest.mark.asyncio
async def test_handle_request_returns_no_provider_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = _FakeRegistry(None)

    async def _select_fn(r, **k):
        config = _default_config(registry)
        return await _real_select(r, registry=registry, config=config, **k)

    body = {"model": "claude", "messages": [{"role": "user", "content": "hi"}]}

    non_stream = await service.handle_request(body, request_id="req_x", stream=False, select_fn=_select_fn)
    stream = await service.handle_request(body, request_id="req_x", stream=True, select_fn=_select_fn)
    chunks = [chunk async for chunk in stream]

    assert non_stream["type"] == "error"
    assert "No provider currently available for this request." in non_stream["error"]["message"]
    assert len(chunks) == 1
    assert chunks[0].startswith(b"event: error")


@pytest.mark.asyncio
async def test_handle_request_success_non_stream_and_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _FakeProvider()
    registry = _FakeRegistry(provider)
    
    monkeypatch.setattr(model_map_mod, "resolve_model", lambda *a, **k: "fake-model")
    monkeypatch.setattr(capability_mod, "get", lambda *a, **k: Capability(supports_tools=True, supports_vision=True, supports_thinking=False, max_context_tokens=100000))
    
    async def _select_fn(r, **k):
        config = _default_config(registry)
        return await _real_select(r, registry=registry, config=config, **k)

    body = {"model": "claude", "messages": [{"role": "user", "content": "hi"}]}

    non_stream = await service.handle_request(body, request_id="req_ok", stream=False, select_fn=_select_fn)
    stream = await service.handle_request(body, request_id="req_ok", stream=True, select_fn=_select_fn)
    chunks = [chunk async for chunk in stream]

    assert non_stream.get("role") == "assistant"
    assert any(b"message_start" in c for c in chunks)
    assert any(b"message_stop" in c for c in chunks)


@pytest.mark.asyncio
async def test_handle_request_stream_error_is_wrapped_as_sse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from clasp.cache.response_cache import get_cache
    cache = get_cache()
    if cache is not None:
        await cache.clear()

    provider = _FakeProvider(fail_stream=True)
    registry = _FakeRegistry(provider)

    monkeypatch.setattr(model_map_mod, "resolve_model", lambda *a, **k: "fake-model")
    monkeypatch.setattr(capability_mod, "get", lambda *a, **k: Capability(supports_tools=True, supports_vision=True, supports_thinking=False, max_context_tokens=100000))

    async def _select_fn(r, **k):
        config = _default_config(registry)
        return await _real_select(r, registry=registry, config=config, **k)

    body = {"model": "claude", "messages": [{"role": "user", "content": "hi"}]}

    stream = await service.handle_request(body, request_id="req_err", stream=True, select_fn=_select_fn)
    chunks = [chunk async for chunk in stream]

    assert len(chunks) == 1
    assert chunks[0].startswith(b"event: error")

    payload = json.loads(chunks[0].split(b"data: ", 1)[1])
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "api_error"


@pytest.mark.asyncio
async def test_handle_request_with_prefixed_no_thinking_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from clasp.cache.response_cache import get_cache
    cache = get_cache()
    if cache is not None:
        await cache.clear()

    monkeypatch.setattr(model_map_mod, "resolve_model", lambda *a, **k: "some-model")

    from types import SimpleNamespace
    provider = _FakeProvider()
    registry = _FakeRegistry(provider)

    class _FakeSettings:
        providers = {"fake_provider": SimpleNamespace(keys=["fake-key"], enabled=True)}
        provider_chain = ["fake_provider"]
        class routing:
            models = SimpleNamespace(opus="", sonnet="", haiku="", fable="", default="")
            by_type = SimpleNamespace(think="", long_context="", background="", vision="")

    monkeypatch.setattr(capability_mod, "get", lambda *a, **k: Capability(supports_tools=True, supports_vision=True, supports_thinking=False, max_context_tokens=100000))

    async def _select_fn(r, **k):
        config = _default_config(registry)
        return await _real_select(r, registry=registry, config=config, settings=_FakeSettings(), **k)

    body = {
        "model": "claude-3-freecc-no-thinking/fake_provider/some-model",
        "messages": [{"role": "user", "content": "hi"}],
        "thinking": {"type": "enabled", "budget_tokens": 1024}
    }

    streamed_request_body = None

    async def fake_stream(req, **k):
        nonlocal streamed_request_body
        streamed_request_body = req.body
        yield b"event: message_start\ndata: {}\n\n"
        yield b"event: message_stop\ndata: {}\n\n"

    monkeypatch.setattr(provider, "stream", fake_stream)

    stream = await service.handle_request(body, request_id="req_prefixed", stream=True, select_fn=_select_fn)
    chunks = [chunk async for chunk in stream]

    assert len(chunks) == 2
    assert streamed_request_body is not None
    assert "thinking" not in streamed_request_body
    assert streamed_request_body["model"] == "some-model"
