"""
clasp/queue/manager.py
Priority queue for requests that can't be dispatched immediately.

Priority levels:
  0 = INTERACTIVE  — user is waiting; highest priority
  1 = TOOL_USE     — tool call mid-session
  2 = BACKGROUND   — file indexing, summarization

The drain_task() coroutine runs as a background asyncio task (started by
server.py at startup). It retries selector.select() every 1 s until a
provider becomes available, then dispatches and resolves the Future.

No external dependencies — pure asyncio + heapq.
"""

from __future__ import annotations

import asyncio
import heapq
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from clasp.queue.sse_hold import _error_event


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass(order=False)
class QueuedRequest:
    """
    A request waiting for a provider slot.

    Ordering is by (priority, enqueued_at) so that lower priority numbers
    and earlier arrival times both favour dispatch.
    """

    request: Any               # AnthropicRequest or dict (Sprint 1 compat)
    future: asyncio.Future     # resolved with list[bytes] on success
    priority: int = 0          # 0=INTERACTIVE, 1=TOOL_USE, 2=BACKGROUND
    enqueued_at: float = field(default_factory=time.monotonic)

    # Comparison operators for heapq (min-heap — smallest first)
    def __lt__(self, other: "QueuedRequest") -> bool:
        return (self.priority, self.enqueued_at) < (other.priority, other.enqueued_at)

    def __le__(self, other: "QueuedRequest") -> bool:
        return (self.priority, self.enqueued_at) <= (other.priority, other.enqueued_at)


class QueueTimeoutError(Exception):
    """Raised when a queued request exceeds max_wait_seconds."""

    def __str__(self) -> str:
        return "All providers rate-limited. Try again shortly."


# ---------------------------------------------------------------------------
# Selector type alias (injected; avoids circular import)
# ---------------------------------------------------------------------------

# Signature: async (request, exclude=None) → (provider, key, key_idx) | None
SelectorFn = Callable[..., Awaitable[Optional[tuple[Any, str, int]]]]


# ---------------------------------------------------------------------------
# Queue Manager
# ---------------------------------------------------------------------------

class QueueManager:
    """
    Priority queue + background drain task.

    Parameters
    ----------
    selector_fn:
        Async callable matching the signature of ``router.selector.select``.
        Injected to avoid circular imports and to simplify testing.
    max_wait_seconds:
        How long a queued request may wait before timing out.
    drain_poll_interval:
        How often the drain task polls for an available provider (seconds).
        Default 1 s; reduced in tests for speed.
    """

    def __init__(
        self,
        selector_fn: SelectorFn,
        max_wait_seconds: float = 120.0,
        drain_poll_interval: float = 1.0,
    ) -> None:
        self._selector_fn = selector_fn
        self.max_wait_seconds = max_wait_seconds
        self._drain_poll_interval = drain_poll_interval

        # Heap invariant maintained by heapq — protected by asyncio.Lock.
        self._heap: list[QueuedRequest] = []
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Event()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def enqueue(self, req: QueuedRequest) -> None:
        """Add a request to the priority queue."""
        async with self._lock:
            heapq.heappush(self._heap, req)
            self._not_empty.set()

    async def drain_task(self) -> None:
        """
        Background coroutine — runs forever.
        Dispatches queued requests as provider slots become available.
        """
        while True:
            try:
                # Wait until the queue is non-empty.
                await self._not_empty.wait()

                # Peek at the highest-priority item without popping.
                async with self._lock:
                    if not self._heap:
                        self._not_empty.clear()
                        continue
                    req = self._heap[0]

                # Try to find a provider.
                selection = await self._selector_fn(req.request)

                if selection is None:
                    # Check overall timeout.
                    if time.monotonic() - req.enqueued_at > self.max_wait_seconds:
                        async with self._lock:
                            if self._heap and self._heap[0] is req:
                                heapq.heappop(self._heap)
                                if not self._heap:
                                    self._not_empty.clear()
                        if not req.future.done():
                            req.future.set_exception(QueueTimeoutError())
                    else:
                        # No provider yet — wait and retry.
                        await asyncio.sleep(self._drain_poll_interval)
                    continue

                # We have a selection — pop from queue and dispatch.
                async with self._lock:
                    if self._heap and self._heap[0] is req:
                        heapq.heappop(self._heap)
                    if not self._heap:
                        self._not_empty.clear()

                provider, key, key_idx = selection
                await self._dispatch(req, provider, key, key_idx)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                # Log but keep running.
                try:
                    from loguru import logger  # type: ignore[import]
                    logger.error("drain_task error", exc=str(exc))
                except ImportError:
                    import traceback
                    traceback.print_exc()
                await asyncio.sleep(1)

    async def _dispatch(
        self,
        req: QueuedRequest,
        provider: Any,
        key: str,
        key_idx: int,
    ) -> None:
        """Stream from provider and resolve the future with collected chunks."""
        if req.future.done():
            return
        chunks: list[bytes] = []
        try:
            async for chunk in provider.stream(req.request, key=key, key_index=key_idx):
                chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
            req.future.set_result(chunks)
        except Exception as exc:
            if not req.future.done():
                req.future.set_exception(exc)

    @property
    def depth(self) -> int:
        return len(self._heap)


# ---------------------------------------------------------------------------
# Module-level singleton (created lazily by absorber / server)
# ---------------------------------------------------------------------------

_queue_manager: Optional[QueueManager] = None


def init_queue_manager(
    selector_fn: SelectorFn,
    max_wait_seconds: float = 120.0,
    drain_poll_interval: float = 1.0,
) -> QueueManager:
    global _queue_manager
    _queue_manager = QueueManager(
        selector_fn=selector_fn,
        max_wait_seconds=max_wait_seconds,
        drain_poll_interval=drain_poll_interval,
    )
    return _queue_manager


def get_queue_manager() -> Optional[QueueManager]:
    return _queue_manager