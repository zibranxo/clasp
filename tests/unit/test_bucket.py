"""
tests/unit/test_bucket.py

Covers clasp/ratelimit/bucket.py per plan.md §18 testing strategy:

- fill to capacity → can_consume() True
- exhaust to 0 → can_consume() False
- soft threshold at 85% used → can_consume() False
- TPM limit independently blocks even when RPM has room
- partial refill after waiting
- concurrent consume from 20 asyncio tasks never over-consumes capacity
- consume_actual() correctly adjusts TPM for estimation delta

Run with: pytest tests/unit/test_bucket.py -v --asyncio-mode=auto
(async test functions use plain `async def` — asyncio-mode=auto means no
explicit @pytest.mark.asyncio decorator is required, matching the project's
CI config in plan.md §18.)
"""

from __future__ import annotations

import asyncio

import pytest

from clasp.ratelimit.bucket import TokenBucket

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 1. Fill to capacity → can_consume() True
# ---------------------------------------------------------------------------

async def test_full_bucket_can_consume():
    bucket = TokenBucket(rpm_limit=40, tpm_limit=None, soft_threshold=0.80)
    assert await bucket.can_consume() is True


# ---------------------------------------------------------------------------
# 2. Exhaust to 0 → can_consume() False
# ---------------------------------------------------------------------------

async def test_exhausted_bucket_cannot_consume():
    bucket = TokenBucket(rpm_limit=5, tpm_limit=None, soft_threshold=0.80)
    for _ in range(5):
        assert await bucket.can_consume() is True
        await bucket.consume()
    # All 5 RPM tokens consumed — rpm_tokens should now be ~0.
    assert await bucket.can_consume() is False


# ---------------------------------------------------------------------------
# 3. Soft threshold at 85% used → can_consume() False
# ---------------------------------------------------------------------------

async def test_soft_threshold_blocks_before_hard_limit():
    # soft_threshold=0.80 → admission stops once more than 80% of RPM
    # capacity has been consumed, well before the bucket is truly empty.
    bucket = TokenBucket(rpm_limit=100, tpm_limit=None, soft_threshold=0.80)

    # Consume 85 of 100 tokens (85% used, 15% remaining).
    for _ in range(85):
        await bucket.consume()

    # 15 tokens remain (rpm_ok is True — bucket isn't literally empty), but
    # the soft threshold (20% headroom required) is violated: 15 < 20.
    assert bucket.rpm_tokens == pytest.approx(15.0, abs=0.5)
    assert await bucket.can_consume() is False


async def test_soft_threshold_allows_under_threshold():
    # Consuming only 10% should stay safely under the 80% soft threshold.
    bucket = TokenBucket(rpm_limit=100, tpm_limit=None, soft_threshold=0.80)
    for _ in range(10):
        await bucket.consume()
    assert await bucket.can_consume() is True


# ---------------------------------------------------------------------------
# 4. TPM limit independently blocks even when RPM has room
# ---------------------------------------------------------------------------

async def test_tpm_limit_blocks_despite_rpm_headroom():
    # Plenty of RPM capacity (only 1 of 1000 consumed), but TPM is nearly
    # exhausted — a large estimated_tokens request should still be refused.
    bucket = TokenBucket(rpm_limit=1000, tpm_limit=1000, soft_threshold=0.80)

    await bucket.consume(estimated_tokens=900)  # tpm_tokens: 1000 -> 100
    assert bucket.rpm_tokens == pytest.approx(999.0, abs=0.01)  # RPM barely touched

    # RPM is wide open, but requesting 500 more tokens than the 100 left in TPM.
    assert await bucket.can_consume(estimated_tokens=500) is False

    # A request that fits within the remaining 100 TPM tokens should pass.
    assert await bucket.can_consume(estimated_tokens=50) is True


async def test_unlimited_tpm_never_blocks():
    # tpm_limit=None means TPM is not enforced at all.
    bucket = TokenBucket(rpm_limit=100, tpm_limit=None, soft_threshold=0.80)
    assert await bucket.can_consume(estimated_tokens=10_000_000) is True


# ---------------------------------------------------------------------------
# 5. Partial refill after waiting
# ---------------------------------------------------------------------------

async def test_partial_refill_after_waiting():
    # rpm_limit=60 → refill_rate = 1 token/second exactly.
    # soft_threshold=1.0 disables the soft-threshold gate (covered by its own
    # tests above) so this isolates the pure _refill() arithmetic: with the
    # default soft_threshold=0.80 a bucket needs to refill back to 20% of
    # capacity (12 tokens here) before can_consume() passes again, not just
    # 1 token, which would make this test about the soft threshold instead
    # of about refill timing.
    bucket = TokenBucket(rpm_limit=60, tpm_limit=None, soft_threshold=1.0)

    # Drain to exactly 0 tokens.
    for _ in range(60):
        await bucket.consume()
    assert bucket.rpm_tokens == pytest.approx(0.0, abs=0.01)
    assert await bucket.can_consume() is False

    # Wait long enough for ~3 tokens to refill (3 seconds at 1 token/sec).
    await asyncio.sleep(3.05)

    # _refill() runs lazily inside can_consume(); no manual trigger needed.
    assert await bucket.can_consume() is True
    assert bucket.rpm_tokens == pytest.approx(3.0, abs=0.5)


async def test_refill_never_exceeds_capacity():
    bucket = TokenBucket(rpm_limit=10, tpm_limit=None, soft_threshold=0.80)
    # Bucket starts full; waiting should not push it over capacity.
    await asyncio.sleep(0.2)
    assert await bucket.can_consume() is True
    assert bucket.rpm_tokens <= 10.0


# ---------------------------------------------------------------------------
# 6. Concurrent consume from 20 asyncio tasks never over-consumes capacity
# ---------------------------------------------------------------------------

async def test_concurrent_consume_never_over_consumes():
    # 20 tasks each try to consume once from a bucket with only 10 RPM
    # capacity. The asyncio.Lock inside TokenBucket must serialise access so
    # that at most 10 of the 20 "win" (rpm_used never exceeds capacity).
    # soft_threshold=1.0 fully disables the soft-threshold gate (it's tested
    # separately above) so this test purely measures raw capacity safety.
    bucket = TokenBucket(rpm_limit=10, tpm_limit=None, soft_threshold=1.0)

    granted = 0
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal granted
        if await bucket.can_consume():
            await bucket.consume()
            async with lock:
                granted += 1

    await asyncio.gather(*(worker() for _ in range(20)))

    # Exactly 10 of the 20 concurrent attempts should have been admitted —
    # never more (that would mean the lock failed to serialise access and
    # capacity was over-consumed), and the bucket's internal accounting
    # should agree with the observed grant count.
    assert granted == 10, f"expected exactly 10 grants, got {granted}"
    assert bucket.rpm_tokens == pytest.approx(0.0, abs=0.01)
    # int(capacity - rpm_tokens) truncates, and lazy refill means a sliver of
    # wall-clock time has elapsed since the last consume() by now — same
    # truncation hazard as test_rpm_used_property_tracks_consumption above.
    assert bucket.rpm_used in (9, 10)


async def test_concurrent_consume_respects_lock_under_heavy_contention():
    # Same idea but with a tighter capacity/worker ratio and TPM also engaged,
    # to make sure the lock guards both dimensions together atomically.
    bucket = TokenBucket(rpm_limit=5, tpm_limit=500, soft_threshold=1.0)

    granted = 0
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal granted
        if await bucket.can_consume(estimated_tokens=50):
            await bucket.consume(estimated_tokens=50)
            async with lock:
                granted += 1

    await asyncio.gather(*(worker() for _ in range(20)))

    # RPM allows 5, TPM allows 500/50 = 10 — RPM is the binding constraint.
    assert granted == 5, f"expected exactly 5 grants, got {granted}"
    assert bucket.rpm_used in (4, 5)


# ---------------------------------------------------------------------------
# 7. consume_actual() correctly adjusts TPM for estimation delta
# ---------------------------------------------------------------------------

async def test_consume_actual_adjusts_tpm_for_overestimate():
    bucket = TokenBucket(rpm_limit=100, tpm_limit=1000, soft_threshold=0.80)

    # Reserved 200 tokens up front (estimate), but the real usage was only 150.
    await bucket.consume(estimated_tokens=200)
    assert bucket.tpm_tokens == pytest.approx(800.0, abs=0.01)

    # delta = actual(150) - estimated(200) = -50 → tpm_tokens should increase
    # back by 50 (we over-reserved, so refund the difference).
    await bucket.consume_actual(actual=150, estimated=200)
    assert bucket.tpm_tokens == pytest.approx(850.0, abs=0.01)


async def test_consume_actual_adjusts_tpm_for_underestimate():
    bucket = TokenBucket(rpm_limit=100, tpm_limit=1000, soft_threshold=0.80)

    await bucket.consume(estimated_tokens=100)
    assert bucket.tpm_tokens == pytest.approx(900.0, abs=0.01)

    # delta = actual(180) - estimated(100) = +80 → consume 80 more.
    await bucket.consume_actual(actual=180, estimated=100)
    assert bucket.tpm_tokens == pytest.approx(820.0, abs=0.01)


async def test_consume_actual_noop_when_tpm_unlimited():
    bucket = TokenBucket(rpm_limit=100, tpm_limit=None, soft_threshold=0.80)
    await bucket.consume(estimated_tokens=999)  # no-op for unlimited TPM
    await bucket.consume_actual(actual=99999, estimated=1)  # should not raise
    assert bucket.tpm_tokens == 0.0  # never tracked since tpm_capacity is None


async def test_consume_actual_floors_at_zero():
    bucket = TokenBucket(rpm_limit=100, tpm_limit=100, soft_threshold=0.80)
    await bucket.consume(estimated_tokens=10)
    # A wildly under-estimated actual shouldn't drive tpm_tokens negative.
    await bucket.consume_actual(actual=10_000, estimated=10)
    assert bucket.tpm_tokens == 0.0


# ---------------------------------------------------------------------------
# Misc: seconds_until_available() and rpm_used property
# ---------------------------------------------------------------------------

async def test_seconds_until_available_when_full():
    bucket = TokenBucket(rpm_limit=60, tpm_limit=None, soft_threshold=0.80)
    assert bucket.seconds_until_available() == 0.0


async def test_seconds_until_available_when_empty():
    bucket = TokenBucket(rpm_limit=60, tpm_limit=None, soft_threshold=0.80)
    for _ in range(60):
        await bucket.consume()
    # refill_rate = 1 token/sec, deficit = 1.0 → ~1 second until next token.
    assert bucket.seconds_until_available() == pytest.approx(1.0, abs=0.05)


async def test_rpm_used_property_tracks_consumption():
    bucket = TokenBucket(rpm_limit=40, tpm_limit=None, soft_threshold=0.80)
    assert bucket.rpm_used == 0
    for _ in range(15):
        await bucket.consume()
    # Lazy refill means a tiny amount of real wall-clock time elapses between
    # each consume() call (even in a tight loop), nudging rpm_tokens up by a
    # fraction of a token each time. rpm_used = int(capacity - rpm_tokens)
    # truncates, so this can legitimately read 14 instead of 15 by a hair.
    # Check the underlying float directly with a tolerance, and allow the
    # truncated int property to land on either side of that hair-line.
    assert bucket.rpm_tokens == pytest.approx(25.0, abs=0.05)
    assert bucket.rpm_used in (14, 15)