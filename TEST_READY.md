# TEST_READY: Clasp Dynamic Model Selector Test Suite

## Test Execution Command
To run the integration and E2E test suite, execute:
```bash
uv run pytest tests/integration/test_dynamic_model_routing.py -v
```

## Coverage Summary
- **Tier 1: Feature Coverage**
  - Dynamic Model Listing: 5 tests (100% covered)
  - OpenAI Routing: 5 tests (100% covered)
  - Gemini Routing: 5 tests (100% covered)
  - Caching and Latency Checks: 5 tests (100% covered)
  - *Subtotal*: 20 tests
- **Tier 2: Boundary & Corner Cases**
  - Listing boundaries: 5 tests (100% covered)
  - OpenAI routing boundaries: 5 tests (100% covered)
  - Gemini routing boundaries: 5 tests (100% covered)
  - Caching boundaries: 5 tests (100% covered)
  - *Subtotal*: 20 tests
- **Tier 3: Cross-Feature Combinations**
  - Multi-provider model switching, concurrency, cache isolation, token counting: 4 tests (100% covered)
- **Tier 4: Real-World Scenarios**
  - Multi-turn conversation, tool calling loop, vision prompt routing, rate limit failover, full Claude Code workspace start: 5 tests (100% covered)
- **Total Suite Size**: 49 tests (all executing and passing)

## Feature Checklist
### 1. Dynamic Model Listing
- [x] `test_list_models_empty_providers` — Returns default Anthropic models only when no providers enabled.
- [x] `test_list_models_one_provider_enabled` — Dynamically appends models from enabled provider.
- [x] `test_list_models_multiple_providers_enabled` — Combines models from multiple active providers.
- [x] `test_list_models_contains_no_thinking_variants` — Returns both standard and no-thinking variant model IDs.
- [x] `test_list_models_response_format` — Conforms strictly to standard OpenAI/Anthropic model list JSON schema.
- [x] `test_list_models_missing_settings` — Gracefully defaults to static models if settings config is missing.
- [x] `test_list_models_provider_in_chain_but_disabled` — Excludes models of disabled chain providers.
- [x] `test_list_models_duplicate_configs` — Safely de-duplicates identical model IDs.
- [x] `test_list_models_invalid_provider_name` — Ignores invalid provider names in routing chain.
- [x] `test_list_models_empty_keys_for_paid_provider` — Excludes paid providers when no API keys are configured.

### 2. OpenAI Routing
- [x] `test_route_openai_non_stream` — Routes request to OpenAI format and receives standard JSON response.
- [x] `test_route_openai_stream` — Translates OpenAI streaming chunks to valid Anthropic SSE events.
- [x] `test_route_openai_custom_headers` — Propagates custom request headers to upstream.
- [x] `test_route_openai_custom_base_url` — Targets custom provider endpoints (Ollama/LM Studio).
- [x] `test_route_openai_api_key_header` — Restricts access to loopback client using bearer token auth.
- [x] `test_route_openai_missing_provider_api_key` — Returns descriptive authentication error if provider API key is missing.
- [x] `test_route_openai_unsupported_provider_model` — Gracefully rejects requests to unsupported models.
- [x] `test_route_openai_empty_request_messages` — Raises 422 Unprocessable Entity for empty message inputs.
- [x] `test_route_openai_extreme_system_prompt` — Handles serialization of large system prompts (e.g. 200KB).
- [x] `test_route_openai_upstream_429_handling` — Handles and reports upstream provider 429 rate limit errors.

### 3. Gemini Routing
- [x] `test_route_gemini_non_stream` — Standard Gemini API completions routing.
- [x] `test_route_gemini_stream` — SSE translation from Gemini OpenAI-compatible stream.
- [x] `test_route_gemini_thinking_enabled` — Passes thinking budget constraints to Gemini model configurations.
- [x] `test_route_gemini_thinking_disabled` — Disables thinking when no-thinking variant model ID is used.
- [x] `test_route_gemini_safety_block_handling` — Converts Gemini safety blocks into correct Anthropic invalid_request_error structure.
- [x] `test_route_gemini_missing_api_key` — Validates missing API key validation.
- [x] `test_route_gemini_safety_block_empty_response` — Handles empty response block due to safety filter trigger.
- [x] `test_route_gemini_invalid_model_string` — Rejects invalid model syntax.
- [x] `test_route_gemini_upstream_500_error` — Gracefully reports upstream Gemini server errors.
- [x] `test_route_gemini_special_characters_in_prompt` — Ensures proper encoding of emojis and unicode content.

### 4. Caching & Latency
- [x] `test_cache_hit_prevents_upstream_call` — Retrieves from memory cache and skips selector/provider call.
- [x] `test_cache_miss_calls_upstream` — Populates cache on first request.
- [x] `test_cache_ttl_expiration` — Expires old cache entries according to TTL settings and calls upstream again.
- [x] `test_cache_disabled_always_calls_upstream` — Overrides caching if cache.enabled is False.
- [x] `test_cache_key_generation` — Generates unique hash keys from model, messages, system prompt, and tools.
- [x] `test_cache_db_read_failure_fallback` — Falls back gracefully to memory/upstream if SQLite DB is locked or fails.
- [x] `test_cache_huge_payload` — Skips caching if payload size exceeds maximum threshold limits.
- [x] `test_cache_different_parameters` — Generates separate cache keys if temperature or max_tokens vary.
- [x] `test_cache_empty_response` — Restricts caching on error or empty event payloads.
- [x] `test_cache_sqlite_path_expansion` — Expands user path prefix (e.g. `~`) for sqlite database path configuration.

### 5. Multi-Feature & Real-World Scenarios
- [x] `test_cross_model_switching_mid_session` — Switch models mid-session (Gemini -> Sonnet).
- [x] `test_concurrent_requests_different_providers` — Parallel requests route cleanly without state bleeding.
- [x] `test_cache_hit_one_model_miss_another` — Ensures independent caching per model key.
- [x] `test_token_counting_cross_provider` — Token count estimations work across Sonnet/Gemini request payloads.
- [x] `test_scenario_multiturn_dev_conversation` — Simulates multi-turn coding help request.
- [x] `test_scenario_tool_calling_dev_flow` — Simulates developer tool use loop.
- [x] `test_scenario_vision_prompt_routing` — Simulates image prompt inspection routing.
- [x] `test_scenario_failover_and_queueing` — Simulates primary rate limit and seamless failover to secondary provider.
- [x] `test_scenario_full_workspace_startup` — Simulates Claude Code workspace startup sequence (discovery -> count -> probe -> stream).
