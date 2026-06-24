# CLASP vs Free Claude Code Feature & Architecture Comparison

This report details the architectural and capability discrepancies between the current **CLASP** implementation and the upstream **Free Claude Code (FCC)** codebase (`free-claude-code-main`).

---

## Executive Summary

**Free Claude Code (FCC)** is a dual-client proxy that routes both Anthropic Messages API traffic (from Claude Code) and OpenAI Responses API traffic (from Codex) to 17 backend providers. It relies on a flat configuration, a simple global sliding-window rate limiter, and reactive backoff retries, but features advanced integrations like head-less CLI session managers for Discord/Telegram bots, local web tool execution, and client-specific prompt optimizations.

**CLASP** is a rate-limit-aware, multi-provider proxy focused on highly robust local queueing, preemptive rate-limiting (Token Bucket algorithm), multi-key rotation per provider, and two-tier response caching. However, it lacks support for Codex, web tool execution, messaging bots, and several provider adapters.

---

## 1. Provider Support Discrepancies

FCC supports **17 providers** compared to CLASP's **10 providers**. 

### Missing Providers in CLASP
The following 7 providers are implemented in FCC but completely missing in CLASP:
1. **Mistral Codestral** (`mistral_codestral`): Routes to `https://codestral.mistral.ai/v1` via distinct API keys (separate from La Plateforme).
2. **DeepSeek** (`deepseek`): Routes to DeepSeek's native Anthropic-compatible Messages endpoint (`https://api.deepseek.com/anthropic`) and automatically strips unsupported image/document attachments.
3. **Kimi (Moonshot AI)** (`kimi`): Routes to Kimi's Anthropic-compatible Messages API (`https://api.moonshot.ai/anthropic/v1/messages`).
4. **llama.cpp** (`llamacpp`): Local provider using native Anthropic Messages style endpoints.
5. **OpenCode Zen** (`opencode`): Curated gateway routing to `https://opencode.ai/zen/v1`.
6. **OpenCode Go** (`opencode_go`): Curated subscription gateway routing to `https://opencode.ai/zen/go/v1`.
7. **Wafer** (`wafer`): Routes to Wafer Pass Anthropic-compatible Messages endpoint (`https://pass.wafer.ai/v1/messages`).
8. **Z.ai** (`zai`): Routes to Z.ai's Anthropic-compatible Messages API (`https://api.z.ai/api/anthropic/v1`).

### Transport Type Discrepancies
FCC implements two primary transport families: `openai_chat` and `anthropic_messages`. Several providers use `anthropic_messages` in FCC but are implemented as `openai_chat` in CLASP:
* **OpenRouter** (`open_router`): Uses `anthropic_messages` in FCC; uses `openai_chat` (completions) in CLASP.
* **Ollama** (`ollama`): Uses `anthropic_messages` in FCC; uses `openai_chat` in CLASP.
* **LM Studio** (`lmstudio`): Uses `anthropic_messages` in FCC; uses `openai_chat` in CLASP.

*Note: CLASP includes support for `together` (Together AI), which is not present in FCC.*

---

## 2. Model Discovery and Listing

### GET `/v1/models`
* **FCC**: Builds the `/v1/models` list dynamically by checking `settings.configured_chat_model_refs()` and querying providers. For every model that supports thinking, it advertises the standard ID (`anthropic/provider/model`) and a no-thinking variant using the prefix `claude-3-freecc-no-thinking/provider/model`. This prefix leverages the Claude Code client-side heuristic (treating any model containing `claude-3-` as not supporting thinking) to selectively disable client thinking.
* **CLASP**: Serves a static `STATIC_MODEL_LIST` in `clasp/api/optimize.py` but appends dynamic provider models from its registry in the format `anthropic/provider/model` and `claude-3-freecc-no-thinking/provider/model`. It does not perform capability-based filtering or dynamic settings queries to build this catalog.

### Codex Model Catalog & Selector
* **FCC**: The `fcc-codex` launcher makes a local GET request to `/v1/models` to generate a Codex-compatible model catalog file stored at `~/.fcc/codex-model-catalog.json`. This catalog is injected into Codex's native `/model` picker, allowing the user to select FCC gateway models.
* **CLASP**: Completely lacks Codex support; therefore, no Codex-compatible catalog generation exists.

---

## 3. Custom Endpoints, Tools, and Proxy Routing

### OpenAI Responses API (`POST /v1/responses`)
* **FCC**: Implements a dedicated endpoint for Codex (`/v1/responses`). It includes a complete conversion subsystem under `core/openai_responses/` that converts OpenAI Responses requests to Anthropic Messages requests, maps tool/function declarations, manages reasoning/thinking block streaming, and translates Anthropic SSE events back to OpenAI Responses SSE packets.
* **CLASP**: Does not support the OpenAI Responses API.

### Local Web Server Tools (`ENABLE_WEB_SERVER_TOOLS`)
* **FCC**: Claude Code natively requests `web_search` and `web_fetch` tools. FCC includes a local tool handler (`api/web_tools/`) that intercepts these requests:
  - `web_search`: Scrapes `lite.duckduckgo.com` and formats results as text deltas.
  - `web_fetch`: Crawls target webpages, utilizing a static DNS resolver (`_PinnedEgressStaticResolver`) and strict egress rules to validate and pin IP addresses, rejecting private/local network access to prevent Server-Side Request Forgery (SSRF).
* **CLASP**: Does not support local web tool execution.

### Headless Session Management & Messaging Bots
* **FCC**: Supports running headless Claude Code CLI sessions remotely via Telegram or Discord bot integration:
  - `cli/managed/`: Spawns and manages a pool of background Claude Code subprocesses (using PTYs/subprocesses), streams stdout/stderr, intercepts confirmations, and handles state.
  - `messaging/`: Integrates Discord and Telegram bot adapters, including support for voice-note transcription using local Whisper (CPU/CUDA) or NVIDIA NIM Riva gRPC.
* **CLASP**: Designed only as a local proxy; has no headless session manager or bot integrations.

### Agent Optimizations
* **FCC**: Implements localized mocks and quick-answer handlers in `api/optimization_handlers.py` to bypass calling the LLM for specific Claude Code agent probes:
  - `try_prefix_detection`: Extracts command prefixes locally (e.g. `git`, `npm`, `docker` etc.) via shell tokenizing.
  - `try_quota_mock`: Instantly answers Claude Code quota probes.
  - `try_title_skip`: Instantly mocks conversation title generation.
  - `try_suggestion_skip`: Bypasses suggestion mode queries.
  - `try_filepath_mock`: Statically parses output paths from commands (`cat`, `head`, `grep` etc.).
* **CLASP**: Implements local answering for `count_tokens` and trivial probes (using a single-space text SSE delta), but lacks command prefix, quota, title, suggestion, and file path extraction optimizations.

---

## 4. Caching and Rate Limiting Discrepancies

### Caching
* **FCC**: Does not cache request responses. Caching is limited to settings loading (`lru_cache` on `get_settings`) and registry model catalogs.
* **CLASP**: Features a robust two-tier response cache (`clasp/cache/`):
  - **Tier 1**: In-memory LRU cache protecting up to 500 entries with an `asyncio.Lock`.
  - **Tier 2**: Persistent SQLite database (`~/.clasp/cache.db`) enforcing TTL limits per row. Full SSE streams are stored and cached (up to 4MB).

### Rate Limiting & Key Management
* **FCC**: Enforces flat, global limits (`PROVIDER_RATE_LIMIT`, `PROVIDER_RATE_WINDOW`, `PROVIDER_MAX_CONCURRENCY`) using a simple rolling window (`StrictSlidingWindowLimiter`) and concurrency semaphore. Supports only a single API key per provider.
* **CLASP**: Features a highly complex rate-limiting subsystem (`clasp/ratelimit/`):
  - Uses the **Token Bucket** algorithm (per provider and key) to track both requests (RPM) and tokens (TPM) locally.
  - Enforces local soft thresholds (e.g. 80% limit) to preemptively queue requests before hitting provider limits.
  - Supports **Multi-Key Rotation** (`key_pool.py`) by maintaining a list of API keys for each provider, dynamically rotating keys as they hit thresholds.
  - State, counters, and cooldowns are stored persistently in SQLite so they survive restarts.

---

## 5. Error Handling and Retry Logic

### FCC Error Handling & Retries
* FCC handles retries reactively inside the transport caller (`execute_with_retry` in `rate_limit.py`).
* If a provider returns a 429 or 5xx, FCC sets a reactive block (pausing new requests for a calculated delay) and retries the *same* provider using exponential backoff with jitter.
* It does not support failover to other providers. If all retries fail, the error propagates back to the client.

### CLASP Error Handling & Retries
* CLASP implements a **429 Absorber** (`clasp/queue/absorber.py`) and a Circuit Breaker.
* If a provider returns a 429:
  1. It trips the circuit breaker or marks the API key as cooling down.
  2. It attempts **immediate failover** to the next healthy provider in the configured `provider_chain`.
  3. If no other provider is immediately available, it places the request in an `asyncio.PriorityQueue` and keeps the client SSE connection open with keep-alive comments (`sse_hold.hold_until_resolved`).
  4. Once a provider key cools down or recovers, the queue manager dispatches the request from the queue, sending the stream back to the waiting client. Only if the queue timeout expires does the client see an error.

---

## Summary Matrix

| Feature | Free Claude Code (`free-claude-code-main`) | CLASP (`clasp/`) |
| :--- | :--- | :--- |
| **Providers Supported** | 17 (includes DeepSeek, Codestral, Kimi, Wafer, Z.ai, OpenCode Zen/Go) | 10 (includes Together AI) |
| **Native Anthropic Transport** | Used for 9 providers (including Ollama, LM Studio, OpenRouter) | Used for Fireworks AI only |
| **Codex Support (`POST /v1/responses`)** | Full conversion subsystem (tools, reasoning, streams) | None |
| **Local Web Tools** | Intercepts & runs `web_search` (DuckDuckGo) / `web_fetch` (DNS pinned) | None |
| **Headless Sessions & Bots** | Telegram and Discord bots, head-less PTY runner, Whisper STT | None |
| **Agent Mocks** | Local command prefix, quota, title skip, suggestion skip, file paths | Trivial probe (space) only |
| **Response Caching** | None | Two-tier cache (In-Memory LRU + Persistent SQLite) |
| **Rate Limiter Type** | Global sliding window | Token Bucket (RPM + TPM) |
| **Multi-Key Rotation** | Single key per provider | List of keys per provider, rotated dynamically |
| **Rate Limit Persistence** | None (in-memory only) | SQLite-backed persistence across restarts |
| **429 Absorption** | Sleep retry on same provider (blocks client) | Failover provider chain, Priority Queue buffer, SSE-hold keep-alives |
