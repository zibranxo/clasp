## 2026-06-23T14:59:50Z
You are a Reviewer agent. Your working directory is c:\code\clasp\.agents\reviewer_m4.
Your task is to review the correctness, style, performance, and architecture of the ported dynamic model selector (R2).

Check the modified files list in `c:\code\clasp\.agents\worker_m3\changes.md` and examine the code:
1. `clasp/providers/base.py`, `clasp/providers/registry.py` (caching, background refresh task)
2. `clasp/server.py` (lifespan startup integration)
3. `clasp/api/optimize.py` (GET /v1/models prefixed list construction)
4. `clasp/router/types.py`, `clasp/router/model_map.py`, `clasp/router/selector.py` (gateway model decoding, resolved slug, restricted candidate providers)
5. `clasp/api/service.py` (thinking configuration block pruning)

Verify:
- Correctness against R2 specifications.
- Async safety: no blocking calls inside async functions, correct locks on shared mutable state.
- Type safety: no typing violations.
- Error handling: exceptions caught at right boundary.
- Web UI routing preservation: /internal/* routes remain untouched.
- Run `uv run pytest tests/unit -v --tb=short` and `uv run pytest tests/integration -v --tb=short` to verify.

Save your review report in `c:\code\clasp\.agents\reviewer_m4\review.md` and notify parent orchestrator via send_message to 510a5845-3526-49f9-a5c8-42812af32309 when complete.
