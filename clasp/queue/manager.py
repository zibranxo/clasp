"""
clasp/queue/manager.py

Priority queue + background drain loop for requests that couldn't be served
immediately (every provider in the chain was disabled, circuit-broken,
cooling, or out of rate-limit headroom).

Priority levels (lower = served first): 0=INTERACTIVE, 1=TOOL_USE,
2=BACKGROUND. `asyncio.PriorityQueue` orders by the first tuple element, so
`enqueue()` pushes `(priority, enqueued_at, queued_request)` — the
`enqueued_at` timestamp is the tiebreaker, giving FIFO order within the same
priority tier.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

from clasp.ratelimit.cooldown import CooldownManager
from clasp.router.types import AnthropicRequest, SelectorConfig

if TYPE_CHECKING:
    from clasp.providers.registry import ProviderRegistry


class QueueTimeoutError(Exception):
    """Raised (via the future) when a queued request waits longer than
    `max_wait_seconds` without any provider becoming available."""

    def __init__(self, message: str = "All providers rate-limited. Try again shortly."):
        super().__init__(message)


@dataclass
class QueuedRequest:
    request: AnthropicRequest
    future: "asyncio.Future"
    priority: int
    enqueued_at: float


class QueueManager:
    """Holds the priority queue and runs the background drain loop."""

    def __init__(self, max_wait_seconds: float = 180.0, drain_poll_interval: float = 1.0) -> None:
        self.max_wait_seconds = max_wait_seconds
        self.drain_poll_interval = drain_poll_interval
        self._q: "asyncio.PriorityQueue[tuple[int, float, QueuedRequest]]" = (
            asyncio.PriorityQueue()
        )

    async def enqueue(self, req: QueuedRequest) -> None:
        """Add *req* to the priority queue, to be picked up by drain_task()."""
        await self._q.put((req.priority, req.enqueued_at, req))
        logger.info(
            "request queued",
            priority=req.priority,
            queue_size=self._q.qsize(),
        )

    def qsize(self) -> int:
        return self._q.qsize()

    @property
    def depth(self) -> int:
        return self._q.qsize()

    async def drain_task(
        self,
        *,
        config: SelectorConfig | None = None,
        registry: "ProviderRegistry | None" = None,
        cooldown_mgr: CooldownManager | None = None,
    ) -> None:
        """
        Background coroutine. Runs forever. Dispatches queued requests as
        providers recover.

        Parameters mirror `router.selector.select()`'s injectable
        collaborators so the same `config`/`registry`/`cooldown_mgr`
        instances used by `queue.absorber.on_upstream_429()` are also used
        here — important for tests, where each test builds its own
        isolated set of fakes rather than relying on process-wide
        singletons.
        """
        from clasp.router import selector  # noqa: PLC0415

        while True:
            try:
                _priority, _ts, req = await self._q.get()
                selection = None
                while selection is None:
                    selection = await selector.select(
                        req.request,
                        config=config,
                        registry=registry,
                        cooldown_mgr=cooldown_mgr,
                    )
                    if selection is None:
                        if time.monotonic() - req.enqueued_at > self.max_wait_seconds:
                            req.future.set_exception(QueueTimeoutError())
                            self._q.task_done()
                            break
                        await asyncio.sleep(self.drain_poll_interval)
                else:
                    provider, key, key_idx = selection
                    chunks: list[bytes] = []
                    try:
                        async for chunk in provider.stream(
                            req.request, key=key, key_index=key_idx
                        ):
                            raw = chunk if isinstance(chunk, bytes) else chunk.encode()
                            chunks.append(raw)
                        req.future.set_result(chunks)
                    except Exception as e:  # noqa: BLE001
                        req.future.set_exception(e)
                    finally:
                        self._q.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.error("drain_task error", error=str(e))
                await asyncio.sleep(1)


# ---------------------------------------------------------------------------
# Process-wide singleton (production default; tests construct their own
# QueueManager() instances and pass them explicitly instead).
# ---------------------------------------------------------------------------

_queue_manager: QueueManager | None = None


def get_queue_manager() -> QueueManager:
    """Return the process-wide QueueManager singleton, creating it lazily."""
    global _queue_manager  # noqa: PLW0603
    if _queue_manager is None:
        _queue_manager = QueueManager()
    return _queue_manager


def reset_queue_manager() -> None:
    """Reset the singleton to a fresh QueueManager (test hygiene helper)."""
    global _queue_manager  # noqa: PLW0603
    _queue_manager = QueueManager()