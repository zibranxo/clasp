"""
clasp/queue/sse_hold.py

Keeps an SSE connection alive while a queued request waits for a provider
to become available, per plan.md §11.

While `future` is unresolved, this yields a `: keep-alive elapsed=Ns\n\n`
SSE comment line every `keepalive_interval_seconds` so Claude Code's HTTP
client (and any intermediate proxies) don't time out the connection. Once
`max_wait` total seconds have elapsed with no resolution, it yields a single
Anthropic-shaped `overloaded_error` event and stops — that's the *only*
error a client should ever see from the queueing path; everything else is
either a real response or continued silence-with-keepalives.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator


async def hold_until_resolved(
    future: "asyncio.Future",
    max_wait: float,
    *,
    keepalive_interval_seconds: float = 15.0,
) -> AsyncGenerator[str, None]:
    """
    Yield SSE keep-alive comments until *future* resolves or *max_wait*
    seconds elapse, then yield either the resolved chunks or a single
    Anthropic-shaped error event.

    Parameters
    ----------
    future:
        Resolves to a `list[str]` of SSE chunks (set by the queue drain
        task) or has an exception set on it.
    max_wait:
        Total seconds to wait before giving up and yielding an error event.
    keepalive_interval_seconds:
        How often to check in / emit a keep-alive comment while waiting.
        Defaults to 15s per plan.md §11; tests may pass a smaller value to
        make the keep-alive behavior observable without a long real wait.
    """
    start = time.monotonic()
    while not future.done():
        try:
            await asyncio.wait_for(
                asyncio.shield(future), timeout=keepalive_interval_seconds
            )
        except asyncio.TimeoutError:
            elapsed = int(time.monotonic() - start)
            yield f": keep-alive elapsed={elapsed}s\n\n"
            if time.monotonic() - start > max_wait:
                yield _error_event("All providers rate-limited. Try again shortly.")
                return
        except Exception:  # noqa: BLE001
            # The future itself completed with an exception while we were
            # waiting on it (e.g. the queue manager's own max-wait check in
            # drain_task fired concurrently with ours and called
            # future.set_exception()). asyncio.wait_for re-raises a shielded
            # future's exception immediately rather than waiting for the
            # next `future.done()` check, so it lands here instead of in
            # the TimeoutError branch above. Fall through to the same
            # `future.exception()` handling below by simply breaking — the
            # `while not future.done()` condition is now False, since the
            # future is in fact done (just done with an exception).
            break

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