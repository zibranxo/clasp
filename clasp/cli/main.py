"""
clasp/cli/main.py
``clasp`` Typer entry point.

Entry point is declared in ``pyproject.toml``::

    [project.scripts]
    clasp = "clasp.cli.main:app"

Subcommands
-----------
  clasp server   — Start proxy + open config UI.
  clasp claude   — Launch Claude Code through the proxy (os.execvp).
  clasp status   — One-line status line (Sprint 6).
  clasp stop     — Graceful shutdown via SIGTERM (Sprint 6).
  clasp reset    — Clear provider cooldowns (Sprint 6).
  clasp init     — Interactive first-run wizard (Sprint 6).

Sprint 1 ships ``server`` and ``claude`` only.
The other commands print a «coming soon» stub so the CLI entry point is
complete and users get clear feedback rather than a crash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Optional

import typer

app = typer.Typer(
    name="clasp",
    help=(
        "CLASP — Claude API Switching Proxy.\n\n"
        "Rate-limit-aware local proxy that lets Claude Code use free-tier\n"
        "OpenAI-compatible APIs without ever seeing a 429 error.\n\n"
        "Quick start:\n\n"
        "  clasp server    # Start proxy + open config UI\n\n"
        "  clasp claude    # Launch Claude Code through the proxy"
    ),
    invoke_without_command=True,
    no_args_is_help=True,
    add_completion=False,
)


# ---------------------------------------------------------------------------
# clasp server
# ---------------------------------------------------------------------------

@app.command("server", help="Start the CLASP proxy and open the config UI in the browser.")
def cmd_server(
    port: Annotated[
        int,
        typer.Option("--port", "-p", help="Proxy port.", envvar="CLASP_PORT"),
    ] = 8082,
    host: Annotated[
        str,
        typer.Option("--host", help="Bind address."),
    ] = "127.0.0.1",
    no_browser: Annotated[
        bool,
        typer.Option("--no-browser", help="Start server without opening the browser."),
    ] = False,
    live: Annotated[
        bool,
        typer.Option("--live", help="Show rich TUI live panel (Sprint 6)."),
    ] = False,
    config: Annotated[
        Optional[Path],
        typer.Option(
            "--config",
            "-c",
            help="Path to config.yaml.",
            envvar="CLASP_CONFIG_PATH",
            exists=False,
            file_okay=True,
            dir_okay=False,
        ),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Enable debug logging."),
    ] = False,
) -> None:
    from clasp.cli.cmd_server import run

    run(
        port=port,
        host=host,
        no_browser=no_browser,
        live=live,
        config=config,
        debug=debug,
    )


# ---------------------------------------------------------------------------
# clasp claude
# ---------------------------------------------------------------------------

@app.command(
    "claude",
    help="Launch Claude Code connected to the CLASP proxy.",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def cmd_claude(
    ctx: typer.Context,
    auto_start: Annotated[
        bool,
        typer.Option(
            "--auto-start",
            help="Auto-start the CLASP server if it is not running.",
            hidden=True,
        ),
    ] = False,
) -> None:
    """
    Launch Claude Code through the CLASP proxy.

    All extra arguments are forwarded unchanged to the ``claude`` binary::

      clasp claude --resume abc123def456
      clasp claude --continue
      clasp claude --print "fix this bug"
      clasp claude --dangerously-skip-permissions
    """
    from clasp.cli.cmd_claude import run

    run(passthrough_args=list(ctx.args), auto_start=auto_start)


# ---------------------------------------------------------------------------
# clasp status  (Sprint 6 stub)
# ---------------------------------------------------------------------------

@app.command("status", help="Print one-line proxy status (Sprint 6).")
def cmd_status(
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Output as JSON."),
    ] = False,
) -> None:
    try:
        from clasp.cli.cmd_status import run  # type: ignore[import]

        run(json_output=json_output)
    except ImportError:
        _sprint6_stub("status")


# ---------------------------------------------------------------------------
# clasp stop  (Sprint 6 stub)
# ---------------------------------------------------------------------------

@app.command("stop", help="Gracefully stop the running CLASP server (Sprint 6).")
def cmd_stop() -> None:
    try:
        from clasp.cli.cmd_stop import run  # type: ignore[import]

        run()
    except ImportError:
        _sprint6_stub("stop")


# ---------------------------------------------------------------------------
# clasp reset  (Sprint 6 stub)
# ---------------------------------------------------------------------------

@app.command("reset", help="Reset provider cooldown state (Sprint 6).")
def cmd_reset(
    provider: Annotated[
        Optional[str],
        typer.Argument(help="Provider to reset (omit to reset all)."),
    ] = None,
) -> None:
    try:
        from clasp.cli.cmd_reset import run  # type: ignore[import]

        run(provider=provider)
    except ImportError:
        _sprint6_stub("reset")


# ---------------------------------------------------------------------------
# clasp init  (Sprint 6 stub)
# ---------------------------------------------------------------------------

@app.command("init", help="Interactive first-run setup wizard (Sprint 6).")
def cmd_init() -> None:
    try:
        from clasp.cli.cmd_init import run  # type: ignore[import]

        run()
    except ImportError:
        _sprint6_stub("init")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sprint6_stub(command: str) -> None:
    typer.echo(
        f"\n  `clasp {command}` is coming in Sprint 6.\n"
        "  For now, use the web UI at http://127.0.0.1:8082.\n",
        err=True,
    )
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()