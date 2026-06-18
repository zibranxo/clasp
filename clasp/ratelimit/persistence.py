"""
clasp/ratelimit/persistence.py

Saves/loads ~/.clasp/ratelimit.json so cooldown timers and daily usage
counters survive a `clasp server` restart (design principle P7).

Recovery timestamps are stored as absolute Unix time (not
time.monotonic(), which resets to an arbitrary epoch every process
start) — the monotonic<->wall-clock conversion itself lives in
CooldownTracker.export_cooldowns()/import_cooldowns(), since that's
where the underlying _cooling_until dict actually lives; this module
just orchestrates when to call those and where the bytes go on disk.

On load, if the stored date doesn't match today, daily counters reset
to {} (cooldowns/failure counts are NOT date-gated — a key cooling down
at 11:59pm should still be cooling at 12:01am).
"""

from __future__ import annotations

import asyncio
import json
from datetime import date as date_cls
from pathlib import Path
from typing import Callable

from loguru import logger

from clasp.ratelimit.cooldown import CooldownTracker

DEFAULT_STATE_PATH = Path.home() / ".clasp" / "ratelimit.json"
DEFAULT_SAVE_INTERVAL_SECONDS = 30.0


def _today_str() -> str:
    return date_cls.today().isoformat()


# --------------------------------------------------------------------------- #
# Save
# --------------------------------------------------------------------------- #


def build_snapshot(tracker: CooldownTracker, daily_counters: dict | None = None) -> dict:
    return {
        "date": _today_str(),
        "cooldowns": tracker.export_cooldowns(),
        "failure_counts": tracker.export_failure_counts(),
        "daily_counters": daily_counters or {},
    }


def save_state(
    tracker: CooldownTracker,
    *,
    daily_counters: dict | None = None,
    path: Path | None = None,
) -> None:
    """
    Write the current rate-limit state to disk, atomically (write to a
    temp file, then rename — rename is atomic on POSIX, so a reader never
    sees a half-written file). Safe to call from a periodic task or a
    shutdown handler; never raises — a failed save is logged and
    swallowed rather than crashing the caller.
    """
    path = path or DEFAULT_STATE_PATH
    snapshot = build_snapshot(tracker, daily_counters)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(snapshot, indent=2))
        tmp_path.replace(path)
    except OSError as e:
        logger.warning(f"ratelimit persistence: failed to save state to {path}: {e}")


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #


def load_state(tracker: CooldownTracker, *, path: Path | None = None) -> dict:
    """
    Load persisted state into `tracker` in place (cooldowns + failure
    counts). Returns the daily counters dict to seed elsewhere — reset to
    `{}` if the stored date doesn't match today.

    Never raises: a missing file returns `{}` silently (nothing persisted
    yet); a corrupt/unreadable file logs a warning and also returns `{}`
    rather than taking down startup.
    """
    path = path or DEFAULT_STATE_PATH
    if not path.exists():
        return {}

    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"ratelimit persistence: failed to read {path}, starting fresh: {e}")
        return {}

    if not isinstance(raw, dict):
        logger.warning(f"ratelimit persistence: {path} is not a JSON object, starting fresh")
        return {}

    tracker.import_cooldowns(raw.get("cooldowns") or {})
    tracker.import_failure_counts(raw.get("failure_counts") or {})

    stored_date = raw.get("date")
    daily_counters = raw.get("daily_counters") or {}
    today = _today_str()
    if stored_date != today:
        logger.info(
            f"ratelimit persistence: stored date {stored_date!r} != today {today!r}, "
            f"resetting daily counters"
        )
        return {}
    return daily_counters


# --------------------------------------------------------------------------- #
# Periodic background save
# --------------------------------------------------------------------------- #


async def periodic_save_task(
    tracker: CooldownTracker,
    *,
    daily_counters_provider: Callable[[], dict] | None = None,
    interval_seconds: float = DEFAULT_SAVE_INTERVAL_SECONDS,
    path: Path | None = None,
) -> None:
    """
    Background task: save_state() every `interval_seconds`, forever,
    until cancelled. Intended use from server.py:

        save_task = asyncio.create_task(periodic_save_task(tracker, ...))
        ...
        save_task.cancel()
        await save_task   # the CancelledError handler below does one
                           # final save before re-raising, satisfying
                           # "save on clean shutdown"

    `daily_counters_provider`, if given, is called fresh on every save
    (and on the final shutdown save) so this module never has to know
    how/where daily usage counts are actually tracked.
    """
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            counters = daily_counters_provider() if daily_counters_provider else None
            save_state(tracker, daily_counters=counters, path=path)
    except asyncio.CancelledError:
        counters = daily_counters_provider() if daily_counters_provider else None
        save_state(tracker, daily_counters=counters, path=path)
        raise