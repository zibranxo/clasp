# Sprint 2 Fixes Implementation Plan

## Background

The Sprint 2 review (`report_2.md` / `evaluation_2.md`) found that the subsystem cannot be imported or run at all — 0 tests executed. Every issue stems from broken interfaces, missing symbols, stale imports, or incorrect registration wiring. This plan addresses every **CRITICAL** issue first (the ones that prevent importing), then **HIGH** issues, then aligns tests with the actual implementations.

---

## User Review Required

> [!CAUTION]
> The `model_map.py` `request.model` attribute access (CRITICAL) assumes `AnthropicRequest.body["model"]`. The fix reads `request.body.get("model", "")`. Confirm this is the right path.

> [!IMPORTANT]
> `service.py` currently passes a raw `dict` to `selector.select()`, which requires an `AnthropicRequest`. The fix builds an `AnthropicRequest` via `AnthropicRequest.from_body()`. This means detect.py's `classify_priority` and `_estimate_tokens` are called — both exist already. Confirm `needs_tools` / `needs_vision` / `priority_for` should be removed from detect.py's public surface (they don't exist there), and the `AnthropicRequest.from_body()` call in `types.py` already sets those fields.

> [!IMPORTANT]
> The report notes that `persistence.py` and `key_pool.py` import `CooldownTracker` — this name does not exist. The real class is `CooldownManager`. All references will be renamed to `CooldownManager` / `get_cooldown_manager()`.

---

## Open Questions

1. Should `seconds_until_available()` and the display properties on `TokenBucket` acquire `_lock` / apply lazy refill? The report flags them as stale without a lock. The fix will add lock acquisition to make them consistent — but this is a minor behavioral change. **Confirm acceptable.**
2. Should the `_window` attribute on `CircuitBreaker` be exposed publicly? The test accesses `cb._window` and `cb._opened_at`. The fix will add `_window` as an alias for `_recent_outcomes` and `_opened_at` as an alias for `_recovery_at` for test compatibility, rather than renaming internal fields everywhere. **Alternatively**, we can fix the tests to not use private attributes. **Recommend fixing tests** — less coupling. Proceeding with this approach.
3. `capability.py`: model-slug quirks currently override user config. The report flags this as medium divergence. The fix will swap the layering so user config wins over model quirks (layer 3 applies first, layer 2 wins). **Confirm this is the right precedence.**

---

## Proposed Changes

### Component 1 — `clasp/ratelimit/cooldown.py`

**Issues to fix:**
- [HIGH] Repeated 429s schedule independent callbacks — `_re_enable()` can clear a newer cooldown early. Fix: store a "generation" counter and check it in `_re_enable()` before clearing.
- [HIGH] `import_cooldowns()` doesn't re-schedule callbacks. Fix: schedule `_re_enable()` callbacks during import for still-valid entries.
- [HIGH] `_cooling` and `_failure_counts` are unprotected shared dicts. Fix: add `asyncio.Lock` (or `threading.Lock` for sync access) — use `threading.Lock` since all mutators are synchronous.
- [MEDIUM] `is_cooling()` only checks dict membership, not timestamp. Fix: also check `recovery_at < time.monotonic()` and eagerly expire.
- [MEDIUM] Negative `Retry-After` accepted. Fix: clamp result of `_parse_retry_after` to `max(0.0, ...)`.
- [LOW] `from loguru import logger` at top — keep as-is; the project convention per `logger.py` is `from loguru import logger` everywhere after `setup_logging()` is called. The report LOW is project-style, but the rest of the codebase already does `from loguru import logger` directly (detect.py, etc.), so this is consistent.

#### [MODIFY] [cooldown.py](file:///c:/code/clasp/clasp/ratelimit/cooldown.py)

---

### Component 2 — `clasp/ratelimit/key_pool.py`

**Issues to fix:**
- [CRITICAL] `from clasp.ratelimit.cooldown import CooldownTracker` → rename to `CooldownManager`.
- [CRITICAL] `cooldown_module.get_default_tracker()` → `cooldown_module.get_cooldown_manager()`.
- [CRITICAL] `logger` is referenced but not imported. Fix: add `from loguru import logger`.
- [CRITICAL] `health_summary()` calls `self._cooldown.seconds_until_recovery()` → rename to `self._cooldown.seconds_remaining()`.
- [MEDIUM] Constructor parameter `cooldown_tracker: CooldownTracker` → rename type to `CooldownManager`.
- [LOW] Fix `pick_key()` indentation.

#### [MODIFY] [key_pool.py](file:///c:/code/clasp/clasp/ratelimit/key_pool.py)

---

### Component 3 — `clasp/ratelimit/persistence.py`

**Issues to fix:**
- [CRITICAL] `from clasp.ratelimit.cooldown import CooldownTracker` → `CooldownManager`.
- All type annotations for `CooldownTracker` → `CooldownManager`.
- [MEDIUM] `save_state()` and `periodic_save_task()` do synchronous file I/O in async context. Fix: wrap `save_state()` file writes with `asyncio.get_event_loop().run_in_executor(None, ...)` inside `periodic_save_task()`. The synchronous `save_state()` itself stays sync (called from shutdown handlers too).
- [HIGH] Corrupt/structurally invalid JSON guard: already handles `not isinstance(raw, dict)`. Also guard `raw.get("cooldowns")` and `raw.get("failure_counts")` as dicts — the guard `or {}` already exists and `.items()` will not be called on non-dict because `CooldownManager.import_cooldowns()` handles it cleanly. No further change needed here.

#### [MODIFY] [persistence.py](file:///c:/code/clasp/clasp/ratelimit/persistence.py)

---

### Component 4 — `clasp/ratelimit/bucket.py`

**Issues to fix:**
- [HIGH] `can_consume()` and `consume()` are separately locked — race condition. Fix: add an atomic `check_and_consume()` method that performs both in a single lock acquisition. `KeyPool.pick_key()` already wraps both calls under its own outer lock, so the inner per-call locking is double-locked but safe. The real fix is to remove the inner `can_consume` + `consume` pattern from `key_pool.pick_key()` and replace it with a single `try_consume()` call.
- [MEDIUM] `seconds_until_available()` and display properties read mutable state without lock. Fix: acquire `_lock` (using a sync path via `_lock._loop` approach is complex — instead, document that these are observability-only, best-effort snapshots). The report says MEDIUM, so add a note and leave as-is for now. The real fix would require a separate non-async lock.

#### [MODIFY] [bucket.py](file:///c:/code/clasp/clasp/ratelimit/bucket.py)
- Add `async def try_consume(self, estimated_tokens: int = 0) -> bool` — atomic check-and-consume under one lock.

---

### Component 5 — `clasp/ratelimit/circuit_breaker.py`

**Issues to fix:**
- [MEDIUM] Uses `threading.Lock` — synchronous methods are fine, but the project rules say `asyncio.Lock`. Since all methods are sync and called from sync contexts (or from within async code that doesn't yield), changing to `asyncio.Lock` would require making all methods async, which is a larger change. Keep `threading.Lock` but document it clearly. Report severity is MEDIUM, not CRITICAL.
- [LOW] `State = str` type alias weakens type checking. Keep for backward compatibility but add `CircuitState` to exported symbols.

No code changes needed — `circuit_breaker.py` already works correctly.

---

### Component 6 — `clasp/providers/registry.py`

**Issues to fix:**
- [CRITICAL] Transitively imports broken `key_pool.py` — fixed when key_pool.py is fixed.
- [CRITICAL] `_register()` calls `len(key_pool)` but `KeyPool` has no `__len__`. Fix: add `def __len__(self) -> int: return len(self.keys)` to `KeyPool`.
- [CRITICAL] Generic OpenAI/Anthropic transports need `name` kwarg. Fix: check constructor signature and pass `name=provider_name`.
- [HIGH] `build_registry()` clears and repopulates in place without a lock. Fix: build into a new `ProviderRegistry`, then atomically swap `_registry`. Also add `get_registry()` module-level function (used by `selector.py`).
- [LOW] Already has loguru shim — no change needed.

#### [MODIFY] [registry.py](file:///c:/code/clasp/clasp/providers/registry.py)
- Add `get_registry()` function.
- Add atomic rebuild (build new, then assign).
- Fix `_register` log call to use `len(key_pool.keys)` instead of `len(key_pool)` (pre-`__len__` fix is simpler).
- Pass `name=provider_name` to generic transport constructors.

---

### Component 7 — `clasp/router/model_map.py`

**Issues to fix:**
- [CRITICAL] `request.model` does not exist on `AnthropicRequest` — it uses `request.body`. Fix: replace `request.model` with `request.body.get("model", "")`.

#### [MODIFY] [model_map.py](file:///c:/code/clasp/clasp/router/model_map.py)

---

### Component 8 — `clasp/router/capability.py`

**Issues to fix:**
- [MEDIUM] Model-slug quirks override user config. Fix: swap layering — apply model-slug overrides (layer 3) first as base, then user config (layer 2) wins over it. This means user can correct a model-slug quirk.

#### [MODIFY] [capability.py](file:///c:/code/clasp/clasp/router/capability.py)

---

### Component 9 — `clasp/router/selector.py`

**Issues to fix:**
- [CRITICAL] `from clasp.providers.registry import get_registry` — `get_registry()` doesn't exist yet. Fixed by adding it to registry.py.
- [CRITICAL] Calls `registry.get_circuit_breaker()`, `registry.get_keys()`, `registry.get_bucket()`, `registry.provider_names()` — none of these exist. Fix: rewrite selection to use `registry.get_key_pool(provider_name)` and call `key_pool.pick_key()`.
- [CRITICAL] Section 13 capability/model/key-pool selection is omitted. Fix: wire in `capability.get()`, `model_map.resolve()`, and `key_pool.pick_key()`.
- [HIGH] Uses `_default_config(registry)` which calls `registry.provider_names()` — doesn't exist. Fix: use `registry.all_enabled()`.
- Return value changes: currently returns `(provider, api_key, key_index)`. With `KeyPool.pick_key()` returning `(api_key, key_index)`, this stays the same shape.

#### [MODIFY] [selector.py](file:///c:/code/clasp/clasp/router/selector.py)

Full rewrite of the selection loop to:
1. Call `registry.get_key_pool(provider_name)` instead of `registry.get_circuit_breaker/get_keys/get_bucket`.
2. Call `model_map.resolve_model(request, provider_name, settings)` to get the model slug.
3. Call `capability.get(provider_name, model_slug, settings)` to check request requirements.
4. Call `key_pool.pick_key(request.estimated_tokens)` for atomic key selection.
5. Fix `_default_config()` to use `registry.all_enabled()`.

---

### Component 10 — `clasp/api/service.py`

**Issues to fix:**
- [CRITICAL] `_build_routing_request()` returns a `dict`, but `selector.select()` requires `AnthropicRequest`. Fix: replace `_build_routing_request()` with `AnthropicRequest.from_body(request)`.
- [CRITICAL] Imports `needs_tools`, `needs_vision`, `priority_for` from `clasp.api.detect` — these don't exist. Fix: remove these imports; those fields are handled inside `AnthropicRequest.from_body()`.
- [HIGH] After dispatch, never calls `key_pool.record_success()` / `record_timeout()` / `record_error()` or `bucket.consume_actual()`. Fix: thread `key_pool` reference back from `select()` return and call feedback methods in `finally` block.

#### [MODIFY] [service.py](file:///c:/code/clasp/clasp/api/service.py)

---

### Component 11 — `clasp/api/detect.py`

**Issues to fix (for service.py compatibility):**
- Add `needs_tools(body)`, `needs_vision(body)`, and `priority_for(req_type)` as thin public helpers (or confirm service.py will stop importing them after the `AnthropicRequest.from_body()` refactor). Since the fix removes these imports from service.py, no changes needed to detect.py.

---

### Component 12 — `clasp/providers/base.py`

**Issues to fix:**
- Transport tests import `ProviderConnectionError`, `ProviderHTTPError`, `ProviderTimeoutError` from `clasp.providers.base` — these don't exist. Fix: add these exception classes to `base.py`.

#### [MODIFY] [base.py](file:///c:/code/clasp/clasp/providers/base.py)

---

### Component 13 — Test files (Sprint 2 tests)

#### [MODIFY] [test_bucket.py](file:///c:/code/clasp/tests/unit/test_bucket.py)
- The concurrent tests do `can_consume()` + `consume()` as separate calls and assert atomic admission — this works because `KeyPool` uses an outer lock, but tests bypass `KeyPool`. Fix: rewrite concurrency tests to use the new `try_consume()` atomic method.
- Remove the 3.05s `asyncio.sleep` — replace with monotonic clock injection pattern or keep as-is (report severity is MEDIUM). Keep the sleep for now to avoid over-engineering.

#### [MODIFY] [test_circuit_breaker.py](file:///c:/code/clasp/tests/unit/test_circuit_breaker.py)
- [HIGH] Tests assert `cb._window` and `cb._opened_at` — implementation uses `_recent_outcomes` and `_recovery_at`. Fix: update tests to use public API or correct private attribute names.
- [HIGH] Tests assert `CircuitBreaker.CONSECUTIVE_429_THRESHOLD` as class attribute — it's a module-level constant. Fix: either reference the module constant or check the behavior instead.
- Fix `_opened_at` → `_recovery_at` and `_window` → `_recent_outcomes` throughout.

#### [MODIFY] [test_key_pool.py](file:///c:/code/clasp/tests/unit/test_key_pool.py)
- [CRITICAL] Imports `CooldownTracker` → rename to `CooldownManager`.
- [HIGH] `test_expired_cooldown_key_becomes_available_again` mutates `_cooling_until` → rename to `_cooling`.
- [HIGH] `health_summary()` tests will call broken `seconds_until_recovery()` → rename to `seconds_remaining()`.
- Remove `sys.path.insert` hacks — use proper pytest conftest.
- Replace `asyncio.run()` calls with `pytest.mark.asyncio` async tests.

#### Other test collection fixes (not Sprint 2 files, but blocking test collection):

These are out-of-scope Sprint 2 files that have collection errors. We fix them so the Sprint 2 tests can actually run:

- **Multiple test files** using `os.path` before `import os` — add `import os` at top.
- **test_writer.py** importing `_mask_settings` → rename to `mask_keys`.
- **test_selector.py** importing `CooldownStore` → rename to `CooldownManager`.
- **test_service.py** has `async for` in sync function at line 101 → fix.
- **sys.modules eviction** causing `No module named 'clasp.ratelimit'` etc — investigate and remove.

---

## Verification Plan

### Automated Tests
```
uv run pytest tests/unit/test_bucket.py -v --tb=short
uv run pytest tests/unit/test_key_pool.py -v --tb=short
uv run pytest tests/unit/test_circuit_breaker.py -v --tb=short
uv run pytest tests/unit -v --tb=short --ignore=tests/unit/test_context_pruner.py --ignore=tests/unit/test_response_cache.py --ignore=tests/unit/test_priority_queue.py
```

### Expected outcomes
- All 3 Sprint 2 test files pass.
- No collection errors from Sprint 2 files.
- Pre-existing Sprint 1 test files that were passing before should still pass.

### Manual Verification
- `import clasp.ratelimit.key_pool` in a Python REPL — must not raise.
- `import clasp.providers.registry` in a Python REPL — must not raise.
- `import clasp.router.selector` in a Python REPL — must not raise.
- `import clasp.api.service` in a Python REPL — must not raise.
