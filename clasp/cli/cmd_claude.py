"""
`clasp claude` — launch Claude Code connected to the CLASP proxy.

Implements the exact behavior from plan.md §3 "clasp claude":
  1. Check if the CLASP server is running (PID file). If not: warn, and
     auto-start it in the background (prompting first if stdin is a TTY).
  2. Read config to get server port and api_key.
  3. Set ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_API_KEY /
     CLAUDE_CODE_AUTO_COMPACT_WINDOW / CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY
     / CLASP_SESSION.
  4. os.execvp() the real `claude` binary with all passed-through flags.

Per plan.md §19 "clasp claude exec semantics": this MUST use os.execvp(), never
subprocess.run(). exec replaces the current process image in-place, so:
  - Terminal raw mode (arrow keys, multiline input) works natively.
  - Ctrl+C goes directly to `claude`, not to a Python wrapper.
  - `--resume` / `--continue` behave exactly as if the user invoked `claude` directly.
  - Process title shows as `claude`, not `python`.
Nothing after a successful os.execvp() call runs — it only "fails" (raises
OSError) if the binary can't be found/executed, which we've already guarded
against via shutil.which().

Integration note: this file assumes clasp.config.settings.get_settings()
exists with the interface shown in plan.md (settings.server.port,
settings.server.api_key) — verify against the actual file if env vars come
out wrong.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import time

import httpx
import typer
from loguru import logger

from clasp.utils.pid import DEFAULT_PID_PATH, is_running


def claude(ctx: typer.Context) -> None:
    """Launch Claude Code through the CLASP proxy. All flags pass through to `claude`.

    Registered into the main Typer app (main.py) with
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
    so unrecognized flags (--resume, --continue, --print, etc.) land in
    ctx.args instead of causing a Typer parse error.
    """
    run(list(ctx.args))


def run(passthrough_args: list[str]) -> None:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        sys.exit(
            "claude binary not found. Install: npm install -g @anthropic-ai/claude-code"
        )

    _ensure_server_running()

    from clasp.config.settings import get_settings

    settings = get_settings()

    os.environ.update(
        {
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{settings.server.port}",
            "ANTHROPIC_AUTH_TOKEN": settings.server.api_key,
            "ANTHROPIC_API_KEY": settings.server.api_key,
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": "190000",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
            "CLASP_SESSION": "1",
        }
    )

    logger.debug(
        "Exec'ing {} with {} passthrough args (proxy at {})",
        claude_bin,
        len(passthrough_args),
        os.environ["ANTHROPIC_BASE_URL"],
    )

    os.execvp(claude_bin, [claude_bin] + passthrough_args)
    # exec replaces this process — nothing below this line ever runs if it succeeds.


def _ensure_server_running() -> None:
    """Warn and auto-start the CLASP server if it isn't already running.

    Prompts for confirmation only when stdin is a TTY (interactive shell);
    in non-interactive contexts (scripts, CI) it auto-starts without
    blocking on input, since there's no one there to answer a prompt.
    """
    already_running, _ = is_running(DEFAULT_PID_PATH)
    if already_running:
        return

    print("⚠ CLASP server is not running.")

    should_start = True
    if sys.stdin.isatty():
        should_start = typer.confirm("Start it now in the background?", default=True)

    if not should_start:
        sys.exit("Aborted. Start it manually with: clasp server")

    print("→ Starting CLASP server in the background...")
    # Detached background process: start_new_session=True so it survives this
    # process later replacing itself via os.execvp() into `claude`.
    subprocess.Popen(
        [sys.executable, "-m", "clasp.cli.main", "server", "--no-browser"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not asyncio.run(_wait_for_health(timeout=8.0)):
        sys.exit(
            "✗ CLASP server did not become healthy within 8s. "
            "Check `clasp status` or run `clasp server` directly to see the error."
        )
    print("✓ CLASP server is up.")


async def _wait_for_health(*, timeout: float) -> bool:
    """Poll /health until it responds 200 or the timeout elapses.

    Reads the port from settings rather than assuming the default, since the
    user's config.yaml may have a custom server.port.
    """
    from clasp.config.settings import get_settings

    port = get_settings().server.port
    url = f"http://127.0.0.1:{port}/health"

    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            try:
                r = await client.get(url, timeout=0.5)
                if r.status_code == 200:
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.2)
    return False