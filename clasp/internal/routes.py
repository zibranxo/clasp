"""
clasp/internal/routes.py
Loopback-only management API.

All routes are protected by the IPGuard middleware (clasp/utils/ip_guard.py),
which rejects non-loopback callers with 403 before any handler runs.

Endpoints implemented here (Sprint 1 scope + stubs for Sprint 2+):

  Config
    GET  /internal/config              — full config JSON, keys masked
    POST /internal/config              — validate + write + hot-reload
    POST /internal/config/test-key     — live-test a provider API key
    GET  /internal/config/export       — config.yaml as plain text
    POST /internal/config/import       — replace config from YAML text

  Catalog
    GET  /internal/catalog             — full PROVIDER_CATALOG (no keys)
    GET  /internal/catalog/defaults    — default routing.models + by_type

  Status / metrics (stubs; populated by Sprint 2 rate-limit engine)
    GET  /internal/status              — full system state snapshot
    GET  /internal/stream              — SSE: status JSON every 2 s

  Provider management
    POST /internal/reset/{name}        — clear cooldown for one provider
    POST /internal/reset/all           — clear cooldown for all providers
    GET  /internal/providers/{name}/models — live model list (cached 1 h)

  Queue
    GET  /internal/queue               — queue depth snapshot

  Logs
    GET  /internal/logs/stream         — SSE: live log tail
    GET  /internal/logs/download       — full router.log as text/plain

  Cache
    POST /internal/cache/clear         — evict all cached responses
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from loguru import logger

from clasp.config.provider_catalog import PROVIDER_CATALOG
from clasp.config.settings import Settings, get_settings
from clasp.config.writer import write_config
from clasp.utils.logger import get_log_file

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/internal", tags=["internal"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MASK_SHOW = 4  # how many trailing chars to reveal in masked keys


def _mask_key(key: str) -> str:
    """Mask an API key: ``nvapi-***fG9a`` style."""
    if not key or len(key) <= _MASK_SHOW:
        return "***"
    prefix = key.split("-")[0] + "-" if "-" in key else ""
    return f"{prefix}***{key[-_MASK_SHOW:]}"


def _mask_settings(cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of the config dict with all provider keys masked."""
    import copy

    masked = copy.deepcopy(cfg)
    for _pname, pcfg in masked.get("providers", {}).items():
        if isinstance(pcfg, dict) and "keys" in pcfg:
            pcfg["keys"] = [_mask_key(k) if isinstance(k, str) else k for k in pcfg["keys"]]
    return masked


def _restore_redacted(new_cfg: dict[str, Any], original: Settings) -> dict[str, Any]:
    """
    When the UI round-trips masked keys back to us, restore the real values
    from the in-memory settings so we don't overwrite live keys with ``***``.
    """
    for pname, pcfg in new_cfg.get("providers", {}).items():
        if not isinstance(pcfg, dict):
            continue
        orig_prov = original.providers.get(pname)
        orig_keys: list[str] = getattr(orig_prov, "keys", []) if orig_prov else []
        restored: list[str] = []
        for i, entry in enumerate(pcfg.get("keys", [])):
            value = entry if isinstance(entry, str) else entry.get("value", "")
            if "***" in value:
                restored.append(orig_keys[i] if i < len(orig_keys) else value)
            else:
                restored.append(value)
        pcfg["keys"] = restored
    return new_cfg


def _settings_to_dict(s: Settings) -> dict[str, Any]:
    """Serialise Settings to a plain dict suitable for JSON responses."""
    return s.model_dump(mode="json")


# Sprint 2+ — these will be populated by the rate-limit / queue layer.
# For Sprint 1 they return safe stubs so the UI can render without errors.

def _get_live_status() -> dict[str, Any]:
    """
    Return the current system status snapshot.

    Sprint 1: returns a minimal healthy stub.
    Sprint 2+: import from clasp.ratelimit and clasp.queue registries.
    """
    try:
        from clasp.api.service import get_service_stats  # type: ignore[import]

        return get_service_stats()
    except ImportError:
        pass

    settings = get_settings()
    return {
        "status": "healthy",
        "uptime_seconds": 0,
        "active_requests": 0,
        "queue_depth": 0,
        "queue_interactive": 0,
        "queue_background": 0,
        "requests_today": 0,
        "tokens_today": 0,
        "absorbed_429s_today": 0,
        "failovers_today": 0,
        "cache_hits_today": 0,
        "providers": {
            name: {
                "status": "HEALTHY" if pcfg.enabled else "OFF",
                "requests_today": 0,
                "tokens_today": 0,
                "daily_token_limit": None,
                "errors_today": 0,
                "p50_latency_ms": None,
                "keys": [
                    {
                        "index": i,
                        "redacted": _mask_key(k),
                        "status": "HEALTHY",
                        "rpm_used": 0,
                        "rpm_limit": getattr(
                            PROVIDER_CATALOG.get(name), "rpm_limit", None
                        ),
                        "recovery_in": None,
                    }
                    for i, k in enumerate(pcfg.keys)
                ],
            }
            for name, pcfg in settings.providers.items()
        },
    }


# ---------------------------------------------------------------------------
# ── Config endpoints ────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


@router.get("/config", summary="Get current config (keys masked)")
async def get_config() -> dict[str, Any]:
    """
    Return the full running configuration as JSON.
    All provider API keys are masked (``nvapi-***fG9a``).
    """
    settings = get_settings()
    return _mask_settings(_settings_to_dict(settings))


@router.post("/config", summary="Update config and hot-reload")
async def post_config(request: Request) -> dict[str, Any]:
    """
    Accept a full config JSON body, validate it with Pydantic, restore any
    masked keys from the current in-memory config, write ``config.yaml``, and
    signal the config watcher to reload.

    Returns ``{"status": "ok", "reloaded_at": "<ISO-8601>"}`` on success.
    Returns HTTP 422 with Pydantic error details on validation failure.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    original = get_settings()
    body = _restore_redacted(body, original)

    # Validate via Pydantic before touching the file.
    try:
        new_settings = Settings(**body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await asyncio.to_thread(write_config, new_settings.model_dump(mode="json"))

    # The watchfiles watcher in server.py will pick up the file change and
    # call settings.reload() automatically.  Force a manual reload as well
    # so the in-process state is immediately consistent.
    try:
        from clasp.config.settings import reload_settings  # type: ignore[import]

        reload_settings()
    except ImportError:
        pass

    reloaded_at = datetime.now(timezone.utc).isoformat()
    logger.info("Config updated via /internal/config", reloaded_at=reloaded_at)
    return {"status": "ok", "reloaded_at": reloaded_at}


@router.post("/config/test-key", summary="Live-test a provider API key")
async def test_key(request: Request) -> dict[str, Any]:
    """
    Body: ``{"provider": "nvidia_nim", "key": "nvapi-xxx", "key_index": 0}``

    Makes a minimal authenticated request to the provider (e.g. list-models)
    and reports round-trip latency or the HTTP error.
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    provider_name: str = body.get("provider", "")
    key: str = body.get("key", "")

    if not provider_name or not key:
        raise HTTPException(status_code=400, detail="'provider' and 'key' are required")

    profile = PROVIDER_CATALOG.get(provider_name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name!r}")

    # Use the provider's models endpoint as a lightweight probe.
    base_url = profile.base_url.rstrip("/")
    probe_url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {key}"}

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(probe_url, headers=headers)
        latency_ms = round((time.monotonic() - start) * 1000)

        if resp.status_code == 200:
            return {"ok": True, "latency_ms": latency_ms}
        else:
            return {
                "ok": False,
                "error": f"{resp.status_code} {resp.reason_phrase}",
                "latency_ms": latency_ms,
            }
    except httpx.TimeoutException:
        return {"ok": False, "error": "Request timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/config/export", summary="Export config.yaml as plain text")
async def export_config() -> PlainTextResponse:
    """Return the raw ``config.yaml`` file content so the UI can offer a download."""
    from clasp.config.settings import get_config_path  # type: ignore[import]

    config_path = get_config_path()
    if not config_path.is_file():
        raise HTTPException(status_code=404, detail="config.yaml not found")

    content = config_path.read_text(encoding="utf-8")
    return PlainTextResponse(content, media_type="text/yaml")


@router.post("/config/import", summary="Replace config from YAML text")
async def import_config(request: Request) -> dict[str, Any]:
    """
    Body: raw YAML text (``Content-Type: text/plain``).
    Validates the YAML and replaces the active config.
    """
    try:
        text = (await request.body()).decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read body: {exc}") from exc

    try:
        from ruamel.yaml import YAML  # type: ignore[import]

        y = YAML()
        import io

        body: dict[str, Any] = y.load(io.StringIO(text)) or {}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid YAML: {exc}") from exc

    try:
        new_settings = Settings(**body)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    await asyncio.to_thread(write_config, new_settings.model_dump(mode="json"))

    try:
        from clasp.config.settings import reload_settings  # type: ignore[import]

        reload_settings()
    except ImportError:
        pass

    logger.info("Config imported via /internal/config/import")
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# ── Catalog endpoints ───────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


@router.get("/catalog", summary="Full provider catalog (no keys)")
async def get_catalog() -> dict[str, Any]:
    """
    Return the static ``PROVIDER_CATALOG`` as JSON.
    Contains all supported providers with their free-tier limits and metadata.
    No API keys are present in the catalog.
    """
    import dataclasses
    return {
        name: dataclasses.asdict(profile)
        for name, profile in PROVIDER_CATALOG.items()
    }


@router.get("/catalog/defaults", summary="Default routing.models + by_type values")
async def get_catalog_defaults() -> dict[str, Any]:
    """
    Return the default model-tier and request-type routing config so the UI
    can offer a «Reset to defaults» button without hard-coding values in JS.
    """
    # Import here to avoid circular dependency at module load time.
    from clasp.config.settings import DEFAULT_ROUTING_MODELS, DEFAULT_ROUTING_BY_TYPE  # type: ignore[import]

    return {
        "models": DEFAULT_ROUTING_MODELS,
        "by_type": DEFAULT_ROUTING_BY_TYPE,
    }


# ---------------------------------------------------------------------------
# ── Status / metrics ────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


@router.get("/status", summary="Full system state snapshot")
async def get_status() -> dict[str, Any]:
    """
    Returns a complete status snapshot including per-provider health,
    key states, queue depths, and today's aggregate counters.

    Sprint 1: returns a live-accurate stub (no rate-limit engine yet).
    Sprint 2+: populated by the rate-limit / queue layer.
    """
    return _get_live_status()


@router.get("/stream", summary="SSE: live status every 2 s")
async def stream_status(request: Request) -> StreamingResponse:
    """
    Server-Sent Events stream that emits the full status JSON every 2 seconds.
    The UI connects once on load and keeps this stream open.

    Event format::

        data: {"status":"healthy","providers":{...},...}\\n\\n
    """

    async def _generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                snapshot = _get_live_status()
                yield f"data: {json.dumps(snapshot)}\n\n"
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# ── Provider management ─────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


@router.post("/reset/{provider_name}", summary="Clear cooldown for one provider")
async def reset_provider(provider_name: str) -> dict[str, Any]:
    """
    Clear all cooldown / circuit-breaker state for ``provider_name``.
    Sprint 2+: delegates to the KeyPool / CircuitBreaker instances.
    """
    settings = get_settings()

    if provider_name not in settings.providers:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name!r}")

    try:
        from clasp.providers.registry import get_registry  # type: ignore[import]

        registry = get_registry()
        provider = registry.get(provider_name)
        if provider and hasattr(provider, "reset_cooldown"):
            await provider.reset_cooldown()
    except ImportError:
        pass  # Sprint 1 — registry not wired yet

    logger.info("Cooldown reset", provider=provider_name)
    return {"reset": True, "provider": provider_name}


@router.post("/reset/all", summary="Clear cooldown for all providers")
async def reset_all_providers() -> dict[str, Any]:
    """Clear cooldown / circuit-breaker state for every configured provider."""
    settings = get_settings()
    reset_names: list[str] = []

    for name in settings.providers:
        try:
            from clasp.providers.registry import get_registry  # type: ignore[import]

            registry = get_registry()
            provider = registry.get(name)
            if provider and hasattr(provider, "reset_cooldown"):
                await provider.reset_cooldown()
        except ImportError:
            pass
        reset_names.append(name)

    logger.info("All cooldowns reset", providers=reset_names)
    return {"reset": True, "providers": reset_names}


# Cache for live model lists: provider_name → (timestamp, [model_slug, ...])
_model_list_cache: dict[str, tuple[float, list[str]]] = {}
_MODEL_CACHE_TTL = 3600.0  # 1 hour


def _models_endpoint_for(profile, settings_provider) -> tuple[str, dict[str, str]]:
    """
    Build (url, headers) for a provider's live model-list endpoint, based on
    its catalog transport type. Mirrors the auth conventions used by
    providers/openai_transport.py and providers/anthropic_transport.py.

      - tier == "local"               -> no auth, OpenAI-compatible /v1/models
      - transport == "anthropic_messages" -> x-api-key header, swap
                                              .../messages -> .../models
      - otherwise (openai_chat, etc.) -> Authorization: Bearer <key>,
                                          base_url already ends in /v1
    """
    base_url = profile.base_url.rstrip("/")
    key = None
    keys = getattr(settings_provider, "keys", None) or []
    if keys:
        key = keys[0]

    if getattr(profile, "tier", None) == "local":
        # ollama / lm_studio — no auth, OpenAI-compatible /v1/models
        return f"{base_url}/v1/models", {}

    if getattr(profile, "transport", None) == "anthropic_messages":
        # e.g. fireworks: base_url ends in /v1/messages -> swap for /v1/models
        url = base_url.rsplit("/messages", 1)[0] + "/models"
        headers = {"x-api-key": key} if key else {}
        return url, headers

    # openai_chat (most providers): base_url already ends in /v1
    url = f"{base_url}/models"
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return url, headers


@router.get(
    "/providers/{provider_name}/models",
    summary="Live model list for a provider (cached 1 h)",
)
async def list_provider_models(provider_name: str) -> dict[str, Any]:
    """
    Fetch the live model list from a provider's API and return it.
    Results are cached in-process for 1 hour to avoid hammering the
    provider's endpoint on every page load.

    URL/header construction is transport-aware (local / anthropic_messages /
    openai_chat) via ``_models_endpoint_for`` — see that helper for details.
    """
    now = time.monotonic()
    cached = _model_list_cache.get(provider_name)
    if cached and (now - cached[0]) < _MODEL_CACHE_TTL:
        return {"models": cached[1], "cached": True}

    settings = get_settings()
    pcfg = settings.providers.get(provider_name)
    if pcfg is None:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider_name!r}")

    profile = PROVIDER_CATALOG.get(provider_name)
    if profile is None:
        raise HTTPException(
            status_code=404, detail=f"No catalog entry for provider: {provider_name!r}"
        )

    keys = getattr(pcfg, "keys", [])
    if not keys and getattr(profile, "tier", None) != "local":
        raise HTTPException(
            status_code=400,
            detail=f"No API keys configured for provider: {provider_name!r}",
        )

    url, headers = _models_endpoint_for(profile, pcfg)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        models: list[str] = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception as exc:
        logger.warning("Failed to fetch model list", provider=provider_name, error=str(exc))
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch models from {provider_name}: {exc}",
        ) from exc

    _model_list_cache[provider_name] = (now, models)
    return {"models": models, "cached": False}


# ---------------------------------------------------------------------------
# ── Queue ───────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


@router.get("/queue", summary="Queue depth snapshot")
async def get_queue() -> dict[str, Any]:
    """
    Return current queue depths.
    Sprint 1: returns zeros (queue not yet implemented).
    Sprint 3+: reads from clasp.queue.manager.
    """
    try:
        from clasp.queue.manager import get_queue_stats  # type: ignore[import]

        return get_queue_stats()
    except ImportError:
        return {
            "depth": 0,
            "interactive": 0,
            "tool_use": 0,
            "background": 0,
        }


# ---------------------------------------------------------------------------
# ── Logs ────────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


@router.get("/logs/stream", summary="SSE: live log tail")
async def stream_logs(request: Request) -> StreamingResponse:
    """
    Server-Sent Events stream that tails the rotating log file.
    Each event is a JSON log line::

        data: {"time":"10:42:15","level":"INFO","message":"..."}\\n\\n

    Sprint 1: reads buffered lines from loguru's in-process sink via a
    simple asyncio.Queue populated by a loguru sink.  Falls back to
    keep-alive events if no sink is wired yet.
    """

    async def _generate():
        try:
            from clasp.utils.logger import get_log_queue  # type: ignore[import]

            q: asyncio.Queue[str] = get_log_queue()
        except (ImportError, AttributeError):
            q = None  # type: ignore[assignment]

        try:
            while True:
                if await request.is_disconnected():
                    break
                if q is not None:
                    try:
                        line = q.get_nowait()
                        yield f"data: {line}\n\n"
                        continue
                    except asyncio.QueueEmpty:
                        pass
                # Keep-alive to prevent proxy timeouts.
                yield ": keep-alive\n\n"
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/logs/download", summary="Download full router.log")
async def download_logs() -> PlainTextResponse:
    """
    Return the full rotating JSON log file as ``text/plain`` so the UI
    can offer a one-click download.
    """
    log_path = get_log_file()
    if not log_path.is_file():
        return PlainTextResponse(
            content="# No log file found yet.\n",
            media_type="text/plain",
        )
    content = await asyncio.to_thread(log_path.read_text, encoding="utf-8", errors="replace")
    return PlainTextResponse(content, media_type="text/plain")


# ---------------------------------------------------------------------------
# ── Cache ───────────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


@router.post("/cache/clear", summary="Evict all cached responses")
async def clear_cache() -> dict[str, Any]:
    """
    Evict every entry from both the in-memory LRU cache and the SQLite cache.
    Sprint 1: stub (cache not yet implemented).
    Sprint 5+: delegates to clasp.cache.response_cache.
    """
    entries_removed = 0
    try:
        from clasp.cache.response_cache import get_cache  # type: ignore[import]

        cache = get_cache()
        entries_removed = await cache.clear()
    except ImportError:
        pass

    logger.info("Cache cleared", entries_removed=entries_removed)
    return {"cleared": True, "entries_removed": entries_removed}