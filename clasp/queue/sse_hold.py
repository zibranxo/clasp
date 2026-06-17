"""
SSE hold-open generator for CLASP's 429-absorption layer.

plan.md §11 "Phase 3 — 429 Absorber + Priority Queue", "SSE Hold-Open (queue/sse_hold.py)".

hold_until_resolved() is consumed by queue/absorber.py (Sprint 3 step 48 — not
built in this pass) when no provider has immediate capacity for a request: the
request is enqueued, and this generator keeps the client's SSE connection alive
with `": keep-alive\\n\\n"` comments while the queue's drain_task() works on
finding capacity, until the request's Future resolves (success), errors, or the
overall wait exceeds max_wait seconds (timeout — yields an error event instead
of letting the connection silently die).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator

KEEP_ALIVE_INTERVAL_SECONDS = 15


async def hold_until_resolved(
    future: asyncio.Future,
    max_wait: int,
    *,
    keep_alive_interval: float = KEEP_ALIVE_INTERVAL_SECONDS,
) -> AsyncGenerator[str, None]:
    """Yield SSE keep-alive comments every `keep_alive_interval` seconds while
    `future` is pending, then yield the resolved result (or an error event).

    `keep_alive_interval` is parameterized (defaulting to the spec's 15s) so
    tests can exercise the keep-alive cadence without waiting 15 real seconds
    per tick — the algorithm itself is unchanged from plan.md.

    asyncio.shield() is essential here: without it, asyncio.wait_for()'s
    timeout would cancel `future` itself on each 15s tick, destroying state
    other code (queue/manager.py's drain_task) is still working to resolve.
    Shielding lets wait_for() time out and retry-wait on the *same* future
    indefinitely, while only ever cancelling its own internal wrapper task.
    """
    start = time.monotonic()
    while not future.done():
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=keep_alive_interval)
        except asyncio.TimeoutError:
            elapsed = int(time.monotonic() - start)
            yield f": keep-alive elapsed={elapsed}s\n\n"
            if time.monotonic() - start > max_wait:
                yield _error_event("All providers rate-limited. Try again shortly.")
                return

    if future.exception():
        yield _error_event(str(future.exception()))
        return

    for chunk in future.result():
        yield chunk


def _error_event(msg: str) -> str:
    payload = json.dumps(
        {"type": "error", "error": {"type": "overloaded_error", "message": msg}}
    )
    return f"event: error\ndata: {payload}\n\n"