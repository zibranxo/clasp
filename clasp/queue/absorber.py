"""
clasp/queue/absorber.py
The 429 Absorber — the most important file in the project (P2: Never propagate 429).

``on_upstream_429()`` is called by ``providers/base.py`` whenever an upstream
provider responds with HTTP 429.  It:

  1. Updates cooldown + circuit-breaker state for the failed (provider, key).
  2. Tries immediate failover via ``selector.select(exclude={failed_provider})``.
  3. If failover succeeds: streams the failover response transparently.
  4. If no failover available: enqueues the request and yields SSE keep-alive
     comments until a provider recovers (or timeout → yields an error event).

Claude Code never sees a 429.  It either sees a slightly-delayed response or
an "overloaded_error" event (if all providers stay rate-limited past the queue
timeout).

All dependencies are injected so the function is unit-testable without any
real network or global state.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncGenerator, Callable, Optional, Awaitable

from clasp.ratelimit.cooldown import CooldownStore, get_cooldown_store
from clasp.ratelimit.circuit_breaker import CircuitBreakerStore, get_cb_store
from clasp.queue.sse_hold import hold_until_resolved, DEFAULT_KEEPALIVE_INTERVAL
from clasp.queue.manager import QueueManager, QueuedRequest, get_queue_manager


# Selector signature: async (request, exclude=None, **kwargs) → (provider, key, idx) | None
SelectorFn = Callable[..., Awaitable[Optional[tuple[Any, str, int]]]]


async def on_upstream_429(
    request: Any,
    failed_provider: str,
    failed_key_index: int,
    retry_after_header: Optional[str],
    *,
    # Injected dependencies (use module globals when None)
    cooldown_store: Optional[CooldownStore] = None,
    cb_store: Optional[CircuitBreakerStore] = None,
    selector_fn: Optional[SelectorFn] = None,
    queue_mgr: Optional[QueueManager] = None,
    max_queue_wait_seconds: float = 120.0,
    keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL,
    # Extra kwargs forwarded to selector_fn (provider_chain, instances, etc.)
    selector_kwargs: Optional[dict[str, Any]] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Handle an upstream 429 response without propagating it to Claude Code.

    Yields SSE bytes — either response chunks from a failover provider, keep-alive
    comment lines while queued, or an Anthropic error event on final timeout.

    Parameters
    ----------
    request:
        The original request object/dict being proxied.
    failed_provider:
        Name of the provider that returned 429.
    failed_key_index:
        Index of the API key within that provider's key list.
    retry_after_header:
        Value of the ``Retry-After`` response header, if present.
    cooldown_store, cb_store:
        Rate-limit state stores.  Defaults to module-level singletons.
    selector_fn:
        Provider selection callable.  Defaults to ``router.selector.select``.
    queue_mgr:
        Queue manager instance.  Defaults to the module-level singleton.
    max_queue_wait_seconds:
        Maximum time a request may wait in the queue before a timeout error
        event is emitted.
    keepalive_interval:
        Seconds between SSE keep-alive comments (default 15 s; reduce in tests).
    selector_kwargs:
        Extra keyword arguments forwarded to ``selector_fn`` (e.g.
        ``provider_chain``, ``provider_instances``, ``provider_keys``, etc.)
    """

    # ------------------------------------------------------------------
    # 1. Update state for the failed (provider, key)
    # ------------------------------------------------------------------
    cd = cooldown_store or get_cooldown_store()
    cb = cb_store or get_cb_store()
    selector_kwargs = selector_kwargs or {}

    cd.on_429(failed_provider, failed_key_index, retry_after_header)
    cb.get(failed_provider, failed_key_index).record_429()

    try:
        from loguru import logger  # type: ignore[import]
        logger.warning(
            "Upstream 429 received",
            provider=failed_provider,
            key_index=failed_key_index,
            retry_after=retry_after_header,
        )
    except ImportError:
        pass

    # ------------------------------------------------------------------
    # 2. Immediate failover — try another provider/key right now
    # ------------------------------------------------------------------
    if selector_fn is None:
        from clasp.router.selector import select as _default_select
        selector_fn = _default_select

    selection = await selector_fn(
        request,
        exclude={failed_provider},
        cooldown_store=cd,
        cb_store=cb,
        **selector_kwargs,
    )

    if selection is not None:
        provider, api_key, key_idx = selection
        try:
            from loguru import logger  # type: ignore[import]
            logger.info(
                "Immediate failover",
                from_provider=failed_provider,
                to_provider=getattr(provider, "name", str(provider)),
                key_index=key_idx,
            )
        except ImportError:
            pass

        async for chunk in provider.stream(request, key=api_key, key_index=key_idx):
            yield chunk if isinstance(chunk, bytes) else chunk.encode()
        return

    # ------------------------------------------------------------------
    # 3. No failover available — enqueue and hold the SSE connection open
    # ------------------------------------------------------------------
    try:
        from loguru import logger  # type: ignore[import]
        logger.warning(
            "No failover available — queuing request",
            failed_provider=failed_provider,
        )
    except ImportError:
        pass

    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()

    priority = getattr(request, "priority", 0)

    queued = QueuedRequest(
        request=request,
        future=future,
        priority=priority,
        enqueued_at=time.monotonic(),
    )

    mgr = queue_mgr or get_queue_manager()
    if mgr is None:
        # Queue not initialised — yield error immediately.
        from clasp.queue.sse_hold import _error_event
        yield _error_event(
            "All providers rate-limited and no queue available. Try again shortly."
        )
        return

    await mgr.enqueue(queued)

    async for chunk in hold_until_resolved(
        future,
        max_queue_wait_seconds,
        keepalive_interval=keepalive_interval,
    ):
        yield chunk