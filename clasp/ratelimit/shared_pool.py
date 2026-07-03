"""
clasp/ratelimit/shared_pool.py
==============================
Shared Pool Mode: Per-User token bucket enforcement (Phase 8).
"""
from __future__ import annotations

import asyncio
from typing import Any

from clasp.ratelimit.bucket import TokenBucket

class PerUserBucket:
    """
    Limits each shared-pool user to per_user_rpm_limit requests/min.
    Separate from provider buckets — applies on top of provider limits.
    """
    def __init__(self, per_user_rpm: int):
        self.buckets: dict[str, TokenBucket] = {}  # user_id → bucket
        self.usage: dict[str, int] = {}  # user_id → requests today
        self.per_user_rpm = per_user_rpm
        self._lock = asyncio.Lock()

    async def check_and_consume(self, user_id: str) -> bool:
        """
        Check if the user has available quota and consume a token if so.
        Updates daily usage stats atomically.
        """
        async with self._lock:
            if user_id not in self.buckets:
                self.buckets[user_id] = TokenBucket(
                    rpm_limit=self.per_user_rpm,
                    tpm_limit=None,
                    soft_threshold=1.0  # users get exactly their limit
                )
                self.usage[user_id] = 0
            
            # Note: try_consume already holds the bucket lock inside TokenBucket
            can_proceed = await self.buckets[user_id].try_consume(0)
            if can_proceed:
                self.usage[user_id] += 1
            return can_proceed

    def get_daily_usage(self) -> dict[str, int]:
        """Return the current snapshot of requests_today for all users."""
        return dict(self.usage)

    def set_daily_usage(self, usage: dict[str, int]) -> None:
        """Load state from persistence (e.g. daily rollover)."""
        self.usage = dict(usage)


# Global instance
_shared_pool: PerUserBucket | None = None

def get_shared_pool(per_user_rpm: int) -> PerUserBucket:
    global _shared_pool
    if _shared_pool is None:
        _shared_pool = PerUserBucket(per_user_rpm)
    else:
        # Hot-reload update in case settings changed
        _shared_pool.per_user_rpm = per_user_rpm
    return _shared_pool
