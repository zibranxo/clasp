"""
clasp/ratelimit/cooldown.py

Tracks per-(provider, key_index) cooldown state after a 429: how long to
wait before that key is eligible for selection again, with the wait time
derived from the upstream `Retry-After` header when present, or an
exponential backoff (`base * 2^n`, capped at 600s) otherwise.

Locking
-------
All mutations to `_cooling` and `_failure_counts` are protected by a
`threading.Lock` (`_lock`).  The class is called from both sync and async
contexts (registry, key_pool, persistence) — `threading.Lock` is safe in
both without holding the event loop.

Generation counter
------------------
Each call to `on_429()` bumps a per-key generation counter.  The asyncio
callback scheduled to re-enable the key captures the generation at
scheduling time and only clears the key if the generation still matches
when it fires.  This prevents an older, shorter timer from silently
cancelling a newer, longer cooldown when a key receives repeated 429s.

Restart persistence
-------------------
`import_cooldowns()` now re-schedules the re-enable callback for every
entry whose recovery timestamp is still in the future, so a cooldown
restored after a restart expires correctly rather than remaining active
indefinitely.

Retry-After parsing
-------------------
Both numeric seconds and RFC-7231 date strings are accepted.  Values
(whether parsed from a header or computed by exponential backoff) are
clamped to [0.0, 600.0] before being used.
"""

from __future__ import annotations

import asyncio
import email.utils
import time
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from loguru import logger


class CooldownManager:
    """
    Per-(provider, key_index) cooldown tracker.

    `on_429()` records the failure, computes cooldown window, schedules
    automatic re-enablement, and returns the wait in seconds.
    """

    def __init__(self, catalog: dict[str, Any] | None = None) -> None:
        if catalog is None:
            from clasp.config.provider_catalog import PROVIDER_CATALOG  # noqa: PLC0415

            catalog = PROVIDER_CATALOG
        self._catalog = catalog

        # (provider, key_index) -> monotonic timestamp when cooling ends.
        self._cooling: dict[tuple[str, int], float] = {}
        # (provider, key_index) -> consecutive failure count
        self._failure_counts: dict[tuple[str, int], int] = {}
        # (provider, key_index) -> generation counter (incremented each on_429)
        self._generation: dict[tuple[str, int], int] = {}
        # Thread-safety for shared dicts
        self._lock = Lock()

    # ── Public API ───────────────────────────────────────────────────────

    def on_429(
        self,
        provider: str,
        key_index: int,
        retry_after_header: str | None,
    ) -> float:
        """
        Record a 429 for (provider, key_index) and start its cooldown.
        Returns the number of seconds the key will be cooling for.
        """
        if retry_after_header:
            raw = self._parse_retry_after(retry_after_header)
            seconds = max(0.0, min(raw, 600.0))
        else:
            n = self._get_failure_count(provider, key_index)
            profile = self._catalog.get(provider)
            base = getattr(profile, "backoff_base_seconds", 60) if profile else 60
            seconds = min(base * (2 ** n), 600.0)

        recovery_at = time.monotonic() + seconds

        with self._lock:
            self._cooling[(provider, key_index)] = recovery_at
            self._failure_counts[(provider, key_index)] = (
                self._failure_counts.get((provider, key_index), 0) + 1
            )
            gen = self._generation.get((provider, key_index), 0) + 1
            self._generation[(provider, key_index)] = gen

        # Schedule re-enable callback with the current generation captured.
        self._schedule_reenable(provider, key_index, seconds, gen)

        logger.warning(
            "provider key cooling down",
            provider=provider,
            key_index=key_index,
            seconds=seconds,
        )
        return seconds

    def is_cooling(self, provider: str, key_index: int) -> bool:
        """True if (provider, key_index) is currently in its cooldown window."""
        with self._lock:
            recovery = self._cooling.get((provider, key_index))
            if recovery is None:
                return False
            if time.monotonic() >= recovery:
                # Eagerly expire — clean up so the dict doesn't accumulate stale entries.
                self._cooling.pop((provider, key_index), None)
                return False
            return True

    def recovery_at(self, provider: str, key_index: int) -> float | None:
        """Monotonic timestamp when (provider, key_index) stops cooling, or None."""
        with self._lock:
            return self._cooling.get((provider, key_index))

    def seconds_remaining(self, provider: str, key_index: int) -> float:
        """Seconds left in the cooldown window (0.0 if not cooling)."""
        with self._lock:
            recovery = self._cooling.get((provider, key_index))
        if recovery is None:
            return 0.0
        return max(0.0, recovery - time.monotonic())

    def reset(self, provider: str, key_index: int) -> None:
        """Clear cooldown + failure count for (provider, key_index)."""
        with self._lock:
            self._cooling.pop((provider, key_index), None)
            self._failure_counts.pop((provider, key_index), None)
            # Bump generation so any in-flight callback won't fire.
            self._generation[(provider, key_index)] = (
                self._generation.get((provider, key_index), 0) + 1
            )

    # ── Persistence Export Hooks ─────────────────────────────────────────

    def export_cooldowns(self) -> dict[str, float]:
        """Snapshot active recovery timestamps as absolute wall Unix timestamps."""
        now_monotonic = time.monotonic()
        now_wall = time.time()
        with self._lock:
            items = list(self._cooling.items())
        return {
            f"{provider}:{idx}": now_wall + (recovery_at - now_monotonic)
            for (provider, idx), recovery_at in items
        }

    def import_cooldowns(self, snapshot: dict[str, float]) -> None:
        """
        Convert absolute wall timestamps back into process-relative monotonic
        time and reschedule re-enable callbacks for still-active entries.
        """
        now_monotonic = time.monotonic()
        now_wall = time.time()
        for key, recovery_at_wall in snapshot.items():
            parsed = self._parse_composite_key(key)
            if not parsed:
                continue
            provider, idx = parsed
            recovery_at_monotonic = now_monotonic + (recovery_at_wall - now_wall)
            if recovery_at_monotonic > now_monotonic:
                remaining = recovery_at_monotonic - now_monotonic
                with self._lock:
                    self._cooling[(provider, idx)] = recovery_at_monotonic
                    gen = self._generation.get((provider, idx), 0) + 1
                    self._generation[(provider, idx)] = gen
                # Re-schedule the expiry callback so it fires correctly.
                self._schedule_reenable(provider, idx, remaining, gen)

    def export_failure_counts(self) -> dict[str, int]:
        """Snapshot failure counts for persistence."""
        with self._lock:
            return {f"{p}:{idx}": count for (p, idx), count in self._failure_counts.items()}

    def import_failure_counts(self, snapshot: dict[str, int]) -> None:
        """Load snapshotted failure counts back into the manager."""
        for key, count in snapshot.items():
            parsed = self._parse_composite_key(key)
            if parsed:
                with self._lock:
                    self._failure_counts[parsed] = count

    # ── Internal helpers ─────────────────────────────────────────────────

    def _schedule_reenable(
        self, provider: str, key_index: int, seconds: float, generation: int
    ) -> None:
        """Schedule a re-enable callback on the running event loop, if any."""
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(seconds, self._re_enable, provider, key_index, generation)
        except RuntimeError:
            # No running event loop (e.g. called from a sync test or startup
            # before the server loop is started).  is_cooling() already does
            # timestamp-based lazy expiry, so the entry will expire correctly
            # when checked even without a scheduled callback.
            pass

    def _parse_composite_key(self, key: str) -> tuple[str, int] | None:
        provider, _, idx_str = key.rpartition(":")
        if not provider:
            return None
        try:
            return provider, int(idx_str)
        except ValueError:
            return None

    def _parse_retry_after(self, value: str) -> float:
        try:
            return float(value)
        except ValueError:
            pass
        try:
            dt = email.utils.parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except Exception:  # noqa: BLE001
            return 60.0

    def _get_failure_count(self, provider: str, key_index: int) -> int:
        with self._lock:
            return self._failure_counts.get((provider, key_index), 0)

    def _re_enable(self, provider: str, key_index: int, generation: int) -> None:
        """
        Remove the cooling entry — but only if the generation matches.

        A mismatch means `on_429()` was called again after this callback
        was scheduled, extending the cooldown.  In that case, a newer
        callback with the updated generation will do the cleanup.
        """
        with self._lock:
            current_gen = self._generation.get((provider, key_index), 0)
            if current_gen != generation:
                return  # Superseded by a newer cooldown — leave it in place.
            self._cooling.pop((provider, key_index), None)
        logger.info(
            "provider key cooldown elapsed, re-enabled",
            provider=provider,
            key_index=key_index,
        )


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------

_cooldown_manager: CooldownManager | None = None


def get_cooldown_manager() -> CooldownManager:
    global _cooldown_manager  # noqa: PLW0603
    if _cooldown_manager is None:
        _cooldown_manager = CooldownManager()
    return _cooldown_manager


def reset_cooldown_manager(catalog: dict[str, Any] | None = None) -> None:
    global _cooldown_manager  # noqa: PLW0603
    _cooldown_manager = CooldownManager(catalog=catalog)