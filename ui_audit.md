# UI Audit for CLASP

This audit cross-references every named function and handler in `ui/static/app.js` and `ui/static/index.html` against `plan.md` Section 5 and Section 17.

## Audit Table

| Function / Handler | UI Element it drives | Backend Endpoint (plan.md §17) | Currently wired to real endpoint? | Currently returns real data? | Notes |
|---|---|---|---|---|---|
| `loadConfig()` / `init()` | Page load | `GET /internal/config` | Yes | Yes | Fetches actual config, masks keys, populates UI state. |
| `loadCatalog()` | Page load | `GET /internal/catalog` | Yes | Yes | Fetches actual `PROVIDER_CATALOG` data. |
| `startLiveStream()` | Dashboard stats, Sparklines, Badges | `GET /internal/stream` | Yes | **No (Stubbed)** | The stream calls `_get_live_status()` in `routes.py` which currently falls back to a hardcoded stub (0 requests, 0 tokens, fake `HEALTHY` status). `get_service_stats` is missing in `service.py`. |
| `startLogStream()` | Logs Panel | `GET /internal/logs/stream` | Yes | Yes | Reads from the actual loguru `get_log_queue()`. |
| `saveAndApply()` | Header `[Save & Apply]` | `POST /internal/config` | Yes | Yes | Re-injects masked keys, passes Pydantic validation, writes to disk, reloads. |
| `discardChanges()` | Header `[Discard]` | `GET /internal/config` (via `loadConfig`) | Yes | Yes | Re-fetches current active config. |
| `testKey()` | Providers `[Test]` button | `POST /internal/config/test-key` | Yes | **Yes** | Uses `httpx.AsyncClient` to make an actual authenticated network call to the provider's `/models` endpoint and returns real wall-clock `latency_ms`. (Note: This is already implemented correctly in the current backend). |
| `addKey()`, `removeKey()` | Providers `[+ Add key]`, `[x]` | N/A | N/A | N/A | UI local state array manipulation. |
| `providerStatusText()`, `providerStatusClass()` | Status badges (Providers, Dashboard) | `GET /internal/stream` | Yes | **No (Stubbed)** | Relies on the fake `_get_live_status()` payload. |
| `resetModelDefaults()` | Models `[Reset to defaults]` | `GET /internal/catalog/defaults` | Yes | Yes | Fetches actual default routing config from backend. |
| `removeFromChain()` | Routing `[Remove]` button | N/A | N/A | N/A | UI local state manipulation. |
| `clearCache()` | Advanced `[Clear cache]` | `POST /internal/cache/clear` | Yes | Yes/Partial | Calls `clasp.cache.response_cache.get_cache().clear()`. Module exists, but if it fails to import, silently returns `0`. |
| `syncSharedTokens()` | Advanced Textarea | N/A | N/A | N/A | UI local string-to-array parsing. |
| `exportConfig()` | Advanced `[Export config.yaml]` | `GET /internal/config/export` | Yes | Yes | Returns actual `config.yaml` file content. |
| `importConfig()` | Advanced `[Import config.yaml]` | `POST /internal/config/import` | Yes | Yes | Parses provided YAML and overwrites actual config. |
| `startClock()`, `initParallax()`, `initCardTilt()`, `initMagneticButtons()`, `animateStatCard()`, `animateValue()`, `renderSparkline()` | Visual/Cosmetic Layer | N/A | N/A | N/A | Purely cosmetic, frontend only. |

## Fake Data Summary (The "Why we are here" section)

The biggest remaining offender for fake data is the **live metrics & status system**. 

The SSE stream (`GET /internal/stream`) successfully connects, but the backend `_get_live_status()` function in `clasp/internal/routes.py` (which powers it) is currently a **Sprint 1 stub**. It catches an `ImportError` when trying to load `clasp.api.service.get_service_stats` (which doesn't exist) and falls back to a hardcoded dictionary. This causes:
1. All Dashboard numbers (requests, tokens, 429s, cache hits) to be locked at `0`.
2. All sparklines to render flat.
3. All provider status badges to show `HEALTHY` blindly instead of reflecting real circuit breaker or cooldown state.
4. Latency P50 to be `None`.

The `test_key` function, previously noted as a major offender, **has already been implemented with real logic** in `routes.py` — it fires a genuine authenticated request using `httpx` and measures real latency. 

## Proposed Fix Order (Task 2)

Before proceeding, please review and sign off on this fix order:

1. **Dashboard Stats & Sparklines:** Implement `get_service_stats()` in `clasp/api/service.py` to aggregate real token usage, request counts, and 429 tallies from the actual rate limiting/tracking layer, replacing the stub in `routes.py`.
2. **Provider Status Badges:** Wire the `_get_live_status()` dictionary to read the actual `HEALTHY` / `COOLING_DOWN` / `CIRCUIT_OPEN` states from `clasp/ratelimit/key_pool.py` and `circuit_breaker.py`.
3. **Queue Depth:** Wire `_get_live_status()` to read actual queue counts from `clasp.queue.manager`. 
4. **Latency Tracking (P50):** Ensure that rolling latency is actually tracked per provider in the backend state (or add it if it doesn't exist) so the UI can display real P50 values instead of `None`.
5. **Verify `clearCache()`:** Ensure `clasp.cache.response_cache` imports correctly in `routes.py` and executes a real eviction.

Let me know if you approve this audit and the proposed fix order, or if you'd like to adjust the priorities.
