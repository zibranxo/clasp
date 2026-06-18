from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
PARENT = ROOT.parent
sys.modules.pop("clasp", None)
sys.path = [p for p in sys.path if Path(p).resolve() != PARENT.resolve()]
sys.path.insert(0, str(ROOT))

import clasp.server as server


@pytest.mark.asyncio
async def test_create_app_exposes_health_and_ui_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_init_logging", lambda *args, **kwargs: None)

    app = server.create_app(debug=True)
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        root = await client.get("/", follow_redirects=False)

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert root.status_code in (302, 307)
    assert root.headers["location"] == "/ui"


@pytest.mark.asyncio
async def test_ip_guard_is_active_on_internal_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "_init_logging", lambda *args, **kwargs: None)

    app = server.create_app(debug=True)
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 4000))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/internal/catalog")

    assert resp.status_code == 403
