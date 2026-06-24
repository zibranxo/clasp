## 2026-06-24T05:32:20Z
You are acting as an independent senior software engineer performing a neutral code review and test evaluation for Sprint 3 Checkpoint.
Your working directory is `c:\code\clasp\.agents\worker_review`.

Please follow these tasks exactly as specified in `c:\code\clasp\AGENTS.md`:

Task 1 — Code Quality Report
Read every implemented file under `clasp/` in scope for this checkpoint. Files in scope are everything implemented through Sprint 3 (which includes Sprint 1, 2, and 3) per `plan.md` Section 20, as well as V2 Milestones 1, 2, 3 files:
- clasp/utils/logger.py
- clasp/utils/pid.py
- clasp/utils/hash.py
- clasp/utils/ip_guard.py
- clasp/config/provider_catalog.py
- clasp/config/settings.py
- clasp/config/writer.py
- clasp/config/watcher.py
- clasp/providers/common/token_counter.py
- clasp/providers/common/error_mapper.py
- clasp/providers/common/message_converter.py
- clasp/providers/common/sse_builder.py
- clasp/providers/base.py
- clasp/providers/openai_transport.py
- clasp/providers/anthropic_transport.py
- clasp/providers/nvidia_nim.py
- clasp/providers/registry.py
- clasp/api/optimize.py
- clasp/api/detect.py
- clasp/api/service.py
- clasp/api/proxy_routes.py
- clasp/ui/routes.py
- clasp/internal/routes.py
- clasp/server.py
- clasp/cli/cmd_server.py
- clasp/cli/cmd_claude.py
- clasp/cli/main.py
- clasp/ratelimit/bucket.py
- clasp/ratelimit/circuit_breaker.py
- clasp/ratelimit/cooldown.py
- clasp/ratelimit/persistence.py
- clasp/ratelimit/key_pool.py
- clasp/router/capability.py
- clasp/router/model_map.py
- clasp/router/selector.py
- clasp/queue/sse_hold.py
- clasp/queue/manager.py
- clasp/queue/absorber.py
- clasp/providers/deepseek.py
- clasp/core/openai_responses/ (all files)

Cross-reference each file against its specification in `plan.md` (and V2 spec / handoff reports if relevant) — not against general best practices in isolation, but against what was actually asked for.
Assess correctness, async correctness, error handling, type safety, consistency, and adherence to project rules.
Write findings to `c:\code\clasp\report.md`, overwriting any previous version, using the exact structure specified in `AGENTS.md`.

Task 2 — Run Tests and Produce Evaluation
Run:
```
uv run pytest tests/unit -v --tb=short
uv run pytest tests/integration -v --tb=short
```
Run them as two separate invocations. Capture full output from both, including individual test pass/fail status, tracebacks, and warnings.
Write findings to `c:\code\clasp\evaluation.md`, overwriting any previous version, using the exact structure specified in `AGENTS.md`.

Report your completion back to the parent agent with details of the run and the generated files.
Do not modify any implementation files. This is read-only analysis.
