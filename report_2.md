# Code Quality Report — Sprint 2 Checkpoint
Date: 2026-06-20
Files reviewed: 13

## Summary
The token-bucket and circuit-breaker algorithms largely reflect the state rules in `plan.md`, but the Sprint 2 integration is not runnable as a coherent subsystem. Incompatible cooldown APIs, stale selector/registry interfaces, and a dict-versus-`AnthropicRequest` mismatch prevent normal registry construction and request selection; concurrency and restart behavior also have material gaps.

## Per-file findings

### clasp/ratelimit/bucket.py
- Status: partial
- Findings:
  - [HIGH] `can_consume()` and `consume()` are separately locked operations (lines 100-131), so direct concurrent callers can all pass admission before any deduction occurs. This does not provide the no-over-admission guarantee required by the Section 18 concurrent-consume case unless every caller adds a separate outer lock, as `KeyPool` currently does.
  - [MEDIUM] `seconds_until_available()` and the display properties read mutable token state without acquiring `_lock` or applying lazy refill (lines 144-171). Their results can be stale relative to concurrent consumption/refill.
  - [LOW] The module imports `loguru` directly (lines 45-56) rather than using the project logging entry point in `clasp.utils.logger`.

### clasp/ratelimit/circuit_breaker.py
- Status: partial
- Findings:
  - [MEDIUM] Shared breaker state is guarded by `threading.Lock` (lines 40, 80-81), not `asyncio.Lock` as required by the session's async-correctness rule. The methods are synchronous and short, so this is primarily a design divergence rather than an observed deadlock.
  - [LOW] The public `state` type is declared as plain `str` (line 55) while the module also defines `CircuitState`; this weakens type checking and has already allowed tests and implementation to diverge on public/internal shape.
  - [LOW] The module imports `loguru` directly (line 43) rather than using `clasp.utils.logger`.

### clasp/ratelimit/cooldown.py
- Status: partial
- Findings:
  - [HIGH] Repeated 429s schedule independent callbacks, and `_re_enable()` unconditionally removes the key (lines 63-71 and 170-176). An older, shorter timer can therefore clear a newer cooldown early.
  - [HIGH] `import_cooldowns()` restores entries without scheduling callbacks (lines 112-123), while `is_cooling()` checks only dictionary membership and never expires timestamps lazily (lines 81-83). A cooldown restored after restart remains active indefinitely, contrary to the persistence requirement.
  - [HIGH] `_cooling` and `_failure_counts` are shared mutable dictionaries with no lock (lines 36-39 and all mutators), contrary to the stated locking discipline.
  - [MEDIUM] When no event loop is running, `on_429()` silently skips re-enable scheduling (lines 67-71); because `is_cooling()` does not compare the timestamp, that key can remain cooling indefinitely.
  - [MEDIUM] Numeric negative `Retry-After` values are accepted unchanged (lines 53-55 and 147-150), producing a negative returned wait rather than clamping to zero.
  - [LOW] The module imports `loguru` directly (line 18) rather than using `clasp.utils.logger`.

### clasp/ratelimit/persistence.py
- Status: deviates
- Findings:
  - [CRITICAL] The module imports `CooldownTracker` at line 29, but `clasp/ratelimit/cooldown.py` defines only `CooldownManager`. Importing this module fails before save/load functionality can be used.
  - [HIGH] Structurally invalid but syntactically valid JSON is not contained: values such as `"cooldowns": []` reach `.items()` in the tracker import methods (lines 106-107) and raise, contradicting the documented never-raise startup behavior.
  - [MEDIUM] `periodic_save_task()` performs directory creation and file writes synchronously inside an async task (lines 148-155), blocking the event loop during persistence.
  - [MEDIUM] The file persists cooldowns, failure counts, and optional daily counters only (lines 44-50); token-bucket balances and circuit-breaker state do not survive restarts even though P7 broadly calls for rate-limit state to survive. The narrower Section 10 text is ambiguous on whether those additional states are required.
  - [LOW] The module imports `loguru` directly (line 27) rather than using `clasp.utils.logger`.

### clasp/ratelimit/key_pool.py
- Status: deviates
- Findings:
  - [CRITICAL] Line 23 imports nonexistent `CooldownTracker`, and line 53 calls nonexistent `get_default_tracker()`; `cooldown.py` exposes `CooldownManager` and `get_cooldown_manager()` instead. The module cannot import, so key rotation and every module importing `KeyPool` fail at collection/startup.
  - [CRITICAL] `logger` is not imported, but the normal soft-threshold path calls `logger.warning()` at lines 80-88. Once a key reaches the configured threshold, selection raises `NameError` rather than returning the key.
  - [CRITICAL] `health_summary()` calls nonexistent `seconds_until_recovery()` at line 131; the cooldown implementation exposes `seconds_remaining()`, so health reporting raises `AttributeError`.
  - [MEDIUM] `health_summary()` reads bucket and cooldown state synchronously without either component's lock (lines 115-135), so the snapshot is not concurrency-consistent.
  - [LOW] The indentation of `pick_key()`'s body (lines 62-91) is inconsistent with the rest of the class and obscures the actual block structure, though it remains syntactically valid.

### clasp/providers/registry.py
- Status: deviates
- Findings:
  - [CRITICAL] Importing the registry transitively imports the broken `KeyPool` module at line 94, preventing registry startup.
  - [CRITICAL] `_register()` calls `len(key_pool)` at line 216, but `KeyPool` has no `__len__`; registration raises `TypeError`, which `build_registry()` catches and converts into a skipped provider (lines 329-345). Even after the cooldown import mismatch is resolved, the registry remains empty.
  - [CRITICAL] Generic OpenAI and Anthropic transport constructors require both `name` and `base_url`, but `build_registry()` supplies only `base_url` at lines 327-336. All enabled non-NVIDIA providers therefore fail construction and are silently skipped, defeating the multi-provider chain.
  - [HIGH] `build_registry()` clears and repopulates the process-wide registry in place (lines 282-337) without a lock. Concurrent selectors can observe an empty or partially rebuilt registry; the `rebuild()` docstring's atomic-replacement claim (lines 370-379) does not match the implementation.
  - [LOW] The module imports `loguru` directly (lines 67-87) rather than using `clasp.utils.logger`.

### clasp/router/capability.py
- Status: partial
- Findings:
  - [MEDIUM] Hard-coded model quirks override explicit user configuration because model overrides are applied after settings overrides (lines 169-190). `plan.md` says capabilities are built from catalog plus user overrides but does not establish that built-in model rules should take precedence over a user's explicit correction.
  - [MEDIUM] Model overrides are keyed only by slug substring, not `(provider, model)` (lines 102-120), so a matching slug hosted by a different provider receives the same override even when provider capabilities differ.
  - [LOW] The module imports `loguru` directly (lines 44-58) rather than using `clasp.utils.logger`.

### clasp/router/model_map.py
- Status: partial
- Findings:
  - [CRITICAL] The real `clasp.router.types.AnthropicRequest` stores the raw model under `request.body`; it has no `request.model` attribute. Lines 134 and 159 therefore raise `AttributeError` during normal tier resolution.
  - [MEDIUM] `_as_dict()` is annotated to return `dict[str, str]` but returns Pydantic dumps containing nullable fields without filtering (lines 72-90). The implementation relies on truthiness later rather than preserving the stated type.
  - [LOW] The module imports `loguru` directly (lines 48-59) rather than using `clasp.utils.logger`.

### clasp/router/selector.py
- Status: deviates
- Findings:
  - [CRITICAL] The default path imports nonexistent `get_registry()` from `clasp.providers.registry` (lines 73-76). Production calls that do not inject a registry fail immediately.
  - [CRITICAL] The selector calls nonexistent registry methods `get_circuit_breaker()`, `get_keys()`, `get_bucket()`, and `provider_names()` (lines 112, 122, 136, and 170). The Sprint 2 registry exposes `get_key_pool()`/`all_enabled()` instead.
  - [CRITICAL] The required Sprint 2 capability/model/key-pool selection is explicitly omitted (lines 107-144). The implementation assumes every provider is capable, selects only key index 0, and does not use `model_map.resolve()`, `capability.get()`, or `KeyPool.pick_key()` as Section 13 specifies.
  - [HIGH] With no injected `config`, selection builds a permissive configuration from registry contents (lines 77-80 and 156-174) rather than using `get_settings()`. Real enabled flags, provider-chain order, and by-type overrides can therefore be ignored.
  - [LOW] The module imports `loguru` directly (line 25) rather than using `clasp.utils.logger`.

### clasp/api/service.py
- Status: deviates
- Findings:
  - [CRITICAL] `_build_routing_request()` returns a dictionary (lines 320-337), which is passed to `selector.select()` at line 132. The selector requires `AnthropicRequest` and immediately accesses `request.type`; the normal service path therefore emits a routing-failed error instead of selecting a provider.
  - [HIGH] Successful and failed provider outcomes never call the selected `KeyPool`'s `record_success()`, `record_timeout()`, or `record_error()`, and actual token usage never reaches `TokenBucket.consume_actual()` (lines 185-235). Circuit-breaker error-rate state and TPM reconciliation therefore cannot reflect real dispatch outcomes.
  - [MEDIUM] Broad exception handlers include raw exception strings in client-visible errors (lines 122-145, 167-183, and 189-211), exposing internal details and collapsing classification, routing, optimization, and upstream failures into generic server errors.
  - [LOW] The module imports `loguru` directly (lines 41-45) rather than using `clasp.utils.logger`.

### tests/unit/test_bucket.py
- Status: partial
- Findings:
  - [HIGH] The concurrency tests perform `can_consume()` and `consume()` as two separate awaited calls (lines 156-163 and 185-192), but assert atomic admission. They can pass under favorable scheduling while leaving the check-then-consume race in the public API unproven.
  - [MEDIUM] `test_partial_refill_after_waiting` uses a real 3.05-second sleep (lines 109-130), making the suite slower and timing-dependent when monotonic time could be controlled.
  - [LOW] Tests directly mutate `rpm_tokens` in several cases, which verifies downstream behavior but bypasses the public API and refill timestamp invariants.

### tests/unit/test_key_pool.py
- Status: deviates
- Findings:
  - [CRITICAL] The test imports nonexistent `CooldownTracker` at line 37, so the entire module fails during collection and none of the planned key-pool cases run.
  - [HIGH] `test_expired_cooldown_key_becomes_available_again` mutates nonexistent `_cooling_until` at line 237; the implementation uses `_cooling`. The intended expiry behavior is therefore neither executable nor tested through a public interface.
  - [HIGH] Health-summary tests call the broken production method but do not account for its nonexistent `seconds_until_recovery()` dependency (lines 248-276); once collection is restored, these tests will error rather than isolate expected summary behavior.
  - [MEDIUM] Tests repeatedly call `asyncio.run()` against the same `KeyPool`/`asyncio.Lock` (lines 41-42 and throughout) instead of using the configured pytest asyncio loop, which risks loop-affinity failures and closes scheduled cooldown callbacks after each call.
  - [MEDIUM] No test drives a key far enough to execute the soft-threshold warning branch, so the missing `logger` import is uncovered.

### tests/unit/test_circuit_breaker.py
- Status: deviates
- Findings:
  - [HIGH] Tests assert nonexistent private attributes `_window` and `_opened_at` (lines 131, 146, 162, 167, 263, and 278); the implementation uses `_recent_outcomes` and `_recovery_at`. These failures test stale implementation details rather than the Section 18 public state-machine behavior.
  - [HIGH] Lines 241-242 expect threshold constants as `CircuitBreaker` class attributes, but the implementation exposes module-level constants. The spec does not require either location, so these assertions create test/implementation coupling unrelated to behavior.
  - [MEDIUM] Several recovery tests force internal timestamps instead of controlling time through a clock abstraction or observing the public cooldown behavior, making the suite brittle to valid internal refactoring.

## Cross-cutting observations
The Sprint 2 modules do not share one stable set of interfaces: `CooldownTracker` versus `CooldownManager`, `get_default_tracker()` versus `get_cooldown_manager()`, `seconds_until_recovery()` versus `seconds_remaining()`, and the selector's old registry API versus the registry's new `KeyPool` API. Logging is consistently structured but consistently bypasses `clasp.utils.logger`. Locking is present in the bucket, key pool, and circuit breaker, but compound admission, cooldown state, registry rebuilds, and synchronous health snapshots are not consistently protected.

## Suggestions (prioritized)
1. Reconcile the cooldown, key-pool, and registry interfaces in `clasp/ratelimit/cooldown.py`, `clasp/ratelimit/key_pool.py`, `clasp/ratelimit/persistence.py`, and `clasp/providers/registry.py`; fix the missing logger and unsupported `len(key_pool)` call because these currently prevent imports and all provider registration.
2. Replace the stale selection path in `clasp/router/selector.py` with the Section 13 `model_map`/`capability`/`KeyPool` flow, and pass a real `AnthropicRequest` from `clasp/api/service.py`.
3. Correct generic provider construction and make registry rebuild publication atomic so non-NVIDIA failover and hot reload cannot silently produce an empty/partial registry.
4. Make cooldown expiry generation-aware or timestamp-aware, reschedule imported cooldowns, and protect shared cooldown state so repeat 429s and restarts preserve the intended recovery time.
5. Provide an atomic bucket reservation operation (or make `KeyPool.pick_key()` the only supported admission API), then update the concurrency test to exercise that atomic boundary deterministically.
6. Align the Sprint 2 tests with public APIs and add focused tests for cooldown parsing/repeated timers, persistence restart/corrupt data, registry construction, capability filtering, model resolution with the real request type, selector integration, and service-to-selector wiring.

## Open questions / spec ambiguities
`plan.md` Section 10 explicitly names persisted cooldowns and daily counters, while P7 says rate-limit state generally survives restarts; it is unclear whether bucket balances and circuit-breaker state must also be persisted in Sprint 2. The plan also does not state whether explicit user capability overrides should win over built-in model quirks, nor how one tier-to-provider mapping is intended to support cross-provider failover. Finally, Section 19 requires Groq TPM accounting per `(provider, model)`, while the Sprint 2 `KeyPool` creates one TPM bucket per key; the intended ownership of per-model TPM state needs confirmation.
