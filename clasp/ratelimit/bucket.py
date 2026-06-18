"""
clasp/ratelimit/bucket.py
============================
Dual-dimension token bucket — RPM + TPM tracked separately (plan.md §10
"Phase 2 — Rate Limit Engine", `ratelimit/bucket.py`).

This is the mechanical core of P3 ("Pre-emptive limiting" — CLAUDE.md "Key
design decisions"): track usage locally, enforce soft thresholds *before*
hitting provider limits, so a 429 from upstream is the fallback, not the
plan.

The algorithm is implemented exactly as specified in plan.md §10: lazy
refill on each access (no background task needed for Sprint-scope
correctness — a refill happens at the start of every `can_consume`/
`consume` call), `asyncio.Lock` for concurrency safety, and a soft
threshold that intentionally starts rejecting new requests *before* the
bucket is literally empty.

Soft threshold mechanics
--------------------------
`soft_threshold=0.80` means: once 80% of RPM capacity has been used (i.e.
only 20% remains as a safety reserve), `can_consume()` starts returning
``False`` — even though the raw bucket might still have a fractional token
left. This is deliberate: it leaves headroom so a burst of Claude Code
background activity doesn't immediately slam into the provider's *actual*
hard limit and trigger a real 429.

``near_soft_threshold`` (a property, not a gate) exposes whether the bucket
has crossed that line, purely for logging/observability — `ratelimit/key_pool.py`
uses it to emit a WARNING the moment a key crosses into soft-limited
territory, which is what the manual RPM test (see CLAUDE.md) looks for in
the logs.

References: plan.md §10 "Token Bucket (`ratelimit/bucket.py`)" — the
            `can_consume`/`consume`/`consume_actual`/`_refill` algorithm
            below is a faithful, fully-typed implementation of that spec.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover — shim for test environments
    import logging as _logging

    class _Shim:
        _log = _logging.getLogger("clasp.bucket")

        def debug(self, msg: str, **kw: Any) -> None:
            self._log.debug(msg + ("  " + str(kw) if kw else ""))

    logger = _Shim()  # type: ignore[assignment]


class TokenBucket:
    """
    Dual-dimension token bucket: RPM + TPM tracked separately.

    Lazy refill on each access. `asyncio.Lock` for concurrency safety across
    simultaneous requests sharing the same bucket (i.e. the same provider
    API key).

    Parameters
    ----------
    rpm_limit:
        Requests-per-minute capacity. The bucket starts full.
    tpm_limit:
        Tokens-per-minute capacity, or ``None`` for providers with no TPM
        cap (e.g. NVIDIA NIM in the default catalog).
    soft_threshold:
        Fraction of RPM capacity (0.0–1.0) at which `can_consume()` starts
        returning ``False`` even though raw capacity technically remains.
        Default 0.80 — stop at 80% utilized, keep 20% as a safety reserve.
    """

    def __init__(
        self,
        rpm_limit: int,
        tpm_limit: int | None,
        soft_threshold: float = 0.80,
    ) -> None:
        self.rpm_capacity = rpm_limit
        self.rpm_tokens = float(rpm_limit)              # Start full
        self.rpm_refill_rate = rpm_limit / 60.0          # per second
        self.tpm_capacity = tpm_limit                    # None = unlimited
        self.tpm_tokens = float(tpm_limit or 0)
        self.tpm_refill_rate = (tpm_limit or 0) / 60.0
        self.soft_threshold = soft_threshold
        self._lock = asyncio.Lock()
        self._last_refill = time.monotonic()

    # ------------------------------------------------------------------
    # Core algorithm — faithful to plan.md §10
    # ------------------------------------------------------------------

    async def can_consume(self, estimated_tokens: int = 0) -> bool:
        """
        Whether a request can be issued right now without crossing either
        the hard RPM floor (need ≥1 raw token) or the soft-threshold
        reserve, and (if tracked) without exceeding TPM capacity.
        """
        async with self._lock:
            self._refill()
            rpm_ok = self.rpm_tokens >= 1.0
            # Crossed into the soft-limited zone: fewer tokens remain than
            # the configured reserve (capacity * (1 - soft_threshold)).
            below_reserve = self.rpm_tokens < (self.rpm_capacity * (1.0 - self.soft_threshold))
            tpm_ok = self.tpm_capacity is None or self.tpm_tokens >= estimated_tokens
            result = rpm_ok and not below_reserve and tpm_ok
            if not result:
                logger.debug(
                    "bucket: can_consume=False",
                    rpm_tokens=round(self.rpm_tokens, 3),
                    rpm_capacity=self.rpm_capacity,
                    below_reserve=below_reserve,
                    rpm_ok=rpm_ok,
                    tpm_ok=tpm_ok,
                )
            return result

    async def consume(self, estimated_tokens: int = 0) -> None:
        """Deduct one RPM token and *estimated_tokens* TPM tokens (pre-flight estimate)."""
        async with self._lock:
            self._refill()
            self.rpm_tokens = max(0.0, self.rpm_tokens - 1.0)
            if self.tpm_capacity is not None:
                self.tpm_tokens = max(0.0, self.tpm_tokens - estimated_tokens)

    async def consume_actual(self, actual: int, estimated: int) -> None:
        """
        Reconcile the TPM bucket once the provider's real `usage.output_tokens`
        (or full actual count) is known. Only the *delta* vs. the pre-flight
        estimate is applied — the estimate was already deducted by `consume()`.
        """
        delta = actual - estimated
        if self.tpm_capacity is not None and delta != 0:
            async with self._lock:
                self.tpm_tokens = max(0.0, self.tpm_tokens - delta)

    def seconds_until_available(self) -> float:
        """Estimated wall-clock seconds until at least 1 RPM token is available."""
        deficit = max(0.0, 1.0 - self.rpm_tokens)
        return deficit / self.rpm_refill_rate if self.rpm_refill_rate > 0 else 60.0

    @property
    def rpm_used(self) -> int:
        """RPM tokens consumed out of capacity, as a whole number (for display)."""
        return max(0, int(round(self.rpm_capacity - self.rpm_tokens)))

    @property
    def rpm_utilization(self) -> float:
        """Fraction (0.0–1.0) of RPM capacity currently used."""
        if self.rpm_capacity <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.rpm_capacity - self.rpm_tokens) / self.rpm_capacity))

    @property
    def near_soft_threshold(self) -> bool:
        """
        True once RPM utilization has reached or crossed `soft_threshold`.

        Observability-only — does not gate `can_consume()` itself (that's
        already handled by the `below_reserve` check inline). Exposed so
        callers (e.g. `key_pool.py`) can log a clear warning the moment a
        key enters soft-limited territory.
        """
        return self.rpm_utilization >= self.soft_threshold

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self.rpm_tokens = min(
            self.rpm_capacity, self.rpm_tokens + elapsed * self.rpm_refill_rate
        )
        if self.tpm_capacity is not None:
            self.tpm_tokens = min(
                self.tpm_capacity, self.tpm_tokens + elapsed * self.tpm_refill_rate
            )
        self._last_refill = now

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"TokenBucket(rpm={self.rpm_tokens:.2f}/{self.rpm_capacity}, "
            f"tpm={self.tpm_tokens:.0f}/{self.tpm_capacity}, "
            f"soft={self.soft_threshold})"
        )