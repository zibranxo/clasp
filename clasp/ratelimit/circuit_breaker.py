"""
clasp/ratelimit/circuit_breaker.py

Per-key circuit breaker implementing the CLOSED → OPEN → HALF_OPEN state
machine.

Trip conditions (any):
  • 3 consecutive 429 responses
  • 5 consecutive timeouts
  • Error rate > 50% over last 10 requests (min 5 seen)

Recovery:
  OPEN → HALF_OPEN after cooldown_seconds (doubles on repeated trips, max 600s).
  HALF_OPEN + success → CLOSED.
  HALF_OPEN + failure → OPEN (cooldown doubled again).

Consecutive-streak semantics
-----------------------------
"Consecutive" is tracked per failure type: a run of `record_429()` calls
increments `_consecutive_429`, and any other event (`record_timeout`,
`record_error`, or `record_success`) breaks that streak back to zero. The
same applies symmetrically to `_consecutive_timeouts`. This means
interleaving 429s and timeouts will never trip the breaker via the
consecutive-count conditions alone — but every failure (429, timeout, or
generic 5xx via `record_error`) still feeds the rolling 10-request window
used by the error-rate condition, so a mix of failure types can still trip
the breaker if more than half of the last 10 (with at least 5 seen) failed.

HALF_OPEN is special-cased: while in that state, exactly one outcome (the
"test request") decides everything — any single failure call immediately
reopens the circuit (with the cooldown doubled), regardless of consecutive
counts; a success closes it and resets counters.
"""

from __future__ import annotations

import time
from collections import deque
from enum import Enum
import asyncio
from typing import Any

from loguru import logger
from clasp.config.provider_catalog import ProviderProfile


class CircuitState(str, Enum):
    """String enum so state serialises cleanly into JSON logs / UI payloads."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


State = str  # keeps compatibility with the first version's public shape


#: Trip thresholds from plan.md's state-machine description.
CONSECUTIVE_429_THRESHOLD = 3
CONSECUTIVE_TIMEOUT_THRESHOLD = 5
ERROR_RATE_THRESHOLD = 0.50
ERROR_RATE_MIN_SAMPLES = 5
ERROR_RATE_WINDOW = 10
MAX_COOLDOWN_SECONDS = 600


class CircuitBreaker:
    def __init__(self, profile: ProviderProfile | float) -> None:
        self.profile = profile
        self._base_cooldown_seconds = float(getattr(profile, "cooldown_seconds", profile))

        self._state: State = "closed"
        self._consecutive_429 = 0
        self._consecutive_timeouts = 0
        self._recent_outcomes: deque[bool] = deque(maxlen=ERROR_RATE_WINDOW)  # True = success
        self._trip_count = 0
        self._recovery_at = 0.0
        self._half_open_probe_dispatched = False

        # Thread safety
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # State observation
    # ------------------------------------------------------------------ #

    async def get_state(self) -> State:
        """
        Read-only state observation.
        """
        async with self._lock:
            self._maybe_transition_to_half_open_locked()
            return self._state

    async def get_cooldown_seconds(self) -> float:
        async with self._lock:
            return min(
                self._base_cooldown_seconds * (2 ** max(self._trip_count - 1, 0)),
                MAX_COOLDOWN_SECONDS,
            )

    async def get_consecutive_429s(self) -> int:
        async with self._lock:
            return self._consecutive_429

    async def get_consecutive_timeouts(self) -> int:
        async with self._lock:
            return self._consecutive_timeouts

    async def is_closed(self) -> bool:
        """
        The actual gating check `KeyPool.pick_key()` uses.

        CLOSED -> always True.
        OPEN, cooldown not yet elapsed -> False.
        OPEN, cooldown elapsed -> transitions to HALF_OPEN and returns
            True for exactly the first caller (claiming the probe slot);
            every other caller sees False until that probe's outcome is
            every other caller sees False until that probe's outcome is
            recorded via record_success()/record_429()/record_timeout()/
            record_error().
        """
        async with self._lock:
            self._maybe_transition_to_half_open_locked()
            if self._state == "closed":
                return True
            if self._state == "half_open":
                if not self._half_open_probe_dispatched:
                    self._half_open_probe_dispatched = True
                    return True
                return False
            return False  # still open

    async def check_recovery(self) -> State:
        """
        Explicit recovery check, useful for callers that want the latest
        state without claiming the half-open probe slot.
        """
        async with self._lock:
            self._maybe_transition_to_half_open_locked()
            return self._state

    def _maybe_transition_to_half_open_locked(self) -> None:
        if self._state == "open" and time.monotonic() >= self._recovery_at:
            self._state = "half_open"
            self._half_open_probe_dispatched = False
            logger.info(
                "circuit breaker recovered to HALF_OPEN",
                provider=getattr(self.profile, "display_name", None),
            )

    # ------------------------------------------------------------------ #
    # Outcome recording
    # ------------------------------------------------------------------ #

    async def record_success(self) -> None:
        async with self._lock:
            if self._state == "half_open":
                self._close_locked()
                return
            self._consecutive_429 = 0
            self._consecutive_timeouts = 0
            self._recent_outcomes.append(True)

    async def record_429(self) -> None:
        async with self._lock:
            self._consecutive_429 += 1
            self._consecutive_timeouts = 0
            self._recent_outcomes.append(False)
            self._on_failure_locked(
                trip_immediately=self._consecutive_429 >= CONSECUTIVE_429_THRESHOLD
            )

    async def record_timeout(self) -> None:
        async with self._lock:
            self._consecutive_timeouts += 1
            self._consecutive_429 = 0
            self._recent_outcomes.append(False)
            self._on_failure_locked(
                trip_immediately=self._consecutive_timeouts >= CONSECUTIVE_TIMEOUT_THRESHOLD
            )

    async def record_error(self) -> None:
        """Generic failure that's neither a 429 nor a timeout (e.g. a 5xx)."""
        async with self._lock:
            self._consecutive_429 = 0
            self._consecutive_timeouts = 0
            self._recent_outcomes.append(False)
            self._on_failure_locked(trip_immediately=False)

    async def reset(self) -> None:
        async with self._lock:
            self._state = "closed"
            self._consecutive_429 = 0
            self._consecutive_timeouts = 0
            self._recent_outcomes.clear()
            self._trip_count = 0
            self._recovery_at = 0.0
            self._half_open_probe_dispatched = False
            self._base_cooldown_seconds = float(
                getattr(self.profile, "cooldown_seconds", self.profile)
            )

    def _on_failure_locked(self, *, trip_immediately: bool) -> None:
        if self._state == "half_open":
            # The single test request failed — back to OPEN, cooldown doubles.
            self._trip_locked()
            return
        if self._state == "open":
            return  # already open, nothing more to evaluate
        if trip_immediately:
            self._trip_locked()
            return
        if (
            len(self._recent_outcomes) >= ERROR_RATE_MIN_SAMPLES
            and (self._recent_outcomes.count(False) / len(self._recent_outcomes))
            > ERROR_RATE_THRESHOLD
        ):
            self._trip_locked()

    # ------------------------------------------------------------------ #
    # State transitions
    # ------------------------------------------------------------------ #

    def _trip_locked(self) -> None:
        self._trip_count += 1
        cooldown = min(
            self._base_cooldown_seconds * (2 ** (self._trip_count - 1)),
            MAX_COOLDOWN_SECONDS,
        )
        self._recovery_at = time.monotonic() + cooldown
        self._state = "open"
        self._half_open_probe_dispatched = False
        logger.warning(
            "circuit breaker tripped to OPEN",
            provider=getattr(self.profile, "display_name", None),
            cooldown_seconds=cooldown,
            consecutive_429s=self._consecutive_429,
            consecutive_timeouts=self._consecutive_timeouts,
        )

    def _close_locked(self) -> None:
        self._state = "closed"
        self._trip_count = 0
        self._consecutive_429 = 0
        self._consecutive_timeouts = 0
        self._recent_outcomes.clear()
        self._half_open_probe_dispatched = False
        self._recovery_at = 0.0
        logger.info(
            "circuit breaker recovered to CLOSED",
            provider=getattr(self.profile, "display_name", None),
        )


# ---------------------------------------------------------------------------
# Per-(provider, key_index) store
# ---------------------------------------------------------------------------

class CircuitBreakerStore:
    """Maps (provider, key_index) → CircuitBreaker instance."""

    def __init__(self, base_cooldown_seconds: float = 30.0) -> None:
        self._breakers: dict[tuple[str, int], CircuitBreaker] = {}
        self._base_cooldown = base_cooldown_seconds
        self._lock = asyncio.Lock()

    async def get(self, provider: str, key_index: int) -> CircuitBreaker:
        key = (provider, key_index)
        async with self._lock:
            if key not in self._breakers:
                self._breakers[key] = CircuitBreaker(self._base_cooldown)
            return self._breakers[key]

    async def reset_provider(self, provider: str) -> None:
        async with self._lock:
            for (p, _), cb in self._breakers.items():
                if p == provider:
                    await cb.reset()

    async def reset_all(self) -> None:
        async with self._lock:
            for cb in self._breakers.values():
                await cb.reset()
            self._breakers.clear()


_default_cb_store = CircuitBreakerStore()


def get_cb_store() -> CircuitBreakerStore:
    return _default_cb_store


def reset_global_cb_store() -> None:
    _default_cb_store.reset_all()