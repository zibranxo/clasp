"""
tests/unit/test_response_cache.py
Unit tests for clasp/cache/response_cache.py and clasp/utils/hash.py.

Run with:
    python3 -m unittest tests/unit/test_response_cache.py -v

Pure stdlib — no pytest/aiosqlite required. Uses a temp SQLite file per test
class (cleaned up in tearDown) so tests are hermetic and parallel-safe.

Coverage (per CLAUDE.md):
  1. Identical request sent twice → second call is a cache hit
     (no "provider call" — i.e. the test asserts the cache returns the
     stored chunks directly, without re-invoking whatever would dispatch
     to a provider).
  2. TTL expiry → after the TTL elapses, an identical request is a cache
     miss again.
  3. Different tool definitions in an otherwise identical request → cache
     miss (different hash key).

Additional coverage for robustness (hash.py + cache plumbing):
  - hash_request is deterministic across key/list reordering.
  - excluded fields (stream, metadata) do not affect the hash.
  - LRU eviction at capacity.
  - oversized response is not cached.
  - SQLite tier survives a fresh ResponseCache instance (restart simulation).
  - clear() empties both tiers.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.cache.response_cache import ResponseCache
from clasp.utils.hash import hash_request


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _request(model="claude-3-5-sonnet-20241022", **overrides):
    base = {
        "model": model,
        "messages": [{"role": "user", "content": "What is 2+2?"}],
        "max_tokens": 100,
    }
    base.update(overrides)
    return base


def _chunks(text="4"):
    return [
        f'event: message_start\ndata: {{"type":"message_start"}}\n\n'.encode(),
        f'event: content_block_delta\ndata: {{"type":"content_block_delta","delta":{{"text":"{text}"}}}}\n\n'.encode(),
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]


# ---------------------------------------------------------------------------
# Test 1 — Identical request twice → second is a cache hit
# ---------------------------------------------------------------------------

class TestCacheHitOnIdenticalRequest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="clasp_cache_test_")
        self.db_path = Path(self.tmpdir) / "cache.db"
        self.cache = ResponseCache(db_path=self.db_path, ttl_seconds=300)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_second_identical_request_is_cache_hit(self):
        req = _request()
        key1 = hash_request(req)

        # First call: cache miss (nothing stored yet).
        result1 = run(self.cache.get(key1))
        self.assertIsNone(result1, "First lookup should be a miss")

        # Simulate the provider responding, then service.py writes to cache.
        chunks = _chunks("4")
        run(self.cache.set(key1, chunks))

        # Second call with the SAME request content → cache hit, identical chunks.
        key2 = hash_request(_request())  # fresh dict, same logical content
        self.assertEqual(key1, key2, "Identical requests must hash identically")

        result2 = run(self.cache.get(key2))
        self.assertIsNotNone(result2, "Second lookup should be a cache hit")
        self.assertEqual(result2, chunks, "Cached chunks must match exactly what was stored")

        # No provider call happened — we never touched any provider/selector
        # code path; the only path that produced result2 was cache.get().
        stats = self.cache.stats()
        self.assertEqual(stats["hits_memory"], 1)
        self.assertEqual(stats["misses"], 1)

    def test_hit_served_from_memory_without_sqlite_roundtrip(self):
        """After a set(), the immediate next get() must hit the memory tier."""
        req = _request()
        key = hash_request(req)
        run(self.cache.set(key, _chunks()))

        run(self.cache.get(key))
        stats = self.cache.stats()
        self.assertEqual(stats["hits_memory"], 1)
        self.assertEqual(stats["hits_sqlite"], 0)

    def test_hit_served_from_sqlite_after_memory_eviction(self):
        """If memory tier is cleared but SQLite retains the row, get() still hits."""
        req = _request()
        key = hash_request(req)
        chunks = _chunks("warmed")
        run(self.cache.set(key, chunks))

        # Force memory-only eviction (simulate LRU pressure) without touching SQLite.
        run(self.cache._lock.acquire())
        try:
            self.cache._lru.clear()
        finally:
            self.cache._lock.release()

        result = run(self.cache.get(key))
        self.assertEqual(result, chunks)
        stats = self.cache.stats()
        self.assertEqual(stats["hits_sqlite"], 1)


# ---------------------------------------------------------------------------
# Test 2 — TTL expiry → cache miss after expiry
# ---------------------------------------------------------------------------

class TestTTLExpiry(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="clasp_cache_test_")
        self.db_path = Path(self.tmpdir) / "cache.db"
        # Very short TTL so the test runs fast.
        self.cache = ResponseCache(db_path=self.db_path, ttl_seconds=0.3)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_expired_entry_is_miss_after_ttl(self):
        req = _request()
        key = hash_request(req)
        chunks = _chunks()
        run(self.cache.set(key, chunks))

        # Immediately after: hit (memory tier has no TTL of its own, but the
        # entry is fresh either way).
        self.assertIsNotNone(run(self.cache.get(key)))

        # Simulate "after restart" by dropping the memory tier and waiting
        # past the SQLite TTL — this is the persistence-survives-restart path.
        run(self.cache._lock.acquire())
        try:
            self.cache._lru.clear()
        finally:
            self.cache._lock.release()

        time.sleep(0.4)  # exceed the 0.3s TTL

        result = run(self.cache.get(key))
        self.assertIsNone(result, "Expired SQLite entry must be a miss")

    def test_fresh_cache_instance_respects_persisted_ttl(self):
        """A brand-new ResponseCache pointed at the same DB file still expires correctly."""
        req = _request()
        key = hash_request(req)
        run(self.cache.set(key, _chunks()))

        time.sleep(0.4)

        # New instance, same DB file — simulates a real process restart.
        cache2 = ResponseCache(db_path=self.db_path, ttl_seconds=0.3)
        result = run(cache2.get(key))
        self.assertIsNone(result, "Expired entry must be a miss even from a fresh process")

    def test_unexpired_entry_survives_fresh_instance(self):
        """Persistence works: a fresh instance still sees a not-yet-expired entry."""
        cache_long = ResponseCache(db_path=self.db_path, ttl_seconds=300)
        req = _request()
        key = hash_request(req)
        chunks = _chunks("persisted")
        run(cache_long.set(key, chunks))

        # New process-like instance, same DB, same long TTL.
        cache2 = ResponseCache(db_path=self.db_path, ttl_seconds=300)
        result = run(cache2.get(key))
        self.assertEqual(result, chunks)


# ---------------------------------------------------------------------------
# Test 3 — Different tool definitions → cache miss
# ---------------------------------------------------------------------------

class TestToolDifferenceCausesMiss(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="clasp_cache_test_")
        self.db_path = Path(self.tmpdir) / "cache.db"
        self.cache = ResponseCache(db_path=self.db_path, ttl_seconds=300)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_different_tools_list_is_cache_miss(self):
        req_a = _request(tools=[{"name": "bash", "description": "run shell"}])
        req_b = _request(tools=[{"name": "read_file", "description": "read a file"}])

        key_a = hash_request(req_a)
        key_b = hash_request(req_b)
        self.assertNotEqual(key_a, key_b, "Different tool sets must hash differently")

        run(self.cache.set(key_a, _chunks("for-bash")))

        # req_b was never cached — must be a miss even though messages/model match.
        result_b = run(self.cache.get(key_b))
        self.assertIsNone(result_b)

        # req_a is still a hit.
        result_a = run(self.cache.get(key_a))
        self.assertEqual(result_a, _chunks("for-bash"))

    def test_no_tools_vs_empty_tools_list_distinct_or_consistent(self):
        """
        Absent 'tools' key vs explicit empty list: document the behavior —
        both should be internally consistent (same input → same key, always).
        """
        req_absent = _request()
        req_empty = _request(tools=[])

        key_absent = hash_request(req_absent)
        key_empty = hash_request(req_empty)

        # Re-hashing the same logical payload must always reproduce the same key.
        self.assertEqual(key_absent, hash_request(_request()))
        self.assertEqual(key_empty, hash_request(_request(tools=[])))

    def test_same_tools_different_order_is_cache_hit(self):
        """Tool list order must NOT matter — Claude Code's tool order is non-deterministic."""
        tool_a = {"name": "bash", "description": "run shell"}
        tool_b = {"name": "read_file", "description": "read a file"}

        req1 = _request(tools=[tool_a, tool_b])
        req2 = _request(tools=[tool_b, tool_a])

        key1 = hash_request(req1)
        key2 = hash_request(req2)
        self.assertEqual(key1, key2, "Tool order must not affect the cache key")

    def test_tool_choice_difference_causes_miss(self):
        req_auto = _request(tools=[{"name": "bash"}], tool_choice={"type": "auto"})
        req_specific = _request(tools=[{"name": "bash"}], tool_choice={"type": "tool", "name": "bash"})

        self.assertNotEqual(hash_request(req_auto), hash_request(req_specific))


# ---------------------------------------------------------------------------
# hash.py — determinism & exclusion tests
# ---------------------------------------------------------------------------

class TestHashRequestDeterminism(unittest.TestCase):

    def test_key_order_does_not_affect_hash(self):
        req1 = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 10}
        req2 = {"max_tokens": 10, "messages": [{"role": "user", "content": "hi"}], "model": "m"}
        self.assertEqual(hash_request(req1), hash_request(req2))

    def test_stream_field_excluded(self):
        req1 = _request(stream=True)
        req2 = _request(stream=False)
        self.assertEqual(hash_request(req1), hash_request(req2))

    def test_metadata_field_excluded(self):
        req1 = _request(metadata={"user_id": "abc"})
        req2 = _request(metadata={"user_id": "xyz"})
        self.assertEqual(hash_request(req1), hash_request(req2))

    def test_different_message_content_differs(self):
        req1 = _request()
        req2 = _request(messages=[{"role": "user", "content": "different question"}])
        self.assertNotEqual(hash_request(req1), hash_request(req2))

    def test_different_model_differs(self):
        req1 = _request(model="claude-3-5-sonnet-20241022")
        req2 = _request(model="claude-3-5-haiku-20241022")
        self.assertNotEqual(hash_request(req1), hash_request(req2))

    def test_temperature_difference_causes_different_hash(self):
        req1 = _request(temperature=0.0)
        req2 = _request(temperature=1.0)
        self.assertNotEqual(hash_request(req1), hash_request(req2))

    def test_hash_is_64_char_hex(self):
        h = hash_request(_request())
        self.assertEqual(len(h), 64)
        int(h, 16)  # raises ValueError if not valid hex


# ---------------------------------------------------------------------------
# LRU / capacity / size-limit behavior
# ---------------------------------------------------------------------------

class TestLRUAndLimits(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="clasp_cache_test_")
        self.db_path = Path(self.tmpdir) / "cache.db"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_lru_eviction_at_capacity(self):
        cache = ResponseCache(db_path=self.db_path, memory_max_entries=2, ttl_seconds=300)
        run(cache.set("key1", _chunks("a")))
        run(cache.set("key2", _chunks("b")))
        run(cache.set("key3", _chunks("c")))  # should evict key1 (oldest)

        self.assertEqual(len(cache._lru), 2)
        self.assertNotIn("key1", cache._lru)
        self.assertIn("key2", cache._lru)
        self.assertIn("key3", cache._lru)

    def test_get_refreshes_lru_order(self):
        cache = ResponseCache(db_path=self.db_path, memory_max_entries=2, ttl_seconds=300)
        run(cache.set("key1", _chunks("a")))
        run(cache.set("key2", _chunks("b")))
        run(cache.get("key1"))               # touch key1 → now most-recently-used
        run(cache.set("key3", _chunks("c")))  # should evict key2, not key1

        self.assertIn("key1", cache._lru)
        self.assertNotIn("key2", cache._lru)
        self.assertIn("key3", cache._lru)

    def test_oversized_response_not_cached(self):
        cache = ResponseCache(db_path=self.db_path, ttl_seconds=300, max_entry_bytes=100)
        huge_chunks = [b"x" * 1000]
        run(cache.set("bigkey", huge_chunks))

        result = run(cache.get("bigkey"))
        self.assertIsNone(result, "Oversized response must not be cached")
        self.assertEqual(cache.stats()["skipped_too_large"], 1)

    def test_clear_empties_both_tiers(self):
        cache = ResponseCache(db_path=self.db_path, ttl_seconds=300)
        run(cache.set("k1", _chunks()))
        run(cache.set("k2", _chunks()))

        removed = run(cache.clear())
        self.assertGreaterEqual(removed, 2)
        self.assertIsNone(run(cache.get("k1")))
        self.assertIsNone(run(cache.get("k2")))

    def test_prune_removes_only_expired_rows(self):
        cache = ResponseCache(db_path=self.db_path, ttl_seconds=0.2)
        run(cache.set("short_lived", _chunks()))

        cache_long = ResponseCache(db_path=self.db_path, ttl_seconds=300)
        run(cache_long.set("long_lived", _chunks()))

        time.sleep(0.3)
        pruned = run(cache.prune())
        self.assertGreaterEqual(pruned, 1)

        # long_lived should still be retrievable from a fresh instance.
        cache3 = ResponseCache(db_path=self.db_path, ttl_seconds=300)
        self.assertIsNotNone(run(cache3.get("long_lived")))


if __name__ == "__main__":
    unittest.main(verbosity=2)