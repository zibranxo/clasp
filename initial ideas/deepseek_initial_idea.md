# Claude Code Router — Design & Feature Ideas

A local proxy that lets **Claude Code** (Anthropic’s native CLI) work with any OpenAI-compatible API, while intelligently avoiding `429 Too Many Requests` errors by spreading requests across multiple free providers.  
The primary goal is to enable students to use free tiers (NVIDIA NIM, Google Gemini, OpenAI, Fireworks, Cerebras, OpenRouter, etc.) reliably for long coding sessions.

---

## 1. Core Rate‑Limit Management

### a. Multi‑token‑bucket per provider
Each provider gets a **token bucket** that strictly enforces its RPM (requests per minute) / TPM (tokens per minute):
- Configurable limits, pre‑filled for popular free tiers (e.g., NVIDIA: 40 RPM, Gemini: 15 RPM, etc.).
- Token consumption is tracked locally (in‑memory + persisted to SQLite).
- A request is only dispatched when a token is available; otherwise it is **queued** or routed to another provider.

### b. 429‑aware back‑pressure
When a provider returns a `429` response, the system:
- Immediately drains its bucket and enters a **cooldown** state.
- Parses the `Retry-After` header to schedule re‑activation.
- Uses **exponential backoff** for repeated failures (e.g., 429s without a header).
- Silently retries the same request on the next available provider (transparent failover).

### c. Pre‑request quota check & scheduling
Before sending, the router evaluates all enabled providers and picks one based on a configurable strategy:
- `least-loaded` – provider with the highest remaining RPM capacity.
- `priority-chain` – try Provider A first, if full fall back to B, then C.
- `cost-aware` – prefer completely free endpoints, then ones that consume limited free credits.
- Session stickiness can be relaxed to allow migration after a few turns.

### d. Request queue with estimated wait time
If **all** providers are currently rate-limited, the request goes into a priority queue.
- The CLI displays: _“All free providers are cooling down – your request will be processed in ~2 minutes.”_
- A spinner or progress indicator is shown until a provider becomes available and the stream begins.
- The user never sees a raw 429 error unless the queue times out.

---

## 2. Provider Abstraction & Model Mapping

### a. Smart model aliases with capability tiers
The router maps Claude models to the best matching free model that can currently serve the request:
```yaml
mappings:
  claude-3-opus: 
    - nvidia/llama-3.1-nemotron-70b
    - openai/gpt-4o-mini
  claude-3-sonnet:
    - gemini-1.5-pro
    - fireworks/llama-v3p1-70b
  claude-3-haiku:
    - cerebras/llama3.1-8b
    - openrouter/mistral-7b-instruct