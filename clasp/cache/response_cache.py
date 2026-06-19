"""
clasp/cache/response_cache.py
Two-tier response cache: in-memory LRU + persistent SQLite.

Plan §14 / §8 spec:

  Tier 1 — in-memory LRU (OrderedDict)
    • Capacity: ``memory_max_entries`` (default 500).
    • O(1) get/put via ``OrderedDict.move_to_end``.
    • Evicts the least-recently-used entry on overflow.
    • No TTL — entries live until evicted or the process restarts.
    • Protected by asyncio.Lock (safe for concurrent coroutines).

  Tier 2 — SQLite (stdlib sqlite3 via asyncio.to_thread)
    • Persists across proxy restarts.
    • TTL enforced per row: ``expires_at = time.time() + ttl_seconds``.
    • On SQLite hit: entry is warmed up into memory LRU.
    • Stale rows are lazily removed on every read and on ``clear()``.
    • Periodic vacuum pruning via ``prune()`` — call from a background task.

  Cache key: 64-char SHA-256 hex from ``clasp.utils.hash.hash_request``.

  Stored value: list[bytes] — the collected SSE chunks of a full response.
    Serialised as JSON: each chunk is base64-encoded, joined into a JSON array.
    Maximum stored blob size: ``max_entry_bytes`` (default 4 MB). Responses
    larger than this are not cached to avoid unbounded SQLite growth.

  Public API (called by service.py)
  ----------------------------------
  ``ResponseCache.get(key)  → list[bytes] | None``
  ``ResponseCache.set(key, chunks)  → None``
  ``ResponseCache.clear()   → int``   (returns number of entries removed)
  ``ResponseCache.prune()   → int``   (remove expired SQLite rows)
  ``ResponseCache.stats()   → dict``  (hit/miss counters, entry counts)

  Module-level singleton
  ----------------------
  ``init_cache(db_path, memory_max_entries, sqlite_ttl_seconds) → ResponseCache``
  ``get_cache() → ResponseCache``

No external dependencies — pure stdlib: sqlite3, asyncio, json, base64,
collections.OrderedDict.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

try:
    from loguru import logger as _logger
    _LOG = True
except ImportError:
    _LOG = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path.home() / ".clasp" / "cache.db"
_DEFAULT_MEMORY_MAX = 500
_DEFAULT_TTL = 300          # seconds (5 minutes)
_DEFAULT_MAX_BYTES = 4 * 1024 * 1024   # 4 MB per cached response

_SCHEMA = """
CREATE TABLE IF NOT EXISTS response_cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,      -- JSON array of base64-encoded chunks
    expires_at REAL NOT NULL,      -- unix timestamp
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expires ON response_cache (expires_at);
"""


# ---------------------------------------------------------------------------
# ResponseCache
# ---------------------------------------------------------------------------

class ResponseCache:
    """
    Two-tier response cache.

    Parameters
    ----------
    db_path:
        Path to the SQLite database file. Created automatically.
        Pass ``None`` to disable the SQLite tier (memory-only mode).
    memory_max_entries:
        Maximum number of entries in the in-memory LRU cache.
    ttl_seconds:
        Time-to-live for SQLite entries.  Memory entries have no TTL —
        they live until evicted by the LRU policy or until the process exits.
    max_entry_bytes:
        Skip caching a response if its total serialised size exceeds this.
    """

    def __init__(
        self,
        db_path: Optional[Path] = _DEFAULT_DB_PATH,
        memory_max_entries: int = _DEFAULT_MEMORY_MAX,
        ttl_seconds: float = _DEFAULT_TTL,
        max_entry_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        self._db_path = db_path
        self._max_entries = memory_max_entries
        self._ttl = ttl_seconds
        self._max_bytes = max_entry_bytes

        # Tier 1: LRU dict  key → list[bytes]
        self._lru: OrderedDict[str, list[bytes]] = OrderedDict()
        self._lock = asyncio.Lock()

        # Counters
        self._hits_memory = 0
        self._hits_sqlite = 0
        self._misses = 0
        self._writes = 0
        self._skipped_too_large = 0

        # Initialise DB synchronously at construction time so it's ready
        # before the event loop starts. (Also called from to_thread for
        # async callers that construct after the loop is running.)
        if db_path is not None:
            self._init_db_sync()

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Optional[list[bytes]]:
        """
        Look up *key*.

        Check memory LRU first (O(1)), then SQLite (I/O via to_thread).
        Returns the cached chunk list or ``None`` on miss/expiry.
        """
        # Tier 1: memory
        async with self._lock:
            if key in self._lru:
                self._lru.move_to_end(key)
                self._hits_memory += 1
                if _LOG:
                    _logger.debug("cache: memory hit", key=key[:12])
                return self._lru[key]

        # Tier 2: SQLite
        if self._db_path is None:
            self._misses += 1
            return None

        row = await asyncio.to_thread(self._sqlite_get, key)
        if row is None:
            self._misses += 1
            if _LOG:
                _logger.debug("cache: miss", key=key[:12])
            return None

        # Deserialise and warm up into memory LRU.
        chunks = _deserialise(row)
        async with self._lock:
            self._hits_sqlite += 1
            self._lru[key] = chunks
            self._lru.move_to_end(key)
            await self._maybe_evict()
        if _LOG:
            _logger.debug("cache: sqlite hit (warmed to memory)", key=key[:12])
        return chunks

    async def set(self, key: str, chunks: list[bytes]) -> None:
        """
        Store *chunks* under *key*.

        Writes to both the memory LRU and SQLite (unless the response exceeds
        ``max_entry_bytes``).
        """
        blob = _serialise(chunks)
        if len(blob) > self._max_bytes:
            self._skipped_too_large += 1
            if _LOG:
                _logger.debug(
                    "cache: response too large, not caching",
                    key=key[:12],
                    size=len(blob),
                    limit=self._max_bytes,
                )
            return

        # Write to memory LRU.
        async with self._lock:
            self._lru[key] = chunks
            self._lru.move_to_end(key)
            await self._maybe_evict()

        # Write to SQLite (background — don't block the response stream).
        if self._db_path is not None:
            expires_at = time.time() + self._ttl
            await asyncio.to_thread(self._sqlite_set, key, blob, expires_at)

        self._writes += 1
        if _LOG:
            _logger.debug(
                "cache: stored",
                key=key[:12],
                chunks=len(chunks),
                bytes=len(blob),
            )

    async def clear(self) -> int:
        """
        Evict all entries from both tiers.
        Returns the total number of entries removed.
        """
        async with self._lock:
            mem_count = len(self._lru)
            self._lru.clear()

        db_count = 0
        if self._db_path is not None:
            db_count = await asyncio.to_thread(self._sqlite_clear)

        total = mem_count + db_count
        if _LOG:
            _logger.info("cache: cleared", memory=mem_count, sqlite=db_count)
        return total

    async def prune(self) -> int:
        """
        Delete expired SQLite rows. Returns number of rows deleted.
        Call periodically from a background task (e.g. every 60 s).
        """
        if self._db_path is None:
            return 0
        count = await asyncio.to_thread(self._sqlite_prune)
        if _LOG and count:
            _logger.debug("cache: pruned expired rows", count=count)
        return count

    def stats(self) -> dict:
        return {
            "memory_entries": len(self._lru),
            "memory_max": self._max_entries,
            "hits_memory": self._hits_memory,
            "hits_sqlite": self._hits_sqlite,
            "hits_total": self._hits_memory + self._hits_sqlite,
            "misses": self._misses,
            "writes": self._writes,
            "skipped_too_large": self._skipped_too_large,
            "ttl_seconds": self._ttl,
        }

    # ------------------------------------------------------------------
    # Internal: LRU helpers (called under self._lock)
    # ------------------------------------------------------------------

    async def _maybe_evict(self) -> None:
        """Evict the LRU entry if over capacity. Called under self._lock."""
        while len(self._lru) > self._max_entries:
            evicted_key, _ = self._lru.popitem(last=False)
            if _LOG:
                _logger.debug("cache: LRU eviction", key=evicted_key[:12])

    # ------------------------------------------------------------------
    # Internal: SQLite helpers (blocking — must run in to_thread)
    # ------------------------------------------------------------------

    def _get_connection(self) -> sqlite3.Connection:
        """Open a new SQLite connection (thread-local, not shared)."""
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_db_sync(self) -> None:
        """Create the DB schema. Safe to call multiple times (CREATE IF NOT EXISTS)."""
        assert self._db_path is not None
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._get_connection() as conn:
            conn.executescript(_SCHEMA)

    def _sqlite_get(self, key: str) -> Optional[str]:
        """
        Return the raw JSON blob for *key* if it exists and has not expired.
        Deletes the row if expired (lazy TTL eviction).
        """
        now = time.time()
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM response_cache WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            value_blob, expires_at = row
            if now >= expires_at:
                conn.execute(
                    "DELETE FROM response_cache WHERE key = ?", (key,)
                )
                return None
            return value_blob

    def _sqlite_set(self, key: str, blob: str, expires_at: float) -> None:
        now = time.time()
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO response_cache (key, value, expires_at, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value      = excluded.value,
                    expires_at = excluded.expires_at,
                    created_at = excluded.created_at
                """,
                (key, blob, expires_at, now),
            )

    def _sqlite_clear(self) -> int:
        with self._get_connection() as conn:
            cur = conn.execute("DELETE FROM response_cache")
            return cur.rowcount

    def _sqlite_prune(self) -> int:
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.execute(
                "DELETE FROM response_cache WHERE expires_at <= ?", (now,)
            )
            return cur.rowcount


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _serialise(chunks: list[bytes]) -> str:
    """Encode list[bytes] as a compact JSON string (base64 per chunk)."""
    return json.dumps(
        [base64.b64encode(c).decode("ascii") for c in chunks],
        separators=(",", ":"),
    )


def _deserialise(blob: str) -> list[bytes]:
    """Decode a JSON string produced by ``_serialise`` back to list[bytes]."""
    return [base64.b64decode(s) for s in json.loads(blob)]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_cache: Optional[ResponseCache] = None


def init_cache(
    db_path: Optional[Path] = _DEFAULT_DB_PATH,
    memory_max_entries: int = _DEFAULT_MEMORY_MAX,
    ttl_seconds: float = _DEFAULT_TTL,
    max_entry_bytes: int = _DEFAULT_MAX_BYTES,
) -> ResponseCache:
    """
    Initialise and register the module-level cache singleton.

    Call once at server startup (e.g. from ``server.py`` lifespan).
    """
    global _cache
    _cache = ResponseCache(
        db_path=db_path,
        memory_max_entries=memory_max_entries,
        ttl_seconds=ttl_seconds,
        max_entry_bytes=max_entry_bytes,
    )
    return _cache


def get_cache() -> Optional[ResponseCache]:
    """Return the active cache singleton, or ``None`` if not initialised."""
    return _cache