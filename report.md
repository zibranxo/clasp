# Code Quality Report — Sprint 6 Checkpoint
Date: 2026-06-22
Files reviewed: 73

## Summary
The codebase has successfully implemented all core features through the Web UI (Sprint 6). However, recent modifications to configuration and key handling logic have introduced several regressions. Additionally, there are structural issues in test integration files and un-awaited coroutines indicative of async correctness gaps.

## Per-file findings

### clasp/config/writer.py
- Status: partial
- Findings:
  - [CRITICAL] Key masking logic throws `IndexError: list index out of range` and `AssertionError` for short keys and missing originals. This impacts configuration saves from the UI.
  - [HIGH] The mask generation assumes a fixed length and format which breaks on edge case API keys.

### clasp/ratelimit/cooldown.py
- Status: matches spec
- Findings:
  - [LOW] Renamed `CooldownTracker` to `CooldownManager` but failed to update integration tests accordingly, causing test collection errors.

### clasp/ratelimit/bucket.py
- Status: partial
- Findings:
  - [HIGH] `TokenBucket.can_consume` is an async function (coroutine) but is called synchronously without `await` in some places (e.g., in `tests/unit/test_selector.py::TestModelMap`), causing `RuntimeWarning: coroutine was never awaited`.

### tests/integration/test_key_roatation.py
- Status: deviates
- Findings:
  - [LOW] Typo in filename: `test_key_roatation.py` instead of `test_key_rotation.py`.

### clasp/server.py
- Status: matches spec
- Findings:
  - [MEDIUM] `build_selector_config()` is hardcoded to only enable `nvidia_nim` as a placeholder. It should be dynamically using `clasp.config.settings.get_settings()` as per the TODO.

## Cross-cutting observations
- **Asyncio Discipline:** Mixing synchronous and asynchronous logic has resulted in un-awaited coroutine warnings which will cause unexpected behavior at runtime if a rate limit bucket check passes silently instead of returning a boolean.
- **Test Integrity:** The unit tests have 68 failures mainly cascading from `clasp.config.settings` and `clasp.config.writer` regressions. The integration tests cannot be collected due to import errors.

## Suggestions (prioritized)
1. Fix the `clasp/config/writer.py` key masking logic to properly handle lists of different sizes and short string keys to resolve the 68 failing unit tests.
2. Fix the `ImportError` in `tests/integration/` by updating `CooldownTracker` to `CooldownManager` to unblock integration testing.
3. Fix the un-awaited `TokenBucket.can_consume` coroutine in rate limit checks.

## Open questions / spec ambiguities
- Plan.md Section 19 mentions restoring original keys based on the `***` mask pattern, but how should the system handle when a user manually modifies the masked string or changes the order of keys in the UI? The `IndexError` indicates the UI and backend arrays are out of sync.
