"""
clasp/server.py

FastAPI application factory for CLASP.

This version keeps the prototype-friendly clarity of v1 while retaining the
cleaner structure and extensibility of v2.

Highlights:
- Explicit /health route for readiness checks
- Logging initialized early
- Config load + provider registry init on startup
- Config watcher started as a background task when available
- Optional background tasks for later sprints
- Graceful shutdown: cancel tasks, flush logs, delete PID file
- Direct router/static wiring, but with safe optional imports
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

__version__ = "1.0.0"

_background_tasks: list[asyncio.Task] = []


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _init_logging(log_level: str, debug: bool) -> None:
    from clasp.utils.logger import setup_logging

    setup_logging(log_level=log_level, debug=debug)


# ---------------------------------------------------------------------------
# Config / registry
# ---------------------------------------------------------------------------

def _load_config():
    from clasp.config.settings import get_settings

    settings = get_settings()
    logger.info(
        "Config loaded",
        config_path=str(getattr(settings, "_config_path", "~/.clasp/config.yaml")),
    )
    return settings


def _init_registry(settings) -> None:
    """
    Initialise the provider registry.

    Supports both styles:
    - clasp.providers.registry.init_registry(settings)
    - clasp.providers.registry.registry.init(settings)
    """
    try:
        from clasp.providers.registry import init_registry

        init_registry(settings)
        logger.info("Provider registry initialised")
        return
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("init_registry failed", error=str(exc))
        return

    try:
        from clasp.providers.registry import registry

        registry.init(settings)
        logger.info("Provider registry initialised")
    except ImportError:
        logger.warning("Provider registry module not available yet")
    except AttributeError:
        logger.warning("Provider registry found but has no init/init_registry")
    except Exception as exc:
        logger.warning("Provider registry init failed", error=str(exc))


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

def _track_task(task: asyncio.Task) -> asyncio.Task:
    _background_tasks.append(task)
    return task


def _cancel_background_tasks() -> None:
    for task in _background_tasks:
        if not task.done():
            task.cancel()
    _background_tasks.clear()


async def _start_config_watcher() -> None:
    """
    Start config watcher if available.

    Supports either:
    - start_watcher()
    - start_watching()
    """
    for module_path, fn_name in (
        ("clasp.config.watcher", "start_watcher"),
        ("clasp.config.watcher", "start_watching"),
    ):
        try:
            mod = importlib.import_module(module_path)
            fn = getattr(mod, fn_name)
            result = fn()

            if isinstance(result, asyncio.Task):
                _track_task(result)
                logger.info("Config watcher started")
                return

            if inspect.isawaitable(result):
                task = asyncio.create_task(result, name="config_watcher")
                _track_task(task)
                logger.info("Config watcher started")
                return

            logger.warning("Config watcher returned unsupported value")
            return

        except ImportError:
            continue
        except AttributeError:
            continue
        except Exception as exc:
            logger.warning("Config watcher could not be started", error=str(exc))
            return

    logger.warning("Config watcher not available")


def _start_optional_background_tasks() -> None:
    """
    Optional future tasks.

    These are skipped silently if modules/functions are not present yet.
    """
    optional_tasks = [
        ("clasp.ratelimit.persistence", "start_persistence_task", "ratelimit_persistence"),
        ("clasp.queue.manager", "start_drain_task", "queue_drain"),
    ]

    for module_path, func_name, task_name in optional_tasks:
        try:
            mod = importlib.import_module(module_path)
            fn = getattr(mod, func_name)
            result = fn()

            if isinstance(result, asyncio.Task):
                _track_task(result)
                logger.debug("Background task started", task_name=task_name)
                continue

            if inspect.isawaitable(result):
                task = asyncio.create_task(result, name=task_name)
                _track_task(task)
                logger.debug("Background task started", task_name=task_name)
                continue

        except (ImportError, AttributeError):
            continue
        except Exception as exc:
            logger.warning("Optional background task failed", task_name=task_name, error=str(exc))


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup
    settings = _load_config()
    _init_registry(settings)
    await _start_config_watcher()
    _start_optional_background_tasks()

    try:
        from clasp.utils.pid import write_pid

        try:
            write_pid()
        except TypeError:
            write_pid()
        logger.debug("PID file written")
    except Exception as exc:
        logger.warning("Could not write PID file", error=str(exc))

    logger.info(
        "CLASP ready",
        version=__version__,
        host=getattr(settings.server, "host", "127.0.0.1"),
        port=getattr(settings.server, "port", 8082),
    )

    yield

    # Shutdown
    logger.info("CLASP shutting down")
    _cancel_background_tasks()

    try:
        from clasp.ratelimit.persistence import save_state  # type: ignore

        await asyncio.to_thread(save_state)
        logger.debug("Rate-limit state flushed")
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Could not flush rate-limit state", error=str(exc))

    try:
        from clasp.utils.pid import delete_pid, DEFAULT_PID_PATH

        try:
            delete_pid(DEFAULT_PID_PATH)
        except TypeError:
            delete_pid()
        logger.debug("PID file deleted")
    except Exception as exc:
        logger.warning("Could not delete PID file", error=str(exc))

    try:
        complete_result = logger.complete()
        if inspect.isawaitable(complete_result):
            await complete_result
    except Exception:
        pass

    logger.info("CLASP stopped cleanly")


# ---------------------------------------------------------------------------
# Routers / app factory
# ---------------------------------------------------------------------------

def _register_routers(app: FastAPI) -> None:
    health_router = APIRouter()

    @health_router.get("/health", tags=["health"], include_in_schema=True)
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    app.include_router(health_router)

    try:
        from clasp.api.proxy_routes import router as proxy_router

        app.include_router(proxy_router)
        logger.debug("proxy_routes registered")
    except Exception as exc:
        logger.warning("proxy_routes not available", error=str(exc))

    try:
        from clasp.ui.routes import router as ui_router

        app.include_router(ui_router)
        logger.debug("ui routes registered")
    except Exception as exc:
        logger.warning("ui routes not available", error=str(exc))

    try:
        from clasp.internal.routes import router as internal_router

        app.include_router(internal_router)
        logger.debug("internal routes registered")
    except Exception as exc:
        logger.warning("internal routes not available", error=str(exc))


def create_app(log_level: str = "INFO", debug: bool = False) -> FastAPI:
    """
    Create and configure the FastAPI application.
    """
    _init_logging(log_level=log_level, debug=debug)

    # Re-read configured log level after config becomes available.
    try:
        from clasp.config.settings import get_settings

        settings = get_settings()
        configured_level = getattr(settings.server, "log_level", log_level)
        if configured_level != log_level:
            _init_logging(log_level=configured_level, debug=debug)
    except Exception:
        pass

    app = FastAPI(
        title="CLASP — Claude API Switching Proxy",
        version=__version__,
        description="Rate-limit-aware multi-provider local proxy.",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
        debug=debug,
    )

    # IP guard first, so it protects internal routes.
    from clasp.utils.ip_guard import IPGuard

    app.add_middleware(IPGuard)

    # Nice for the local web UI.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:8082", "http://localhost:8082"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_routers(app)

    # Static assets for UI.
    try:
        from clasp.ui.routes import STATIC_DIR

        app.mount("/ui/assets", StaticFiles(directory=STATIC_DIR), name="ui-assets")
    except Exception as exc:
        logger.warning("Could not mount UI static files", error=str(exc))

    return app