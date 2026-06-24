# Project: Clasp Dynamic Model Selector Port

## Architecture
`clasp` acts as an OpenAI/Anthropic-compatible API proxy for Claude Code. It catches requests, manages rate limits, caches responses, and forwards calls to various providers.
This project extends `clasp` to support dynamic model discovery from all configured providers, exposing them via the `/model` selector in Claude Code (using `GET /v1/models`), and routing requests for these models appropriately, while preserving the Web UI routing architecture and optimizing latency.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Analysis & Feature Mapping | Analyze `free-claude-code-main` features and providers, map to `clasp` codebase, define mapping report. | None | DONE (27eaf559) |
| M2 | E2E Test Suite Design | Create E2E test infrastructure and write Tier 1-4 tests for models endpoint and routing. | None | DONE (a367706d) |
| M3 | Core Porting & Implementation | Implement model selection listing in `GET /v1/models` and route requests to correct models. | M1 | DONE (77b791f9) |
| M4 | Verification, Hardening & Audit | Execute all tests, run Challenger for edge cases, run Forensic Auditor, ensure clean audit. | M2, M3 | DONE (53e34426, 7254ab58, 3776a81e, a3b995df) |

## Interface Contracts
### GET /v1/models
- Response format: JSON array of model objects containing `id`, `object`, `created`, `owned_by`.
- Must include Anthropic models AND all available non-Anthropic models from configured providers (OpenAI, Gemini, etc.).

### POST /v1/messages
- Requests specifying non-Anthropic models in the `model` parameter must be routed to the respective configured provider API (e.g. OpenAI, Google Gemini) rather than Anthropic.
- Must preserve caching, logging, and rate limiting logic without introducing extra latency or breaking the Web UI.

## Code Layout
- `clasp/api/proxy_routes.py` - Contains endpoints including `GET /v1/models` and POST requests routing.
- `clasp/config/settings.py` - Provider and setting loading.
- `clasp/config/provider_catalog.py` - Catalog of providers and their supported models.
- `tests/integration/` - Integration tests.
- `tests/unit/` - Unit tests.
