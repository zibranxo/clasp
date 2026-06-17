"""
clasp/server.py
FastAPI application factory for CLASP.

Usage::

    from clasp.server import create_app
    app = create_app()

Or via the uvicorn entry-point in cmd_server.py::

    uvicorn clasp.server:create_app --factory ...

Startup sequence:
  1. Setup logging (before anything else logs).
  2. Load & validate config.
  3. Initialise provider registry.
  4. Register all routers.
  5. Add middleware (IPGuard, CORS for loopback).
  6. Start background tasks:
       • Config watcher (hot-reload on config.yaml changes).
       • (Sprint 2+) Rate-limit bucket refill.
       • (Sprint 3+) Queue drain task.
       • (Sprint 7+) Rate-limit state persistence.

Shutdown sequence:
  1. Cancel background tasks.
  2. (Sprint 7+) Flush rate-limit state to ratelimit.json.
  3. Delete PID file.
  4. Flush loguru queue.
"""

from __future__ import annotations

import asyncio
import importlib
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

__version__ = "1.0.0"

# ---------------------------------------------------------------------------
# Background task registry (populated during startup)
# ---------------------------------------------------------------------------

_background_tasks: list[asyncio.Task] = []


def _cancel_background_tasks() -> None:
    for t in _background_tasks:
        if not t.done():
            t.cancel()
    _background_tasks.clear()


# ---------------------------------------------------------------------------
# Startup helpers
# ---------------------------------------------------------------------------

def _init_logging(log_level: str, debug: bool) -> None:
    from clasp.utils.logger import setup_logging  # local import — logger not yet up

    setup_logging(log_level=log_level, debug=debug)


def _load_config():
    """Load settings and return the Settings instance."""
    from clasp.config.settings import get_settings

    settings = get_settings()
    logger.info(
        "Config loaded",
        config_path=str(getattr(settings, "_config_path", "~/.clasp/config.yaml")),
    )
    return settings


def _init_registry(settings) -> None:
    """Initialise the provider registry (instantiate enabled providers)."""
    try:
        from clasp.providers.registry import init_registry

        init_registry(settings)
        enabled = [n for n, p in settings.providers.items() if p.enabled]
        logger.info("Provider registry initialised", enabled=enabled)
    except Exception as exc:
        logger.warning("Provider registry init failed (non-fatal in Sprint 1)", error=str(exc))


def _start_config_watcher(settings) -> None:
    """Start the watchfiles config watcher as a background task."""
    try:
        from clasp.config.watcher import start_watcher

        task = asyncio.create_task(start_watcher(), name="config_watcher")
        _background_tasks.append(task)
        logger.debug("Config watcher started")
    except Exception as exc:
        logger.warning("Config watcher not available", error=str(exc))


def _start_sprint2_tasks() -> None:
    """
    Placeholder for Sprint 2+ background tasks.
    Importing these modules will fail gracefully in Sprint 1.
    """
    _optional_tasks = [
        ("clasp.ratelimit.persistence", "start_persistence_task", "ratelimit_persistence"),
        ("clasp.queue.manager", "start_drain_task", "queue_drain"),
    ]
    for module_path, func_name, task_name in _optional_tasks:
        try:
            mod = importlib.import_module(module_path)
            fn = getattr(mod, func_name)
            task = asyncio.create_task(fn(), name=task_name)
            _background_tasks.append(task)
            logger.debug(f"Background task started: {task_name}")
        except (ImportError, AttributeError):
            pass  # Not yet implemented — skip silently


# ---------------------------------------------------------------------------
# Lifespan context manager (replaces deprecated on_event handlers)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan — runs startup then yields, then runs shutdown."""

    # ── STARTUP ───────────────────────────────────────────────────────────
    settings = _load_config()
    _init_registry(settings)
    _start_config_watcher(settings)
    _start_sprint2_tasks()

    # Write PID (cmd_server.py also does this, but server.py ensures it's
    # always present even when imported directly without the CLI).
    try:
        from clasp.utils.pid import write_pid

        write_pid()
        logger.debug("PID file written")
    except Exception as exc:
        logger.warning("Could not write PID file", error=str(exc))

    logger.info(
        "CLASP proxy ready",
        version=__version__,
        host=settings.server.host,
        port=settings.server.port,
    )

    yield  # ←── server is running

    # ── SHUTDOWN ──────────────────────────────────────────────────────────
    logger.info("CLASP shutting down…")
    _cancel_background_tasks()

    # Sprint 7+: flush rate-limit state.
    try:
        from clasp.ratelimit.persistence import save_state  # type: ignore[import]

        await asyncio.to_thread(save_state)
        logger.debug("Rate-limit state flushed")
    except ImportError:
        pass

    # Clean up PID file.
    try:
        from clasp.utils.pid import delete_pid

        delete_pid()
        logger.debug("PID file deleted")
    except Exception:
        pass

    # Ensure loguru's async sink is fully flushed before the process exits.
    from loguru import logger as _log

    await asyncio.to_thread(_log.complete)
    logger.info("CLASP stopped cleanly")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(log_level: str = "INFO", debug: bool = False) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Parameters
    ----------
    log_level:
        Loguru log level for the stderr sink (``INFO``, ``DEBUG``, …).
        Overridden by ``settings.server.log_level`` after config is loaded.
    debug:
        When True, enables FastAPI debug mode and DEBUG logging.

    Returns
    -------
    FastAPI
        The fully wired application, ready for ``uvicorn``.
    """
    # Logging must be up before the first import that may log.
    _init_logging(log_level=log_level, debug=debug)

    # After logging is set up, pull the configured level from settings.
    try:
        from clasp.config.settings import get_settings

        settings = get_settings()
        effective_level = settings.server.log_level
        if effective_level != log_level:
            _init_logging(log_level=effective_level, debug=debug)
    except Exception:
        pass  # Fall back to the caller-supplied level

    app = FastAPI(
        title="CLASP — Claude API Switching Proxy",
        version=__version__,
        description=(
            "Rate-limit-aware multi-provider local proxy. "
            "Translates Claude Code's Anthropic API calls to free-tier providers."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
        debug=debug,
    )

    # ── Middleware ─────────────────────────────────────────────────────────
    # IPGuard: must be added *before* other middleware so it runs last
    # (FastAPI middleware stack is LIFO).
    from clasp.utils.ip_guard import IPGuard

    app.add_middleware(IPGuard)

    # CORS: allow the local UI (same origin in practice, but explicit is safer).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8082", "http://localhost:8082"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ────────────────────────────────────────────────────────────
    _register_routers(app)

    # ── Static files (must come AFTER explicit routes) ─────────────────────
    try:
        from clasp.ui.routes import mount_static

        mount_static(app)
    except Exception as exc:
        logger.warning("Could not mount static files", error=str(exc))

    return app


def _register_routers(app: FastAPI) -> None:
    """Include all sub-routers into the main app."""

    # ── Health (no auth) ───────────────────────────────────────────────────
    from fastapi.responses import JSONResponse
    from fastapi import APIRouter

    health_router = APIRouter()

    @health_router.get("/health", tags=["health"], include_in_schema=True)
    async def health() -> JSONResponse:
        """
        Liveness probe used by ``clasp server`` to detect when the proxy is
        ready to accept traffic.  No authentication required.
        """
        return JSONResponse({"status": "ok", "version": __version__})

    app.include_router(health_router)

    # ── Proxy routes (POST /v1/messages, GET /v1/models, …) ───────────────
    try:
        from clasp.api.proxy_routes import router as proxy_router

        app.include_router(proxy_router)
        logger.debug("proxy_routes registered")
    except Exception as exc:
        logger.warning("proxy_routes not available", error=str(exc))

    # ── UI static routes (GET /, GET /ui, …) ──────────────────────────────
    try:
        from clasp.ui.routes import router as ui_router

        app.include_router(ui_router)
        logger.debug("ui routes registered")
    except Exception as exc:
        logger.warning("ui routes not available", error=str(exc))

    # ── Internal management API (loopback-only) ────────────────────────────
    try:
        from clasp.internal.routes import router as internal_router

        app.include_router(internal_router)
        logger.debug("internal routes registered")
    except Exception as exc:
        logger.warning("internal routes not available", error=str(exc))