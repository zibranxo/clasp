# Code Quality Report — Sprint 3 Checkpoint
Date: 2026-06-20
Files reviewed: 7

## Summary
The Sprint 3 implementation successfully adds the 429 absorber and priority queue logic, matching the spec's resilience requirements. The code exhibits strong async correctness, particularly around background task lifecycle and queueing, but suffers from a type mismatch in chunk encoding and completely omits one required test file.

## Per-file findings

### clasp/queue/sse_hold.py
- Status: matches spec
- Findings:
  - [LOW] Yields mixed types (its own keep-alive and error events are `str`, but the chunks from `future.result()` might be `bytes` due to `manager.py`), despite the type hint stating `AsyncGenerator[str, None]`.

### clasp/queue/manager.py
- Status: partial
- Findings:
  - [MEDIUM] Line 116-118 encodes string chunks to `bytes` before appending to `chunks` list for the future. `sse_hold.py` then yields these `bytes`, mixing them with its own `str` keepalives and violating its `AsyncGenerator[str, None]` return type.

### clasp/queue/absorber.py
- Status: matches spec
- Findings:
  - None.

### clasp/providers/base.py
- Status: matches spec
- Findings:
  - None.

### clasp/server.py
- Status: matches spec
- Findings:
  - None.

### tests/integration/test_absorber.py
- Status: matches spec
- Findings:
  - None.

### tests/integration/test_priority_queue.py
- Status: deviates
- Findings:
  - [CRITICAL] File is entirely missing from the repository, despite being explicitly listed in plan.md Section 20 for Sprint 3.

## Cross-cutting observations
The implementation maintains consistent logging using `loguru` and successfully isolates test environments from global singletons by passing collaborators (`config`, `registry`, `cooldown_mgr`, `queue_mgr`) into functions like `select()` and `on_upstream_429()`.

## Suggestions (prioritized)
1. Implement `tests/integration/test_priority_queue.py` to ensure the priority levels (`INTERACTIVE`, `TOOL_USE`, `BACKGROUND`) are actually respected during queue drain.
2. Fix the chunk type mismatch in `clasp/queue/manager.py` by either storing `str` chunks in the future (removing `.encode()`), or updating `sse_hold.py` to properly handle and yield `bytes` consistently (e.g. converting keepalives to `bytes`).

## Open questions / spec ambiguities
The spec is slightly ambiguous on whether the proxy should yield pure `str` or `bytes` to the FastAPI `StreamingResponse`. While Starlette handles both, mixing them in a single stream (str keepalives + bytes chunks) can lead to subtle bugs depending on the client parser.
