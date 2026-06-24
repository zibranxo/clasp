# Handoff Report — Explorer V2 Checkpoint

This report summarizes the comparative analysis between `free-claude-code-main` (FCC) and `clasp`.

## 1. Observation
The following code structures were directly observed in both repositories:
* **Upstream Providers**: 
  - `free-claude-code-main/config/provider_catalog.py:57` defines 17 providers, routing deepseek, kimi, wafer, lmstudio, llamacpp, ollama, and open_router through native Anthropic messages transport (`transport_type="anthropic_messages"`).
  - `clasp/config/settings.py:215` defines 10 providers in `_DEFAULT_PROVIDERS` and routes Ollama, LM Studio, and OpenRouter through the `OpenAIChatTransport`.
* **Codex API & Subsystem**:
  - `free-claude-code-main/api/routes.py:60` exposes the `POST /v1/responses` endpoint.
  - `free-claude-code-main/core/openai_responses/` contains a conversion module converting Codex Requests ↔ Anthropic Messages.
  - `free-claude-code-main/cli/launchers/codex.py` launches Codex and generates local catalog files.
* **Local Web Tools**:
  - `free-claude-code-main/api/web_tools/streaming.py:40` implements local execution of `web_search` (via DuckDuckGo lite scraper) and `web_fetch` (via a DNS-pinned, egress-restricted static resolver to prevent SSRF).
* **Agent Optimizations**:
  - `free-claude-code-main/api/optimization_handlers.py:137` defines client-side mocks and fast-path handlers: `try_prefix_detection`, `try_quota_mock`, `try_title_skip`, `try_suggestion_skip`, and `try_filepath_mock`.
* **Headless Sessions & Bots**:
  - `free-claude-code-main/cli/managed/manager.py:11` implements `ManagedClaudeSessionManager` to spawn background PTYs for Telegram and Discord bot adapters.
* **Response Cache**:
  - `clasp/cache/response_cache.py:87` implements a two-tier `ResponseCache` (in-memory LRU + SQLite) for full SSE streams, which is not present in FCC.
* **Limiting & Failover**:
  - `clasp/ratelimit/bucket.py` implements preemptive Token Bucket limits.
  - `clasp/ratelimit/key_pool.py:127` implements dynamic multi-key rotation.
  - `clasp/queue/absorber.py:40` implements 429 absorption with dynamic failover and priority queue buffer.

## 2. Logic Chain
1. **Scope and Support**: The presence of `providers/codestral`, `providers/deepseek`, `providers/kimi`, etc., in FCC and their absence in `clasp/providers/` indicates that CLASP lacks 8 provider adapters.
2. **OpenAI Chat vs Anthropic Messages**: The transport types defined in FCC's `PROVIDER_CATALOG` (`anthropic_messages` for OpenRouter, Ollama, LM Studio) versus CLASP's subclassing of `OpenAIChatTransport` shows a divergence in transport logic.
3. **Endpoint Discrepancy**: The presence of `POST /v1/responses` and the `core/openai_responses/` folder in FCC, along with the lack of responses-related code in `clasp/api/proxy_routes.py` and `clasp/server.py`, proves that Codex integration is entirely missing in CLASP.
4. **Local Tools Discrepancy**: The files under `free-claude-code-main/api/web_tools/` implement local scraping and crawling of tools, which is absent from CLASP's routers.
5. **Caching & Queueing Discrepancy**: The presence of SQLite caching (`clasp/cache/response_cache.py`) and absorber failovers (`clasp/queue/absorber.py`) in CLASP contrasted with their absence in FCC proves CLASP is designed for high reliability and request consolidation, while FCC relies on reactive, single-provider retries.

## 3. Caveats
* The analysis is strictly read-only and does not verify performance differences through live execution.
* The analysis assumes that the current clasp implementation corresponds to Sprint 2 complete.

## 4. Conclusion
CLASP is missing 8 provider adapters, the Codex Responses API (`/v1/responses`), local `web_search`/`web_fetch` tool execution, headless messaging wrappers (Discord/Telegram), and specific Claude Code prompt optimizations. However, CLASP includes a two-tier response cache, preemptive Token Bucket limits, key rotation, and 429 absorber queueing that FCC does not have.

## 5. Verification Method
Verify that the output file `c:\code\clasp\.agents\explorer_v2\analysis.md` exists and contains the complete feature matrix.
To verify CLASP tests continue to pass, run:
```powershell
uv run pytest tests/unit -v --tb=short
uv run pytest tests/integration -v --tb=short
```
