from __future__ import annotations

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

import clasp.internal.routes as internal_routes
from clasp.utils.ip_guard import IPGuard


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, reason_phrase: str = "OK", data: dict | None = None):
        self.status_code = status_code
        self.reason_phrase = reason_phrase
        self._data = data or {"data": []}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._data


class _FakeAsyncClient:
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        _FakeAsyncClient.calls += 1
        return _FakeResponse(data={"data": [{"id": "model-1"}, {"id": "model-2"}]})


class _RuntimeSettings:
    def __init__(self) -> None:
        self.server = SimpleNamespace(host="127.0.0.1", port=8082)
        self.provider_chain = ["nvidia_nim"]
        self.providers = {
            "nvidia_nim": SimpleNamespace(enabled=True, keys=["nvapi-abcdef1234"]),
        }

    def model_dump(self, mode: str = "json") -> dict:
        return {
            "server": {"host": "127.0.0.1", "port": 8082, "api_key": "freecc"},
            "provider_chain": ["nvidia_nim"],
            "providers": {
                "nvidia_nim": {
                    "enabled": True,
                    "keys": ["nvapi-abcdef1234"],
                    "base_url": None,
                    "rpm_limit": None,
                    "soft_threshold_pct": None,
                }
            },
        }


class _SettingsValidator:
    def __init__(self, **data):
        self._data = data

    def model_dump(self, mode: str = "json") -> dict:
        return self._data


@pytest.fixture
def settings_obj() -> _RuntimeSettings:
    return _RuntimeSettings()


def _make_app(monkeypatch: pytest.MonkeyPatch, settings_obj: _RuntimeSettings) -> FastAPI:
    monkeypatch.setattr(internal_routes, "get_settings", lambda: settings_obj)
    monkeypatch.setattr(internal_routes, "Settings", _SettingsValidator)

    app = FastAPI()
    app.add_middleware(IPGuard)
    app.include_router(internal_routes.router)
    return app


@pytest.mark.asyncio
async def test_internal_routes_block_non_loopback(monkeypatch: pytest.MonkeyPatch, settings_obj: _RuntimeSettings) -> None:
    app = _make_app(monkeypatch, settings_obj)
    transport = httpx.ASGITransport(app=app, client=("8.8.8.8", 12345))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/internal/catalog")

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_get_config_masks_provider_keys(monkeypatch: pytest.MonkeyPatch, settings_obj: _RuntimeSettings) -> None:
    app = _make_app(monkeypatch, settings_obj)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/internal/config")

    assert resp.status_code == 200
    masked = resp.json()["providers"]["nvidia_nim"]["keys"][0]
    assert "***" in masked
    assert masked.endswith("1234")


@pytest.mark.asyncio
async def test_post_config_restores_redacted_keys_before_write(
    monkeypatch: pytest.MonkeyPatch,
    settings_obj: _RuntimeSettings,
) -> None:
    captured: list[dict] = []

    def _fake_write_config(data):
        captured.append(data)
        return None

    monkeypatch.setattr(internal_routes, "write_config", _fake_write_config)

    app = _make_app(monkeypatch, settings_obj)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))

    payload = settings_obj.model_dump(mode="json")
    payload["providers"]["nvidia_nim"]["keys"] = ["nvapi-***1234"]

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/internal/config", json=payload)

    assert resp.status_code == 200
    assert captured, "write_config should have been called"
    assert captured[0]["providers"]["nvidia_nim"]["keys"][0] == "nvapi-abcdef1234"


@pytest.mark.asyncio
async def test_provider_models_endpoint_uses_cache(
    monkeypatch: pytest.MonkeyPatch,
    settings_obj: _RuntimeSettings,
) -> None:
    internal_routes._model_list_cache.clear()
    _FakeAsyncClient.calls = 0

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(internal_routes.httpx, "AsyncClient", _FakeAsyncClient)

    app = _make_app(monkeypatch, settings_obj)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))

    async with real_async_client(transport=transport, base_url="http://test") as client:
        first = await client.get("/internal/providers/nvidia_nim/models")
        second = await client.get("/internal/providers/nvidia_nim/models")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["cached"] is False
    assert second.json()["cached"] is True
    assert _FakeAsyncClient.calls == 1
