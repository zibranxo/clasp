# Feature Porting Proposal: CLASP v2

## 1. Executive Summary
This proposal outlines the feature porting strategy from the upstream **Free Claude Code (FCC)** codebase (`free-claude-code-main`) to the **CLASP** codebase. CLASP is designed as a rate-limit-aware proxy with robust token-bucket limits, key rotation, and 429 failover queueing. By porting these missing features natively, we will expand CLASP's capabilities to include dynamic model discovery, Codex support, local web scraper tools, and agent optimizations, while preserving CLASP's advanced caching and failover architecture.

---

## 2. Missing Feature & Integration Inventory

| Category | Feature Name | Description / Specification | Target Implementation Location |
| :--- | :--- | :--- | :--- |
| **Model Discovery** | **Dynamic Model Selector (`/models`)** | Exposes all available non-Anthropic models from configured providers dynamically via `GET /v1/models`. Allows users to select these models directly using `/model` in Claude Code. Handles prefixed IDs (`anthropic/provider/model` and `claude-3-freecc-no-thinking/...` to skip client-side thinking). | `clasp/api/proxy_routes.py`, `clasp/api/optimize.py`, `clasp/providers/registry.py` |
| **Providers** | **8 Upstream Provider Adapters** | 1. **Mistral Codestral** (`mistral_codestral`) • routes to Mistral's Codestral API.<br>2. **DeepSeek** (`deepseek`) • routes to DeepSeek's native Anthropic Messages endpoint, stripping unsupported attachments.<br>3. **Kimi (Moonshot AI)** (`kimi`) • routes to Kimi's Anthropic Messages API.<br>4. **llama.cpp** (`llamacpp`) • local provider adapter.<br>5. **OpenCode Zen** (`opencode`) • gateway router.<br>6. **OpenCode Go** (`opencode_go`) • gateway router.<br>7. **Wafer** (`wafer`) • Wafer Pass Messages API.<br>8. **Z.ai** (`zai`) • Z.ai Messages API. | `clasp/config/provider_catalog.py`, `clasp/providers/registry.py`, `clasp/providers/` |
| **Providers** | **Anthropic Messages Transport for OpenRouter, Ollama, LM Studio** | Routes OpenRouter, Ollama, and LM Studio via native Anthropic messages transport (`transport_type="anthropic_messages"`) instead of converting them to OpenAI chat completions, maintaining direct compatibility. | `clasp/config/provider_catalog.py`, `clasp/providers/registry.py` |
| **Codex Support** | **OpenAI Responses API (`POST /v1/responses`)** | Adds support for the `/v1/responses` endpoint used by Codex. Converts Codex requests (OpenAI format) to Anthropic Messages requests, parses/maps tools, translates thinking/reasoning blocks, and converts Anthropic SSE events back to OpenAI Response SSE packets. Also generates local `~/.fcc/codex-model-catalog.json` for model selection. | `clasp/api/proxy_routes.py`, `clasp/core/openai_responses/` |
| **Local Tools** | **Web Search & Egress-Pinned Web Fetch** | Intercepts Claude Code calls to `web_search` and `web_fetch` to execute them locally:<br>1. `web_search`: scrapes DuckDuckGo Lite.<br>2. `web_fetch`: crawls target webpages, utilizing a static DNS resolver (`_PinnedEgressStaticResolver`) and strict egress rules to reject private/local network IPs, preventing SSRF. | `clasp/api/web_tools/`, `clasp/api/proxy_routes.py` |
| **Optimizations** | **Client-Side Agent Mocks** | Speeds up Claude Code initialization by mocking probe responses locally:<br>1. Prefix detection (extract command prefixes via tokenizing).<br>2. Quota probe mock.<br>3. Conversation title generation skip.<br>4. Suggestion query skip.<br>5. File path extractor mock from commands like `grep`, `cat`, etc. | `clasp/api/optimization_handlers.py`, `clasp/api/optimize.py` |
| **Remoting/Bots** | **Managed Headless Sessions & Messaging Bots** | Telegram and Discord bot adapters to remotely interact with Claude Code CLI running in background PTY processes, featuring voice note transcription via Whisper or NIM Riva gRPC. | `clasp/cli/managed/`, `clasp/messaging/` |

---

## 3. Detailed Feature Breakdown & Spec

### Feature 1: Dynamic Model Selector (`/models` / `GET /v1/models`)
* **Problem**: In CLASP, the model list is statically returned. Non-Anthropic models from providers cannot be discovered or chosen inside Claude Code.
* **Proposed Implementation**:
  1. Update `GET /v1/models` in `clasp/api/proxy_routes.py` (and the `answer_models()` function in `clasp/api/optimize.py`) to dynamically query enabled provider models cached in the registry.
  2. Format exposed models:
     - `anthropic/{provider}/{model_id}`: Standard routing.
     - `claude-3-freecc-no-thinking/{provider}/{model_id}`: Prefixed model name to trick Claude Code into skipping thinking blocks (for providers/models that don't support or don't require extended thinking).
  3. Ensure that when `POST /v1/messages` receives a request with these model IDs, it parses the provider prefix, extracts the raw model ID, and routes it to the corresponding provider.
  4. Preserve existing caching, rate limiting, and 429 absorption for these routed requests.

### Feature 2: Upstream Provider Adapters
* **Problem**: CLASP lacks 8 provider definitions and adapters present in FCC.
* **Proposed Implementation**:
  1. Add profiles to `PROVIDER_CATALOG` in `clasp/config/provider_catalog.py` with correct endpoints, transports, limits, and capabilities.
  2. Implement native `anthropic_messages` transport mapping for DeepSeek, Kimi, Wafer, Z.ai, llama.cpp, etc.
  3. Implement specific provider pre-processing (e.g. DeepSeek adapter stripping image/document blocks since it only supports text).

### Feature 3: OpenAI Responses API (`POST /v1/responses` / Codex Support)
* **Problem**: Codex uses OpenAI's `/v1/responses` format instead of Anthropic's Messages format. CLASP does not expose this endpoint.
* **Proposed Implementation**:
  1. Add a `POST /v1/responses` endpoint to the FastAPI proxy router.
  2. Port the conversion modules from `core/openai_responses/` to map request/response structures between OpenAI Responses and Anthropic Messages.
  3. Implement Codex model catalog generation on startup.

### Feature 4: Local Web Tools (DuckDuckGo Search & Secure Fetch)
* **Problem**: Claude Code relies on browser tools for searching and reading web pages, which is slow and depends on external APIs.
* **Proposed Implementation**:
  1. Add local tools execution matching FCC.
  2. Port `web_search` to scrape `lite.duckduckgo.com` and format the text results.
  3. Port `web_fetch` with a secure, DNS-pinned resolver that prevents SSRF by validating that IP addresses do not resolve to local/private ranges before crawling.

### Feature 5: Agent Optimizations (Mocks)
* **Problem**: Claude Code does several startup check queries (quota, suggestions, titles) which add roundtrip latency.
* **Proposed Implementation**:
  1. Add quick-answer handlers in `clasp/api/optimization_handlers.py`.
  2. Mock quota, suggestion, title, and file path extraction probes locally to avoid calling upstream providers.

### Feature 6: Managed Headless Sessions & Messaging Bots
* **Problem**: Users cannot control Claude Code remotely or via Discord/Telegram.
* **Proposed Implementation**:
  1. Port the managed session pool manager using local PTYs.
  2. Add Telegram/Discord bot polling/webhook integrations with voice note transcription.

---

## 4. Latency & Performance Strategy
1. **Asynchronous I/O**: Use non-blocking `httpx.AsyncClient` inside all new provider adapters and tools.
2. **Caching**: Extend CLASP's existing two-tier response cache to cover Codex `/v1/responses` and local web search responses.
3. **No-Thinking Optimization**: Use the `claude-3-freecc-no-thinking` prefix to skip unnecessary thinking blocks, reducing latency by up to 50% for models that don't need it.

---

## 5. Verification Plan
- **Unit & Integration Tests**: Port existing unit/integration tests from FCC to CLASP under `tests/`.
- **E2E Testing**: Add E2E tests for the dynamic models selector, verifying that `GET /v1/models` returns the prefixed models and that `POST /v1/messages` correctly routes OpenAI/Gemini/DeepSeek calls.
- **SSRF Efficacy**: Verify the DNS-pinned static resolver rejects private network ranges (e.g. `127.0.0.1`, `10.0.0.0/8`, `192.168.0.0/16`).
- **Forensic Auditor Gating**: Run a full audit verification session before signing off on implementation.
