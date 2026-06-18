"""
tests/unit/test_circuit_breaker.py

Covers clasp/ratelimit/circuit_breaker.py per plan.md §18 testing strategy:

- 3 consecutive record_429() → state OPEN.
- OPEN + elapsed cooldown → check_recovery() → state HALF_OPEN.
- HALF_OPEN + record_success() → state CLOSED, counters reset.
- HALF_OPEN + record_429() → state OPEN, cooldown doubled.
- 5 consecutive timeouts → trips to OPEN.
- Mixed errors < threshold → stays CLOSED.

Run with: pytest tests/unit/test_circuit_breaker.py -v --asyncio-mode=auto
(Most tests here are synchronous since CircuitBreaker has no async methods;
asyncio.sleep is used only where real wall-clock elapsing of the cooldown
needs to be observed.)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from clasp.ratelimit.circuit_breaker import CircuitBreaker, CircuitState


# ---------------------------------------------------------------------------
# Lightweight stand-in for ProviderProfile — only `cooldown_seconds` and
# `display_name` are actually read by CircuitBreaker.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FakeProfile:
    cooldown_seconds: float
    display_name: str = "fake-provider"


def make_cb(cooldown_seconds: float = 0.05) -> CircuitBreaker:
    """Build a CircuitBreaker with a short cooldown so tests run fast."""
    return CircuitBreaker(_FakeProfile(cooldown_seconds=cooldown_seconds))


# ---------------------------------------------------------------------------
# 1. 3 consecutive 429s → OPEN
# ---------------------------------------------------------------------------

def test_three_consecutive_429s_trips_open():
    cb = make_cb()
    assert cb.state == CircuitState.CLOSED

    cb.record_429()
    assert cb.state == CircuitState.CLOSED  # 1st — not yet tripped
    cb.record_429()
    assert cb.state == CircuitState.CLOSED  # 2nd — not yet tripped
    cb.record_429()
    assert cb.state == CircuitState.OPEN    # 3rd — trips


def test_two_consecutive_429s_then_success_does_not_trip():
    cb = make_cb()
    cb.record_429()
    cb.record_429()
    cb.record_success()  # breaks the streak
    cb.record_429()
    assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# 2. OPEN + cooldown elapsed → HALF_OPEN
# ---------------------------------------------------------------------------

async def test_open_transitions_to_half_open_after_cooldown():
    cb = make_cb(cooldown_seconds=0.05)
    cb.record_429()
    cb.record_429()
    cb.record_429()
    assert cb.state == CircuitState.OPEN

    # Before cooldown elapses, check_recovery() should leave it OPEN.
    assert cb.check_recovery() == CircuitState.OPEN

    await asyncio.sleep(0.08)  # exceed the 0.05s cooldown

    assert cb.check_recovery() == CircuitState.HALF_OPEN
    assert cb.state == CircuitState.HALF_OPEN


def test_is_closed_returns_true_for_half_open():
    # is_closed() means "allow a request through" — HALF_OPEN must say yes
    # exactly once so the breaker can test recovery.
    cb = make_cb(cooldown_seconds=0.0)  # cooldown already "elapsed" at trip time
    cb.record_429()
    cb.record_429()
    cb.record_429()
    assert cb.state == CircuitState.OPEN
    # cooldown_seconds=0.0 means check_recovery() flips it immediately.
    assert cb.is_closed() is True
    assert cb.state == CircuitState.HALF_OPEN


def test_is_closed_returns_false_while_open_and_cooling():
    cb = make_cb(cooldown_seconds=10.0)  # long cooldown, won't elapse mid-test
    cb.record_429()
    cb.record_429()
    cb.record_429()
    assert cb.state == CircuitState.OPEN
    assert cb.is_closed() is False
    assert cb.state == CircuitState.OPEN  # unchanged — cooldown hasn't elapsed


# ---------------------------------------------------------------------------
# 3. HALF_OPEN + success → CLOSED, counters reset
# ---------------------------------------------------------------------------

def test_half_open_success_closes_and_resets_counters():
    cb = make_cb(cooldown_seconds=0.0)
    cb.record_429()
    cb.record_429()
    cb.record_429()
    assert cb.is_closed() is True            # transitions OPEN → HALF_OPEN
    assert cb.state == CircuitState.HALF_OPEN

    cb.record_success()

    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_429s == 0
    assert cb.consecutive_timeouts == 0
    # Internal rolling window should also be cleared by a full reset.
    assert len(cb._window) == 0


# ---------------------------------------------------------------------------
# 4. HALF_OPEN + failure → OPEN, cooldown doubled
# ---------------------------------------------------------------------------

def test_half_open_failure_reopens_with_doubled_cooldown():
    cb = make_cb(cooldown_seconds=1.0)
    cb.record_429()
    cb.record_429()
    cb.record_429()
    assert cb.cooldown_seconds == pytest.approx(1.0)  # first trip: no doubling

    # Force into HALF_OPEN without waiting a full second.
    cb._opened_at = cb._opened_at - 10.0  # simulate cooldown having elapsed
    assert cb.check_recovery() == CircuitState.HALF_OPEN

    cb.record_429()  # the single test request fails

    assert cb.state == CircuitState.OPEN
    assert cb.cooldown_seconds == pytest.approx(2.0)  # doubled from 1.0 → 2.0


def test_repeated_half_open_failures_keep_doubling_up_to_cap():
    cb = make_cb(cooldown_seconds=300.0)
    cb.record_429()
    cb.record_429()
    cb.record_429()
    assert cb.cooldown_seconds == pytest.approx(300.0)

    cb._opened_at = cb._opened_at - 1000.0
    cb.check_recovery()
    cb.record_429()  # HALF_OPEN failure → doubles to 600 (capped)
    assert cb.cooldown_seconds == pytest.approx(600.0)

    cb._opened_at = cb._opened_at - 1000.0
    cb.check_recovery()
    cb.record_429()  # would double to 1200, but capped at 600
    assert cb.cooldown_seconds == pytest.approx(600.0)
    assert cb.state == CircuitState.OPEN


# ---------------------------------------------------------------------------
# 5. 5 consecutive timeouts → trips to OPEN
# ---------------------------------------------------------------------------

def test_five_consecutive_timeouts_trips_open():
    cb = make_cb()
    for _ in range(4):
        cb.record_timeout()
        assert cb.state == CircuitState.CLOSED
    cb.record_timeout()  # 5th
    assert cb.state == CircuitState.OPEN


def test_timeout_streak_broken_by_success():
    cb = make_cb()
    cb.record_timeout()
    cb.record_timeout()
    cb.record_success()
    cb.record_timeout()
    # window=[T,T,F,T], len=4 — stays under MIN_WINDOW_SIZE(5), so only the
    # consecutive-counter behavior is being exercised here, not the rate
    # condition: the streak should have restarted at 1 after the success.
    assert cb.consecutive_timeouts == 1
    assert cb.state == CircuitState.CLOSED


def test_timeout_streak_broken_by_429():
    # A 429 in between timeouts resets the timeout streak (and starts its own).
    cb = make_cb()
    cb.record_timeout()
    cb.record_timeout()
    cb.record_429()
    # window=[T,T,T], len=3 — stays under MIN_WINDOW_SIZE(5), so only the
    # consecutive-counter behavior is being exercised, not the rate condition.
    assert cb.consecutive_timeouts == 0   # broken by the 429
    assert cb.consecutive_429s == 1
    assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# 6. Mixed errors below threshold → stays CLOSED
# ---------------------------------------------------------------------------

def test_mixed_errors_below_threshold_stays_closed():
    cb = make_cb()
    # 2 failures out of 5 events = 40% error rate, under the 50% threshold,
    # and neither failure type reaches its own consecutive trip count.
    cb.record_success()
    cb.record_429()
    cb.record_success()
    cb.record_timeout()
    cb.record_success()
    assert cb.state == CircuitState.CLOSED


def test_error_rate_above_threshold_trips_even_without_consecutive_streak():
    cb = make_cb()
    # Alternate failure types so neither consecutive counter reaches its own
    # threshold (3 for 429, 5 for timeout) — only the rolling error-rate
    # condition can explain the trip.
    cb.record_429()      # fail 1/1 — window len=1, below MIN_WINDOW_SIZE
    cb.record_success()  # ok    — window len=2
    cb.record_timeout()  # fail 2/3 — window len=3
    cb.record_success()  # ok    — window len=4
    cb.record_error()    # fail 3/5 — window len=5, MIN_WINDOW_SIZE reached:
    #                        window=[T,F,T,F,T], rate=3/5=60% > 50% → trips here.
    assert cb.state == CircuitState.OPEN
    assert cb.consecutive_429s < CircuitBreaker.CONSECUTIVE_429_THRESHOLD
    assert cb.consecutive_timeouts < CircuitBreaker.CONSECUTIVE_TIMEOUT_THRESHOLD


def test_exactly_half_error_rate_does_not_trip():
    cb = make_cb()
    # Window of exactly 10, exactly 5 failures (50%) — must NOT trip since
    # the condition is strictly greater than 50%.
    sequence = [
        cb.record_success,
        cb.record_429,
        cb.record_success,
        cb.record_timeout,
        cb.record_success,
        cb.record_error,
        cb.record_success,
        cb.record_429,      # consecutive_429 would be 1 (success just before)
        cb.record_success,
        cb.record_timeout,
    ]
    for fn in sequence:
        fn()
    assert len(cb._window) == 10
    assert sum(cb._window) == 5  # exactly 50%
    assert cb.state == CircuitState.CLOSED


# ---------------------------------------------------------------------------
# Window eviction: only the last 10 events matter for the rate condition.
# ---------------------------------------------------------------------------

def test_window_evicts_old_entries_beyond_ten():
    cb = make_cb()
    # 20 successes should never trip regardless of order, and the deque
    # should never grow past maxlen=10.
    for _ in range(20):
        cb.record_success()
    assert len(cb._window) == 10
    assert cb.state == CircuitState.CLOSED