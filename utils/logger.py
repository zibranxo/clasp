"""
loguru logger configuration for CLASP.

Two sinks are registered:
  - stderr: colored, human-readable, INFO+ level.
  - file:   JSON-formatted, DEBUG+ level, rotating at 10 MB, 5 backups kept.
             Written to ~/.clasp/logs/router.log.

Call setup_logging() once at process startup before importing anything else
that logs.  After that, every module does:

    from loguru import logger
"""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

# Default log directory / file
_CLASP_DIR = Path.home() / ".clasp"
_LOG_DIR = _CLASP_DIR / "logs"
_LOG_FILE = _LOG_DIR / "router.log"

# Rotation / retention parameters (matches Appendix C spec)
_ROTATION = "10 MB"
_RETENTION = 5  # number of backup files to keep


def setup_logging(log_level: str = "INFO", *, debug: bool = False) -> None:
    """Configure loguru sinks.

    Parameters
    ----------
    log_level:
        Minimum level for the stderr sink.  Typically comes from
        ``settings.server.log_level`` or the ``CLASP_LOG_LEVEL`` env var.
        Defaults to ``"INFO"``.
    debug:
        When *True*, overrides ``log_level`` to ``"DEBUG"`` for both sinks
        (matches the ``--debug`` CLI flag behaviour).
    """
    if debug:
        log_level = "DEBUG"

    # Remove the default loguru sink so we control everything.
    logger.remove()

    # ── Sink 1: stderr — colored, human-readable ──────────────────────────
    logger.add(
        sys.stderr,
        level=log_level,
        colorize=True,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
    )

    # ── Sink 2: file — JSON, DEBUG+, rotating ─────────────────────────────
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger.add(
        str(_LOG_FILE),
        level="DEBUG",
        serialize=True,          # loguru built-in JSON serialisation
        rotation=_ROTATION,
        retention=_RETENTION,
        encoding="utf-8",
        enqueue=True,            # non-blocking writes from async context
    )

    logger.debug("Logging initialised (stderr={}, file={})", log_level, _LOG_FILE)


def get_log_file() -> Path:
    """Return the absolute path to the active log file.

    Used by ``GET /internal/logs/download`` to serve the raw file.
    """
    return _LOG_FILE
