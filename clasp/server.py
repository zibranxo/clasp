"""
clasp/server.py

FastAPI app factory for the CLASP proxy server.

Startup responsibilities (via the lifespan context manager):
  - Build the process-wide `SelectorConfig`, `ProviderRegistry`, and
    `CooldownManager` that the routing/absorber pipeline shares.
  - Start `QueueManager.drain_task()` as a background asyncio task so
    requests queued by `queue.absorber.on_upstream_429()` (because every
    provider was unavailable at the moment they arrived) get dispatched the
    instant a provider recovers, without any caller having to poll.
  - Cancel that background task cleanly on shutdown.

Anything not explicitly part of this task (mounting `internal/routes.py`,
the `IPGuard` middleware, `config.watcher`'s hot-reload task, the static UI
routes) is left as a clearly-marked follow-up — see the TODOs below — since
those modules don't exist in this codebase yet.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

__version__ = "0.1.0"

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from clasp.api.proxy_routes import router as proxy_router
from clasp.providers.registry import ProviderRegistry, get_registry
from clasp.queue.manager import QueueManager, get_queue_manager
from clasp.ratelimit.cooldown import CooldownManager, get_cooldown_manager
from clasp.router.types import ProviderEnableConfig, SelectorConfig


def build_selector_config(settings) -> SelectorConfig:
    """
    Build the `SelectorConfig` the routing pipeline will use for the life of
    this process, based on the current settings.
    """
    return SelectorConfig(
        provider_chain=settings.provider_chain,
        providers={
            name: ProviderEnableConfig(enabled=p.enabled)
            for name, p in settings.providers.items()
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: launch the queue drain task. Shutdown: cancel it cleanly."""
    from clasp.config.settings import get_settings
    from clasp.providers.registry import build_registry
    
    settings = get_settings()
    build_registry(settings)
    
    config: SelectorConfig = build_selector_config(settings)
    registry: ProviderRegistry = get_registry()
    cooldown_mgr: CooldownManager = get_cooldown_manager()
    queue_mgr: QueueManager = get_queue_manager()

    app.state.selector_config = config
    app.state.registry = registry
    app.state.cooldown_mgr = cooldown_mgr
    app.state.queue_mgr = queue_mgr

    drain_task = asyncio.create_task(
        queue_mgr.drain_task(
            config=config,
            registry=registry,
            cooldown_mgr=cooldown_mgr,
        )
    )
    logger.info("queue drain task started")

    # TODO: also start clasp.config.watcher's hot-reload background task
    # here once config/watcher.py's ConfigWatcher is wired into the
    # SelectorConfig rebuild path (today build_selector_config() is static
    # for the lifetime of the process).

    try:
        yield
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass
        logger.info("queue drain task stopped")

        # ── ADOPTED FROM V2: Flush async log sinks before process exit ────
        from loguru import logger as _log
        try:
            await asyncio.to_thread(_log.complete)
        except Exception:
            pass


def create_app(debug: bool = False) -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(title="CLASP", lifespan=lifespan, debug=debug)

    # ── ADOPTED FROM V2: CORS verification for local loopback tools ───────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8082", "http://localhost:8082"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(proxy_router)

    try:
        from clasp.utils.ip_guard import IPGuard
        app.add_middleware(IPGuard)
    except ImportError:
        pass

    try:
        from clasp.internal.routes import router as internal_router
        app.include_router(internal_router)
    except ImportError:
        pass

    try:
        from clasp.ui.routes import router as ui_router
        app.include_router(ui_router)
    except ImportError:
        pass

    return app


app = create_app()