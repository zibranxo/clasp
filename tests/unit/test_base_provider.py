from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.modules.pop("clasp", None)
sys.path.insert(0, str(ROOT))

from clasp.providers.base import (
    BaseProvider,
    ProviderConnectionError,
    ProviderError,
    ProviderHTTPError,
    ProviderTimeoutError,
)


class _DummyProvider(BaseProvider):
    async def count_tokens(self, request: dict) -> int:
        return len(str(request))

    async def list_models(self) -> list[str]:
        return ["dummy-model"]

    async def stream(self, request: dict, *, api_key: str, model: str) -> AsyncIterator[str]:
        yield f"{model}:{api_key}:{request.get('x', '')}"


@pytest.mark.asyncio
async def test_client_lifecycle_is_lazy_reused_and_reset_after_aclose() -> None:
    p = _DummyProvider("dummy", "https://example.test/")
    assert p._client is None

    c1 = p.client
    c2 = p.client
    assert c1 is c2

    await p.aclose()
    assert p._client is None


@pytest.mark.asyncio
async def test_async_context_manager_closes_client() -> None:
    async with _DummyProvider("dummy", "https://example.test") as p:
        _ = p.client
        assert p._client is not None
    assert p._client is None


def test_repr_and_base_url_normalization() -> None:
    p = _DummyProvider("dummy", "https://example.test/")
    assert p.base_url == "https://example.test"
    assert "name='dummy'" in repr(p)


def test_provider_http_error_fields() -> None:
    err = ProviderHTTPError(429, "rate limited", retry_after=3.5, body="{...}")
    assert isinstance(err, ProviderError)
    assert err.status_code == 429
    assert err.retry_after == 3.5
    assert err.body == "{...}"


def test_exception_hierarchy() -> None:
    assert issubclass(ProviderTimeoutError, ProviderError)
    assert issubclass(ProviderConnectionError, ProviderError)
