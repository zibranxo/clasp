"""
clasp/ratelimit/cooldown.py

Tracks per-(provider, key_index) cooldown state after a 429: how long to
wait before that key is eligible for selection again, with the wait time
derived from the upstream `Retry-After` header when present, or an
exponential backoff (`base * 2^n`, capped at 600s) otherwise.
"""

from __future__ import annotations

import asyncio
import email.utils
import time
from datetime import datetime, timezone
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
            seconds = self._parse_retry_after(retry_after_header)
            seconds = min(seconds, 600.0)  # Cap at 10 minutes regardless of header
        else:
            n = self._get_failure_count(provider, key_index)
            # FROM V2: Safe fallback lookup using .get() to prevent KeyError crashes
            profile = self._catalog.get(provider)
            base = getattr(profile, "backoff_base_seconds", 60) if profile else 60
            seconds = min(base * (2**n), 600.0)

        recovery_at = time.monotonic() + seconds
        self._set_cooling(provider, key_index, recovery_at)
        self._increment_failure_count(provider, key_index)

        try:
            loop = asyncio.get_running_loop()
            loop.call_later(seconds, self._re_enable, provider, key_index)
        except RuntimeError:
            pass

        logger.warning(
            "provider key cooling down",
            provider=provider,
            key_index=key_index,
            seconds=seconds,
        )
        return seconds

    def is_cooling(self, provider: str, key_index: int) -> bool:
        """True if (provider, key_index) is currently in its cooldown window."""
        return (provider, key_index) in self._cooling

    def recovery_at(self, provider: str, key_index: int) -> float | None:
        """Monotonic timestamp when (provider, key_index) stops cooling, or None."""
        return self._cooling.get((provider, key_index))

    def seconds_remaining(self, provider: str, key_index: int) -> float:
        """Seconds left in the cooldown window (0.0 if not cooling)."""
        recovery = self._cooling.get((provider, key_index))
        if recovery is None:
            return 0.0
        return max(0.0, recovery - time.monotonic())

    def reset(self, provider: str, key_index: int) -> None:
        """Clear cooldown + failure count for (provider, key_index)."""
        self._cooling.pop((provider, key_index), None)
        self._failure_counts.pop((provider, key_index), None)

    # ── FROM V2: Persistence Export Hooks ────────────────────────────────

    def export_cooldowns(self) -> dict[str, float]:
        """Snapshot active recovery timestamps as absolute wall Unix timestamps."""
        now_monotonic = time.monotonic()
        now_wall = time.time()
        return {
            f"{provider}:{idx}": now_wall + (recovery_at - now_monotonic)
            for (provider, idx), recovery_at in self._cooling.items()
        }

    def import_cooldowns(self, snapshot: dict[str, float]) -> None:
        """Convert absolute wall timestamps back into process-relative monotonic time."""
        now_monotonic = time.monotonic()
        now_wall = time.time()
        for key, recovery_at_wall in snapshot.items():
            parsed = self._parse_composite_key(key)
            if not parsed:
                continue
            provider, idx = parsed
            recovery_at_monotonic = now_monotonic + (recovery_at_wall - now_wall)
            if recovery_at_monotonic > now_monotonic:
                self._cooling[(provider, idx)] = recovery_at_monotonic

    def export_failure_counts(self) -> dict[str, int]:
        """Snapshot failure counts for persistence."""
        return {f"{p}:{idx}": count for (p, idx), count in self._failure_counts.items()}

    def import_failure_counts(self, snapshot: dict[str, int]) -> None:
        """Load snapshotted failure counts back into the manager."""
        for key, count in snapshot.items():
            parsed = self._parse_composite_key(key)
            if parsed:
                self._failure_counts[parsed] = count

    # ── Internal helpers ─────────────────────────────────────────────────

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
        return self._failure_counts.get((provider, key_index), 0)

    def _increment_failure_count(self, provider: str, key_index: int) -> None:
        key = (provider, key_index)
        self._failure_counts[key] = self._failure_counts.get(key, 0) + 1

    def _set_cooling(self, provider: str, key_index: int, recovery_at: float) -> None:
        self._cooling[(provider, key_index)] = recovery_at

    def _re_enable(self, provider: str, key_index: int) -> None:
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