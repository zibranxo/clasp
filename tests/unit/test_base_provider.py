from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parents[2]
sys.modules.pop("clasp", None)
sys.path.insert(0, str(ROOT))

from clasp.providers.base import (
    BaseProvider,
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderTimeoutError,
    UpstreamRateLimitError,
)

class _DummyProvider(BaseProvider):
    provider_name = "dummy"
    async def _stream_raw(self, request, key, key_index) -> AsyncIterator[str]:
        yield f"chunk-for-{request['model']}"


@pytest.mark.asyncio
async def test_client_is_initialized_and_aclose_calls_client_aclose() -> None:
    p = _DummyProvider("dummy", "https://example.test/")
    assert p.base_url == "https://example.test"  # stripped slash
    assert p.client is not None
    
    with patch.object(p.client, "aclose", new_callable=AsyncMock) as mock_aclose:
        await p.aclose()
        mock_aclose.assert_awaited_once()

@pytest.mark.asyncio
async def test_stream_catches_upstream_rate_limit_error() -> None:
    class _FailingProvider(BaseProvider):
        provider_name = "failing"
        async def _stream_raw(self, request, key, key_index) -> AsyncIterator[str]:
            raise UpstreamRateLimitError(retry_after="10.5")
            yield "never"

    p = _FailingProvider("failing", "https://example.test")
    
    async def fake_on_upstream_429(*args, **kwargs):
        yield "failover chunk"

    with patch("clasp.queue.absorber.on_upstream_429", new=fake_on_upstream_429):
        chunks = []
        async for c in p.stream({"model": "m"}, key="k", key_index=0):
            chunks.append(c)
        assert chunks == ["failover chunk"]

def test_provider_http_error_fields() -> None:
    err = ProviderHTTPError(500, "internal error")
    assert err.status_code == 500
    assert str(err) == "HTTP 500: internal error"

def test_exception_hierarchy() -> None:
    assert issubclass(ProviderTimeoutError, Exception)
    assert issubclass(ProviderConnectionError, Exception)
