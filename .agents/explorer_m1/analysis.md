# Dynamic Model Selector (R2) Integration Analysis

This analysis outlines the differences between `free-claude-code-main` (the reference proxy codebase) and `clasp` (our implementation), catalogs all missing features, and designs a clear, low-latency integration strategy to port the dynamic model selector (R2) into `clasp`.

---

## 1. Reference Architecture (`free-claude-code-main`)

The `free-claude-code-main` repository acts as a rate-limit-aware Anthropic-to-multi-provider gateway. It handles model routing in two ways: static mapping (default fallback model per Claude tier) and **dynamic model selection (R2)**.

### A. Provider Implementations and Model Loading
- **Structure**: Individual providers (e.g. `gemini`, `nvidia_nim`, `groq`) reside in `providers/` and subclass either `OpenAIChatTransport` or `AnthropicMessagesTransport` (which inherit from `BaseProvider`).
- **Dynamic Listing**: The `BaseProvider` requires implementing `async def list_model_ids() -> frozenset[str]` and `async def list_model_infos() -> frozenset[ProviderModelInfo]`.
  - For OpenAI-compatible providers, `list_model_ids()` requests `GET /v1/models` from the upstream provider and extracts the models via `extract_openai_model_ids()`.
  - The `ProviderRegistry` (`providers/registry.py`) maintains `_model_infos_by_provider`. At server startup (inside the ASGI lifespan in `api/runtime.py`), the registry runs `start_model_list_refresh()`, spawning a background task that concurrently queries and caches the model lists for all enabled providers.

### B. Gateway Model Listing (`GET /v1/models`)
- **Route**: Defined in `api/routes.py` and delegates to `api/model_catalog.py::build_models_list_response()`.
- **Latency Optimization**: The response is constructed purely from the *in-memory cache* of the `ProviderRegistry` (`provider_registry.cached_prefixed_model_infos()`), making it extremely fast and non-blocking.
- **Prefix Encoding**: To expose models to Claude Code while remaining reversible, models are prefixed:
  1. `anthropic/{provider_id}/{provider_model}` (e.g. `anthropic/gemini/gemini-1.5-pro`): Indicates the model supports thinking.
  2. `claude-3-freecc-no-thinking/{provider_id}/{provider_model}` (e.g. `claude-3-freecc-no-thinking/gemini/gemini-1.5-flash`): Uses a client-side heuristic (Claude Code disables thinking for any model containing `claude-3-`) to force thinking off.

### C. Request Routing
- **Interception**: When a request targeting one of these prefixed models arrives at `/v1/messages`, `api/model_router.py::ModelRouter` intercepts and decodes it:
  - If a model matches the prefix pattern, it resolves to `provider_id` and `provider_model`, and sets whether thinking is allowed (e.g., `force_thinking_enabled = False` for the `claude-3-...` prefix).
  - The request pipeline (`api/request_pipeline.py`) replaces the requested model name with the raw upstream `provider_model` and routes the request directly to the target provider.

---

## 2. Target Architecture (`clasp`)

Our target implementation `clasp` has a different structure designed for robust local-first caching and rate-limiting.

### A. Settings and Catalog
- **Settings**: Defined in `clasp/config/settings.py` via nested Pydantic models: `server`, `routing` (holds `models` and `by_type` mapping), `providers` (dict of `{provider_name: ProviderConfig}`), `cache`, `optimizer`, and `shared_pool`.
- **Static Catalog**: Located in `clasp/config/provider_catalog.py`. It holds static metadata (`ProviderProfile`) for each provider, such as default rate limits (`rpm_limit`, `tpm_limit`) and capability flags (`supports_tools`, `supports_thinking`).

### B. Proxy Routing Logic
- **Proxy Endpoints**: Exposed in `clasp/api/proxy_routes.py`. Currently, `GET /v1/models` returns a static list (`STATIC_MODEL_LIST`) containing only Claude model names (e.g. `claude-sonnet-4-5`).
- **Resolution**: Incoming requests are handled by `clasp/api/service.py`. The model is resolved via `clasp/router/selector.py`, which walks the `provider_chain` and calls `clasp/router/model_map.py::resolve_model()`.
- **Limitation**: Currently, `resolve_model` only supports mapping standard Claude model names to the statically configured model for that tier. It lacks any capability to route directly based on a custom model name or prefix.

### C. Web UI / Internal Routing
- **Architecture**: `clasp/ui/routes.py` serves the Alpine.js single-page application at `/ui`. `clasp/internal/routes.py` exposes a loopback-only `/internal/*` API for config management, live status tailing, and log retrieval.
- **Provider Models**: The Web UI uses `GET /internal/providers/{provider_name}/models` to fetch models for a specific provider. This is handled using a separate utility `_models_endpoint_for` and cached for 1 hour.

---

## 3. Gap Analysis (Missing Features in `clasp`)

The following features and integrations present in `free-claude-code-main` are absent in `clasp`:

| Category | Missing Feature / Integration | Description & Impact | Severity |
|---|---|---|---|
| **Routing** | **Dynamic Model Selector (R2)** | The ability to dynamically list and route to specific provider models via `anthropic/` or `claude-3-` prefixes. Crucial for custom model usage. | **HIGH** |
| **Integrations** | **DeepSeek Provider** | Native Anthropic-compatible adapter for DeepSeek. | **MEDIUM** |
| **Integrations** | **Mistral Codestral** | Upstream adapter for Mistral's Codestral IDE endpoint. | **LOW** |
| **Integrations** | **OpenCode / OpenCode Go** | Custom adapter for OpenCode Zen / Go endpoints. | **LOW** |
| **Integrations** | **Wafer / Kimi / Z.ai** | Anthropic-compatible adapters for Wafer, Moonshot Kimi, and Z.ai. | **LOW** |
| **Integrations** | **Llama.cpp** | Local execution adapter for Llama.cpp. | **LOW** |
| **Optimization** | **Web Search/Fetch Tools** | Intercepts model tool calls to perform loopback web fetches (with security/private IP checks). | **MEDIUM** |
| **Optimization** | **Safety Classifier Bypass** | Bypasses thinking for safety classifier checks (reduces latency/costs). | **MEDIUM** |
| **Compatibility** | **OpenAI Responses Endpoint** | `/v1/responses` endpoint allowing OpenAI-compatible clients to query the proxy. | **LOW** |
| **Platform** | **Messaging / CLI sessions** | Telegram/Discord background CLI runners. | **LOW** |

*Note: `clasp` has `together` provider integration, which is absent in `free-claude-code-main`.*

---

## 4. Integration Strategy for Dynamic Model Selector (R2)

To port the dynamic model selector (R2) into `clasp` while preserving its Web UI routing architecture and optimizing for minimal latency, we will follow this strategy:

### Step 1: Extend Provider Registry with Asynchronous Cache Warming
We will modify `clasp/providers/registry.py` to support dynamic model list discovery and caching:
1. **Model Cache Storage**: Add `self._model_lists: dict[str, list[str]] = {}` to `ProviderRegistry`.
2. **Asynchronous Warming**: Implement a background refresher:
   ```python
   async def refresh_model_lists(self, settings: Settings):
       enabled_providers = settings.enabled_providers()
       tasks = {}
       for provider_name in enabled_providers:
           provider = self.get(provider_name)
           if not provider:
               continue
           # Get first API key or empty placeholder for local
           pcfg = settings.providers.get(provider_name)
           api_key = pcfg.keys[0] if pcfg and pcfg.keys else None
           tasks[provider_name] = asyncio.create_task(provider.list_models(api_key))
       
       results = await asyncio.gather(*tasks.values(), return_exceptions=True)
       for name, result in zip(tasks.keys(), results):
           if isinstance(result, Exception):
               logger.warning(f"Failed to fetch models for {name}: {result}")
               continue
           self._model_lists[name] = result
   ```
3. **Start Refresher at Lifespan**: Call `start_model_list_refresh()` inside `clasp/server.py::lifespan` immediately after `build_registry(settings)`. This ensures that registry warming runs concurrently in the background and does not block server startup.

### Step 2: Update Gateway Model List Endpoint
Modify `clasp/api/proxy_routes.py::list_models` (and the underlying `answer_models` in `clasp/api/optimize.py`):
1. **Dynamic Model Merging**: Instead of returning only `STATIC_MODEL_LIST`, read the cached models from the `ProviderRegistry` singleton.
2. **Construct Prefixed Lists**:
   - For every provider and model in the cache, append two variants to the `data` array:
     - `{"id": f"anthropic/{provider_name}/{model_id}", "object": "model", "owned_by": "clasp"}`
     - `{"id": f"claude-3-freecc-no-thinking/{provider_name}/{model_id}", "object": "model", "owned_by": "clasp"}`
3. **Latency Optimization**: The response will be constructed purely from the in-memory cache (`_model_lists`), achieving **O(1) request-time latency** and zero network overhead.

### Step 3: Implement Gateway Model ID Decoder
Create a decoder utility in a new file `clasp/router/gateway_ids.py` (or within `clasp/router/model_map.py`):
```python
def decode_gateway_model_id(model_name: str) -> tuple[str, str, bool] | None:
    """
    Decodes f"anthropic/{provider}/{model}" or f"claude-3-freecc-no-thinking/{provider}/{model}".
    Returns (provider_name, model_slug, thinking_enabled) or None.
    """
    prefix, sep, remainder = model_name.partition("/")
    if not sep:
        return None
        
    if prefix == "anthropic":
        thinking = True
    elif prefix == "claude-3-freecc-no-thinking":
        thinking = False
    else:
        return None
        
    provider, sep2, model_slug = remainder.partition("/")
    if not sep2 or not model_slug:
        return None
        
    return provider, model_slug, thinking
```

### Step 4: Bypass Static Mapping in `model_map` and `selector`
Integrate the decoder into the routing flow:
1. **Model Map Modification**: In `clasp/router/model_map.py::resolve_model`:
   - Check if `request.body.get("model")` is a gateway-safe prefixed ID.
   - If decoded successfully:
     - Verify that the target provider matches the candidate `provider_name`.
     - Return the decoded `model_slug` directly, bypassing all `by_type` and tier substring keyword mapping.
2. **Selector Modification**: In `clasp/router/selector.py::select`:
   - If the requested model is a decoded prefixed ID, restrict the `candidates` loop to **only** contain the target provider.
   - This prevents trying other providers in the chain for a specific requested model.
   - If the target provider has no keys available or is circuit-broken, return `None` (triggers normal failover / queueing mechanism).

### Step 5: Thinking Support Propagation
- If `thinking` was decoded as `False` (from the `claude-3-...` prefix), ensure this overrides any provider capability or request settings.
- Inside `clasp/api/service.py` (or `BaseProvider.stream`), strip the `thinking` block from the Anthropic request body prior to converting to OpenAI format, or force the `thinking` parameters to be disabled.

### Step 6: Strictly Preserve Web UI Routes
- All endpoints under `/internal/*` in `clasp/internal/routes.py` (e.g. `/internal/providers/{provider_name}/models`) are **left unchanged**.
- This preserves the exact contract between the configuration dashboard and the loopback backend. The dashboard continues to view/configure the providers as before, while Claude Code gets the full dynamic model capability via `GET /v1/models` and `/v1/messages`.
