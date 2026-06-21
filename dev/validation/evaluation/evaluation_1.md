# Test Evaluation — Sprint 1 Checkpoint
Date: 2026-06-20

## Summary
Unit tests: 0 passed / 0 failed / 0 skipped out of 0 (collection failed — 3 errors)
Integration tests: 0 passed / 0 failed / 0 skipped out of 0 (collection failed — 2 errors)

Both suites failed at the collection phase. No tests were executed in either run. The total of 508 unit tests and 19 integration tests were collected but 5 total import errors prevented any test from running.

## Failing tests

### tests/unit/test_priority_queue.py (collection error)
- Failure reason: `ImportError: cannot import name 'Priority' from 'clasp.queue.manager'` — the test imports `Priority, QueuedRequest, RequestQueue` but `clasp.queue.manager` does not export `Priority` (it exports only `QueueTimeoutError`, `QueuedRequest`, and `QueueManager`).
- Root cause hypothesis: test bug — the test was written against an expected API that was changed or never implemented. The `Priority` enum (INT_ERACTIVE=0, TOOL_USE=1, BACKGROUND=2) likely lives in `clasp.api.detect` or was planned but never added to `queue/manager.py`.
- Severity: HIGH

### tests/unit/test_server.py (collection error)
- Failure reason: `ImportError: cannot import name '_init_logging' from 'clasp.server'` — the test imports `_init_logging` from `server.py` but the function no longer exists (server.py uses lifespan-managed startup instead).
- Root cause hypothesis: test bug — the test was written against an older version of `server.py` that had an `_init_logging` helper, which was later removed when the startup flow moved to the lifespan context manager.
- Severity: HIGH

### tests/unit/test_writer.py (collection error)
- Failure reason: `ImportError: cannot import name '_mask_settings' from 'clasp.config.writer'` — the test imports `_mask_settings` but `writer.py` only exports `mask_keys` (the public API). The private `_mask_settings` function was likely renamed or moved to `internal/routes.py` where `_mask_settings` and `_mask_key` actually live.
- Root cause hypothesis: test bug — the test was written against a version of `writer.py` that had a private `_mask_settings` function. The function was moved to `internal/routes.py` during development but the test wasn't updated.
- Severity: HIGH

### tests/integration/test_key_roatation.py (collection error)
- Failure reason: `ImportError: cannot import name 'CooldownTracker' from 'clasp.ratelimit.cooldown'` — the integration test imports `CooldownTracker`, but `cooldown.py` only exports `CooldownManager`.
- Root cause hypothesis: test bug — the test uses an old class name. The class was renamed from `CooldownTracker` to `CooldownManager` during implementation.
- Severity: HIGH

### tests/integration/test_provider_chain.py (collection error)
- Failure reason: `ImportError: cannot import name 'CooldownTracker' from 'clasp.ratelimit.cooldown'` — same import error as test_key_roatation.py.
- Root cause hypothesis: same root cause — `CooldownTracker` was renamed to `CooldownManager`.
- Severity: HIGH

## Passing tests with weak coverage
N/A — no tests executed. However, based on code review of the test files that did collect:

- **tests/unit/test_hash.py** — tests basic hashing but doesn't verify that `stream` field exclusion works, or that fields in different order produce identical hashes.
- **tests/unit/test_message_converter.py** — 508 total collected suggests many sub-tests, but the `merge_system=True` edge case with malformed content (where the system prompt is silently dropped, noted in report_1.md) likely has no test coverage.

## Coverage gaps

The following Sprint 1 files from plan.md §20 have no corresponding test files:

- No dedicated test file for `message_converter.py` round-trip correctness (Anthropic → OpenAI → Anthropic identity). The `test_message_converter.py` exists but its content couldn't be evaluated since collection failed.
- No integration test that boots the FastAPI app, sends a real HTTP request to `/v1/messages`, and verifies the proxy pipeline end-to-end with a mocked upstream. `test_smoke.py` does basic import/settings checks only.
- `cmd_server.py` and `cmd_claude.py` are imported but `test_cmd_server.py` and `test_cmd_claude.py` were in the collected set — their content is unknown due to collection failure.
- No test for `ip_guard.py` middleware — `test_ip_guard.py` exists but its content couldn't be verified since it wasn't among the collected failures (it may or may not pass collection).
- `base.py` — `test_base_provider.py` exists but its content is unknown since it collected without error. Need to verify it tests the abstract method contract and the `stream()` → 429 absorption path.

## Flaky or suspicious passes
N/A — no tests executed. However, based on code review, the following tests would be suspicious if they pass:
- Any test that instantiates `OpenAIChatTransport` or `AnthropicMessagesTransport` — if they pass, the test must be providing the expected `BaseProvider.__init__` that doesn't exist in the implementation, meaning the test environment differs from production.

## Recommended next actions (prioritized)
1. **Fix 5 test collection errors first** — all are import mismatches. Update test file imports to match current module exports: `test_server.py` (remove `_init_logging`), `test_writer.py` (`_mask_settings` → import from `internal/routes.py` or use `mask_keys`), `test_priority_queue.py` (`Priority` → find correct location), `test_key_roatation.py` and `test_provider_chain.py` (`CooldownTracker` → `CooldownManager`).
2. **Fix `BaseProvider` constructor** before re-running transport tests — if the constructor issue is real (CRITICAL per report_1.md), transport tests will all fail. Fix the base class first, then re-run.
3. **After collection is fixed, run `uv run pytest tests/unit -v --tb=short`** to see the actual pass/fail counts. With 508 collected items, expect some real test failures given the transport constructor and `stream()` signature issues.
4. **Fix `server.py` wiring** then re-run integration tests — the integration suite depends on a full server bootstrap path that currently won't work due to missing registry build and unmounted routers.
5. **Add missing edge-case tests** for: `message_converter` merge_system with malformed content, `hash_request` field exclusion verification, proxy route auth failure with wrong API key, PID stale detection, and config watcher hot-reload.