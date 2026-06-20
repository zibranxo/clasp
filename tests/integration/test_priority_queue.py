"""
tests/integration/test_priority_queue.py

Integration tests for the QueueManager priority sorting logic.

Tested scenarios
----------------
1. test_priority_ordering
   Enqueue multiple requests with different priorities (INTERACTIVE=0,
   TOOL_USE=1, BACKGROUND=2) simultaneously while providers are unavailable.
   When a provider recovers, verify the requests are served in priority order
   rather than insertion order.

2. test_fifo_within_same_priority
   Enqueue multiple requests with the same priority. Verify they are served
   in the exact order they were enqueued (FIFO tiebreaker).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator

import pytest

from clasp.api.detect import RequestType
from clasp.providers.base import BaseProvider
from clasp.providers.registry import ProviderRegistry
from clasp.queue.manager import QueuedRequest, QueueManager
from clasp.ratelimit.bucket import TokenBucket
from clasp.ratelimit.circuit_breaker import CircuitBreaker
from clasp.ratelimit.cooldown import CooldownManager
from clasp.router.types import AnthropicRequest, ProviderEnableConfig, SelectorConfig
from tests.integration.test_absorber import FAKE_CATALOG

pytestmark = pytest.mark.asyncio


class RecordingProvider(BaseProvider):
    """
    Healthy provider that simply records the requests it receives in order,
    so tests can assert on the drainage sequence.
    """

    def __init__(self, name: str) -> None:
        self.provider_name = name
        self.received_requests: list[str] = []

    async def _stream_raw(
        self, request: AnthropicRequest, key: str, key_index: int
    ) -> AsyncGenerator[str, None]:
        # We store the "user" message content as a unique marker for the request
        marker = request.body["messages"][0]["content"]
        self.received_requests.append(marker)
        yield f"event: content_block_delta\ndata: {{\"delta\": {{\"text\": \"Response to {marker}\"}}}}\n\n"
        yield 'event: message_stop\ndata: {"type":"message_stop"}\n\n'


def _make_request(marker: str, priority: int) -> AnthropicRequest:
    return AnthropicRequest(
        body={
            "model": "claude-sonnet-4-6",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": marker}],
        },
        type=RequestType.INTERACTIVE,  # Doesn't matter for this test
        priority=priority,
        estimated_tokens=10,
    )


def _setup_test_env() -> tuple[RecordingProvider, ProviderRegistry, CooldownManager, QueueManager, SelectorConfig]:
    from clasp.ratelimit.key_pool import KeyPool
    provider = RecordingProvider("provider_a")
    registry = ProviderRegistry()
    cooldown_mgr = CooldownManager(catalog=FAKE_CATALOG)
    profile = FAKE_CATALOG["provider_a"]
    pool = KeyPool("provider_a", ["key_a_0"], profile, cooldown_tracker=cooldown_mgr)
    pool.buckets[0].rpm_limit = 1000
    pool.buckets[0].tpm_limit = None
    registry._register("provider_a", provider, pool)
    queue_mgr = QueueManager(max_wait_seconds=5.0, drain_poll_interval=0.05)
    config = SelectorConfig(
        provider_chain=["provider_a"],
        providers={"provider_a": ProviderEnableConfig(enabled=True)},
        max_queue_wait_seconds=5.0,
    )
    return provider, registry, cooldown_mgr, queue_mgr, config


async def test_priority_ordering():
    """
    Enqueue 3 requests out of order: Background(2), Interactive(0), ToolUse(1).
    Verify they are executed in priority order: Interactive(0), ToolUse(1), Background(2).
    """
    provider, registry, cooldown_mgr, queue_mgr, config = _setup_test_env()

    # Disable the provider so requests get stuck in the queue
    with cooldown_mgr._lock:
        cooldown_mgr._cooling[("provider_a", 0)] = time.monotonic() + 10.0

    # Create futures
    f_bg = asyncio.Future()
    f_interactive = asyncio.Future()
    f_tool = asyncio.Future()

    # Enqueue backwards (least important first)
    now = time.monotonic()
    await queue_mgr.enqueue(QueuedRequest(request=_make_request("bg", 2), future=f_bg, priority=2, enqueued_at=now))
    await queue_mgr.enqueue(QueuedRequest(request=_make_request("interactive", 0), future=f_interactive, priority=0, enqueued_at=now + 0.1))
    await queue_mgr.enqueue(QueuedRequest(request=_make_request("tool", 1), future=f_tool, priority=1, enqueued_at=now + 0.2))

    assert queue_mgr.qsize() == 3

    # Start the drain task
    drain = asyncio.create_task(
        queue_mgr.drain_task(
            config=config,
            registry=registry,
            cooldown_mgr=cooldown_mgr,
        )
    )

    # Let the drain task block
    await asyncio.sleep(0.1)

    # Re-enable the provider
    with cooldown_mgr._lock:
        cooldown_mgr._cooling.pop(("provider_a", 0), None)

    # Wait for all futures to resolve
    await asyncio.wait_for(asyncio.gather(f_bg, f_interactive, f_tool), timeout=2.0)

    # Clean up
    drain.cancel()
    await asyncio.gather(drain, return_exceptions=True)

    # Verify the order the provider received them
    assert provider.received_requests == ["interactive", "tool", "bg"]


async def test_fifo_within_same_priority():
    """
    Enqueue 3 requests with the same priority (1), slightly staggered in time.
    Verify they are executed in FIFO order.
    """
    provider, registry, cooldown_mgr, queue_mgr, config = _setup_test_env()

    with cooldown_mgr._lock:
        cooldown_mgr._cooling[("provider_a", 0)] = time.monotonic() + 10.0

    futures = [asyncio.Future() for _ in range(3)]
    now = time.monotonic()

    # Enqueue 3 ToolUse requests sequentially
    for i, f in enumerate(futures):
        req = _make_request(f"req_{i}", 1)
        await queue_mgr.enqueue(QueuedRequest(request=req, future=f, priority=1, enqueued_at=now + (i * 0.1)))

    assert queue_mgr.qsize() == 3

    drain = asyncio.create_task(queue_mgr.drain_task(config=config, registry=registry, cooldown_mgr=cooldown_mgr))
    await asyncio.sleep(0.1)

    # Re-enable
    with cooldown_mgr._lock:
        cooldown_mgr._cooling.pop(("provider_a", 0), None)

    # Wait for completion
    await asyncio.wait_for(asyncio.gather(*futures), timeout=2.0)

    drain.cancel()
    await asyncio.gather(drain, return_exceptions=True)

    # Verify FIFO order
    assert provider.received_requests == ["req_0", "req_1", "req_2"]
