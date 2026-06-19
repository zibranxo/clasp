"""
tests/integration/test_absorber.py

Integration tests for the 429 absorber pipeline.

Tested scenarios
----------------
1. test_immediate_failover_on_429
   Primary 429s → absorber tries selector.select(exclude={primary}) →
   secondary is healthy → response comes from secondary, no error event,
   no keep-alive events (resolved without ever touching the queue).

2. test_queue_and_hold_when_all_cooling
   Both providers cooling at request time → request is queued →
   keep-alive events emitted while waiting → secondary provider recovers
   after 2 s → drain_task dispatches → response eventually arrives, no
   error event.

3. test_queue_timeout
   All providers cooling; max_queue_wait_seconds=3 →
   hold_until_resolved()'s own per-keepalive check fires after 3 s →
   a single Anthropic overloaded_error event arrives; total elapsed time
   within [2.5 s, 6.0 s]; at least one keep-alive comment was emitted.

Test design principles
----------------------
• Every test builds its own isolated (registry, cooldown_mgr, queue_mgr,
  config) rather than touching process-wide singletons — on_upstream_429()
  and select() both accept explicit injectable collaborators for exactly
  this reason.

• The fake catalog uses backoff_base_seconds=100 so on_429()'s scheduled
  call_later(100, ...) never fires during a short test window.  asyncio.run()
  creates a fresh event loop per test, so even if a stray call_later were
  somehow scheduled it would be dropped when the loop closes.

• keepalive_interval_seconds=0.1 is passed to on_upstream_429() to make
  the keep-alive mechanism observable in tests without a 15-second wall-clock
  wait.

Run with: pytest tests/integration/test_absorber.py -v --asyncio-mode=auto
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest

from clasp.api.detect import RequestType
from clasp.providers.base import BaseProvider, UpstreamRateLimitError
from clasp.providers.registry import ProviderRegistry
from clasp.queue.absorber import on_upstream_429
from clasp.queue.manager import QueueManager
from clasp.ratelimit.bucket import TokenBucket
from clasp.ratelimit.circuit_breaker import CircuitBreaker
from clasp.ratelimit.cooldown import CooldownManager
from clasp.router.types import AnthropicRequest, ProviderEnableConfig, SelectorConfig

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fake catalog — provider_a / provider_b are not real catalog entries;
# backoff_base_seconds=100 ensures cooldown.on_429()'s call_later fires
# well after each test completes so there's no cross-test state leakage.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _FakeProfile:
    display_name: str
    backoff_base_seconds: int = 100
    cooldown_seconds: int = 100


FAKE_CATALOG: dict[str, _FakeProfile] = {
    "provider_a": _FakeProfile(display_name="Provider A"),
    "provider_b": _FakeProfile(display_name="Provider B"),
}


# ---------------------------------------------------------------------------
# Fake provider implementations
# ---------------------------------------------------------------------------

class HealthyProvider(BaseProvider):
    """Streams a fixed list of pre-built SSE chunks without error."""

    def __init__(self, name: str, response_chunks: list[str]) -> None:
        self.provider_name = name
        self._chunks = response_chunks
        self.call_count = 0

    async def _stream_raw(
        self, request: AnthropicRequest, key: str, key_index: int
    ) -> AsyncGenerator[str, None]:
        self.call_count += 1
        for chunk in self._chunks:
            yield chunk


class RateLimitedProvider(BaseProvider):
    """Always raises UpstreamRateLimitError — every call is a 429."""

    def __init__(self, name: str) -> None:
        self.provider_name = name
        self.call_count = 0

    async def _stream_raw(
        self, request: AnthropicRequest, key: str, key_index: int
    ) -> AsyncGenerator[str, None]:
        self.call_count += 1
        raise UpstreamRateLimitError(retry_after=None)
        yield  # Never reached; makes this function an async generator


# ---------------------------------------------------------------------------
# SSE chunk helpers — keep tests readable
# ---------------------------------------------------------------------------

_MSG_STOP_CHUNK = 'event: message_stop\ndata: {"type":"message_stop"}\n\n'

def _text_chunk(text: str) -> str:
    """Build a minimal text-content-block SSE chunk."""
    data = json.dumps({"type": "content_block_delta",
                       "delta": {"type": "text_delta", "text": text}})
    return f"event: content_block_delta\ndata: {data}\n\n"


def _is_error_event(chunk: str) -> bool:
    return "overloaded_error" in chunk

def _is_keepalive(chunk: str) -> bool:
    return chunk.startswith(": keep-alive")

def _is_response_chunk(chunk: str) -> bool:
    return not _is_keepalive(chunk) and not _is_error_event(chunk)


# ---------------------------------------------------------------------------
# Test fixture builders
# ---------------------------------------------------------------------------

def _make_request() -> AnthropicRequest:
    body = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hello"}],
    }
    return AnthropicRequest(
        body=body,
        type=RequestType.INTERACTIVE,
        priority=0,
        estimated_tokens=10,
    )


def _make_registry(
    providers: dict[str, tuple[BaseProvider, list[str]]],
) -> ProviderRegistry:
    registry = ProviderRegistry()
    for name, (provider, keys) in providers.items():
        profile = FAKE_CATALOG[name]
        bucket = TokenBucket(rpm_limit=1000, tpm_limit=None, soft_threshold=1.0)
        cb = CircuitBreaker(profile)
        registry.register(
            name,
            provider=provider,
            bucket=bucket,
            circuit_breaker=cb,
            keys=keys,
        )
    return registry


def _make_config(
    chain: list[str],
    *,
    max_wait: float = 30.0,
) -> SelectorConfig:
    return SelectorConfig(
        provider_chain=chain,
        providers={n: ProviderEnableConfig(enabled=True) for n in chain},
        max_queue_wait_seconds=max_wait,
    )


async def _collect(
    agen: AsyncIterator[str],
    timeout_s: float = 10.0,
) -> list[str]:
    """Drain an async generator into a list, with a hard test-level timeout."""
    chunks: list[str] = []

    async def _drain() -> None:
        async for chunk in agen:
            chunks.append(chunk)

    await asyncio.wait_for(_drain(), timeout=timeout_s)
    return chunks


# ---------------------------------------------------------------------------
# Test 1 — Immediate failover on 429
# ---------------------------------------------------------------------------

async def test_immediate_failover_on_429():
    """
    Primary 429s → absorber excludes primary → secondary is healthy →
    response comes from secondary.  No error events, no keep-alive events
    (the request never touches the priority queue).
    """
    provider_b_chunks = [
        _text_chunk("Hello from provider_b!"),
        _msg_stop_chunk := _MSG_STOP_CHUNK,
    ]

    provider_a = RateLimitedProvider("provider_a")
    provider_b = HealthyProvider("provider_b", provider_b_chunks)

    registry = _make_registry({
        "provider_a": (provider_a, ["key_a_0"]),
        "provider_b": (provider_b, ["key_b_0"]),
    })
    cooldown_mgr = CooldownManager(catalog=FAKE_CATALOG)
    queue_mgr = QueueManager(max_wait_seconds=30.0)
    config = _make_config(["provider_a", "provider_b"])
    request = _make_request()

    # provider_a has just returned a 429; absorber is invoked.
    chunks = await _collect(
        on_upstream_429(
            request=request,
            failed_provider="provider_a",
            failed_key_index=0,
            retry_after_header=None,
            config=config,
            registry=registry,
            cooldown_mgr=cooldown_mgr,
            queue_mgr=queue_mgr,
            keepalive_interval_seconds=0.1,
        )
    )

    # ── Assertions ─────────────────────────────────────────────────────────
    assert chunks, "expected at least one chunk from provider_b"

    # No 429 / overloaded_error should ever reach the client.
    error_chunks = [c for c in chunks if _is_error_event(c)]
    assert not error_chunks, f"unexpected error events: {error_chunks}"

    # No keep-alive comments — failover was immediate, queue was never used.
    keepalive_chunks = [c for c in chunks if _is_keepalive(c)]
    assert not keepalive_chunks, f"unexpected keep-alive events: {keepalive_chunks}"

    # The content should match exactly what HealthyProvider("provider_b", …) yields.
    response_chunks = [c for c in chunks if _is_response_chunk(c)]
    assert response_chunks == provider_b_chunks, (
        f"response content mismatch:\ngot:      {response_chunks}\n"
        f"expected: {provider_b_chunks}"
    )

    # Sanity: provider_a itself was never called for streaming (we only reported
    # its 429 to absorber — the actual UpstreamRateLimitError came from its
    # _stream_raw, which caller simulated by invoking on_upstream_429 directly).
    assert provider_b.call_count == 1, (
        f"expected provider_b.call_count==1, got {provider_b.call_count}"
    )

    # provider_a is now cooling (recorded via cooldown_mgr.on_429()).
    assert cooldown_mgr.is_cooling("provider_a", 0), (
        "provider_a should be marked as cooling after its 429"
    )


# ---------------------------------------------------------------------------
# Test 2 — Queue-and-hold when all providers are cooling
# ---------------------------------------------------------------------------

async def test_queue_and_hold_when_all_cooling():
    """
    Both providers cooling → request is queued → keep-alive SSE comments are
    emitted while waiting → provider_b recovers after 2 s → drain_task
    dispatches → response eventually arrives, no error event.
    """
    provider_b_chunks = [
        _text_chunk("Delayed response from provider_b"),
        _MSG_STOP_CHUNK,
    ]
    provider_b = HealthyProvider("provider_b", provider_b_chunks)
    # provider_a failed with a 429 before this call; provider_b is cooling too.
    provider_a = RateLimitedProvider("provider_a")

    registry = _make_registry({
        "provider_a": (provider_a, ["key_a_0"]),
        "provider_b": (provider_b, ["key_b_0"]),
    })
    cooldown_mgr = CooldownManager(catalog=FAKE_CATALOG)
    queue_mgr = QueueManager(max_wait_seconds=30.0)
    config = _make_config(["provider_a", "provider_b"], max_wait=30.0)
    request = _make_request()

    # Pre-mark provider_b as cooling (its own upstream 429 happened earlier).
    # Use a 200 s duration so it stays cooling for the duration of the test.
    cooldown_mgr._set_cooling("provider_b", 0, time.monotonic() + 200.0)
    # provider_a will be marked cooling by on_upstream_429() → cooldown_mgr.on_429().

    # Start the drain task.
    drain = asyncio.create_task(
        queue_mgr.drain_task(
            config=config,
            registry=registry,
            cooldown_mgr=cooldown_mgr,
        )
    )

    # Concurrent task: re-enable provider_b after 2 s.
    async def _re_enable_b() -> None:
        await asyncio.sleep(2.0)
        cooldown_mgr._re_enable("provider_b", 0)

    re_enable_task = asyncio.create_task(_re_enable_b())

    try:
        t0 = time.monotonic()
        chunks = await _collect(
            on_upstream_429(
                request=request,
                failed_provider="provider_a",
                failed_key_index=0,
                retry_after_header=None,
                config=config,
                registry=registry,
                cooldown_mgr=cooldown_mgr,
                queue_mgr=queue_mgr,
                keepalive_interval_seconds=0.1,
            ),
            timeout_s=10.0,
        )
        elapsed = time.monotonic() - t0
    finally:
        drain.cancel()
        re_enable_task.cancel()
        # Suppress CancelledError from awaiting the cancelled tasks.
        await asyncio.gather(drain, re_enable_task, return_exceptions=True)

    # ── Assertions ─────────────────────────────────────────────────────────

    # No error events — the request resolved successfully.
    error_chunks = [c for c in chunks if _is_error_event(c)]
    assert not error_chunks, f"unexpected error events: {error_chunks}"

    # At least one keep-alive comment should have been emitted while waiting.
    keepalive_chunks = [c for c in chunks if _is_keepalive(c)]
    assert keepalive_chunks, (
        "expected at least one SSE keep-alive comment while waiting for "
        "provider_b to recover, but got none"
    )

    # The actual response chunks should match provider_b's output.
    response_chunks = [c for c in chunks if _is_response_chunk(c)]
    assert response_chunks == provider_b_chunks, (
        f"response content mismatch:\ngot:      {response_chunks}\n"
        f"expected: {provider_b_chunks}"
    )

    # Elapsed time should be ≥2 s (provider_b recovers after 2 s) and
    # well under the 10 s collection timeout.
    assert elapsed >= 1.8, f"resolved too fast ({elapsed:.2f}s) — provider_b should take ~2s"
    assert elapsed < 8.0,  f"resolved too slowly ({elapsed:.2f}s) — something is stuck"


# ---------------------------------------------------------------------------
# Test 3 — Queue timeout: error event arrives at the configured deadline
# ---------------------------------------------------------------------------

async def test_queue_timeout():
    """
    All providers cooling for effectively forever; max_queue_wait_seconds=3 →
    hold_until_resolved()'s own per-keepalive-interval check fires after 3 s
    → a single Anthropic overloaded_error event is yielded;
    at least one keep-alive comment was emitted before that.
    """
    provider_a = RateLimitedProvider("provider_a")
    provider_b = RateLimitedProvider("provider_b")

    registry = _make_registry({
        "provider_a": (provider_a, ["key_a_0"]),
        "provider_b": (provider_b, ["key_b_0"]),
    })
    cooldown_mgr = CooldownManager(catalog=FAKE_CATALOG)
    queue_mgr = QueueManager(max_wait_seconds=3.0)
    config = _make_config(["provider_a", "provider_b"], max_wait=3.0)
    request = _make_request()

    # Pre-mark both providers as cooling for 200 s (effectively forever).
    cooldown_mgr._set_cooling("provider_a", 0, time.monotonic() + 200.0)
    cooldown_mgr._set_cooling("provider_b", 0, time.monotonic() + 200.0)
    # on_upstream_429 will call on_429("provider_a", 0, None) which overwrites
    # provider_a's cooling entry, but with backoff_base_seconds=100 the new
    # cooldown is also effectively forever (100 * 2^0 = 100 s).

    # Start drain_task so it's attempting to dispatch (and repeatedly failing).
    drain = asyncio.create_task(
        queue_mgr.drain_task(
            config=config,
            registry=registry,
            cooldown_mgr=cooldown_mgr,
        )
    )

    try:
        t0 = time.monotonic()
        chunks = await _collect(
            on_upstream_429(
                request=request,
                failed_provider="provider_a",
                failed_key_index=0,
                retry_after_header=None,
                config=config,
                registry=registry,
                cooldown_mgr=cooldown_mgr,
                queue_mgr=queue_mgr,
                keepalive_interval_seconds=0.1,   # fast polling for test
            ),
            timeout_s=10.0,
        )
        elapsed = time.monotonic() - t0
    finally:
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)

    # ── Assertions ─────────────────────────────────────────────────────────

    # Exactly one error event — the overloaded_error at timeout.
    error_chunks = [c for c in chunks if _is_error_event(c)]
    assert len(error_chunks) == 1, (
        f"expected exactly 1 error event, got {len(error_chunks)}: {error_chunks}"
    )
    assert "overloaded_error" in error_chunks[0], error_chunks[0]

    # No real response chunks.
    response_chunks = [c for c in chunks if _is_response_chunk(c)]
    assert not response_chunks, (
        f"expected no response chunks on timeout, got: {response_chunks}"
    )

    # At least one keep-alive comment — the connection was held open.
    keepalive_chunks = [c for c in chunks if _is_keepalive(c)]
    assert keepalive_chunks, (
        "expected at least one SSE keep-alive comment before the timeout "
        "error event, but got none"
    )

    # The error should arrive around the 3 s mark (generous ±3 s window).
    assert elapsed >= 2.5, (
        f"error arrived too early ({elapsed:.2f}s); "
        "absorber should have kept the connection alive for ~3s before giving up"
    )
    assert elapsed < 6.0, (
        f"error arrived too late ({elapsed:.2f}s); "
        "absorber should have given up around the 3s max_queue_wait mark"
    )