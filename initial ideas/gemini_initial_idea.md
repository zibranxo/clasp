# 🚀 Universal Claude Code Router: Feature Architecture & Ideas

## Overview
A locally hosted, intelligent proxy designed to route Anthropic-formatted requests from tools like Claude Code to various OpenAI-compatible endpoints (NVIDIA Build, Google Gemini, OpenRouter, Cerebras, Fireworks, etc.). 

The core mission is to **democratize access** by preventing 429 (Too Many Requests) lockouts on free-tier APIs through advanced traffic shaping, queuing, and fallback strategies.

---

## 1. The "Never 429" Engine (Traffic Shaping)
Instead of blindly forwarding requests and crashing when a limit is hit, the router acts as a smart queue.

* **Connection Holding (The Delay Strategy):**
  * Tracks Requests Per Minute (RPM) locally.
  * If the router detects that the next request will breach the limit (e.g., the 41st request on NVIDIA's 40 RPM limit), it **intercepts and holds** the HTTP connection open.
  * It waits precisely until the rate limit resets (e.g., the start of the next minute) before forwarding. To the user/Claude Code, it just looks like a delayed response, preventing a fatal crash.
* **Optimized Token Bucket Algorithm:**
  * Implements an efficient Token Bucket rate limiter (e.g., using Python's `asyncio` or C++ depending on your backend choice) to track both RPM and TPM (Tokens Per Minute) across simultaneous asynchronous requests.
* **Local Token Estimation:**
  * Uses a local tokenizer (like `tiktoken`) to estimate the prompt size *before* sending. If the payload will exceed the remaining TPM window, the request is queued.

## 2. Multi-Key and Multi-Provider Fallback (Load Balancing)
Maximize free-tier quotas by combining multiple accounts and providers.

* **Provider Pooling (Round-Robin):**
  * Configuration allows arrays of API keys (e.g., `["nvidia_key_1", "nvidia_key_2", "gemini_key_1"]`).
  * The router cycles through these keys, effectively multiplying your RPM limits.
* **Cascading Fallbacks:**
  * If a provider goes down or strictly enforces a rate limit despite the queue, the router automatically reroutes the request to a fallback provider (e.g., from `NVIDIA Llama 3.1 70b` -> `Groq Llama 3.1 70b`). The frontend tool never knows the backend switched.

## 3. Local Caching Layer
Claude Code often gets stuck in loops, asking the exact same questions or reading the same files repeatedly.

* **Exact-Match & Semantic Caching:**
  * Implement an in-memory or SQLite cache.
  * If an outgoing request perfectly matches a request sent in the last 5 minutes, the router instantly serves the cached response.
  * **Result:** 0 API quota used, 0 latency.

## 4. Native LLM Safety & Prompt Filtering
Before spending valuable API quota, process the request locally.

* **Hybrid Detection Middleware:**
  * Inspect outgoing prompts for known agentic hallucination triggers, infinite loop commands, or malformed structures.
  * Block bad requests locally and return a helpful terminal error to the user, saving the API call.

## 5. Payload Optimization & Compression
Free tiers often have strict context window limits (e.g., 8k or 32k tokens), whereas Claude Code assumes a 200k window.

* **System Prompt Trimming:**
  * Middleware that selectively strips non-essential instructions from the massive default Anthropic system prompt before sending it to a smaller-context model.
* **Message History Pruning:**
  * Automatically summarizes or drops the oldest conversation turns when approaching the target model's maximum context limit.

## 6. Terminal UI (TUI) Dashboard
A sleek way to monitor the router while it runs in the background.

* **Real-Time CLI Metrics:**
  * Built using a library like `rich` or `textual` (Python) or `ncurses` (C++).
  * Displays:
    * Active RPM/TPM usage bars.
    * Current active provider and key.
    * Number of requests currently "held" in the queue.
    * Total API quota saved via the Cache.

---

## 📅 Roadmap for `plan.md`

When ready to begin coding, the development phases should be structured as follows:

* **Phase 1: Core Proxy & Translation:** Basic HTTP server intercepting `/v1/messages` and translating the Anthropic JSON schema to the OpenAI JSON schema.
* **Phase 2: State & Queueing:** Building the in-memory rate limit trackers and the async holding pattern.
* **Phase 3: Load Balancing:** Implementing the multi-provider routing and fallback logic.
* **Phase 4: Advanced Features:** Adding the TUI dashboard, caching layer, and prompt optimization.
