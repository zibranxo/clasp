"""
clasp/ratelimit/key_pool.py

KeyPool — N keys per provider, each with its own TokenBucket and
CircuitBreaker, sharing one CooldownTracker (cooldowns are keyed
(provider, key_index), so one tracker instance can serve every key in
every pool). This is what lets "add 3 NIM accounts" multiply free quota
3x instead of just rotating through the same shared limit.

`pick_key()` and `health_summary()` follow plan.md Section 12's
reference code directly; `_key_status()`/`_redact()` aren't given
concrete code there, so they're my own (straightforward) fill-ins.
"""

from __future__ import annotations

import asyncio

from clasp.config.provider_catalog import ProviderProfile
from clasp.ratelimit import cooldown as cooldown_module
from clasp.ratelimit.bucket import TokenBucket
from clasp.ratelimit.circuit_breaker import CircuitBreaker
from clasp.ratelimit.cooldown import CooldownTracker


class KeyPool:
    def __init__(
        self,
        provider_name: str,
        keys: list[str],
        profile: ProviderProfile,
        *,
        cooldown_tracker: CooldownTracker | None = None,
    ) -> None:
        """
        Args:
            cooldown_tracker: Defaults to the process-wide singleton
                (`cooldown.get_default_tracker()`), matching plan.md's
                `cooldown.is_cooling(...)` module-level calling
                convention. Pass an explicit `CooldownTracker()` instance
                instead for isolated tests, or if you ever want fully
                independent cooldown state per pool rather than sharing
                the global one.
        """
        self.provider_name = provider_name
        self.keys = keys
        self.profile = profile
        self.buckets = [
            TokenBucket(profile.rpm_limit, profile.tpm_limit, profile.rpm_soft_threshold)
            for _ in keys
        ]
        self.circuit_breakers = [CircuitBreaker(profile) for _ in keys]
        self._cooldown = cooldown_tracker or cooldown_module.get_default_tracker()
        self._rr = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # Selection
    # ------------------------------------------------------------------ #

    async def pick_key(self, estimated_tokens: int = 0) -> tuple[str, int] | None:
            """
            Round-robin over healthy keys. Returns (api_key, index), or None
            if every key is currently cooling, circuit-open, or out of
            bucket capacity.
            """
            if not self.keys:
                return None
            async with self._lock:
                for _ in range(len(self.keys)):
                    idx = self._rr % len(self.keys)
                    self._rr += 1
                    
                    cooling = self._cooldown.is_cooling(self.provider_name, idx)
                    cb_ok = self.circuit_breakers[idx].is_closed()
                    
                    if not cooling and cb_ok and await self.buckets[idx].can_consume(estimated_tokens):
                        await self.buckets[idx].consume(estimated_tokens)
                        
                        # Log warning if this consumption pushed the bucket into soft-limited territory
                        if self.buckets[idx].near_soft_threshold:
                            logger.warning(
                                "key_pool: provider near RPM soft threshold",
                                provider=self.provider_name, key_index=idx,
                                rpm_used=self.buckets[idx].rpm_used, rpm_limit=self.buckets[idx].rpm_capacity,
                                soft_threshold=self.buckets[idx].soft_threshold,
                                utilization_pct=round(self.buckets[idx].rpm_utilization * 100, 1),
                            )
                            
                        return self.keys[idx], idx
            return None
    # ------------------------------------------------------------------ #
    # Outcome feedback (called by the provider/absorber layer once a
    # request using a given key index completes)
    # ------------------------------------------------------------------ #

    def record_success(self, key_index: int) -> None:
        self.circuit_breakers[key_index].record_success()
        self._cooldown.reset(self.provider_name, key_index)

    def record_429(self, key_index: int, retry_after_header: str | None) -> float:
        self.circuit_breakers[key_index].record_429()
        return self._cooldown.on_429(self.provider_name, key_index, retry_after_header)

    def record_timeout(self, key_index: int) -> None:
        self.circuit_breakers[key_index].record_timeout()

    def record_error(self, key_index: int) -> None:
        self.circuit_breakers[key_index].record_error()

    # ------------------------------------------------------------------ #
    # Health / introspection
    # ------------------------------------------------------------------ #

    def health_summary(self) -> dict:
        return {
            "total": len(self.keys),
            "healthy": sum(
                1
                for i in range(len(self.keys))
                if not self._cooldown.is_cooling(self.provider_name, i)
                and self.circuit_breakers[i].state in ("closed", "half_open")
            ),
            "keys": [
                {
                    "index": i,
                    "redacted": _redact(self.keys[i]),
                    "status": self._key_status(i),
                    "rpm_used": self.buckets[i].rpm_used,
                    "rpm_limit": self.buckets[i].rpm_capacity,
                    "recovery_in": self._cooldown.seconds_until_recovery(self.provider_name, i),
                }
                for i in range(len(self.keys))
            ],
        }

    def _key_status(self, index: int) -> str:
        if self._cooldown.is_cooling(self.provider_name, index):
            return "cooling"
        cb_state = self.circuit_breakers[index].state
        if cb_state == "open":
            return "circuit_open"
        if cb_state == "half_open":
            return "recovering"
        return "healthy"


def _redact(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return key[:6] + "***" + key[-4:]