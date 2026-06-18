"""
tests/integration/test_absorber.py
Integration tests for clasp/queue/absorber.py.

Run with:
    python3 -m unittest tests/integration/test_absorber.py -v

No external dependencies (pytest, httpx, etc.) — pure stdlib asyncio +
unittest.IsolatedAsyncioTestCase.

Tests
-----
1. test_immediate_failover_on_429
   Primary 429s → secondary healthy → response from secondary, no error event.

2. test_queue_and_hold_when_all_cooling
   All providers cooling → request queued → keep-alive events emitted →
   provider recovers after 2 s → response arrives with no error event.

3. test_queue_timeout
   All providers cooling for 200 s with max_queue_wait_seconds=3 →
   Anthropic error event arrives around the 3 s mark.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest
from typing import Any, AsyncGenerator, Optional
from unittest.mock import AsyncMock

# Ensure the repo root is on sys.path so we can import clasp.*
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.queue.absorber import on_upstream_429
from clasp.queue.manager import QueueManager, QueuedRequest
from clasp.ratelimit.cooldown import CooldownStore
from clasp.ratelimit.circuit_breaker import CircuitBreakerStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_events(chunks: list[bytes]) -> list[dict]:
    """
    Parse a list of SSE byte chunks into a list of data dicts.
    Returns only 'data:' lines that contain valid JSON.
    """
    events = []
    for chunk in chunks:
        text = chunk.decode(errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return events


def _decode_raw(chunks: list[bytes]) -> str:
    """Join all chunks to raw text for keep-alive inspection."""
    return b"".join(chunks).decode(errors="replace")


def _has_error_event(events: list[dict]) -> bool:
    return any(e.get("type") == "error" for e in events)


def _has_text_content(events: list[dict]) -> bool:
    return any(
        e.get("type") == "content_block_delta"
        and e.get("delta", {}).get("type") == "text_delta"
        for e in events
    )


# ---------------------------------------------------------------------------
# Fake provider that yields canned Anthropic SSE chunks
# ---------------------------------------------------------------------------

_SECONDARY_CHUNKS = [
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg_sec","type":"message","role":"assistant","content":[],"model":"failover-model","stop_reason":null,"usage":{"input_tokens":10,"output_tokens":0}}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello from secondary."}}\n\n',
    b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":4}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
]


class FakeProvider:
    """A provider that always yields _SECONDARY_CHUNKS successfully."""

    name = "fake_secondary"

    async def stream(self, request, *, key, key_index=0, **kwargs):
        for chunk in _SECONDARY_CHUNKS:
            yield chunk


async def _collect(gen: AsyncGenerator) -> list[bytes]:
    """Collect all bytes from an async generator."""
    chunks = []
    async for chunk in gen:
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Test 1 — Immediate failover on 429
# ---------------------------------------------------------------------------

class TestImmediateFailoverOn429(unittest.IsolatedAsyncioTestCase):
    """
    Primary provider returns 429.
    Secondary provider is healthy.
    Expected: response arrives from secondary; no error event; no 429 visible.
    """

    async def test_immediate_failover_on_429(self):
        cd = CooldownStore()
        cb = CircuitBreakerStore()

        secondary = FakeProvider()

        # selector: exclude={"primary"} → returns secondary; exclude=None → returns primary (irrelevant here)
        async def selector_fn(request, exclude=None, **kwargs):
            exclude = exclude or set()
            if "primary" not in exclude:
                return None  # would return primary but we're after failover
            return (secondary, "secondary-key", 0)

        request = {"model": "claude-3-5-sonnet-20241022", "messages": []}

        chunks = await _collect(on_upstream_429(
            request,
            failed_provider="primary",
            failed_key_index=0,
            retry_after_header="30",
            cooldown_store=cd,
            cb_store=cb,
            selector_fn=selector_fn,
            queue_mgr=None,  # Should not reach queue
            max_queue_wait_seconds=5.0,
            keepalive_interval=1.0,
        ))

        # 1. Cooldown was set for primary.
        self.assertTrue(
            cd.is_cooling("primary", 0),
            "Primary provider should be in cooldown after 429",
        )

        # 2. Chunks are non-empty.
        self.assertTrue(chunks, "Expected response chunks from failover provider")

        # 3. Events contain content from secondary.
        events = _decode_events(chunks)
        raw = _decode_raw(chunks)

        # 4. No error event.
        self.assertFalse(
            _has_error_event(events),
            f"Unexpected error event in response: {events}",
        )

        # 5. Has text content from secondary.
        self.assertTrue(
            _has_text_content(events),
            f"Expected text_delta content from secondary provider. Got: {events}",
        )

        # 6. No keep-alive lines (failover should be immediate).
        self.assertNotIn(
            "keep-alive",
            raw,
            "No keep-alive should appear on immediate failover",
        )

        # 7. No 429-related fields in any event.
        for event in events:
            self.assertNotEqual(
                event.get("type"),
                "error",
                f"Got error event: {event}",
            )

    async def test_cooldown_state_updated_for_failed_provider(self):
        """Cooldown and circuit breaker are updated even if failover succeeds."""
        cd = CooldownStore()
        cd.set_backoff_base("primary", 60.0)
        cb = CircuitBreakerStore()
        secondary = FakeProvider()

        async def selector_fn(request, exclude=None, **kwargs):
            if exclude and "primary" in exclude:
                return (secondary, "k", 0)
            return None

        await _collect(on_upstream_429(
            {"messages": []},
            failed_provider="primary",
            failed_key_index=1,
            retry_after_header=None,
            cooldown_store=cd,
            cb_store=cb,
            selector_fn=selector_fn,
        ))

        self.assertTrue(cd.is_cooling("primary", 1))
        # CB should have recorded a 429 for primary key 1.
        breaker = cb.get("primary", 1)
        # One 429 recorded — not yet tripped (threshold is 3).
        from clasp.ratelimit.circuit_breaker import CBState
        self.assertEqual(breaker.state, CBState.CLOSED)


# ---------------------------------------------------------------------------
# Test 2 — Queue and hold when all cooling, then recover
# ---------------------------------------------------------------------------

class TestQueueAndHoldWhenAllCooling(unittest.IsolatedAsyncioTestCase):
    """
    All providers cooling → request queued → keep-alive events emitted →
    provider recovers after ~2 s → response eventually arrives.
    """

    async def test_queue_and_hold_when_all_cooling(self):
        cd = CooldownStore()
        cb = CircuitBreakerStore()

        secondary = FakeProvider()
        healthy_at = asyncio.get_event_loop().time() + 2.0  # recover in 2 s

        call_count = {"n": 0}

        async def selector_fn(request, exclude=None, **kwargs):
            call_count["n"] += 1
            now = asyncio.get_event_loop().time()
            if now >= healthy_at:
                return (secondary, "secondary-key", 0)
            return None  # all cooling

        request = {"model": "claude-3-5-sonnet-20241022", "messages": [], "priority": 0}

        mgr = QueueManager(
            selector_fn=selector_fn,
            max_wait_seconds=15.0,
            drain_poll_interval=0.3,  # fast polling for test
        )

        # Start drain task as a background task.
        drain = asyncio.create_task(mgr.drain_task(), name="test_drain")

        start = time.monotonic()
        chunks = await asyncio.wait_for(
            _collect(on_upstream_429(
                request,
                failed_provider="primary",
                failed_key_index=0,
                retry_after_header=None,
                cooldown_store=cd,
                cb_store=cb,
                selector_fn=selector_fn,
                queue_mgr=mgr,
                max_queue_wait_seconds=15.0,
                keepalive_interval=0.5,   # fast keep-alive for test
            )),
            timeout=10.0,   # overall test guard
        )
        elapsed = time.monotonic() - start

        drain.cancel()
        try:
            await drain
        except asyncio.CancelledError:
            pass

        # 1. Took at least ~2 s (recovery delay).
        self.assertGreaterEqual(
            elapsed,
            1.5,
            f"Expected ~2s wait, got {elapsed:.2f}s",
        )

        # 2. Chunks are non-empty.
        self.assertTrue(chunks, "Expected response chunks after queue recovery")

        # 3. Keep-alive events were emitted (at least one in ~2 s with 0.5 s interval).
        raw = _decode_raw(chunks)
        self.assertIn(
            "keep-alive",
            raw,
            "Expected at least one keep-alive comment while queued",
        )

        # 4. No error event.
        events = _decode_events(chunks)
        self.assertFalse(
            _has_error_event(events),
            f"Unexpected error event after recovery: {events}",
        )

        # 5. Has text content from secondary.
        self.assertTrue(
            _has_text_content(events),
            f"Expected text_delta content after recovery. Got: {events}",
        )

        # 6. Selector was called multiple times (evidence of polling).
        self.assertGreater(
            call_count["n"],
            2,
            f"Expected drain task to poll selector multiple times, got {call_count['n']}",
        )


# ---------------------------------------------------------------------------
# Test 3 — Queue timeout
# ---------------------------------------------------------------------------

class TestQueueTimeout(unittest.IsolatedAsyncioTestCase):
    """
    All providers cooling for 200 s with max_queue_wait_seconds=3.
    Expected: Anthropic error event arrives around the 3 s mark.
    """

    async def test_queue_timeout(self):
        cd = CooldownStore()
        cb = CircuitBreakerStore()

        # Selector always returns None (all cooling forever).
        async def selector_fn(request, exclude=None, **kwargs):
            return None

        request = {"model": "claude-3-5-sonnet-20241022", "messages": []}

        mgr = QueueManager(
            selector_fn=selector_fn,
            max_wait_seconds=3.0,
            drain_poll_interval=0.5,
        )
        drain = asyncio.create_task(mgr.drain_task(), name="test_drain_timeout")

        start = time.monotonic()
        chunks = await asyncio.wait_for(
            _collect(on_upstream_429(
                request,
                failed_provider="primary",
                failed_key_index=0,
                retry_after_header="200",  # tells system to wait 200 s
                cooldown_store=cd,
                cb_store=cb,
                selector_fn=selector_fn,
                queue_mgr=mgr,
                max_queue_wait_seconds=3.0,
                keepalive_interval=0.5,
            )),
            timeout=10.0,   # overall test guard
        )
        elapsed = time.monotonic() - start

        drain.cancel()
        try:
            await drain
        except asyncio.CancelledError:
            pass

        # 1. Completed around 3 s (allow ±2 s window).
        self.assertGreaterEqual(
            elapsed,
            2.0,
            f"Expected ~3s before timeout, completed too fast: {elapsed:.2f}s",
        )
        self.assertLessEqual(
            elapsed,
            8.0,
            f"Timeout took too long: {elapsed:.2f}s",
        )

        # 2. An error event is present.
        events = _decode_events(chunks)
        self.assertTrue(
            _has_error_event(events),
            f"Expected error event on timeout. Got events: {events}\nRaw: {_decode_raw(chunks)!r}",
        )

        # 3. The error type is overloaded_error.
        error_events = [e for e in events if e.get("type") == "error"]
        self.assertTrue(error_events, "No error events found")
        err = error_events[0]
        self.assertEqual(
            err.get("error", {}).get("type"),
            "overloaded_error",
            f"Unexpected error type: {err}",
        )

        # 4. Keep-alive events were emitted before timeout.
        raw = _decode_raw(chunks)
        self.assertIn(
            "keep-alive",
            raw,
            "Expected keep-alive comments before timeout error",
        )

        # 5. No text content (no provider responded).
        self.assertFalse(
            _has_text_content(events),
            f"Did not expect text content on timeout: {events}",
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)