# Scope: CLASP v2 Feature Port

This document specifies the milestones, interface contracts, and code layout for porting the missing features of `free-claude-code-main` (FCC) into `clasp`.

## Architecture
We are extending the existing `clasp` proxy to support the full feature-set of FCC:
1. Dynamic model discovery and `/models` endpoint.
2. 8 additional providers (Mistral Codestral, DeepSeek, Moonshot Kimi, llama.cpp, OpenCode Zen, OpenCode Go, Wafer, Z.ai).
3. OpenAI Responses API (`POST /v1/responses`) conversion layers.
4. Web Search and Egress-Pinned Web Fetch tools.
5. Startup mocks and optimizations.
6. Managed headless PTY sessions and Telegram/Discord bots.

---

## Milestones

| # | Name | Scope | Dependencies | Status |
|---|---|---|---|---|
| M1 | Models & Transport Alignment | Expose dynamic models in `GET /v1/models` and update routing in `POST /v1/messages`. Re-align OpenRouter, Ollama, and LM Studio to Anthropic Messages transport. | None | DONE |
| M2 | Provider Adapters | Implement Mistral Codestral, DeepSeek, Moonshot Kimi, llama.cpp, OpenCode Zen, OpenCode Go, Wafer, and Z.ai provider integrations. | M1 | DONE |
| M3 | OpenAI Responses API | Implement `POST /v1/responses` and the core OpenAI ↔ Anthropic translation logic for Codex. | M1 | DONE |
| M4 | Web Server Tools | Implement `web_search` and `web_fetch` local tools with strict DNS resolution and SSRF checks. | None | DONE |
| M5 | Mocks & Headless Bots | Implement agent startup optimization mocks and PTY headless session + Discord/Telegram bots. | None | DONE |
| M6 | End-to-End Verification & Audit | Integrate all new unit/integration tests and execute verification with the Forensic Auditor. | M1, M2, M3, M4, M5 | IN_PROGRESS |

---

## Interface Contracts

### GET /v1/models
* Expose dynamic model names.
* Prefix model IDs with `anthropic/` or `claude-3-freecc-no-thinking/`.

### POST /v1/responses
* Accept OpenAI Codex response request.
* Returns OpenAI-compatible chunk stream converting Anthropic Messages stream.

### Local Tools
* `web_search` takes query, scrapes DuckDuckGo Lite, returns textual results.
* `web_fetch` takes url, pins DNS to public IP, verifies not local/private range, fetches content, returns page text.

---

## Code Layout
* `clasp/api/proxy_routes.py` - FastAPI routes (endpoints: /v1/models, /v1/responses, /v1/messages)
* `clasp/api/optimize.py` - answer_models, local probes
* `clasp/config/provider_catalog.py` - Provider profiles catalog
* `clasp/providers/registry.py` - Provider instantiation and model lists caching
* `clasp/providers/` - Provider custom transport classes
* `clasp/core/openai_responses/` - Conversions for Codex
* `clasp/api/web_tools/` - DuckDuckGo scraping & pinned fetch tool
* `clasp/api/optimization_handlers.py` - Mocks and prefix/filepath extraction
* `clasp/cli/managed/` - Managed headless sessions
* `clasp/messaging/` - Bot wrappers
