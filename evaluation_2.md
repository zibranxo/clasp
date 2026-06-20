# Test Evaluation — Sprint 2 Checkpoint
Date: 2026-06-20

## Summary
Unit tests: 0 passed / 0 failed / 0 skipped, with 25 collection errors out of 210 collected items; no test body executed.
Integration tests: 0 passed / 0 failed / 0 skipped, with 6 collection errors and 4 collected items; no test body executed.

## Failing tests

No test reached execution. The entries below are collection failures reported by pytest.

### tests/unit/test_anthropic_transport.py (collection)
- Failure reason: `clasp.providers.base` does not export `ProviderConnectionError`, which `anthropic_transport.py` imports.
- Root cause hypothesis: implementation bug — the transport and base-provider exception interfaces are out of sync.
- Severity: HIGH

### tests/unit/test_base_provider.py (collection)
- Failure reason: `ProviderConnectionError` and related provider exception classes cannot be imported from `clasp.providers.base`.
- Root cause hypothesis: implementation bug — the base module lacks the exception API used by production transports and tests.
- Severity: HIGH

### tests/unit/test_bucket.py (collection)
- Failure reason: pytest reported `No module named 'clasp.ratelimit'` even though that package exists on disk.
- Root cause hypothesis: test bug — other test modules remove/recreate `clasp` in `sys.modules` and alter `sys.path` during collection, leaving the shared interpreter with an inconsistent package root.
- Severity: HIGH

### tests/unit/test_circuit_breaker.py (collection)
- Failure reason: pytest reported `No module named 'clasp.ratelimit'` even though that package exists on disk.
- Root cause hypothesis: test bug — suite-level `sys.modules`/`sys.path` manipulation corrupts package discovery during collection.
- Severity: HIGH

### tests/unit/test_cli_main.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_cmd_claude.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_cmd_server.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_context_pruner.py (collection)
- Failure reason: pytest reported `No module named 'clasp.optimizer'` even though the package and `context_pruner.py` exist on disk.
- Root cause hypothesis: test bug — collection-time package eviction/path manipulation makes existing subpackages undiscoverable.
- Severity: HIGH

### tests/unit/test_detect.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_internal_routes.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_ip_guard.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_key_pool.py (collection)
- Failure reason: `clasp.ratelimit.cooldown` does not export `CooldownTracker`.
- Root cause hypothesis: implementation bug — `key_pool.py`, `persistence.py`, and this test expect `CooldownTracker`, while the cooldown implementation defines `CooldownManager`.
- Severity: CRITICAL

### tests/unit/test_nvidia_nim.py (collection)
- Failure reason: importing the OpenAI transport fails because `ProviderConnectionError` is absent from `clasp.providers.base`.
- Root cause hypothesis: implementation bug — the provider exception interfaces are inconsistent.
- Severity: HIGH

### tests/unit/test_openai_transport.py (collection)
- Failure reason: `ProviderHTTPError` and `ProviderTimeoutError` cannot be imported from `clasp.providers.base`.
- Root cause hypothesis: implementation bug — the transport relies on exception classes absent from its base module.
- Severity: HIGH

### tests/unit/test_priority_queue.py (collection)
- Failure reason: pytest reported `No module named 'clasp.queue'` even though the package exists on disk.
- Root cause hypothesis: test bug — collection-time package/path mutation prevents discovery of existing subpackages.
- Severity: HIGH

### tests/unit/test_provider_catalog.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_proxy_routes.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_registry.py (collection)
- Failure reason: registry import reaches `openai_transport.py`, which cannot import `ProviderConnectionError` from the base provider module.
- Root cause hypothesis: implementation bug — provider/base exception APIs are inconsistent; additional Sprint 2 key-pool import errors remain behind this first error.
- Severity: CRITICAL

### tests/unit/test_response_cache.py (collection)
- Failure reason: pytest reported `No module named 'clasp.cache'` even though the package exists on disk.
- Root cause hypothesis: test bug — collection-time package/path mutation prevents discovery of existing subpackages.
- Severity: HIGH

### tests/unit/test_selector.py (collection)
- Failure reason: the test imports nonexistent `CooldownStore` from `clasp.ratelimit.cooldown`.
- Root cause hypothesis: test bug — the test targets a stale cooldown interface; the implementation currently exposes `CooldownManager`.
- Severity: HIGH

### tests/unit/test_server.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_service.py (collection)
- Failure reason: Python raises `SyntaxError: 'async for' outside async function` at line 101.
- Root cause hypothesis: test bug — asynchronous syntax is placed in a synchronous test function.
- Severity: HIGH

### tests/unit/test_ui_routes.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_watcher.py (collection)
- Failure reason: the module uses `os.path` before importing `os`.
- Root cause hypothesis: test bug — import order in the test file is invalid.
- Severity: HIGH

### tests/unit/test_writer.py (collection)
- Failure reason: the test imports private helper `_mask_settings`, which `clasp.config.writer` does not define.
- Root cause hypothesis: test bug — the test targets a stale private API; the current module exposes `mask_keys()` and `restore_keys()`.
- Severity: MEDIUM

### tests/integration/test_absorber.py (collection)
- Failure reason: pytest reported `No module named 'clasp.api'` even though the package exists on disk.
- Root cause hypothesis: test bug — integration collection also removes `clasp` from `sys.modules` and rewrites `sys.path`, producing inconsistent package discovery.
- Severity: HIGH

### tests/integration/test_key_roatation.py (collection)
- Failure reason: registry import fails because `ProviderConnectionError` is absent from `clasp.providers.base`.
- Root cause hypothesis: implementation bug — provider/base exception APIs are inconsistent; the misspelled filename is non-functional but should also be corrected later.
- Severity: CRITICAL

### tests/integration/test_provider_chain.py (collection)
- Failure reason: registry import fails because `ProviderConnectionError` is absent from `clasp.providers.base`.
- Root cause hypothesis: implementation bug — provider/base exception APIs are inconsistent, blocking provider-chain integration coverage.
- Severity: CRITICAL

### tests/integration/test_proxy_routes_integration.py (collection)
- Failure reason: `clasp.api.service` imports nonexistent `needs_tools`, `needs_vision`, and `priority_for` from `clasp.api.detect`.
- Root cause hypothesis: implementation bug — the scoped Sprint 2 service update targets a detect API that does not exist.
- Severity: CRITICAL

### tests/integration/test_server_integration.py (collection)
- Failure reason: server import reaches `clasp.api.service`, which imports nonexistent detect helpers.
- Root cause hypothesis: implementation bug — service/detect interfaces are inconsistent and prevent app startup.
- Severity: CRITICAL

### tests/integration/test_service_flow_integration.py (collection)
- Failure reason: importing `clasp.api.service` fails because the detect helpers it imports do not exist.
- Root cause hypothesis: implementation bug — the service integration is written against an incompatible detect API.
- Severity: CRITICAL

## Passing tests with weak coverage
No tests passed because both suites stopped during collection. Passing-test quality cannot be evaluated from this run.

## Coverage gaps
Execution coverage is effectively zero for this checkpoint because no test body ran. Static review also found no dedicated Sprint 2 tests for `cooldown.py`, `persistence.py`, or `capability.py`; missing cases include malformed/negative `Retry-After`, overlapping cooldown timers, restart expiry, corrupt persisted JSON, daily reset, and capability-override precedence. The existing bucket tests do not deterministically prove atomic check-and-consume behavior, and the key-pool tests do not reach the missing-logger soft-threshold branch. Registry construction for generic transports, real `AnthropicRequest` model resolution, full selector capability/key-pool checks, and service-to-selector/outcome-feedback wiring need runnable integration coverage.

## Flaky or suspicious passes
There were no passes. If collection is restored, `test_partial_refill_after_waiting` uses a real 3.05-second sleep, and the key-pool suite repeatedly creates/closes event loops around the same asyncio objects; both patterns merit stabilization.

## Recommended next actions (prioritized)
1. Restore deterministic test collection first: remove module-level `sys.modules`/`sys.path` mutation, fix test import order and syntax, and align stale test imports so the suite can produce meaningful pass/fail results.
2. Reconcile the production interfaces blocking startup: provider exception classes, cooldown manager naming, key-pool/registry APIs, and service/detect helper names.
3. Implement the Section 13 selector path with the real `AnthropicRequest`, model mapping, capability checks, and `KeyPool.pick_key()`, then rerun both suites separately.
4. Add the missing cooldown, persistence, capability, registry-construction, and service outcome-feedback tests before using coverage results to approve the checkpoint.
