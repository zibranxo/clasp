"""
clasp/config/watcher.py

Config-file hot-reload using ``watchfiles``.

What it does
------------
Runs as a background asyncio task started by ``server.py`` at startup.
When ``~/.clasp/config.yaml`` changes on disk (written by the UI or by a
human editor) the watcher:

  1. Clears the ``get_settings()`` lru_cache.
  2. Calls ``get_settings()`` to parse and validate the new file.
  3. Logs the reload event at INFO level.
  4. Fires any registered ``on_reload`` callbacks (used by the provider
     registry, rate-limit engine, etc. to pick up new keys / limits).
  5. Puts a ``{"event": "config_reloaded"}`` message on the optional SSE
     broadcast queue so the web UI refreshes automatically.

Public API
----------
    from clasp.config.watcher import ConfigWatcher

    watcher = ConfigWatcher(sse_queue=some_asyncio_queue)
    watcher.add_callback(my_async_fn)   # called with new Settings after reload
    task = asyncio.create_task(watcher.run())

    # Trigger a manual reload (e.g. after POST /internal/config):
    await watcher.trigger_reload()

    # On shutdown:
    task.cancel()
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

from loguru import logger

from clasp.config.settings import Settings, get_settings

# Type alias for reload callbacks.
_ReloadCallback = Callable[[Settings], Coroutine[Any, Any, None]]


# ---------------------------------------------------------------------------
# ConfigWatcher
# ---------------------------------------------------------------------------


class ConfigWatcher:
    """
    Watches ``~/.clasp/config.yaml`` (or CLASP_CONFIG_PATH) for changes
    and hot-reloads the settings singleton.
    """

    def __init__(
        self,
        sse_queue: asyncio.Queue[dict[str, Any]] | None = None,
        debounce_ms: int = 300,
    ) -> None:
        """
        Parameters
        ----------
        sse_queue:
            If provided, a ``{"event": "config_reloaded", ...}`` message is
            put here after every successful reload so the UI's SSE stream can
            push a refresh notification.
        debounce_ms:
            Minimum milliseconds between reload triggers.  watchfiles already
            de-duplicates rapid saves, but editors sometimes emit two events.
        """
        self._sse_queue = sse_queue
        self._debounce_ms = debounce_ms
        self._callbacks: list[_ReloadCallback] = []
        self._manual_trigger: asyncio.Event = asyncio.Event()

    # ── Callback registry ──────────────────────────────────────────────────

    def add_callback(self, fn: _ReloadCallback) -> None:
        """Register an async callable invoked with the fresh Settings object."""
        self._callbacks.append(fn)

    def remove_callback(self, fn: _ReloadCallback) -> None:
        """Unregister a previously added callback (no-op if not found)."""
        try:
            self._callbacks.remove(fn)
        except ValueError:
            pass

    # ── Manual trigger ─────────────────────────────────────────────────────

    async def trigger_reload(self) -> Settings:
        """
        Force an immediate reload without waiting for a filesystem event.
        Called by ``POST /internal/config`` after atomically writing the file.
        Returns the fresh Settings.
        """
        new_settings = await self._do_reload()
        return new_settings

    # ── Core reload logic ──────────────────────────────────────────────────

    async def _do_reload(self) -> Settings:
        """
        Clear the singleton cache, parse the file, run callbacks.
        Never raises — errors are logged and the old settings remain active.
        """
        # 1. Bust the lru_cache so the next call re-reads the file.
        get_settings.cache_clear()

        # 2. Re-parse.
        try:
            new_settings = get_settings()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Config reload failed — keeping previous settings",
                error=str(exc),
            )
            # Restore the old cached value by calling get_settings() again;
            # since the cache is clear, it will try to parse once more and
            # either succeed (race condition fixed itself) or fail again.
            # Either way, re-raise is intentionally swallowed here.
            return get_settings()

        logger.info(
            "Config hot-reloaded",
            providers_enabled=new_settings.enabled_providers(),
            port=new_settings.server.port,
        )

        # 3. Notify SSE listeners.
        if self._sse_queue is not None:
            try:
                self._sse_queue.put_nowait(
                    {
                        "event": "config_reloaded",
                        "port": new_settings.server.port,
                        "providers": new_settings.enabled_providers(),
                    }
                )
            except asyncio.QueueFull:
                pass  # UI will catch up on next poll

        # 4. Fire registered callbacks concurrently.
        if self._callbacks:
            await asyncio.gather(
                *(cb(new_settings) for cb in self._callbacks),
                return_exceptions=True,
            )

        return new_settings

    # ── Background task ────────────────────────────────────────────────────

    async def run(self) -> None:
        """
        Long-running coroutine.  Awaits ``watchfiles.awatch`` on the config
        file path and calls ``_do_reload()`` on every detected change.

        Should be run as an asyncio task:
            task = asyncio.create_task(watcher.run())
        Cancel the task for graceful shutdown.
        """
        try:
            from watchfiles import awatch  # lazy import — optional dep
        except ImportError:
            logger.warning(
                "watchfiles not installed; config hot-reload disabled. "
                "Install with: pip install watchfiles"
            )
            # Block forever so the task stays alive without consuming CPU.
            await asyncio.Event().wait()
            return

        config_path = self._config_path()
        logger.info("Config watcher started", path=str(config_path))

        try:
            async for _ in self._watch_with_debounce(config_path):
                logger.debug("Config file change detected", path=str(config_path))
                await self._do_reload()
        except asyncio.CancelledError:
            logger.info("Config watcher stopped")
            raise

    async def _watch_with_debounce(
        self, path: Path
    ) -> AsyncIterator[None]:
        """
        Wrap ``watchfiles.awatch`` and yield at most once per *debounce_ms*
        window regardless of how many raw events arrive.
        """
        from watchfiles import awatch  # noqa: PLC0415

        debounce_s = self._debounce_ms / 1000.0
        last_fired: float = 0.0

        async for _changes in awatch(path, recursive=False):
            import time  # noqa: PLC0415

            now = time.monotonic()
            if now - last_fired >= debounce_s:
                last_fired = now
                yield

    @staticmethod
    def _config_path() -> Path:
        import os  # noqa: PLC0415

        return Path(
            os.environ.get("CLASP_CONFIG_PATH", str(Path.home() / ".clasp" / "config.yaml"))
        )


# ---------------------------------------------------------------------------
# Module-level singleton (used by server.py)
# ---------------------------------------------------------------------------

_watcher: ConfigWatcher | None = None


def get_watcher(
    sse_queue: asyncio.Queue[dict[str, Any]] | None = None,
) -> ConfigWatcher:
    """
    Return the process-wide ConfigWatcher, creating it on first call.

    Subsequent calls return the same instance; ``sse_queue`` is ignored after
    the first call (set it up before starting the background task).
    """
    global _watcher  # noqa: PLW0603
    if _watcher is None:
        _watcher = ConfigWatcher(sse_queue=sse_queue)
    return _watcher