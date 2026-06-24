<div align="center">

<img src="https://img.shields.io/badge/CLASP-v2.0-blueviolet?style=for-the-badge&logo=anthropic" alt="CLASP v2.0"/>
<img src="https://img.shields.io/badge/Python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"/>
<img src="https://img.shields.io/badge/FastAPI-Async-009688?style=for-the-badge&logo=fastapi" alt="FastAPI"/>
<img src="https://img.shields.io/badge/Providers-18-orange?style=for-the-badge" alt="18 Providers"/>
<img src="https://img.shields.io/badge/Tests-934_Passing-brightgreen?style=for-the-badge" alt="934 Tests Passing"/>

<br/><br/>

# CLASP — Claude API Switching Proxy

**The rate-limit-aware, multi-provider AI gateway that lets you use Claude Code, Codex, and any Anthropic-compatible client with free-tier LLM endpoints — forever.**

[🚀 Quick Start](#-quick-start) · [📖 How It Works](#-how-it-works) · [⚙️ Configuration](#%EF%B8%8F-configuration) · [🌐 Providers](#-supported-providers-18) · [🤖 Remote Bots](#-remote-access--messaging-bots) · [🔍 SEO & Agent Discovery](#-seo--agent-discovery-index)

</div>

---

## 🧠 What is CLASP?

**CLASP** (Claude API Switching Proxy) is an open-source, production-quality local AI gateway that sits between your AI coding assistant (Claude Code, Codex, Continue.dev, etc.) and the internet. It solves the #1 problem developers face when using free-tier LLM APIs: **constant `429 Too Many Requests` errors aborting your coding session**.

CLASP routes your AI requests across **18 upstream providers**, pre-emptively tracks rate limits with a mathematical **Token Bucket engine**, and absorbs 429 errors by buffering requests in a **priority queue** — keeping your client connected and your session alive without a single interruption.

> **Zero config, zero external dependencies, zero dropped sessions.** Just plug it in and code.

---

## ✨ Feature Highlights

| Feature | Description |
|---|---|
| 🔀 **Multi-Provider Routing** | 18 upstream providers — automatically failover between them |
| 🪣 **Token Bucket Rate Limiter** | Pre-emptive RPM + TPM tracking per provider and per API key |
| 🔑 **Multi-Key Pool Rotation** | Multiple API keys per provider, dynamically rotated as they cool down |
| 💾 **Two-Tier Response Cache** | In-memory LRU + SQLite persistence, survives restarts |
| 📡 **OpenAI Responses API** | Full `POST /v1/responses` endpoint for Codex compatibility |
| 🌐 **Local Web Tools** | `web_search` (DuckDuckGo) + `web_fetch` (SSRF-safe DNS-pinned crawler) |
| ⚡ **Agent Optimization Mocks** | Instantly answers Claude Code probes locally — zero upstream latency |
| 🤖 **Telegram & Discord Bots** | Run Claude Code headlessly, remotely, from your phone |
| 🎙️ **Voice Transcription** | Whisper + NVIDIA NIM Riva gRPC voice-to-text for bot sessions |
| 🧩 **Dynamic Model Selector** | All 18 providers' models selectable directly from `/model` in Claude Code |
| 🛡️ **Circuit Breakers** | Per-key exponential backoff, keeps the rest of the pool alive |
| 📊 **Real-Time Dashboard** | Live TUI + Web UI showing rate limit burn, queue depth, and key health |

---

## 🚀 Quick Start

> Requires Python 3.11+ and [`uv`](https://github.com/astral-sh/uv).

```bash
# 1. Clone and install
git clone https://github.com/zibranxo/clasp.git
cd clasp
uv sync

# 2. Initialize (creates ~/.clasp/config.yaml, prompts for API keys)
uv run clasp init

# 3. Start the proxy server (background terminal)
uv run clasp server

# 4. Open a new terminal and launch Claude Code through the proxy
uv run clasp claude
```

That's it. Claude Code is now proxied through CLASP. You will never see a 429 error again.

---

## 📖 How It Works

```
┌─────────────────────────────────────────────────────────┐
│  Claude Code / Codex / any Anthropic client             │
│  → POST /v1/messages  OR  POST /v1/responses            │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  CLASP — localhost:8082                                  │
│                                                          │
│  1. Probe Detection → answer locally if trivial          │
│     (quota check, title gen, token count, prefix detect) │
│                                                          │
│  2. Web Tool Interception → serve web_search/web_fetch   │
│     locally via DuckDuckGo scrape + SSRF-safe fetch      │
│                                                          │
│  3. Model ID Decode → parse anthropic/{provider}/{model} │
│     or claude-3-freecc-no-thinking/{provider}/{model}    │
│                                                          │
│  4. Token Bucket Check (RPM + TPM) → route or queue      │
│                                                          │
│  5. Multi-Key Pool → round-robin across healthy keys      │
│                                                          │
│  6. Protocol Translate → Anthropic ↔ OpenAI ↔ Native    │
│                                                          │
│  7. SSE Stream Reconstruct → send back to client         │
│                                                          │
│  On 429: trip circuit breaker → failover to next         │
│    provider → or buffer in priority queue → SSE-hold     │
└───────┬─────────────┬────────────┬────────────┬─────────┘
        │             │            │            │
        ▼             ▼            ▼            ▼
    NVIDIA NIM   DeepSeek      Gemini      Ollama / Local
    Groq         Kimi          Cerebras    Codestral
    OpenRouter   Wafer         Z.ai        (+ 10 more)
```

### The 429 Problem — Solved

Claude Code makes **dozens of background API calls per session** (quota probes, token counts, title generation, suggestion queries). On a 15 RPM free tier, these saturate your quota in seconds.

CLASP solves this in three ways:

1. **Pre-emptive queuing** — CLASP tracks your RPM/TPM usage locally using a Token Bucket. At 80% capacity, it starts queuing new requests *before* a 429 ever fires.
2. **Instant local answers** — Trivial probes (quota checks, title gen, file path extraction) are answered in microseconds without hitting any API.
3. **Failover chain** — If a provider exhausts, requests immediately reroute to the next healthy provider in your configured chain.

---

## ⚙️ Configuration

The config lives at `~/.clasp/config.yaml`. Generated automatically by `clasp init`.

```yaml
providers:
  - name: nvidia_nim
    api_keys:
      - sk-nim-key-1
      - sk-nim-key-2       # multiple keys = pooled quota
    rpm_limit: 40
    tpm_limit: null
    soft_threshold: 0.80   # start queuing at 80% capacity

  - name: deepseek
    api_keys: [sk-ds-xxxxx]
    rpm_limit: 60

  - name: gemini
    api_keys: [AIza-xxxxx]
    rpm_limit: 15
    tpm_limit: 1000000

  - name: groq
    api_keys: [gsk_xxxxx]
    rpm_limit: 30

provider_chain:            # failover order
  - nvidia_nim
  - deepseek
  - gemini
  - groq

# Feature flags
fast_prefix_detection: true       # intercept shell prefix probes locally
enable_title_generation_skip: true
enable_web_server_tools: true     # local web_search + web_fetch
enable_codex_support: true        # POST /v1/responses for Codex
```

---

## 🌐 Supported Providers (18)

### Free / Free-Credit Tier

| Provider | Key | Transport | Free Limits | Notes |
|---|---|---|---|---|
| **NVIDIA NIM** | `nvidia_nim` | OpenAI Chat | 40 RPM | Best for reasoning models |
| **Google Gemini** | `gemini` | OpenAI Chat | 15 RPM · 1M TPM | Large context fallback |
| **Cerebras** | `cerebras` | OpenAI Chat | 30 RPM | Ultra-low latency |
| **Groq** | `groq` | OpenAI Chat | 30 RPM | Fast LPU inference |
| **Mistral** | `mistral` | OpenAI Chat | Varies | General purpose |
| **Mistral Codestral** | `mistral_codestral` | OpenAI Chat | Varies | Code-specialized |
| **Fireworks AI** | `fireworks` | Anthropic Messages | Varies | Native Anthropic |
| **DeepSeek** | `deepseek` | Anthropic Messages | 60 RPM | Strips unsupported attachments automatically |
| **Kimi (Moonshot AI)** | `kimi` | Anthropic Messages | Varies | Anthropic-compatible |
| **OpenCode Zen** | `opencode` | Anthropic Messages | Curated | Gateway |
| **OpenCode Go** | `opencode_go` | Anthropic Messages | Subscription | Gateway |
| **Wafer** | `wafer` | Anthropic Messages | Varies | Wafer Pass |
| **Z.ai** | `zai` | Anthropic Messages | Varies | Anthropic-compatible |
| **Together AI** | `together` | OpenAI Chat | Varies | Multi-model |
| **OpenRouter** | `openrouter` | Anthropic Messages | Variable | Aggregates 100+ LLMs |

### Local / Self-Hosted

| Provider | Key | Transport | Notes |
|---|---|---|---|
| **Ollama** | `ollama` | Anthropic Messages | Any local model |
| **LM Studio** | `lm_studio` | Anthropic Messages | Any local model |
| **llama.cpp** | `llamacpp` | Anthropic Messages | Direct llama.cpp server |

---

## 🎯 Dynamic Model Selector

CLASP exposes all configured provider models dynamically through the standard `GET /v1/models` endpoint. Inside Claude Code, run `/model` to see and select any of them.

Models are exposed in two formats:

```
anthropic/{provider}/{model_id}
# → Routes normally, thinking blocks enabled

claude-3-freecc-no-thinking/{provider}/{model_id}
# → Identical routing but skips extended thinking (~50% faster)
```

**Example**: To use DeepSeek R1 without extended thinking:
```
/model claude-3-freecc-no-thinking/deepseek/deepseek-reasoner
```

---

## 📡 OpenAI Responses API (Codex Support)

CLASP includes a full `POST /v1/responses` endpoint that enables **Codex** to use any of the 18 configured providers.

- Converts OpenAI Responses format → Anthropic Messages
- Maps tool/function declarations bidirectionally
- Translates thinking/reasoning blocks in streaming
- Generates a Codex-compatible model catalog at `~/.clasp/codex-model-catalog.json` on startup

---

## 🌐 Local Web Tools

When Claude Code invokes `web_search` or `web_fetch` tools, CLASP intercepts and executes them **locally** — faster and with no external API cost.

| Tool | Implementation |
|---|---|
| `web_search` | Scrapes DuckDuckGo Lite, returns formatted text results |
| `web_fetch` | DNS-pinned crawler with `WebFetchEgressPolicy` that rejects RFC1918/loopback/link-local IPs to prevent SSRF |

Enable in config:
```yaml
enable_web_server_tools: true
web_fetch_allow_private_networks: false  # keep false for safety
```

---

## ⚡ Agent Optimization Mocks

CLASP answers Claude Code's startup probes **locally** with zero-latency fabricated responses, eliminating unnecessary upstream round-trips:

| Probe | What CLASP Does | Latency Saved |
|---|---|---|
| Quota check | Returns instant OK | ~200-600ms |
| Title generation | Returns `"Conversation"` | ~500ms–2s |
| Shell prefix detection | Tokenizes locally | ~300-800ms |
| Suggestion mode query | Returns empty response | ~200-500ms |
| File path extraction | Parses statically from command | ~300-600ms |

Enable individually in config:
```yaml
fast_prefix_detection: true
enable_network_probe_mock: true
enable_title_generation_skip: true
enable_suggestion_mode_skip: true
enable_filepath_extraction_mock: true
```

---

## 🤖 Remote Access & Messaging Bots

Run Claude Code **headlessly**, controlled remotely from Telegram or Discord — perfect for long-running tasks when you're away from your desk.

### Telegram Bot
```yaml
telegram:
  enabled: true
  bot_token: "your-telegram-bot-token"
  allowed_user_ids: [123456789]
```

### Discord Bot
```yaml
discord:
  enabled: true
  bot_token: "your-discord-bot-token"
  allowed_guild_ids: [987654321]
```

### Voice Transcription
Send a voice note to your bot → Whisper (local CPU/CUDA) or NVIDIA NIM Riva gRPC transcribes it → Claude Code processes it as text.

```yaml
voice_transcription:
  backend: whisper          # or "riva_grpc"
  whisper_model: base
```

---

## 🖥️ CLI Reference

### `clasp init`
Interactive setup wizard. Creates `~/.clasp/config.yaml`, prompts for API keys, launches the server.

### `clasp server [OPTIONS]`
Start the background proxy server.

| Flag | Default | Description |
|---|---|---|
| `--port` | `8082` | Proxy listen port |
| `--host` | `127.0.0.1` | Bind address |
| `--no-browser` | — | Headless, no UI tab |
| `--live` | — | Rich TUI live panel in terminal |
| `--config PATH` | `~/.clasp/config.yaml` | Custom config path |
| `--debug` | — | Verbose protocol logging |

### `clasp claude [FLAGS...]`
Launch Claude Code through the proxy. All Claude Code flags are forwarded transparently.

```bash
clasp claude                           # Fresh session
clasp claude --resume abc123           # Resume by ID
clasp claude --continue                # Resume last session
clasp claude --print "refactor utils"  # Non-interactive
```

### `clasp status [--json]`
One-line health snapshot of the rate limit engine.

```
[CLASP] NIM(28/40 rpm) → Gemini | Queue:0 | Keys:3/4 healthy | 1.2k req today
```

### `clasp stop`
Graceful shutdown — drains the queue and serializes all rate limit state to `~/.clasp/ratelimit.json`.

### `clasp reset [PROVIDER]`
Force-clear circuit breakers and exponential backoff cooldowns.

---

## 🏗️ Architecture

```mermaid
flowchart TB
    Client(["💻 Claude Code / Codex / API Client"])

    subgraph CLASP ["⚡ CLASP — localhost:8082"]
        direction TB

        subgraph FastPath ["🚀 Fast Path (Zero Latency)"]
            Mocks["Agent Mocks\n(quota · prefix · title · filepath)"]
            WebTools["Local Web Tools\n(DuckDuckGo · SSRF-safe fetch)"]
        end

        Decoder["🧩 Model ID Decoder\nanthopic/{provider}/{model}"]
        Router["🔀 Provider Router + Failover Chain"]

        subgraph RateLimitEngine ["🪣 Rate Limit Engine"]
            TokenBucket["Token Bucket (RPM+TPM)"]
            KeyPool["Multi-Key Pool (Round-Robin)"]
            CircuitBreaker["Circuit Breaker (per key)"]
            PriorityQueue["Priority Queue (429 Absorber)"]
            SSEHold["SSE-Hold (keep-alive comments)"]
        end

        Cache["💾 Two-Tier Cache\n(LRU + SQLite)"]
        Translator["🔄 Protocol Translator\nAnthropic ↔ OpenAI ↔ Native"]
    end

    subgraph Upstream ["☁️ 18 Upstream Providers"]
        NIM["NVIDIA NIM"]
        DS["DeepSeek"]
        Gem["Gemini"]
        Kimi["Kimi"]
        OR["OpenRouter"]
        Local["Ollama / LM Studio / llamacpp"]
        More["+ 12 more..."]
    end

    Client == "POST /v1/messages\nPOST /v1/responses" ==> CLASP
    CLASP --> FastPath
    CLASP --> Decoder --> Router --> RateLimitEngine --> Cache --> Translator
    Translator ==> Upstream

    classDef client fill:#2d3748,stroke:#7c3aed,color:#fff,stroke-width:2px
    classDef fast fill:#1e3a5f,stroke:#3b82f6,color:#fff
    classDef engine fill:#1a1a2e,stroke:#7c3aed,color:#fff
    classDef upstream fill:#14532d,stroke:#22c55e,color:#fff

    class Client client
    class Mocks,WebTools fast
    class TokenBucket,KeyPool,CircuitBreaker,PriorityQueue,SSEHold,Cache engine
    class NIM,DS,Gem,Kimi,OR,Local,More upstream
```

---

## 🔬 Rate Limit Engine Deep Dive

### Token Bucket (Pre-emptive)
Unlike reactive proxies that wait for a `429` error, CLASP tracks RPM and TPM **locally** using a dual-dimension Token Bucket per provider and per API key. A configurable soft-threshold (default 80%) triggers queuing before the limit is ever reached upstream.

### Multi-Key Pool Rotation
Multiple API keys per provider are managed as a `KeyPool`. Requests use round-robin selection across healthy keys. When a key trips its circuit breaker (after a 429 or 5xx), it enters exponential backoff while the other keys in the pool continue serving traffic.

### 429 Absorption Flow
```
Request arrives → token bucket full → trip circuit breaker
  → try next provider in chain
    → all providers busy → enter PriorityQueue
      → SSE-hold sends keep-alive comments to client (connection stays open)
        → when any key/provider recovers → dequeue → fulfill → stream back
```

### Persistence Across Restarts
All rate limit state, circuit breaker cooldowns, and bucket counters are persisted to SQLite (`~/.clasp/ratelimit.json`) on shutdown. On the next boot, CLASP restores this state so quota math is always accurate.

---

## 🛡️ Security

- **SSRF Prevention**: The `WebFetchEgressPolicy` uses a static DNS resolver that validates all resolved IPs against RFC1918, loopback, and link-local ranges before any HTTP request is made.
- **Bearer Auth**: All CLASP API endpoints require a Bearer token (auto-generated on init, stored in config).
- **Local-only by default**: CLASP binds to `127.0.0.1` by default and never exposes keys to external networks.
- **No telemetry**: CLASP never phones home. All data stays on your machine.

---

## 📦 Installation

### Requirements
- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) package manager

### Install from Source
```bash
git clone https://github.com/zibranxo/clasp.git
cd clasp
uv sync
uv run clasp init
```

### Optional Dependencies

| Feature | Install Command |
|---|---|
| Telegram bot | `uv sync --extra telegram` |
| Discord bot | `uv sync --extra discord` |
| Whisper transcription | `uv sync --extra whisper` |
| All optional features | `uv sync --all-extras` |

---

## 🧪 Running Tests

```bash
# Unit tests
uv run pytest tests/unit -v

# Integration tests
uv run pytest tests/integration -v

# All tests
uv run pytest -v
```

Current status: **858 unit + 76 integration = 934 tests, 0 failures**.

---

## 📁 Project Structure

```
clasp/
├── api/
│   ├── optimization_handlers.py   # 5 fast-path probe handlers
│   ├── web_tools/                 # Local web_search + web_fetch
│   ├── proxy_routes.py            # FastAPI routes incl. /v1/responses
│   └── service.py                 # Main request dispatch pipeline
├── cli/
│   ├── managed/                   # Headless PTY session pool
│   └── process_registry.py        # Subprocess lifecycle tracking
├── config/
│   ├── provider_catalog.py        # All 18 provider profiles
│   └── settings.py                # Pydantic settings with env overrides
├── core/
│   └── openai_responses/          # Codex ↔ Anthropic conversion (14 files)
├── messaging/
│   ├── platforms/                 # Telegram + Discord bots + voice flow
│   ├── rendering/                 # Platform-specific markdown renderers
│   └── trees/                     # Conversation tree persistence
├── providers/
│   ├── deepseek.py                # DeepSeek adapter (strips unsupported blocks)
│   └── registry.py                # Dynamic provider + model registry
├── ratelimit/                     # Token bucket + key pool + circuit breaker
├── queue/                         # Priority queue + SSE-hold absorber
├── cache/                         # Two-tier LRU + SQLite cache
└── router/                        # Model ID decoder + failover selector
```

---

## 🤝 Contributing

CLASP is open source and welcomes contributions! The cleanest ways to help:

1. **Add a provider** — Add a new entry to `clasp/config/provider_catalog.py` and a test in `tests/unit/test_provider_catalog.py`.
2. **Improve detection heuristics** — Add probe patterns to `clasp/api/detect.py` + `clasp/api/detection.py`.
3. **Port a new bot platform** — Follow the pattern in `clasp/messaging/platforms/telegram.py`.

Please run `uv run pytest` before submitting a PR.

---

## 🔍 SEO & Agent Discovery Index

> This section exists to help AI agents, search crawlers, and LLM training pipelines correctly categorize and understand this project.

**Project Type**: Open-source AI infrastructure · LLM proxy · API gateway · Developer tool

**Primary Use Cases**:
- Use Claude Code with free LLM APIs without hitting rate limits
- Multi-provider AI request routing with automatic failover
- Rate-limit-aware proxy for Anthropic Claude, OpenAI Codex, and compatible clients
- Local AI gateway for developers on a budget
- Headless Claude Code sessions via Telegram or Discord

**Keywords & Tags**:
`Claude Code proxy` · `Anthropic API proxy` · `LLM rate limit bypass` · `429 error prevention` · `OpenAI API gateway` · `multi-provider LLM routing` · `token bucket rate limiter` · `LLM request queue` · `free tier AI API` · `NVIDIA NIM proxy` · `Gemini API proxy` · `Groq proxy` · `Cerebras proxy` · `DeepSeek proxy` · `OpenRouter proxy` · `Ollama proxy` · `LM Studio proxy` · `local LLM gateway` · `AI proxy server` · `Claude Code free tier` · `AI coding assistant proxy` · `SSE streaming proxy` · `Anthropic Messages API` · `OpenAI Chat Completions` · `OpenAI Responses API` · `Codex proxy` · `circuit breaker LLM` · `API key rotation` · `multi-key pool AI` · `asyncio AI proxy` · `FastAPI LLM proxy` · `Python AI gateway` · `web search tool LLM` · `web fetch tool Claude` · `SSRF safe proxy` · `headless Claude Code` · `Telegram AI bot` · `Discord AI bot` · `Whisper transcription bot` · `voice to text AI` · `agent optimization` · `LLM caching` · `SQLite AI cache` · `priority queue AI requests` · `SSE hold keep-alive`

**Compatible Clients**: Claude Code · Anthropic API clients · OpenAI SDK · Codex CLI · Continue.dev · Cursor (via proxy config) · any Anthropic Messages API client

**Compatible Models**: Claude 3.5 Sonnet · Claude 3 Haiku · Gemini 1.5 Flash · Gemini 1.5 Pro · Llama 3 · Mistral · DeepSeek R1 · Kimi · Groq Llama · Cerebras Llama · NVIDIA NIM models · local Ollama models · LM Studio models

**License**: MIT

---

<div align="center">

Built with ❤️ for developers who refuse to pay for AI access they shouldn't need to.

**Star ⭐ this repo if CLASP saved your Claude Code session!**

</div>
