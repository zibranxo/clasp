"""
Priority-ordered request queue + drain coroutine for CLASP's 429-absorption
layer.

plan.md §11 "Phase 3 — 429 Absorber + Priority Queue", "Priority Queue Drain
(queue/manager.py)".

Priority levels (lower int = served first; under provider pressure,
interactive requests always win):
    0 = INTERACTIVE  — user typed something, wants it now
    1 = TOOL_USE     — tool call mid-session
    2 = BACKGROUND   — file indexing, summarization, etc.

queue/absorber.py (Sprint 3 step 48 — not built in this pass) is the producer:
on a 429 with no immediate failover available, it builds a QueuedRequest and
calls RequestQueue.enqueue(). drain_task() is the consumer: a single
long-running background coroutine (started from server.py's startup hook,
step 50 — not wired in this pass) that pulls the highest-priority item and
repeatedly asks the provider selector for capacity until either a provider
frees up or the request has waited past max_wait_seconds.

Integration note: router/selector.py (plan.md §13, Sprint 5) doesn't exist
yet. RequestQueue takes a `selector` object via constructor injection — at
real call sites this will be `clasp.router.selector` (the module itself,
since it exposes an async `select()` function matching this shape:
`async def select(request, exclude=None) -> tuple[BaseProvider, str, int] | None`)
— rather than importing it at module level, so this file doesn't hard-fail
on import before selector.py exists, and so tests can substitute a mock.
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Protocol

from loguru import logger


class Priority(IntEnum):
    INTERACTIVE = 0
    TOOL_USE = 1
    BACKGROUND = 2


class QueueTimeoutError(Exception):
    """Set as the result of a QueuedRequest's future when it waited longer
    than max_wait_seconds without any provider ever freeing up capacity."""


class QueueFullError(Exception):
    """Raised by enqueue() when the queue is already at max_queue_depth.

    Not present in plan.md's literal code sample, but max_queue_depth IS part
    of the documented config schema (plan.md §6, server.max_queue_depth) —
    accepting unlimited requests with no bound on a documented limit would be
    the gap, not this check.
    """


class Selector(Protocol):
    async def select(
        self, request: Any, exclude: set[str] | None = None
    ) -> tuple[Any, str, int] | None: ...


@dataclass
class QueuedRequest:
    """One request waiting for provider capacity.

    `request` is duck-typed (the real AnthropicRequest type lives in a module
    not visible in this session) — RequestQueue itself only ever reads
    `.priority` off of it directly; everything else about `request` is opaque
    and passed straight through to `selector.select()` / `provider.stream()`.
    """

    request: Any
    future: asyncio.Future
    priority: int
    enqueued_at: float = field(default_factory=time.monotonic)


class RequestQueue:
    """Wraps asyncio.PriorityQueue so queued requests are dispatched in
    priority order (INTERACTIVE first), not arrival order, and drained by a
    single background task that retries the selector until capacity exists
    or the request times out.
    """

    def __init__(
        self,
        *,
        selector: Selector,
        max_wait_seconds: int = 180,
        max_queue_depth: int = 50,
        poll_interval: float = 1.0,
    ) -> None:
        self._q: asyncio.PriorityQueue[tuple[int, int, QueuedRequest]] = (
            asyncio.PriorityQueue()
        )
        # Tie-breaker for asyncio.PriorityQueue: QueuedRequest objects aren't
        # orderable, so two same-priority items with no distinct second tuple
        # element would raise TypeError on comparison. itertools.count() gives
        # a strictly increasing, guaranteed-unique sequence number — safer
        # than e.g. time.monotonic(), which *could* collide at high enough
        # enqueue rates on a fast clock.
        self._counter = itertools.count()
        self._selector = selector
        self.max_wait_seconds = max_wait_seconds
        self.max_queue_depth = max_queue_depth
        # Parameterized rather than the spec's hardcoded `await asyncio.sleep(1)`
        # so tests can drive the retry loop quickly. Same algorithm, just a
        # configurable constant — production code gets the spec's default of 1s.
        self.poll_interval = poll_interval

    def qsize(self) -> int:
        return self._q.qsize()

    async def enqueue(self, queued: QueuedRequest) -> None:
        if self._q.qsize() >= self.max_queue_depth:
            raise QueueFullError(
                f"Queue is at capacity ({self.max_queue_depth}); rejecting new request."
            )
        seq = next(self._counter)
        await self._q.put((queued.priority, seq, queued))
        logger.debug(
            "Enqueued request priority={} (queue depth now {})",
            queued.priority,
            self._q.qsize(),
        )

    async def drain_task(self) -> None:
        """Background coroutine. Runs forever. Dispatches queued requests as
        providers recover. Intended to be started once via
        `asyncio.create_task(queue.drain_task())` from server.py's startup
        hook and cancelled on shutdown.
        """
        while True:
            try:
                await self._drain_one()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — must never kill the drain loop
                logger.error("drain_task error: {}", e)
                await asyncio.sleep(1)

    async def _drain_one(self) -> None:
        """Pull exactly one item off the queue and dispatch-or-timeout it.

        Split out from drain_task() so each cycle is independently testable
        without needing to manage the infinite outer loop. Behavior is
        identical to plan.md's single-function version; this just replaces
        the while/else control flow with an explicit `timed_out` flag for
        readability — same algorithm, same outcomes.
        """
        _priority, _seq, req = await self._q.get()

        selection = None
        timed_out = False
        while selection is None and not timed_out:
            selection = await self._selector.select(req.request)
            if selection is None:
                if time.monotonic() - req.enqueued_at > self.max_wait_seconds:
                    if not req.future.done():
                        req.future.set_exception(QueueTimeoutError())
                    timed_out = True
                else:
                    await asyncio.sleep(self.poll_interval)

        if timed_out:
            self._q.task_done()
            return

        provider, key, key_idx = selection
        chunks = []
        try:
            async for chunk in provider.stream(req.request, key=key, key_index=key_idx):
                chunks.append(chunk)
            if not req.future.done():
                req.future.set_result(chunks)
        except Exception as e:
            if not req.future.done():
                req.future.set_exception(e)
        finally:
            self._q.task_done()