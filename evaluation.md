# Test Evaluation — Sprint 6 Checkpoint
Date: 2026-06-22

## Summary
Unit tests: 407 passed / 68 failed / 0 skipped out of 475
Integration tests: 0 passed / 2 failed (during collection) / 0 skipped out of 2

## Failing tests

### tests/integration/test_key_roatation.py
- Failure reason: `ImportError: cannot import name 'CooldownTracker' from 'clasp.ratelimit.cooldown'`
- Root cause hypothesis: implementation bug — The class was renamed to `CooldownManager` in `clasp/ratelimit/cooldown.py`, but the integration tests were not updated to reflect this name change. Also, the file name contains a typo (`test_key_roatation.py` instead of `test_key_rotation.py`).
- Severity: HIGH

### tests/integration/test_provider_chain.py
- Failure reason: `ImportError: cannot import name 'CooldownTracker' from 'clasp.ratelimit.cooldown'`
- Root cause hypothesis: implementation bug — Same import error as above.
- Severity: HIGH

### tests/unit/test_writer.py::test_restore_keys
- Failure reason: `IndexError: list index out of range`
- Root cause hypothesis: implementation bug — The function `restore_redacted` assumes that the number of masked keys provided by the UI matches the original configuration exactly, causing out-of-bounds access when keys are added or removed.
- Severity: CRITICAL

### tests/unit/test_writer.py::test_mask_key_string_basic
- Failure reason: `AssertionError: assert 'shor-***hort' == '***'`
- Root cause hypothesis: implementation bug — The masking logic blindly slices strings assuming a minimum length, corrupting shorter keys and causing test assertions to fail.
- Severity: HIGH

### tests/unit/test_selector.py::TestModelMap
- Failure reason: `RuntimeWarning: coroutine 'TokenBucket.can_consume' was never awaited`
- Root cause hypothesis: implementation bug — The test (and potentially the implementation) calls an async method synchronously without awaiting it.
- Severity: HIGH

*(Note: 64 other unit tests are failing. Most are cascading failures from `clasp/config/writer.py` and `clasp/config/settings.py` issues).*

## Passing tests with weak coverage
- `tests/unit/test_proxy_routes.py` and `tests/unit/test_service.py` have basic coverage but lack tests verifying that mid-stream 429 errors fallback gracefully according to plan.md edge cases.

## Coverage gaps
- No coverage for the new `clasp/cli/cmd_claude.py` signal handling modifications implemented in the previous session. 

## Flaky or suspicious passes
- Not applicable for this sprint review, given the large number of hard failures that need to be addressed first.

## Recommended next actions (prioritized)
1. Fix `clasp/config/writer.py` `restore_redacted` out-of-bounds `IndexError` to stabilize settings modification.
2. Update the `CooldownTracker` import to `CooldownManager` in the integration tests.
3. Fix the `TokenBucket.can_consume` un-awaited coroutine issue.
