"""
clasp/queue/absorber.py

The 429 absorber — plan.md §11's core product differentiator. When a
provider returns a 429, this is what stands between that failure and
Claude Code ever seeing it.

Three-step strategy, in order:
  1. Record the failure (cooldown timer + circuit breaker).
  2. Try an immediate failover to the next healthy provider in the chain.
  3. If nothing is immediately available, queue the request and hold the
     SSE connection open with keep-alives until a provider recovers or the
     configured max wait elapses (at which point — and *only* at which
     point — the client finally sees a single `overloaded_error` event).

`on_upstream_429()` is an async generator: callers (chiefly
`providers.base.BaseProvider.stream()`) drain it with `async for chunk in
on_upstream_429(...): yield chunk`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from loguru import logger

from clasp.queue import sse_hold
from clasp.queue.manager import QueuedRequest, QueueManager, get_queue_manager
from clasp.ratelimit.cooldown import CooldownManager, get_cooldown_manager
from clasp.router import selector
from clasp.router.types import AnthropicRequest, SelectorConfig

if TYPE_CHECKING:
    from clasp.providers.registry import ProviderRegistry


async def on_upstream_429(
    request: AnthropicRequest,
    failed_provider: str,
    failed_key_index: int,
    retry_after_header: str | None,
    *,
    config: SelectorConfig | None = None,
    registry: "ProviderRegistry | None" = None,
    cooldown_mgr: CooldownManager | None = None,
    queue_mgr: QueueManager | None = None,
    keepalive_interval_seconds: float = 15.0,
) -> AsyncGenerator[str, None]:
    """
    Handle a 429 from `failed_provider`/`failed_key_index` for `request`,
    yielding Anthropic-format SSE chunks for whatever ultimately resolves
    the request (a failover response, a delayed response, or — as a last
    resort — a single error event).

    Injectable collaborators (`config`, `registry`, `cooldown_mgr`,
    `queue_mgr`) default to the process-wide singletons in production; tests
    pass their own isolated instances so each test run is independent of
    global state and of other tests.
    """
    if registry is None:
        from clasp.providers.registry import get_registry  # noqa: PLC0415

        registry = get_registry()
    if cooldown_mgr is None:
        cooldown_mgr = get_cooldown_manager()
    if queue_mgr is None:
        queue_mgr = get_queue_manager()

    # ── 1. Update state ─────────────────────────────────────────────────
    wait_s = cooldown_mgr.on_429(failed_provider, failed_key_index, retry_after_header)
    cb = registry.get_circuit_breaker(failed_provider)
    if cb is not None:
        cb.record_429()

    logger.warning(
        "absorbing upstream 429",
        provider=failed_provider,
        key_index=failed_key_index,
        cooldown_seconds=wait_s,
    )

    # ── 2. Immediate failover ───────────────────────────────────────────
    selection = await selector.select(
        request,
        exclude={failed_provider},
        config=config,
        registry=registry,
        cooldown_mgr=cooldown_mgr,
    )
    if selection:
        provider, key, key_idx = selection
        logger.info(
            "immediate failover succeeded",
            from_provider=failed_provider,
            to_provider=provider.provider_name,
        )
        async for chunk in provider.stream(request, key=key, key_index=key_idx):
            yield chunk
        return

    # ── 3. No failover — queue and hold the SSE connection open ────────
    logger.warning(
        "no immediate failover available, queueing request",
        priority=request.priority,
    )
    loop = asyncio.get_running_loop()
    future: "asyncio.Future" = loop.create_future()
    await queue_mgr.enqueue(
        QueuedRequest(
            request=request,
            future=future,
            priority=request.priority,
            enqueued_at=time.monotonic(),
        )
    )

    max_wait = (
        config.max_queue_wait_seconds if config is not None else queue_mgr.max_wait_seconds
    )

    async for chunk in sse_hold.hold_until_resolved(
        future,
        max_wait,
        keepalive_interval_seconds=keepalive_interval_seconds,
    ):
        yield chunk