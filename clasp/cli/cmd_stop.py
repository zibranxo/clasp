"""
clasp/cli/cmd_stop.py

`clasp stop`

Gracefully stop the running CLASP server.

  1. Read PID from ~/.clasp/clasp.pid.
  2. Send SIGTERM.
  3. Poll until the process exits (max 15 s).
  4. If it doesn't exit, send SIGKILL as a last resort.
  5. Remove the stale PID file on success.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path

import typer

_PID_PATH = Path.home() / ".clasp" / "clasp.pid"
_WAIT_TIMEOUT = 15.0    # seconds to wait for graceful exit before SIGKILL
_POLL_INTERVAL = 0.25   # seconds between liveness checks


# ── helpers ──────────────────────────────────────────────────────────────────

def _read_pid() -> int | None:
    """Return the PID from the PID file, or None if file is missing / invalid."""
    if not _PID_PATH.exists():
        return None
    try:
        return int(_PID_PATH.read_text().strip())
    except ValueError:
        return None


def _process_alive(pid: int) -> bool:
    """Return True if the process with `pid` is still running."""
    try:
        os.kill(pid, 0)   # signal 0 = existence check only
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True       # exists, just not signallable by us


def _cleanup_pid_file() -> None:
    _PID_PATH.unlink(missing_ok=True)


# ── command ──────────────────────────────────────────────────────────────────

def stop_cmd() -> None:
    """Gracefully stop the running CLASP server."""

    pid = _read_pid()

    if pid is None:
        typer.echo("CLASP is not running (no PID file found).")
        raise typer.Exit(0)

    if not _process_alive(pid):
        typer.echo(f"CLASP is not running (stale PID file for pid {pid}).")
        _cleanup_pid_file()
        raise typer.Exit(0)

    # ── send SIGTERM ──────────────────────────────────────────────────────────
    typer.echo(f"Stopping CLASP (pid {pid})...", nl=False)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        typer.echo(" already stopped.")
        _cleanup_pid_file()
        raise typer.Exit(0)
    except PermissionError:
        typer.echo(f"\n✗ Permission denied to signal pid {pid}.", err=True)
        raise typer.Exit(1)

    # ── wait for clean exit ───────────────────────────────────────────────────
    deadline = time.monotonic() + _WAIT_TIMEOUT
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            typer.echo(" stopped.")
            _cleanup_pid_file()
            raise typer.Exit(0)
        typer.echo(".", nl=False)
        time.sleep(_POLL_INTERVAL)

    # ── SIGKILL fallback ──────────────────────────────────────────────────────
    typer.echo(
        f"\n⚠ Process {pid} did not exit after {_WAIT_TIMEOUT:.0f}s. "
        "Sending SIGKILL...",
        nl=False,
    )
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass  # raced — already gone

    # Brief wait for SIGKILL to take effect
    for _ in range(10):
        time.sleep(0.1)
        if not _process_alive(pid):
            break

    if not _process_alive(pid):
        typer.echo(" killed.")
        _cleanup_pid_file()
    else:
        typer.echo(
            f"\n✗ Could not stop CLASP (pid {pid}). Kill it manually: kill -9 {pid}",
            err=True,
        )
        raise typer.Exit(1)