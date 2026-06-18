"""
tests/unit/test_key_pool.py

Unit tests for clasp/ratelimit/key_pool.py.

Coverage map (as requested):
  1. 3 healthy keys round-robin 0,1,2,0,1,2
  2. key[0] exhausted -> picks key[1] then key[2]
  3. all keys exhausted -> returns None
  4. key in cooldown is skipped even if its bucket has capacity

Each test builds its own isolated CooldownTracker (rather than relying on
the module-level default singleton) and injects it via
KeyPool(..., cooldown_tracker=...), so cooldown state never leaks
between tests.

Additional cases:
  - empty key list -> pick_key() returns None without crashing.
  - a single key, healthy -> always returns index 0.
  - round-robin position survives interleaved partial exhaustion.
  - health_summary()'s "healthy" count and per-key "status" reflect
    cooldown vs. plain exhaustion correctly.
  - record_429()/record_success() integration: 3 consecutive 429s trips
    the circuit breaker open, and pick_key() then skips that key even
    though cooldown.is_cooling() says False for it (a breaker trip and a
    cooldown are two independent reasons a key can be unusable).
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "_stubs"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.config.provider_catalog import ProviderProfile
from clasp.ratelimit.cooldown import CooldownTracker
from clasp.ratelimit.key_pool import KeyPool


def _run(coro):
    return asyncio.run(coro)


def _make_profile(
    rpm_limit: int = 40,
    tpm_limit: int | None = None,
    soft_threshold: float = 0.80,
    cooldown_seconds: int = 5,
    backoff_base_seconds: int = 5,
) -> ProviderProfile:
    return ProviderProfile(
        display_name="Test Provider",
        base_url="https://example.com/v1",
        transport="openai_chat",
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        daily_token_limit=None,
        rpm_soft_threshold=soft_threshold,
        cooldown_seconds=cooldown_seconds,
        backoff_base_seconds=backoff_base_seconds,
        supports_tools=True,
        supports_vision=False,
        supports_thinking=False,
        max_context_tokens=100_000,
        tier="free",
        free_tier_note="test",
    )


def _make_pool(num_keys: int = 3, **profile_kwargs) -> KeyPool:
    keys = [f"key-{i}" for i in range(num_keys)]
    profile = _make_profile(**profile_kwargs)
    # Fresh, isolated tracker per pool — never the shared default singleton.
    tracker = CooldownTracker()
    return KeyPool("test_provider", keys, profile, cooldown_tracker=tracker)


async def _pick_many(pool: KeyPool, n: int) -> list[int | None]:
    indices = []
    for _ in range(n):
        result = await pool.pick_key()
        indices.append(result[1] if result else None)
    return indices


# ===========================================================================
# 1. Round-robin over healthy keys
# ===========================================================================


class TestRoundRobin:
    def test_three_healthy_keys_round_robin(self):
        pool = _make_pool(3)
        indices = _run(_pick_many(pool, 6))
        assert indices == [0, 1, 2, 0, 1, 2]

    def test_returns_correct_api_key_string(self):
        pool = _make_pool(3)
        result = _run(pool.pick_key())
        assert result == ("key-0", 0)

    def test_single_key_always_returns_index_zero(self):
        pool = _make_pool(1)
        indices = _run(_pick_many(pool, 4))
        assert indices == [0, 0, 0, 0]

    def test_round_robin_counter_persists_across_calls(self):
        """The internal _rr counter keeps advancing across separate
        pick_key() calls, not just within a single batch."""
        pool = _make_pool(3)
        first = _run(pool.pick_key())
        second = _run(pool.pick_key())
        third = _run(pool.pick_key())
        fourth = _run(pool.pick_key())
        assert [r[1] for r in (first, second, third, fourth)] == [0, 1, 2, 0]


# ===========================================================================
# 2. Exhausted key gets skipped, round-robin continues over the rest
# ===========================================================================


class TestExhaustedKeySkipped:
    def test_key_zero_exhausted_picks_one_then_two(self):
        pool = _make_pool(3)
        pool.buckets[0].rpm_tokens = 0.0  # simulate key 0 fully exhausted

        first = _run(pool.pick_key())
        second = _run(pool.pick_key())

        assert first == ("key-1", 1)
        assert second == ("key-2", 2)

    def test_exhausted_key_recovers_once_capacity_returns(self):
        pool = _make_pool(3)
        pool.buckets[0].rpm_tokens = 0.0
        _run(pool.pick_key())  # consumes key 1 (key 0 skipped)
        _run(pool.pick_key())  # consumes key 2

        # Restore key 0's capacity — next round-robin pass should pick it up again.
        pool.buckets[0].rpm_tokens = float(pool.buckets[0].rpm_capacity)
        third = _run(pool.pick_key())
        assert third == ("key-0", 0)

    def test_middle_key_exhausted_skips_only_that_one(self):
        pool = _make_pool(3)
        pool.buckets[1].rpm_tokens = 0.0

        first = _run(pool.pick_key())
        second = _run(pool.pick_key())

        assert first == ("key-0", 0)
        assert second == ("key-2", 2)


# ===========================================================================
# 3. Every key exhausted -> None
# ===========================================================================


class TestAllExhausted:
    def test_all_keys_exhausted_returns_none(self):
        pool = _make_pool(3)
        for bucket in pool.buckets:
            bucket.rpm_tokens = 0.0
        result = _run(pool.pick_key())
        assert result is None

    def test_empty_key_list_returns_none(self):
        profile = _make_profile()
        tracker = CooldownTracker()
        pool = KeyPool("test_provider", [], profile, cooldown_tracker=tracker)
        result = _run(pool.pick_key())
        assert result is None

    def test_pick_key_tries_each_key_at_most_once(self):
        """Bounded loop: with all keys unhealthy, pick_key() returns
        rather than spinning — and the round-robin counter only advances
        by exactly len(keys), not unboundedly."""
        pool = _make_pool(3)
        for bucket in pool.buckets:
            bucket.rpm_tokens = 0.0
        _run(pool.pick_key())
        assert pool._rr == 3

    def test_recovers_after_being_fully_exhausted(self):
        pool = _make_pool(3)
        for bucket in pool.buckets:
            bucket.rpm_tokens = 0.0
        assert _run(pool.pick_key()) is None

        pool.buckets[1].rpm_tokens = float(pool.buckets[1].rpm_capacity)
        result = _run(pool.pick_key())
        assert result == ("key-1", 1)


# ===========================================================================
# 4. Cooling key skipped even with bucket capacity
# ===========================================================================


class TestCooldownSkipped:
    def test_cooling_key_skipped_despite_full_bucket(self):
        pool = _make_pool(3)
        # key 1 has full capacity but is in cooldown.
        pool._cooldown.on_429("test_provider", 1, retry_after_header="120")

        first = _run(pool.pick_key())
        second = _run(pool.pick_key())

        assert first == ("key-0", 0)
        assert second == ("key-2", 2)  # key 1 skipped despite healthy bucket

    def test_all_other_keys_cooling_leaves_only_one_available(self):
        pool = _make_pool(3)
        pool._cooldown.on_429("test_provider", 0, retry_after_header="60")
        pool._cooldown.on_429("test_provider", 2, retry_after_header="60")

        indices = _run(_pick_many(pool, 3))
        assert indices == [1, 1, 1]

    def test_cooling_on_one_provider_does_not_affect_another(self):
        """Cooldown keys are (provider, key_index) — a key index cooling
        for a DIFFERENT provider name must not affect this pool."""
        pool = _make_pool(3)
        pool._cooldown.on_429("some_other_provider", 0, retry_after_header="120")

        result = _run(pool.pick_key())
        assert result == ("key-0", 0)  # unaffected — different provider name

    def test_expired_cooldown_key_becomes_available_again(self):
        pool = _make_pool(3)
        # Put key 0 in cooldown, then manually expire it by rewinding the
        # recovery timestamp into the past.
        pool._cooldown.on_429("test_provider", 0, retry_after_header="120")
        pool._cooldown._cooling_until[("test_provider", 0)] = 0.0  # already in the past

        result = _run(pool.pick_key())
        assert result == ("key-0", 0)


# ===========================================================================
# health_summary() reflects cooldown / exhaustion / circuit state
# ===========================================================================


class TestHealthSummary:
    def test_all_healthy_summary(self):
        pool = _make_pool(3)
        summary = pool.health_summary()
        assert summary["total"] == 3
        assert summary["healthy"] == 3
        assert all(k["status"] == "healthy" for k in summary["keys"])

    def test_cooling_key_shows_cooling_status(self):
        pool = _make_pool(3)
        pool._cooldown.on_429("test_provider", 1, retry_after_header="90")
        summary = pool.health_summary()
        assert summary["healthy"] == 2
        assert summary["keys"][1]["status"] == "cooling"
        assert summary["keys"][1]["recovery_in"] > 0

    def test_redacted_key_format(self):
        profile = _make_profile()
        tracker = CooldownTracker()
        pool = KeyPool("p", ["sk-abcdefghijklmnop"], profile, cooldown_tracker=tracker)
        summary = pool.health_summary()
        assert summary["keys"][0]["redacted"] == "sk-abc***mnop"

    def test_short_key_fully_redacted(self):
        profile = _make_profile()
        tracker = CooldownTracker()
        pool = KeyPool("p", ["short"], profile, cooldown_tracker=tracker)
        summary = pool.health_summary()
        assert summary["keys"][0]["redacted"] == "***"


# ===========================================================================
# Circuit breaker integration: tripped breaker also blocks selection,
# independently of cooldown
# ===========================================================================


class TestCircuitBreakerIntegration:
    def test_three_consecutive_429s_trips_breaker_and_blocks_key(self):
        pool = _make_pool(3)
        # Three 429s on key 0, but WITHOUT going through cooldown.on_429
        # (e.g. imagine the breaker's own independent trip — here we
        # isolate it by resetting cooldown after each call so only the
        # breaker's state determines availability).
        for _ in range(3):
            pool.record_429(0, retry_after_header=None)
            pool._cooldown.reset("test_provider", 0)

        assert pool.circuit_breakers[0].state == "open"
        result = _run(pool.pick_key())
        # key 0's cooldown was reset, but its breaker is open -> skipped.
        assert result == ("key-1", 1)

    def test_record_success_resets_breaker_counters(self):
        pool = _make_pool(3)
        pool.record_429(0, retry_after_header=None)
        pool.record_429(0, retry_after_header=None)
        pool.record_success(0)
        assert pool.circuit_breakers[0].state == "closed"
        pool._cooldown.reset("test_provider", 0)
        result = _run(pool.pick_key())
        assert result == ("key-0", 0)