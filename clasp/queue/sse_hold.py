"""
clasp/queue/sse_hold.py
SSE hold-open: emit keep-alive comments until a Future resolves, then
forward the buffered response chunks.

Protocol detail:
  - Keep-alive lines are SSE comment lines (": ...\\n\\n") which Anthropic's
    client ignores but which prevent proxy timeout / connection drops.
  - On timeout: yield an Anthropic ``error`` event so Claude Code can surface
    a useful message rather than a silent hang.
  - On future exception: yield an Anthropic ``error`` event.
  - On future result (list[bytes]): yield each chunk as-is (already Anthropic SSE).

No external dependencies — pure asyncio.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator

# Default keep-alive interval. Override per-call for tests.
DEFAULT_KEEPALIVE_INTERVAL = 15.0


async def hold_until_resolved(
    future: asyncio.Future,
    max_wait_seconds: float,
    *,
    keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL,
) -> AsyncGenerator[bytes, None]:
    """
    Async generator that holds an SSE connection open until *future* is done.

    Yields
    ------
    bytes
        SSE-formatted bytes:
          - Keep-alive comment lines (": keep-alive elapsed=Ns\\n\\n") every
            *keepalive_interval* seconds.
          - Response chunks (list[bytes]) from ``future.result()`` on success.
          - An Anthropic ``error`` event on timeout or exception.
    """
    start = time.monotonic()

    while not future.done():
        wait_left = max(0.0, max_wait_seconds - (time.monotonic() - start))
        if wait_left <= 0:
            # Timeout exceeded — yield error and return.
            yield _error_event("All providers rate-limited. Try again shortly.")
            future.cancel()
            return

        # Wait up to keepalive_interval for the future to resolve.
        try:
            await asyncio.wait_for(
                asyncio.shield(future),
                timeout=min(keepalive_interval, wait_left),
            )
        except asyncio.TimeoutError:
            elapsed = int(time.monotonic() - start)
            # Check overall timeout AFTER emitting keep-alive (so at least
            # one keep-alive is visible before the error event).
            if time.monotonic() - start >= max_wait_seconds:
                yield _error_event("All providers rate-limited. Try again shortly.")
                future.cancel()
                return
            yield f": keep-alive elapsed={elapsed}s\n\n".encode()
        except asyncio.CancelledError:
            return

    # Future is done — check result.
    if future.cancelled():
        return

    exc = future.exception()
    if exc is not None:
        yield _error_event(str(exc))
        return

    # Success — forward the buffered chunks.
    chunks: list[bytes] = future.result()
    for chunk in chunks:
        yield chunk


def _error_event(message: str) -> bytes:
    """Build an Anthropic SSE error event."""
    payload = json.dumps({
        "type": "error",
        "error": {
            "type": "overloaded_error",
            "message": message,
        },
    })
    return f"event: error\ndata: {payload}\n\n".encode()