from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.modules.pop("clasp", None)
sys.path.insert(0, str(ROOT))



from clasp.providers.base import ProviderHTTPError, ProviderTimeoutError
from clasp.providers.openai_transport import OpenAIChatTransport, _parse_retry_after


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, headers: dict[str, str] | None = None, lines: list[str] | None = None, body: bytes = b"", json_data: dict | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._lines = lines or []
        self._body = body
        self._json_data = json_data or {}

    async def aread(self) -> bytes:
        return self._body

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line

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
        self.stream_calls = 0
        self.get_calls = 0
        self._stream_response = stream_response or _FakeResponse()
        self._get_response = get_response or _FakeResponse(json_data={"data": []})

    def stream(self, *_: object, **__: object) -> _FakeStreamCtx:
        self.stream_calls += 1
        return _FakeStreamCtx(self._stream_response)

    async def get(self, *_: object, **__: object) -> _FakeResponse:
        self.get_calls += 1
        return self._get_response


def test_parse_retry_after_seconds_and_invalid() -> None:
    assert _parse_retry_after("12") == 12.0
    assert _parse_retry_after("bogus") is None


def test_build_headers_merges_auth_and_extra_headers() -> None:
    t = OpenAIChatTransport("x", "https://api.example", extra_headers={"X-Test": "1"})
    headers = t._build_headers("secret")
    assert headers["Authorization"] == "Bearer secret"
    assert headers["X-Test"] == "1"


@pytest.mark.asyncio
async def test_list_models_uses_cache() -> None:
    transport = OpenAIChatTransport("x", "https://api.example")
    fake_client = _FakeClient(get_response=_FakeResponse(json_data={"data": [{"id": "m1"}, {"id": "m2"}]}))
    transport.client = fake_client  # inject fake

    first = await transport.list_models(api_key="k")
    second = await transport.list_models(api_key="k")

    assert first == ["m1", "m2"]
    assert second == ["m1", "m2"]
    assert fake_client.get_calls == 1


@pytest.mark.asyncio
async def test_stream_raises_upstream_rate_limit_error_on_429() -> None:
    transport = OpenAIChatTransport("x", "https://api.example")
    fake_client = _FakeClient(
        stream_response=_FakeResponse(status_code=429, headers={"retry-after": "7"}, body=b"too many requests")
    )
    transport.client = fake_client

    from clasp.providers.base import UpstreamRateLimitError
    with pytest.raises(UpstreamRateLimitError) as exc:
        async for _ in transport._stream_raw({"messages": [], "model": "m"}, key="k", key_index=0):
            pass

    assert exc.value.retry_after == "7.0"


@pytest.mark.asyncio
async def test_stream_maps_timeout_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    class _TimeoutClient:
        def stream(self, *_: object, **__: object) -> object:
            import httpx

            raise httpx.ReadTimeout("timed out")

    transport = OpenAIChatTransport("x", "https://api.example")
    transport.client = _TimeoutClient()

    with pytest.raises(ProviderTimeoutError):
        async for _ in transport.stream({"messages": [], "model": "m"}, key="k", key_index=0):
            pass
