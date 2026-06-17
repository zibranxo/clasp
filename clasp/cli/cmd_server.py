"""
clasp/cli/cmd_server.py
``clasp server`` — start the CLASP proxy and open the config UI in the browser.

Behaviour:
  1. Check if a CLASP server is already running (PID file at ~/.clasp/clasp.pid).
     If already running: print status line and optionally open browser to it.
  2. Load and validate config.yaml.
  3. Print startup summary.
  4. Start uvicorn in-process (no subprocess: same PID, clean signal handling).
  5. Write PID file (also done inside lifespan so it's always written).
  6. Poll GET /health until ready (max 8 s).
  7. Open browser (unless --no-browser).

``--live`` is accepted but defers to Sprint 6 (Rich TUI live panel).
"""

from __future__ import annotations

import asyncio
import sys
import time
import webbrowser
from pathlib import Path

import httpx
import typer
import uvicorn
from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8082
_HEALTH_TIMEOUT = 8.0   # seconds to wait for /health to respond
_HEALTH_POLL = 0.2       # seconds between poll attempts


# ---------------------------------------------------------------------------
# Public entrypoint (called by clasp/cli/main.py)
# ---------------------------------------------------------------------------

def run(
    port: int = _DEFAULT_PORT,
    host: str = _DEFAULT_HOST,
    no_browser: bool = False,
    live: bool = False,
    config: Path | None = None,
    debug: bool = False,
) -> None:
    """
    Start the CLASP proxy server.

    This function is *synchronous* — it calls ``uvicorn.run()`` which blocks
    until the server exits (Ctrl+C / SIGTERM).
    """
    from clasp.utils.pid import is_running, write_pid

    # ── 1. Check for existing instance ─────────────────────────────────────
    already_running, existing_pid = is_running()
    if already_running:
        base_url = f"http://{host}:{port}"
        typer.echo(f"\n◆ CLASP is already running (PID {existing_pid or '?'})")
        typer.echo(f"  → {base_url}")
        if not no_browser:
            webbrowser.open(base_url)
        raise typer.Exit(0)

    # ── 2. Apply config path override ──────────────────────────────────────
    if config:
        import os

        os.environ["CLASP_CONFIG_PATH"] = str(config)

    # ── 3. Load config (validates; exits on error) ──────────────────────────
    try:
        from clasp.config.settings import get_settings

        settings = get_settings()
        # CLI flags win over config file values.
        effective_host = host if host != _DEFAULT_HOST else settings.server.host
        effective_port = port if port != _DEFAULT_PORT else settings.server.port
        log_level = settings.server.log_level if not debug else "DEBUG"
    except Exception as exc:
        typer.echo(f"\n✗ Failed to load config: {exc}", err=True)
        typer.echo("  Run `clasp init` to create a default config.", err=True)
        raise typer.Exit(1) from exc

    # ── 4. Print startup banner ─────────────────────────────────────────────
    _print_startup_banner(settings, effective_host, effective_port)

    base_url = f"http://{effective_host}:{effective_port}"

    # ── 5. Write PID now (uvicorn lifespan will also write it) ────────────
    write_pid()

    # ── 6. Register browser-open task (fires once /health is up) ──────────
    if not no_browser:
        # Schedule the browser open on the event loop that uvicorn will run.
        # We use a thread-safe callback registered via uvicorn's on_startup.
        _schedule_browser_open(base_url)

    # ── 7. Start uvicorn (blocks until Ctrl+C / SIGTERM) ───────────────────
    from clasp.server import create_app

    uv_config = uvicorn.Config(
        app=create_app(log_level=log_level, debug=debug),
        host=effective_host,
        port=effective_port,
        log_level=log_level.lower(),
        # Disable uvicorn's default access log — clasp has its own structured log.
        access_log=debug,
        # Allow graceful shutdown with a 5-second timeout for in-flight requests.
        timeout_graceful_shutdown=5,
    )

    server = uvicorn.Server(uv_config)

    if not no_browser:
        # Monkey-patch uvicorn's startup to trigger our browser-open coroutine.
        _patch_uvicorn_startup(server, base_url)

    try:
        server.run()
    except OSError as exc:
        import errno

        if exc.errno == errno.EADDRINUSE:
            typer.echo(f"\n✗ Port {effective_port} is already in use.", err=True)
            typer.echo(f"  Check if another CLASP is running: clasp status", err=True)
            typer.echo(
                f"  Or use a different port: clasp server --port {effective_port + 1}",
                err=True,
            )
            raise typer.Exit(1) from exc
        raise


# ---------------------------------------------------------------------------
# Startup banner
# ---------------------------------------------------------------------------

def _print_startup_banner(settings, host: str, port: int) -> None:
    from clasp.config.provider_catalog import PROVIDER_CATALOG

    provider_summary_parts: list[str] = []
    for name, pcfg in settings.providers.items():
        if not pcfg.enabled:
            continue
        keys = getattr(pcfg, "keys", [])
        if keys:
            label = f"{PROVIDER_CATALOG[name].display_name} ({len(keys)} key{'s' if len(keys) > 1 else ''})"
        else:
            label = PROVIDER_CATALOG[name].display_name
        provider_summary_parts.append(label)

    provider_str = ", ".join(provider_summary_parts) if provider_summary_parts else "(none configured)"

    from clasp.server import __version__

    typer.echo(f"\n◆ CLASP v{__version__} starting…")
    typer.echo(f"  ✓ Config loaded")
    typer.echo(f"  ✓ Providers: {provider_str}")
    typer.echo(f"  ✓ Proxy running at http://{host}:{port}")
    typer.echo(f"  ✓ Config UI open at http://{host}:{port}")
    typer.echo(f"  → Run `clasp claude` in another terminal to start coding.")
    typer.echo("  Press Ctrl+C to stop.\n")


# ---------------------------------------------------------------------------
# Browser-open helper (poll /health then open)
# ---------------------------------------------------------------------------

def _patch_uvicorn_startup(server: "uvicorn.Server", base_url: str) -> None:
    """
    Patch uvicorn.Server.startup to schedule the browser-open task on the
    event loop *after* the server is actually bound and listening.
    """
    original_startup = server.startup

    async def patched_startup(sockets=None):
        await original_startup(sockets=sockets)
        asyncio.create_task(
            _open_browser_when_ready(base_url),
            name="browser_open",
        )

    server.startup = patched_startup  # type: ignore[method-assign]


async def _open_browser_when_ready(url: str, timeout: float = _HEALTH_TIMEOUT) -> None:
    """Poll ``/health`` then open the browser once the server is accepting traffic."""
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(f"{url}/health", timeout=0.5)
                if r.status_code == 200:
                    webbrowser.open(url)
                    logger.debug("Browser opened", url=url)
                    return
            except Exception:
                pass
            await asyncio.sleep(_HEALTH_POLL)

    # Server didn't respond in time — open anyway (better than silence).
    logger.warning("Server health check timed out — opening browser anyway", url=url)
    webbrowser.open(url)


def _schedule_browser_open(base_url: str) -> None:
    """No-op placeholder; actual scheduling happens via _patch_uvicorn_startup."""
    pass