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


class _FakeProvider:
    provider_name = "fake_provider"

    def __init__(self, *, fail_stream: bool = False):
        self.fail_stream = fail_stream

    async def complete(self, _body: dict):
        return {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
        }

    async def stream(self, _body: dict):
        if self.fail_stream:
            raise RuntimeError("boom")
        yield "event: message_start\\ndata: {}\\n\\n"
        yield "event: message_stop\\ndata: {}\\n\\n"


class _FakeRegistry:
    def __init__(self, provider):
        self._provider = provider

    def first_available(self):
        return self._provider


@pytest.mark.asyncio
async def test_handle_request_returns_no_provider_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_get_registry", lambda: _FakeRegistry(None))

    body = {"model": "claude", "messages": [{"role": "user", "content": "hi"}]}

    non_stream = await service.handle_request(body, request_id="req_x", stream=False)
    stream = await service.handle_request(body, request_id="req_x", stream=True)
    chunks = [chunk async for chunk in stream]

    assert non_stream["type"] == "error"
    assert "No providers are currently available" in non_stream["error"]["message"]
    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")


@pytest.mark.asyncio
async def test_handle_request_success_non_stream_and_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _FakeProvider()
    monkeypatch.setattr(service, "_get_registry", lambda: _FakeRegistry(provider))
    monkeypatch.setattr(service, "detect", lambda _body: RequestType.INTERACTIVE)
    monkeypatch.setattr(service, "classify_priority", lambda _rt: 0)

    body = {"model": "claude", "messages": [{"role": "user", "content": "hi"}]}

    non_stream = await service.handle_request(body, request_id="req_ok", stream=False)
    stream = await service.handle_request(body, request_id="req_ok", stream=True)
    chunks = [chunk async for chunk in stream]

    assert non_stream["role"] == "assistant"
    assert any("message_start" in c for c in chunks)
    assert any("message_stop" in c for c in chunks)


@pytest.mark.asyncio
async def test_handle_request_stream_error_is_wrapped_as_sse_error(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _FakeProvider(fail_stream=True)
    monkeypatch.setattr(service, "_get_registry", lambda: _FakeRegistry(provider))
    monkeypatch.setattr(service, "detect", lambda _body: RequestType.INTERACTIVE)
    monkeypatch.setattr(service, "classify_priority", lambda _rt: 0)

    body = {"model": "claude", "messages": [{"role": "user", "content": "hi"}]}

    stream = await service.handle_request(body, request_id="req_err", stream=True)
    chunks = [chunk async for chunk in stream]

    assert len(chunks) == 1
    assert chunks[0].startswith("event: error")

    payload = json.loads(chunks[0].split("data: ", 1)[1])
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "api_error"
