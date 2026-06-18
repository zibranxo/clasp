"""
clasp/ratelimit/cooldown.py

Per-(provider, key_index) cooldown tracking: when a 429 comes back, this
decides how long to wait before that key is usable again, and schedules
its own re-enable.

plan.md's KeyPool and selector.py reference code both call this as a bare
module (`cooldown.is_cooling(...)`, `cooldown.seconds_until_recovery(...)`),
implying one process-wide tracker. The real logic lives in
`CooldownTracker` though, with a default singleton + module-level
wrapper functions delegating to it — same shape `providers/registry.py`
uses — so callers get the module-level convenience plan.md shows, while
tests can construct an isolated `CooldownTracker()` and inject it
directly wherever dependency injection is available (e.g.
`KeyPool(..., cooldown_tracker=...)`), instead of fighting shared global
state across test cases.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from clasp.config.provider_catalog import ProviderProfile

#: Retry-After is capped at this many seconds regardless of what the
#: header says — plan.md Section 10: "Cap at 10 minutes regardless of header".
MAX_COOLDOWN_SECONDS = 600.0

#: Fallback backoff_base_seconds when a provider isn't in the catalog
#: passed to a CooldownTracker (shouldn't normally happen — defensive only).
DEFAULT_BACKOFF_BASE_SECONDS = 60


class CooldownTracker:
    def __init__(self, catalog: dict[str, ProviderProfile] | None = None) -> None:
        self._catalog: dict[str, ProviderProfile] = catalog or {}
        # Keyed (provider, key_index) -> absolute time.monotonic() recovery instant.
        self._cooling_until: dict[tuple[str, int], float] = {}
        # Keyed (provider, key_index) -> consecutive-429-without-Retry-After count,
        # used for the exponential-backoff fallback.
        self._failure_counts: dict[tuple[str, int], int] = {}

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def is_cooling(self, provider: str, key_index: int) -> bool:
        recovery_at = self._cooling_until.get((provider, key_index))
        if recovery_at is None:
            return False
        return time.monotonic() < recovery_at

    def seconds_until_recovery(self, provider: str, key_index: int) -> float:
        recovery_at = self._cooling_until.get((provider, key_index))
        if recovery_at is None:
            return 0.0
        return max(0.0, recovery_at - time.monotonic())

    # ------------------------------------------------------------------ #
    # The main event: a 429 came back
    # ------------------------------------------------------------------ #

    def on_429(
        self,
        provider: str,
        key_index: int,
        retry_after_header: str | None,
    ) -> float:
        """
        Returns seconds to wait. Parses Retry-After when present (capped
        at MAX_COOLDOWN_SECONDS regardless of what the header says);
        otherwise falls back to exponential backoff seeded from the
        provider's catalog `backoff_base_seconds`, doubling per
        consecutive 429-without-header seen for this key.
        """
        if retry_after_header:
            seconds = min(self._parse_retry_after(retry_after_header), MAX_COOLDOWN_SECONDS)
        else:
            n = self._get_failure_count(provider, key_index)
            base = self._get_backoff_base(provider)
            seconds = min(base * (2**n), MAX_COOLDOWN_SECONDS)

        recovery_at = time.monotonic() + seconds
        self._set_cooling(provider, key_index, recovery_at)
        self._increment_failure_count(provider, key_index)

        try:
            loop = asyncio.get_running_loop()
            loop.call_later(seconds, self._re_enable, provider, key_index)
        except RuntimeError:
            # No running event loop (e.g. called from sync test code).
            # is_cooling()'s own monotonic-time check still self-expires
            # correctly without this callback ever firing — the callback
            # is a proactive cleanup, not the only path to recovery.
            pass

        return seconds

    def reset(self, provider: str, key_index: int) -> None:
        """Clear cooldown + failure count for a key — e.g. call this after
        a clean success on that key, so a single old 429 doesn't keep
        inflating the next backoff via a stale failure count."""
        self._cooling_until.pop((provider, key_index), None)
        self._failure_counts.pop((provider, key_index), None)

    def _re_enable(self, provider: str, key_index: int) -> None:
        """call_later's target: proactively clears the cooldown entry once
        it elapses. `is_cooling()` already self-expires via the monotonic
        check regardless, so this mainly keeps `seconds_until_recovery()`
        reporting 0 promptly rather than a stale-but-expired timestamp."""
        self._cooling_until.pop((provider, key_index), None)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _get_backoff_base(self, provider: str) -> int:
        profile = self._catalog.get(provider)
        return profile.backoff_base_seconds if profile else DEFAULT_BACKOFF_BASE_SECONDS

    def _get_failure_count(self, provider: str, key_index: int) -> int:
        return self._failure_counts.get((provider, key_index), 0)

    def _increment_failure_count(self, provider: str, key_index: int) -> None:
        key = (provider, key_index)
        self._failure_counts[key] = self._failure_counts.get(key, 0) + 1

    def _set_cooling(self, provider: str, key_index: int, recovery_at: float) -> None:
        self._cooling_until[(provider, key_index)] = recovery_at

    @staticmethod
    def _parse_retry_after(value: str) -> float:
        """Handles both integer-seconds and HTTP-date formats (RFC 9110)."""
        try:
            return float(value)
        except ValueError:
            pass
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return 60.0

    # ------------------------------------------------------------------ #
    # Persistence hooks (used by ratelimit/persistence.py)
    # ------------------------------------------------------------------ #

    def export_cooldowns(self) -> dict[str, float]:
        """
        Snapshot active recovery timestamps as ABSOLUTE Unix time (not
        time.monotonic(), which is meaningless across a process restart),
        keyed "provider:key_index" since JSON needs string keys.
        """
        now_monotonic = time.monotonic()
        now_wall = time.time()
        return {
            f"{provider}:{key_index}": now_wall + (recovery_at - now_monotonic)
            for (provider, key_index), recovery_at in self._cooling_until.items()
        }

    def import_cooldowns(self, snapshot: dict[str, float]) -> None:
        """Inverse of export_cooldowns(): convert absolute Unix timestamps
        back to this process's monotonic clock. Entries that have already
        passed are silently dropped rather than imported as "already
        cooling for a negative duration"."""
        now_monotonic = time.monotonic()
        now_wall = time.time()
        for key, recovery_at_wall in snapshot.items():
            parsed = self._parse_composite_key(key)
            if parsed is None:
                continue
            provider, key_index = parsed
            recovery_at_monotonic = now_monotonic + (recovery_at_wall - now_wall)
            if recovery_at_monotonic > now_monotonic:
                self._cooling_until[(provider, key_index)] = recovery_at_monotonic

    def export_failure_counts(self) -> dict[str, int]:
        return {f"{provider}:{idx}": count for (provider, idx), count in self._failure_counts.items()}

    def import_failure_counts(self, snapshot: dict[str, int]) -> None:
        for key, count in snapshot.items():
            parsed = self._parse_composite_key(key)
            if parsed is None:
                continue
            self._failure_counts[parsed] = count

    @staticmethod
    def _parse_composite_key(key: str) -> tuple[str, int] | None:
        provider, _, idx_str = key.rpartition(":")
        if not provider:
            return None
        try:
            return provider, int(idx_str)
        except ValueError:
            return None


# --------------------------------------------------------------------------- #
# Default process-wide singleton + module-level convenience functions
# --------------------------------------------------------------------------- #

_default_tracker = CooldownTracker()


def get_default_tracker() -> CooldownTracker:
    return _default_tracker


def configure_catalog(catalog: dict[str, ProviderProfile]) -> None:
    """Wire the real provider catalog into the default tracker (call once
    at startup, from registry.py) so on_429()'s exponential-backoff
    fallback uses each provider's actual backoff_base_seconds instead of
    the generic 60s default."""
    _default_tracker._catalog = catalog


def is_cooling(provider: str, key_index: int) -> bool:
    return _default_tracker.is_cooling(provider, key_index)


def seconds_until_recovery(provider: str, key_index: int) -> float:
    return _default_tracker.seconds_until_recovery(provider, key_index)


def on_429(provider: str, key_index: int, retry_after_header: str | None) -> float:
    return _default_tracker.on_429(provider, key_index, retry_after_header)


def reset(provider: str, key_index: int) -> None:
    _default_tracker.reset(provider, key_index)