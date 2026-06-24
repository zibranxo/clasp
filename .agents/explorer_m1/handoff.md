# Handoff Report — Dynamic Model Selector (R2) Integration Analysis

This report outlines the observations, logic, and integration strategy for porting the dynamic model selector (R2) from `free-claude-code-main` into `clasp`.

## 1. Observation

The following files and lines were observed in `free-claude-code-main`:
- **Model Registry and Cache Refresher**: `providers/registry.py` lines 366-444 implement the cache refresh logic:
  - Line 366: `async def refresh_model_list_cache(self, settings: Settings, *, only_missing: bool = False)`
  - Line 379: `def start_model_list_refresh(self, settings: Settings)` creates a background task using `asyncio.create_task(self._run_model_list_refresh(settings, provider_ids))`.
  - Line 426: `provider.list_model_infos()` is used to gather model information concurrently via `asyncio.gather`.
- **Model List Construction**: `api/model_catalog.py` lines 53-90 implement the listing logic:
  - Line 53: `def build_models_list_response(settings: Settings, provider_registry: ProviderRegistry | None) -> ModelsListResponse:`
  - Line 74: `for model_info in provider_registry.cached_prefixed_model_infos():` appends discovered/prefixed models.
- **Prefix Encoding and Decoding**: `api/gateway_model_ids.py` lines 7-30 define prefix encoding:
  - Line 7: `GATEWAY_MODEL_ID_PREFIX = "anthropic"`
  - Line 12: `NO_THINKING_GATEWAY_MODEL_ID_PREFIX = "claude-3-freecc-no-thinking"`
  - Line 32: `def decode_gateway_model_id(model_name: str) -> DecodedGatewayModelId | None:` handles decoding.
- **Model Router**: `api/model_router.py` lines 86-107 implement the direct routing check:
  - Line 86: `def _direct_provider_model(...)` matches prefix models and extracts provider/model slugs.

The following files and lines were observed in `clasp`:
- **FastAPI Routes**: `clasp/api/proxy_routes.py` lines 95-102 define `/v1/models` which returns `STATIC_MODEL_LIST`:
  - Line 101: `return JSONResponse(answer_models())` (from `clasp/api/optimize.py`).
- **Selector Logic**: `clasp/router/selector.py` lines 116-191 walk candidates and map models via `model_map.resolve_model`:
  - Line 137: `model_slug = model_map.resolve_model(request, provider_name, settings)`
- **Model Map Resolution**: `clasp/router/model_map.py` lines 108-160 only match against `by_type` and Claude tier keywords (e.g. `sonnet` / `opus` / `haiku`). It lacks prefix decoding.
- **Web UI Routes**: `clasp/ui/routes.py` and `clasp/internal/routes.py` handle front-end rendering and management API calls. Specifically, `clasp/internal/routes.py` contains `/internal/providers/{provider_name}/models` for querying individual provider models.

---

## 2. Logic Chain

1. **Static Limitation of `clasp`**: Currently, `clasp` only advertises Claude model names (e.g., `claude-sonnet-4-5`) via `GET /v1/models` (Observation: `clasp/api/proxy_routes.py:101`). If a client requests any non-Anthropic model directly, `model_map.resolve_model` cannot handle it because it only resolves models based on type overrides and tier substrings (Observation: `clasp/router/model_map.py:108-160`).
2. **Reversible Prefixes in Reference**: `free-claude-code-main` resolves this by encoding the provider and model into the model ID returned to the client using `anthropic/` or `claude-3-` prefixes (Observation: `api/gateway_model_ids.py:7-12`). This makes the IDs reversible and lets the client choose specific models.
3. **Decoupled Registry Warming**: Querying upstream providers' `/models` endpoints at request time (within `GET /v1/models`) would introduce significant latency. `free-claude-code-main` solves this by warming the cache in the background during server startup (Observation: `providers/registry.py:379`).
4. **Integration Strategy**:
   - By implementing a similar non-blocking cache refresher inside `clasp/providers/registry.py` and starting it at lifespan startup (`clasp/server.py`), we can retrieve the models with zero request-time latency.
   - Modifying `clasp/api/optimize.py` to merge these cached models into `/v1/models` will let the client see them.
   - Updating `resolve_model` in `clasp/router/model_map.py` to decode prefix IDs and restricting candidate providers in `clasp/router/selector.py` ensures the proxy bypasses the static mappings and routes requests directly to the selected provider.
   - Leaving `/internal/*` routes intact preserves the dashboard/Web UI routing architecture.

---

## 3. Caveats

- **Upstream `/models` Availability**: The integration strategy assumes all enabled providers have a functioning `/models` endpoint. If an upstream provider's `/models` endpoint is offline or times out, that provider's models will not be cached, and the client will not see them. To mitigate this, fallback lists or hardcoded defaults can be added per provider if needed.
- **Dynamic Key Validation**: Key validity is checked at startup in `clasp/providers/registry.py`. If a key is invalid, the background refresher might fail to load the models for that provider.
- **Local/Offline Mode**: In offline testing, we should allow loading `static_models` to prevent long timeouts.

---

## 4. Conclusion

- `clasp` is currently missing the dynamic model selector (R2) and several provider integrations (DeepSeek, Kimi, etc.) present in `free-claude-code-main`.
- Porting R2 into `clasp` requires extending `clasp`'s `ProviderRegistry` with background cache warming, updating `clasp/api/proxy_routes.py` to return the cached prefix models, and updating `clasp/router/model_map.py` and `clasp/router/selector.py` to decode and route requests targeting these prefix models.
- This design achieves O(1) request-time latency by utilizing background registry cache warming, and strictly preserves the Web UI routing architecture.

---

## 5. Verification Method

Once implemented, the changes can be verified by:
1. **Starting the Server**: Run `uv run python -m clasp.cli.main server --no-browser`.
2. **Checking Models List**: Run a request to the proxy's models endpoint using `curl`:
   ```bash
   curl -H "Authorization: Bearer freecc" http://127.0.0.1:8082/v1/models
   ```
   Verify that it contains both standard Claude models and prefixed models (e.g. `anthropic/nvidia_nim/...` and `claude-3-freecc-no-thinking/nvidia_nim/...`).
3. **Running Route Tests**: Execute a streaming request to a specific model to verify that the request bypasses static mappings and is routed correctly:
   ```bash
   curl -X POST -H "Authorization: Bearer freecc" -H "Content-Type: application/json" \
     -d '{"model": "anthropic/nvidia_nim/meta/llama-3.1-70b-instruct", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10}' \
     http://127.0.0.1:8082/v1/messages
   ```
4. **Running Unit/Integration Tests**: Run the existing unit and integration tests to ensure no regressions:
   ```bash
   uv run pytest tests/unit -v --tb=short
   uv run pytest tests/integration -v --tb=short
   ```
