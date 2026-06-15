# Claude Code Router — Ideas & Architecture Braindump

> A rate-limit-aware, multi-provider proxy that lets students use OpenAI-compatible free APIs
> (NVIDIA NIM, Gemini, Cerebras, Groq, etc.) with Anthropic products like Claude Code —
> without ever hitting a 429 error.

---

## Problem Statement

Free API providers impose tight rate limits:

| Provider | Limit |
|---|---|
| NVIDIA NIM (build.nvidia.com) | 40 req/min — then blocked for hours |
| Google Gemini AI Studio (free) | 15 req/min, 1M tokens/day |
| Cerebras | ~30 req/min |
| Groq | ~30 req/min |
| Fireworks (free tier) | varies per model |
| OpenRouter (free models) | varies per model |

When students hit these limits, the entire Claude Code session breaks. Existing routers
(`claude-code-router`, `free-claude-code`) propagate the 429 upstream — they don't absorb it.
**This project fixes that.**

---

## Existing Landscape

### `musistudio/claude-code-router`
- TypeScript / Node.js
- Transformer + plugin system for per-provider request/response shaping
- Routing rules by request type: default, background, think, longContext, webSearch, image
- Web UI for config management, CLI model management
- Supports: OpenRouter, DeepSeek, Ollama, Gemini, Volcengine, SiliconFlow
- **Gap:** Zero rate limit awareness. 429 crashes through to Claude Code.

### `Alishahryar1/free-claude-code`
- Python / FastAPI
- 17 provider backends, per-model-tier routing (Opus/Sonnet/Haiku → different providers)
- Discord/Telegram bot, voice note transcription, VS Code + JetBrains support
- Has a `providers/rate_limiting` folder but it's surface-level
- **Gap:** Same problem — 429 propagates. No queue, no absorption, no key rotation.

### Our differentiation
Everything in `ratelimit/` and `queue/` — the two modules neither project has.

---

## The Killer Feature: Transparent 429 Absorber

When an upstream provider returns 429, instead of propagating the error to Claude Code:

1. The proxy **holds the SSE streaming connection open** (sends keep-alive bytes)
2. **Queues the request** internally
3. Either waits for the rate limit window to reset (NVIDIA NIM resets every 60s) **or** immediately fails over to the next healthy provider
4. Retries the request transparently
5. Claude Code sees a slightly slow response — **not a crash, not a broken session**

This is the core insight. It is entirely doable with Python `asyncio` + `asyncio.Queue` + fake SSE hold-open via `StreamingResponse`. From Claude Code's perspective the request just took a bit longer.

---

## Full Feature Ideas

### 1. Rate Limit Intelligence

- **Sliding window counter** per provider. Track requests in the last 60 seconds. Compare against known hard limits (constants in config, user-overridable).
- **Soft threshold switching** — at 80% of limit (e.g., 32/40 for NIM), proactively start routing to the backup *before* hitting the wall.
- **Recovery timer** — when a provider 429s, mark it as `COOLING_DOWN` with an estimated recovery timestamp. Status line shows `NIM: cooling (38s)`. Re-enable automatically when timer expires.
- **Per-provider circuit breaker** — if error rate on a provider exceeds X% over the last N requests (not just 429s — also 500s, timeouts), pull it from rotation temporarily.

### 2. Multi-Key Rotation

- Support **N API keys per provider**. Students often create 2–3 NVIDIA NIM accounts. Router round-robins across keys. When key A hits 40 RPM, switch to key B. Effectively multiplies free quota N×.
- Track health (status, current RPM, last error) **per key independently**, not just per provider.
- Keys configured in a simple list in `config.yaml` — no code changes needed to add more.

### 3. Multi-Provider Load Balancing

- **Priority chains** — user defines: `[nvidia_nim, gemini_free, cerebras, groq, openrouter_free]`. Router tries in order, skipping any provider in `COOLING_DOWN` or above soft threshold.
- **Token-budget tracking** — providers like Gemini limit by tokens/day, not just requests/minute. Track cumulative token usage since midnight UTC, warn and switch before exhausting daily cap.
- **Latency-weighted routing** — track P50 response latency per provider (rolling 100-request window). When multiple providers are healthy and under threshold, prefer the fastest.
- **Model capability matching** — if a request requires tool use or vision and the currently selected model doesn't support it, automatically pick the next provider whose model does.

### 4. Priority Request Queue

- Tag incoming requests as `interactive` (user typed something) vs `background` (file indexing, context summarization, probes).
- Under rate pressure, **interactive requests skip the queue**. Background tasks wait.
- Configurable: `max_queue_depth`, `max_wait_seconds`. Only surface an error to Claude Code if a request waits beyond `max_wait_seconds` with no provider available.
- Queue stats exposed on the admin dashboard and in logs.

### 5. Quota Stretching

- **Exact-match response cache** — hash the full request body (model + messages + tools), store response in SQLite or memory with a short TTL. Claude Code occasionally sends identical back-to-back requests. Eliminates redundant API calls with zero provider cost.
- **System prompt deduplication** — Claude Code sends the same large system prompt on every single request. Extract it, hash it. If a provider supports prefix/prompt caching (Gemini does), use it. Reduces per-request token cost significantly.
- **Local request answering** — some Claude Code probes are trivial: `/v1/models` list, simple health checks. Answer these entirely locally without touching any provider. (`free-claude-code` does this — worth borrowing.)

### 6. Free-Tier-Aware Provider Profiles

Hard-code known limits as first-class constants, not buried in docs:

```yaml
providers:
  nvidia_nim:
    rpm_limit: 40
    rpm_soft_threshold: 0.80
    cooldown_seconds: 60
    daily_token_limit: null
  gemini_free:
    rpm_limit: 15
    rpm_soft_threshold: 0.80
    daily_token_limit: 1_000_000
  cerebras:
    rpm_limit: 30
    rpm_soft_threshold: 0.80
  groq:
    rpm_limit: 30
    rpm_soft_threshold: 0.80
```

User can override any value. No need to research limits yourself.

### 7. Real-Time Admin Dashboard

Web UI (FastAPI + plain HTML/JS, no heavy framework) showing:

- Per-provider status card: `HEALTHY` / `SOFT_LIMIT` / `COOLING_DOWN` / `CIRCUIT_OPEN`
- Current RPM vs limit (progress bar)
- Per-key health for multi-key providers
- Queue depth + estimated drain time
- Last 429 event + recovery countdown
- Rolling latency chart per provider (last 10 minutes)
- Total requests served today, per provider

### 8. CLI Status Integration

Single-line status output compatible with `tmux` status bar or shell `$PS1`:

```
[Router] Active: NIM (28/40 rpm) | Fallback: Gemini | Queue: 0 | Keys: 2/3 healthy
```

### 9. Usage Reports

Daily/weekly summary logged to file and shown on dashboard:

- Requests per provider
- 429s absorbed (and how many were retried vs failed-over)
- Total tokens used per provider vs daily limit
- Average response latency per provider

### 10. Developer Experience

- **Zero-config quickstart** with NVIDIA NIM as default. One command, paste API key, done. No YAML editing required to get started.
- **Hot-reload config** — SIGHUP or file watcher reloads `config.yaml` without proxy restart. Add a new API key mid-session.
- **`--dry-run` mode** — simulate routing decisions and print what would happen to a given request without sending it to any provider. Useful for debugging routing rules.
- **Persist rate limit state to disk** — recovery timers and usage counters survive proxy restarts. Not reset on every launch.
- **Structured JSON logs** — every request logged with: provider used, model, tokens, latency, outcome (ok/429/fallback/queued/cached). Importable into any log aggregator.

### 11. Optional: Shared Pool Mode

> Spicy but powerful for study groups and college communities.

Deploy a single instance on a cheap VPS (or even on a local machine with `ngrok`). Multiple students connect to it. Each student contributes their own API keys to the pool. The router:

- Manages combined quota across all keys and all providers
- Allocates fair share per user (token bucket per user ID)
- Users authenticate with a simple bearer token (no heavy auth system needed)
- Effectively makes 5 students with 40 RPM each act like one user with 200 RPM

This is the DTU-scale deployment. One person runs it, the whole batch benefits.

---

## Supported Providers (Day 1 Target)

| Provider | Type | Notes |
|---|---|---|
| NVIDIA NIM (build.nvidia.com) | OpenAI-compat | Primary target. 40 RPM hard limit. |
| Google Gemini AI Studio | Native + OpenAI-compat | Free tier: 15 RPM, 1M tokens/day |
| Cerebras | OpenAI-compat | Fast inference, moderate limits |
| Groq | OpenAI-compat | Fast, popular free option |
| Fireworks AI | OpenAI-compat | Free quota on select models |
| OpenRouter (free models) | OpenAI-compat | Aggregates many free models |
| Mistral AI (experiment plan) | OpenAI-compat | Free for research use |
| together.ai (free tier) | OpenAI-compat | Free credits on signup |
| Ollama (local) | OpenAI-compat | Unlimited — fallback of last resort |

---

## Recommended Architecture

```
Claude Code
    │
    ▼ (ANTHROPIC_BASE_URL=http://localhost:8080)
┌─────────────────────────────────────┐
│           FastAPI Proxy             │
│  /v1/messages  →  translate  →  /chat/completions  │
│                                     │
│  ┌──────────┐   ┌───────────────┐  │
│  │  Router  │──▶│ Rate Limiter  │  │
│  └──────────┘   │ Sliding Window│  │
│       │         │ Circuit Breaker│  │
│       │         │ Key Rotator   │  │
│       │         └───────────────┘  │
│       │                            │
│       ▼                            │
│  ┌──────────┐                      │
│  │  Queue   │ (priority, hold-open)│
│  └──────────┘                      │
│       │                            │
│       ▼                            │
│  ┌──────────────────────────────┐  │
│  │       Provider Manager       │  │
│  │  NIM │ Gemini │ Groq │ ...   │  │
│  └──────────────────────────────┘  │
│                                     │
│  ┌──────────┐  ┌────────────────┐  │
│  │  Cache   │  │  Admin Dashboard│  │
│  │ SQLite   │  │  /admin        │  │
│  └──────────┘  └────────────────┘  │
└─────────────────────────────────────┘
```

### Module Breakdown

```
claude-router/
├── main.py                  # FastAPI app, startup
├── config.yaml              # Provider keys, limits, routing rules
├── api/
│   ├── routes.py            # /v1/messages, /v1/models endpoints
│   └── translate.py         # Anthropic Messages ↔ OpenAI Chat format
├── router/
│   └── selector.py          # Provider selection logic (priority chain, health check)
├── ratelimit/
│   ├── window.py            # Sliding window counter per provider/key
│   ├── circuit_breaker.py   # Error rate tracker, open/close logic
│   └── cooldown.py          # Recovery timer, state persistence
├── queue/
│   ├── manager.py           # asyncio priority queue
│   ├── absorber.py          # 429 catch → queue → retry logic
│   └── sse_hold.py          # Fake SSE keep-alive while request is queued
├── providers/
│   ├── base.py              # Abstract provider class
│   ├── nvidia_nim.py
│   ├── gemini.py
│   ├── groq.py
│   ├── cerebras.py
│   └── openrouter.py
├── cache/
│   └── response_cache.py    # Request hash → cached response (SQLite)
├── admin/
│   ├── dashboard.html       # Single-file admin UI
│   └── api.py               # /admin/status, /admin/stats endpoints
└── utils/
    ├── token_counter.py     # Estimate token usage for budget tracking
    └── logger.py            # Structured JSON logger
```

---

## Build Priority Order

When coding, tackle in this sequence. Each step is independently valuable:

1. **Core proxy** — basic request translation (Anthropic Messages ↔ OpenAI Chat), hardcoded NIM as provider. Claude Code works.
2. **Sliding window rate limiter + soft threshold** — track RPM, switch to fallback before hitting wall.
3. **429 absorber + SSE hold-open** — queue on 429, retry transparently. This is the main product.
4. **Multi-key rotation** — multiply free quota N× with multiple accounts.
5. **Provider priority chain** — NIM → Gemini → Groq → Cerebras → Ollama fallback.
6. **Response cache + local answering** — stretch quota further.
7. **Admin dashboard** — visibility into what's happening.
8. **Usage reports + structured logs** — understand patterns, debug issues.
9. **Shared pool mode** (optional) — college-scale deployment.

---

## Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Runtime | Python 3.11+ | asyncio handles queue + streaming naturally |
| Framework | FastAPI | SSE streaming, async routes, built-in docs |
| Queue | `asyncio.PriorityQueue` | Native async, no extra dependency |
| Cache | SQLite (via `aiosqlite`) | Zero-ops, persistent, fast for this scale |
| Config | YAML + `pydantic-settings` | Hot-reload friendly, validated at startup |
| State persistence | JSON file on disk | Rate limit state survives restarts |
| Dashboard | Vanilla HTML + SSE | No build step, ships as a single file |
| Logging | `structlog` → JSON | Machine-readable, grep-friendly |

---

## Name Ideas

- `clout` (Claude + Router)
- `clasp` (Claude API Switching Proxy)
- `overflow` (because it handles overflow from rate limits)
- `relay` (simple, accurate)
- `sponge` (absorbs 429s)

---

*Last updated: brainstorm phase — plan.md and implementation to follow.*
