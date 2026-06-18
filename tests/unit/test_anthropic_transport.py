from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.modules.pop("clasp", None)
sys.path.insert(0, str(ROOT))

import types

sys.modules.setdefault(
    "loguru",
    types.SimpleNamespace(
        logger=types.SimpleNamespace(debug=lambda *a, **k: None, warning=lambda *a, **k: None)
    ),
)

from clasp.providers.anthropic_transport import AnthropicMessagesTransport
from clasp.providers.base import ProviderHTTPError


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, headers: dict[str, str] | None = None, chunks: list[str] | None = None, body: bytes = b"", json_data: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks or []
        self._body = body
        self._json_data = json_data or {}

    async def aread(self) -> bytes:
        return self._body

    async def aiter_text(self) -> AsyncIterator[str]:
        for c in self._chunks:
            yield c

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def json(self) -> dict:
        return self._json_data


class _FakeStreamCtx:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, *_: object) -> None:
        return None


class _FakeClient:
    def __init__(self, *, stream_response: _FakeResponse | None = None, get_response: _FakeResponse | None = None) -> None:
        self._stream_response = stream_response or _FakeResponse()
        self._get_response = get_response or _FakeResponse(json_data={"data": []})
        self.get_calls = 0

    def stream(self, *_: object, **__: object) -> _FakeStreamCtx:
        return _FakeStreamCtx(self._stream_response)

    async def get(self, *_: object, **__: object) -> _FakeResponse:
        self.get_calls += 1
        return self._get_response


def test_derive_sibling_endpoints() -> None:
    base = "https://api.example/v1/messages"
    assert AnthropicMessagesTransport._derive_sibling_endpoint(base, "models") == "https://api.example/v1/models"
    assert AnthropicMessagesTransport._derive_sibling_endpoint(
        base, "count_tokens", relative_to_messages=True
    ) == "https://api.example/v1/messages/count_tokens"


def test_build_headers_with_and_without_authorization_mode() -> None:
    t1 = AnthropicMessagesTransport("x", "https://api.example/v1/messages")
    h1 = t1._build_headers("secret")
    assert h1["x-api-key"] == "secret"
    assert h1["anthropic-version"] == "2023-06-01"

    t2 = AnthropicMessagesTransport(
        "x", "https://api.example/v1/messages", auth_header_name="Authorization"
    )
    h2 = t2._build_headers("secret")
    assert h2["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_stream_forwards_text_chunks() -> None:
    transport = AnthropicMessagesTransport("x", "https://api.example/v1/messages")
    transport._client = _FakeClient(stream_response=_FakeResponse(chunks=["a", "", "b"]))

    out: list[str] = []
    async for chunk in transport.stream({"messages": []}, api_key="k", model="m"):
        out.append(chunk)

    assert out == ["a", "b"]


@pytest.mark.asyncio
async def test_stream_429_raises_provider_http_error() -> None:
    transport = AnthropicMessagesTransport("x", "https://api.example/v1/messages")
    transport._client = _FakeClient(
        stream_response=_FakeResponse(status_code=429, headers={"retry-after": "9"}, body=b"ratelimited")
    )

    with pytest.raises(ProviderHTTPError) as exc:
        async for _ in transport.stream({"messages": []}, api_key="k", model="m"):
            pass

    assert exc.value.status_code == 429
    assert exc.value.retry_after == 9.0


@pytest.mark.asyncio
async def test_list_models_uses_cache() -> None:
    transport = AnthropicMessagesTransport("x", "https://api.example/v1/messages")
    fake_client = _FakeClient(get_response=_FakeResponse(json_data={"data": [{"id": "a"}]}))
    transport._client = fake_client

    first = await transport.list_models(api_key="k")
    second = await transport.list_models(api_key="k")

    assert first == ["a"]
    assert second == ["a"]
    assert fake_client.get_calls == 1
