# Project Context

## Repository Locations
- **Target Repository (clasp)**: `c:\code\clasp`
  - Core files under `clasp/` (contains API, cache, CLI, config, routing, server, etc.)
  - Tests under `tests/`
- **Reference Repository (free-claude-code-main)**: `c:\code\clasp\free-claude-code-main`
  - Reference implementation of Claude Code proxy allowing other models/providers.

## Objectives
- Port the dynamic model listing and routing features from `free-claude-code-main` into `clasp`.
- Expose all provider models via `GET /v1/models` so they display under Claude Code's `/model` command.
- Preserve existing Web UI routing architecture.
- Maintain minimal latency and high performance.

## Key Constraints
- Pure dispatch-only orchestrator (never modify code directly).
- Working directory: `c:\code\clasp\.agents\orchestrator`.
- All subagents must run in their respective folders under `.agents/`.
- No reuse of subagents after handoff.
- Mandatory Forensic Auditor check.
