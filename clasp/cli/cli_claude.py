"""
clasp/cli/cmd_claude.py
``clasp claude [CLAUDE_FLAGS…]`` — launch Claude Code through the CLASP proxy.

All flags after ``clasp claude`` are passed directly to the ``claude`` binary
unchanged, so every Claude Code option (``--resume``, ``--continue``,
``--print``, ``--no-update``, ``--dangerously-skip-permissions``, etc.) works
exactly as documented.

The ``os.execvp`` call replaces this process with the real ``claude`` binary:
- Terminal raw mode (arrow keys, multiline input) works natively.
- Ctrl+C goes directly to ``claude``, not a Python wrapper.
- Process title shows as ``claude``, not ``python``.
- ``--resume`` / ``--continue`` session handling is unaffected.

Environment variables injected:
  ANTHROPIC_BASE_URL                  http://127.0.0.1:{port}
  ANTHROPIC_AUTH_TOKEN                {server.api_key}
  ANTHROPIC_API_KEY                   {server.api_key}   ← some CC versions prefer this
  CLAUDE_CODE_AUTO_COMPACT_WINDOW     190000
  CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY  1
  CLASP_SESSION                       1
"""

from __future__ import annotations

import os
import shutil
import sys
import time

import httpx
import typer
from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CLAUDE_INSTALL_HINT = (
    "Install Claude Code with:\n"
    "  npm install -g @anthropic-ai/claude-code"
)
_SERVER_POLL_TIMEOUT = 3.0   # seconds: how long to wait for server readiness
_SERVER_POLL_INTERVAL = 0.2  # seconds between health checks


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def run(passthrough_args: list[str], auto_start: bool = False) -> None:
    """
    Set CLASP env vars and exec the real ``claude`` binary.

    Parameters
    ----------
    passthrough_args:
        All arguments after ``clasp claude`` — forwarded unchanged to the
        ``claude`` binary.
    auto_start:
        If True and the server is not running, start it in the background
        before exec-ing (future feature — currently just warns).
    """
    # ── 1. Locate the ``claude`` binary ─────────────────────────────────────
    claude_bin = shutil.which("claude")
    if not claude_bin:
        typer.echo(
            "\n✗ `claude` binary not found in PATH.\n" + _CLAUDE_INSTALL_HINT,
            err=True,
        )
        raise typer.Exit(1)

    # ── 2. Load config ──────────────────────────────────────────────────────
    try:
        from clasp.config.settings import get_settings

        settings = get_settings()
    except Exception as exc:
        typer.echo(
            f"\n✗ Could not load CLASP config: {exc}\n"
            "  Run `clasp server` first to create a default config.",
            err=True,
        )
        raise typer.Exit(1) from exc

    port = settings.server.port
    host = settings.server.host
    api_key = settings.server.api_key
    base_url = f"http://{host}:{port}"

    # ── 3. Check server is running ──────────────────────────────────────────
    _ensure_server_running(base_url, auto_start=auto_start)

    # ── 4. Build env var overlay ────────────────────────────────────────────
    env_overlay = {
        "ANTHROPIC_BASE_URL": base_url,
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_API_KEY": api_key,
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "190000",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
        "CLASP_SESSION": "1",
    }
    os.environ.update(env_overlay)

    logger.debug(
        "Exec-ing claude binary",
        binary=claude_bin,
        args=passthrough_args,
        proxy=base_url,
    )

    # ── 5. exec — replaces this process entirely ────────────────────────────
    try:
        os.execvp(claude_bin, [claude_bin] + passthrough_args)
    except OSError as exc:
        typer.echo(f"\n✗ Failed to exec `{claude_bin}`: {exc}", err=True)
        raise typer.Exit(1) from exc

    # Unreachable if exec succeeds.
    sys.exit(1)


# ---------------------------------------------------------------------------
# Server readiness check
# ---------------------------------------------------------------------------

def _ensure_server_running(base_url: str, auto_start: bool = False) -> None:
    """
    Check that the CLASP proxy is accepting traffic.
    Warn (but don't block) if it is not.
    """
    if _is_server_healthy(base_url):
        return

    # Server not reachable.
    if auto_start:
        typer.echo(
            "\n⚠  CLASP server not running — attempting to start it in background…",
            err=True,
        )
        _try_auto_start(base_url)
        return

    typer.echo(
        f"\n⚠  CLASP server does not appear to be running at {base_url}.\n"
        "   Start it first with:\n"
        "     clasp server\n"
        "   Proceeding anyway — Claude Code may fail if the proxy isn't ready.",
        err=True,
    )


def _is_server_healthy(base_url: str) -> bool:
    """Return True if ``GET /health`` responds 200 within 1 second."""
    try:
        with httpx.Client(timeout=1.0) as client:
            r = client.get(f"{base_url}/health")
            return r.status_code == 200
    except Exception:
        return False


def _try_auto_start(base_url: str) -> None:
    """
    (Future) Spawn ``clasp server`` in background and wait for it.
    Currently just warns — auto-start is a Sprint 6 quality-of-life feature.
    """
    typer.echo(
        "  Auto-start is not yet implemented.\n"
        "  Please run `clasp server` in another terminal.",
        err=True,
    )