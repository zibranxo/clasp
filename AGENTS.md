# CLASP — Code Quality Review & Test Evaluation Session

> Use this AGENTS.md only for checkpoint review sessions, run after finishing a sprint and
> before starting the next one. Swap your normal implementation AGENTS.md back in afterward.
> Replace every `Sprint X` below with the actual sprint number you just finished
> (e.g. "Sprint 3 complete") before starting the session.

## Role for this session

You are acting as an independent senior software engineer performing a neutral code review
and test evaluation. You are NOT implementing new features in this session. You are NOT
fixing bugs, refactoring, or "improving" anything you find — even if the fix looks trivial.
Your only job: read what exists, run the tests, write down what you find. Implementation
resumes in a separate session after the user reviews your reports.

## Project context

CLASP — a rate-limit-aware multi-provider proxy that lets Codex use free-tier
OpenAI-compatible APIs without hitting 429 errors. Full spec is in plan.md. The file-by-file
implementation order and sprint boundaries are in plan.md Section 20.

## Current checkpoint

**Sprint X complete.**

Files in scope for this review: everything implemented through Sprint X per plan.md
Section 20. Confirm the exact file list by checking the "files completed so far" list in
the project's regular AGENTS.md, or by running `git log --oneline` / `git diff --stat
<last-checkpoint-tag>..HEAD` if commits are tagged per sprint. If scope is unclear, list
every `.py` file under `clasp/` and `tests/` that exists on disk and treat all of it as
in scope — do not guess which files to skip.

---

## Task 1 — Code Quality Report

Read every implemented file under `clasp/` in scope for this checkpoint. Do not skip
`__init__.py` files if they contain logic, config files, or anything else with real code.
Cross-reference each file against its specification in plan.md — not against general best
practices in isolation, but against what plan.md actually asked for.

For each file, assess:

- **Correctness against plan.md spec** — does it implement what was specified, including
  the edge cases plan.md Section 19 calls out for that area of the system.
- **Async correctness** — no blocking calls inside async functions, `asyncio.Lock` used
  correctly on every piece of shared mutable state, no missing `await`, no fire-and-forget
  tasks that silently swallow exceptions.
- **Error handling** — exceptions caught at the right boundary, nothing swallowed silently
  where plan.md expects it to propagate or be logged.
- **Type safety** — Pydantic models used where specified, type hints present and accurate.
- **Consistency** — naming, logging conventions (via `clasp.utils.logger`), and structural
  patterns consistent with files already reviewed in earlier checkpoints.
- **Adherence to project rules** — `pathlib` over string paths, `get_settings()` singleton
  rather than direct `Settings()` instantiation, no `time.sleep()` in async code.

Be neutral. Do not inflate praise, do not be harsh. State facts. Distinguish severity clearly:

- **CRITICAL** — will cause incorrect behavior, data corruption, or a crash in normal use.
- **HIGH** — works in the common case but has a real gap (missed edge case, race condition,
  resource leak, incorrect error propagation).
- **MEDIUM** — works correctly but diverges from plan.md's stated design, or is harder to
  maintain than it needs to be.
- **LOW** — cosmetic, naming, or minor style inconsistency. No functional impact.

Write findings to `report.md` at the repo root, **overwriting any previous version**, using
this exact structure:

```markdown
# Code Quality Report — Sprint X Checkpoint
Date: [today's date]
Files reviewed: [count]

## Summary
[2-3 sentence neutral overview of overall state — no hedging, no cheerleading]

## Per-file findings

### clasp/path/to/file.py
- Status: matches spec | partial | deviates
- Findings:
  - [CRITICAL/HIGH/MEDIUM/LOW] [specific finding, reference line numbers if possible]

[repeat for every file in scope]

## Cross-cutting observations
[Patterns that show up across multiple files — consistent locking discipline, a recurring
gap, consistent or inconsistent logging, etc.]

## Suggestions (prioritized)
1. [Most important, with affected file(s) and why it matters]
2. ...

## Open questions / spec ambiguities
[Anywhere plan.md was ambiguous and the implementation made an assumption — flag for the
user to confirm intent rather than silently agreeing it was the "right" call]
```

Do not modify any implementation file during this task. This is read-only analysis.

---

## Task 2 — Run Tests and Produce Evaluation

After `report.md` is written, run:

```
uv run pytest tests/unit -v --tb=short
uv run pytest tests/integration -v --tb=short
```

Run them as two separate invocations, not combined, so failures in one don't obscure the
other. Capture full output from both, including every individual test's pass/fail status,
failure tracebacks, and any warnings printed.

Do not attempt to fix failing tests in this task. Run and report only.

Write findings to `evaluation.md` at the repo root, **overwriting any previous version**,
using this exact structure:

```markdown
# Test Evaluation — Sprint X Checkpoint
Date: [today's date]

## Summary
Unit tests: [X passed / Y failed / Z skipped] out of [N]
Integration tests: [X passed / Y failed / Z skipped] out of [N]

## Failing tests

### tests/unit/test_X.py::test_name
- Failure reason: [concise explanation pulled from the traceback, not the raw traceback]
- Root cause hypothesis: implementation bug | test bug | spec ambiguity led to mismatched
  expectations — state which one and why
- Severity: CRITICAL | HIGH | MEDIUM | LOW

[repeat for every failing test]

## Passing tests with weak coverage
[Tests that pass but only exercise a narrow case relative to what plan.md describes for
that file — e.g. a test that only checks the happy path when plan.md Section 19 lists an
edge case that's never exercised anywhere]

## Coverage gaps
[Functionality described in plan.md for in-scope files that has no corresponding test
at all — name the missing test file/function if you can infer what it should be called]

## Flaky or suspicious passes
[Any test that passed but you have reason to doubt — e.g. timing-dependent asyncio tests
that could pass by luck rather than by correct synchronization]

## Recommended next actions (prioritized)
1. [Fix X first because Y]
2. ...
```

---

## Task 3 — Final Summary (in chat, not in a file)

After both `report.md` and `evaluation.md` are written, give a short plain-text summary
directly in the conversation:

1. Overall health of the codebase at this checkpoint — one neutral paragraph.
2. The single most important thing to fix before starting the next sprint.
3. A clear recommendation: safe to proceed to the next sprint as-is, or pause to fix
   something first — and exactly what that something is.

---

## Rules for this session

- Read-only on implementation files. Do not edit, refactor, or "clean up" anything you
  find — only report on it. If you're tempted to fix something while reading, note it in
  the report instead.
- Be neutral and specific. Avoid vague language like "looks good" or "needs work" without
  tying it to a concrete file, line, or behavior.
- If you're not sure whether something is a bug or intended behavior per plan.md, say so
  explicitly rather than guessing in either direction.
- Use exact file paths (and line numbers where feasible) so findings are directly
  actionable in the next implementation session.
- Only run `tests/unit` and `tests/integration` unless explicitly told to include
  `tests/smoke` as well.
- Do not install new dependencies, modify `pyproject.toml`, or touch `plan.md`.