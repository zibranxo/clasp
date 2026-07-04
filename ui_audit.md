# CLASP UI Audit — Task 1

## Executive Summary

The current UI implementation (`clasp/ui/static/app.js` and `index.html`) is **purely cosmetic** — it adds visual enhancements (parallax, tilt effects, command palette, sparklines) but **does not change any backend functionality**. All core Alpine.js data-binding and endpoint calls remain identical to the plan.md specification.

**Critical finding**: The **`testKey()` function** (`app.js` lines 269-283) **DOES call the real backend endpoint** (`POST /internal/config/test-key`) and **DOES NOT fake latency**. The backend implementation in `clasp/internal/routes.py` (lines 243-288) makes a **real authenticated HTTP request** to the provider's `/models` endpoint and returns the **actual measured latency** or real error.

## Function-by-Function Audit

| Function / Handler | UI Element | Backend Endpoint (plan.md §17) | Wired to Real Endpoint? | Returns Real Data? | Notes |
|---------------------|------------|----------------------------------|------------------------|-------------------|-------|
| `loadConfig()` | Initial page load | `GET /internal/config` | ✅ Yes | ✅ Yes | Loads full config with masked keys |
| `loadCatalog()` | Initial page load | `GET /internal/catalog` | ✅ Yes | ✅ Yes | Loads provider catalog |
| `startLiveStream()` | Dashboard SSE | `GET /internal/stream` | ✅ Yes | ✅ Yes | Real SSE stream every 2s |
| `startLogStream()` | Logs SSE | `GET /internal/logs/stream` | ✅ Yes | ✅ Yes | Real log tail stream |
| `saveAndApply()` | [Save & Apply] button | `POST /internal/config` | ✅ Yes | ✅ Yes | Real config write + hot-reload |
| `testKey()` | [Test] key button | `POST /internal/config/test-key` | ✅ Yes | ✅ Yes | **Real provider API call, real latency** |
| `addKey()` | [+ Add key] button | N/A (UI-only) | ✅ N/A | ✅ N/A | Appends empty row |
| `removeKey()` | [✕] remove button | N/A (UI-only) | ✅ N/A | ✅ N/A | Removes row |
| `discardChanges()` | [Discard] button | N/A (UI-only) | ✅ N/A | ✅ N/A | Reloads config |
| `resetModelDefaults()` | [Reset to defaults] | `GET /internal/catalog/defaults` | ✅ Yes | ✅ Yes | Fetches default routing config |
| `removeFromChain()` | [Remove] from chain | N/A (UI-only) | ✅ N/A | ✅ N/A | Removes provider from chain |
| `enabledProviders()` | Model dropdown filter | N/A (UI-only) | ✅ N/A | ✅ N/A | Filters enabled providers |
| `providerStatusText()` | Status badge | N/A (derived from SSE) | ✅ N/A | ✅ Yes | Reads from `live.providers` |
| `providerStatusClass()` | Status badge color | N/A (derived from SSE) | ✅ N/A | ✅ Yes | Reads from `live.providers` |
| `filteredLogs()` | Log level filter | N/A (UI-only) | ✅ N/A | ✅ N/A | Filters log lines by level |
| `syncSharedTokens()` | Shared pool textarea | N/A (UI-only) | ✅ N/A | ✅ N/A | Syncs textarea → array |
| `clearCache()` | [Clear cache] button | `POST /internal/cache/clear` | ✅ Yes | ✅ Yes | Real cache eviction |
| `exportConfig()` | [Export] button | `GET /internal/config/export` | ✅ Yes | ✅ Yes | Real YAML download |
| `importConfig()` | [Import] button | `POST /internal/config/import` | ✅ Yes | ✅ Yes | Real YAML upload + validate |
| `formatUptime()` | Dashboard uptime | N/A (UI-only) | ✅ N/A | ✅ N/A | Formats seconds → "2h 14m" |
| `showToast()` | Toast notification | N/A (UI-only) | ✅ N/A | ✅ N/A | Shows feedback |

## Visual Enhancements (Cosmetic Only)

The following functions are **purely cosmetic** and **do not affect data flow**:

- `initParallax()`: Mouse/scroll-driven background orb animation
- `initCardTilt()`: 3D perspective tilt on cards
- `initMagneticButtons()`: Magnetic pull effect on buttons
- `startClock()`: Live HH:MM:SS clock in header
- `animateStatCard()`: Breathe animation on stat updates
- `animateValue()`: Smooth counter animation
- `pushSparkPoint()` + `renderSparkline()`: Sparkline charts
- Command palette: Ctrl+K shortcut navigator
- SVG nav icons: Replaces emoji with inline SVG

**None of these touch backend endpoints or data.**

## Backend Endpoint Implementation Status

| Endpoint | Implemented? | Real Data? | Notes |
|----------|--------------|-----------|-------|
| `GET /internal/config` | ✅ Yes | ✅ Yes | Full config, keys masked |
| `POST /internal/config` | ✅ Yes | ✅ Yes | Validate + write + hot-reload |
| `POST /internal/config/test-key` | ✅ Yes | ✅ Yes | **Real provider API call** |
| `GET /internal/config/export` | ✅ Yes | ✅ Yes | YAML text |
| `POST /internal/config/import` | ✅ Yes | ✅ Yes | YAML parse + validate |
| `GET /internal/catalog` | ✅ Yes | ✅ Yes | Full catalog |
| `GET /internal/catalog/defaults` | ✅ Yes | ✅ Yes | Default routing |
| `GET /internal/status` | ✅ Yes | ✅ Yes | Live metrics |
| `GET /internal/stream` | ✅ Yes | ✅ Yes | SSE every 2s |
| `POST /internal/reset/{name}` | ✅ Yes | ✅ Yes | Cooldown reset |
| `POST /internal/reset/all` | ✅ Yes | ✅ Yes | All cooldowns |
| `GET /internal/providers/{name}/models` | ✅ Yes | ✅ Yes | Live model list |
| `GET /internal/queue` | ✅ Yes | ✅ Yes | Queue depth |
| `GET /internal/logs/stream` | ✅ Yes | ✅ Yes | Log tail SSE |
| `GET /internal/logs/download` | ✅ Yes | ✅ Yes | Full log file |
| `POST /internal/cache/clear` | ✅ Yes | ✅ Yes | Cache evict |

## Critical Findings

### ✅ **`testKey()` is NOT fake**

**File**: `clasp/ui/static/app.js` lines 269-283
```javascript
async testKey(providerName, keyValue, keyIndex) {
  const prov = this.config.providers[providerName];
  if (!prov?.keys?.[keyIndex]) return;
  prov.keys[keyIndex]._testing = true;
  try {
    const res = await fetch('/internal/config/test-key', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: providerName, key: keyValue, key_index: keyIndex }),
    });
    prov.keys[keyIndex]._testResult = await res.json();
  } finally {
    prov.keys[keyIndex]._testing = false;
  }
}
```

**Backend**: `clasp/internal/routes.py` lines 243-288
```python
@router.post("/config/test-key")
async def test_key(request: Request) -> dict[str, Any]:
    # ... validation ...
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(probe_url, headers=headers)
        latency_ms = round((time.monotonic() - start) * 1000)
        if resp.status_code == 200:
            return {"ok": True, "latency_ms": latency_ms}
        else:
            return {"ok": False, "error": f"{resp.status_code} {resp.reason_phrase}", "latency_ms": latency_ms}
    except httpx.TimeoutException:
        return {"ok": False, "error": "Request timed out"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

**This is 100% real**:
- Makes **real HTTP call** to provider's `/models` endpoint
- Measures **actual wall-clock latency** via `time.monotonic()`
- Returns **real HTTP status** (200, 401, 429, etc.)
- No `Math.random()`, no `setTimeout`, no hardcoded values

### ✅ **No Fake Values Anywhere**

- **Dashboard**: All numbers come from `GET /internal/stream` SSE → `_get_live_status()` → real metrics
- **Provider cards**: RPM usage, token usage, P50 latency all from real backend state
- **Logs**: Real SSE tail from `GET /internal/logs/stream` → loguru queue
- **Status badges**: Derived from real provider health in SSE stream

### ✅ **All Buttons Call Real Endpoints**

| Button | Calls Real Endpoint? | Notes |
|--------|---------------------|-------|
| [Test] | ✅ `POST /internal/config/test-key` | Real provider API call |
| [Save & Apply] | ✅ `POST /internal/config` | Real config write |
| [Clear cache] | ✅ `POST /internal/cache/clear` | Real cache evict |
| [Reset] | ✅ `POST /internal/reset/{name}` | Real cooldown reset |
| [Export] | ✅ `GET /internal/config/export` | Real YAML download |
| [Import] | ✅ `POST /internal/config/import` | Real YAML upload |
| [Download] | ✅ `GET /internal/logs/download` | Real log file |

## Recommendations

### 1. **No Changes Needed**

The UI is **already wired to real endpoints** and **already returns real data**. The visual enhancements are purely cosmetic and do not affect functionality.

### 2. **Verify with Live Server**

To confirm:
```bash
clasp server
```
Then:
1. Open browser to http://127.0.0.1:8082
2. Go to Providers panel
3. Add a real NVIDIA NIM key
4. Click [Test]
5. Observe: real latency number (e.g., 42ms) or real error (e.g., 401 Unauthorized)
6. Check browser DevTools → Network tab → `test-key` request → real HTTP call

### 3. **Test Key Endpoint Verification**

Run a simple curl to verify the backend:
```bash
curl -X POST http://127.0.0.1:8082/internal/config/test-key \
  -H "Content-Type: application/json" \
  -d '{"provider":"nvidia_nim","key":"nvapi-xxx-real-key-here","key_index":0}'
```
Expected: `{"ok":true,"latency_ms":42}` or `{"ok":false,"error":"401 Unauthorized"}` — **never** a fake or random number.

## Conclusion

**The UI is already real.** No fake values, no simulated latency, no hardcoded numbers. The `testKey()` function and all other handlers call real backend endpoints and return real data. The visual enhancements are cosmetic only and do not affect functionality.

**Action**: Proceed to Task 3 (verification) — no fixes needed in Task 2.
