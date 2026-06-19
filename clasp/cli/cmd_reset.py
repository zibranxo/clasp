"""
clasp/cli/cmd_reset.py

`clasp reset [PROVIDER]`

Reset cooldown / circuit-breaker state on the running CLASP server.

  clasp reset              # Reset all providers  →  POST /internal/reset/all
  clasp reset nvidia_nim   # Reset one provider   →  POST /internal/reset/nvidia_nim

The server clears:
  • Cooldown timers for all keys of the named provider(s)
  • Circuit-breaker failure counters (returns to CLOSED state)

Requires the server to be running. The command connects via loopback HTTP
(same port as the proxy, which is trusted by the ip_guard middleware).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
import typer

_PID_PATH = Path.home() / ".clasp" / "clasp.pid"
_DEFAULT_PORT = 8082


# ── helpers ──────────────────────────────────────────────────────────────────

def _read_port() -> int:
    """Try to read the server port from config; fall back to default."""
    try:
        from clasp.config.settings import get_settings  # type: ignore[import]
        return get_settings().server.port
    except Exception:
        pass
    return _DEFAULT_PORT


def _server_running() -> bool:
    if not _PID_PATH.exists():
        return False
    try:
        import os
        pid = int(_PID_PATH.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, OSError):
        return False


# ── command ──────────────────────────────────────────────────────────────────

def reset_cmd(
    provider: Optional[str] = typer.Argument(
        None,
        help=(
            "Provider name to reset (e.g. nvidia_nim, gemini). "
            "Omit to reset all providers."
        ),
    ),
) -> None:
    """Reset cooldown state for a provider (or all providers)."""

    if not _server_running():
        typer.echo("✗ CLASP is not running. Start it with: clasp server", err=True)
        raise typer.Exit(1)

    port = _read_port()
    target = provider.strip() if provider else "all"
    url = f"http://127.0.0.1:{port}/internal/reset/{target}"

    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.post(url)
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError:
        typer.echo("✗ Cannot connect to CLASP server.", err=True)
        raise typer.Exit(1)
    except httpx.HTTPStatusError as e:
        # 404 = unknown provider name
        if e.response.status_code == 404:
            typer.echo(
                f"✗ Provider '{target}' not found. "
                "Run `clasp status --json` to see available providers.",
                err=True,
            )
        else:
            typer.echo(f"✗ Server returned {e.response.status_code}: {e.response.text}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"✗ Error: {e}", err=True)
        raise typer.Exit(2)

    # ── success output ────────────────────────────────────────────────────────
    if target == "all":
        reset_providers = data.get("providers", [])
        if reset_providers:
            typer.echo(f"✓ Reset all providers: {', '.join(reset_providers)}")
        else:
            typer.echo("✓ Reset all providers.")
    else:
        typer.echo(f"✓ Reset provider: {target}")