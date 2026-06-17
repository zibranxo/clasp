"""
plan.md §18 Testing Strategy, "test_priority_queue.py":

    async def test_interactive_dispatched_before_background():
        # All providers at capacity.
        # Enqueue: 5 BACKGROUND (priority=2), then 1 INTERACTIVE (priority=0).
        # Provider recovers (1 slot available).
        # Assert: INTERACTIVE dispatched first despite arriving last.

router/selector.py doesn't exist yet (Sprint 5) — RequestQueue takes a
`selector` via constructor injection (see manager.py's docstring), so this
test substitutes a mock with full control over when "capacity" appears,
without needing the real selector or any real provider.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

from clasp.queue.manager import Priority, QueuedRequest, RequestQueue


class _FakeRequest:
    """Stand-in for the real AnthropicRequest type (not visible in this
    session) — RequestQueue only ever reads `.priority` off it directly."""

    def __init__(self, label: str, priority: int):
        self.label = label
        self.priority = priority


class _FakeProvider:
    """Records, in order, which request labels actually got dispatched
    (i.e. provider.stream() was called and consumed) — this is the test's
    ground truth for dispatch order, not just future-resolution order."""

    def __init__(self, dispatch_order: list[str]):
        self._dispatch_order = dispatch_order

    async def stream(self, request: _FakeRequest, *, key: str, key_index: int):
        self._dispatch_order.append(request.label)
        yield f"chunk-for-{request.label}"


@pytest.mark.asyncio
async def test_interactive_dispatched_before_background():
    dispatch_order: list[str] = []
    fake_provider = _FakeProvider(dispatch_order)

    # "All providers at capacity" until "provider recovers (1 slot available)":
    # the selector's first-ever call succeeds (the one recovered slot), every
    # call after that returns None (busy/cooling again). Because all 6
    # requests are enqueued *before* drain_task starts pulling (asserted
    # below via qsize()), that first call is guaranteed to be for whichever
    # item asyncio.PriorityQueue ranks highest — the INTERACTIVE one — not
    # whichever item happened to arrive first.
    selector = AsyncMock()
    selector.select = AsyncMock(
        side_effect=[(fake_provider, "fake-key", 0)] + [None] * 200
    )

    queue = RequestQueue(
        selector=selector,
        max_wait_seconds=60,  # generous — the 5 BACKGROUND requests must still
        # be retrying (not yet timed out) when we make our assertions below.
        poll_interval=0.02,  # fast retry so the test doesn't sit idle for real seconds
    )

    background_futures: list[asyncio.Future] = []
    for i in range(5):
        req = _FakeRequest(label=f"BACKGROUND_{i}", priority=Priority.BACKGROUND)
        fut = asyncio.get_event_loop().create_future()
        await queue.enqueue(
            QueuedRequest(request=req, future=fut, priority=req.priority)
        )
        background_futures.append(fut)

    interactive_req = _FakeRequest(label="INTERACTIVE", priority=Priority.INTERACTIVE)
    interactive_future: asyncio.Future = asyncio.get_event_loop().create_future()
    await queue.enqueue(
        QueuedRequest(
            request=interactive_req,
            future=interactive_future,
            priority=interactive_req.priority,
        )
    )

    assert queue.qsize() == 6, "all 6 requests must be enqueued before draining starts"

    drain = asyncio.create_task(queue.drain_task())
    try:
        result = await asyncio.wait_for(interactive_future, timeout=2.0)

        assert result == ["chunk-for-INTERACTIVE"]
        assert dispatch_order == ["INTERACTIVE"], (
            f"expected only INTERACTIVE dispatched so far, got: {dispatch_order}"
        )
        for fut in background_futures:
            assert not fut.done(), (
                "BACKGROUND requests must still be waiting, not dispatched"
            )
    finally:
        drain.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await drain
        for fut in background_futures:
            if not fut.done():
                fut.cancel()