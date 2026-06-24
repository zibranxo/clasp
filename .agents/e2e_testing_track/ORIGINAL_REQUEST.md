## 2026-06-23T09:18:04Z
You are the E2E Test Suite Developer. Your working directory is c:\code\clasp\.agents\e2e_testing_track.

Your task is to:
1. Write the E2E Test Infrastructure document at `c:\code\clasp\TEST_INFRA.md` following the template below:
```markdown
# E2E Test Infra: Clasp Dynamic Model Selector

## Test Philosophy
- Opaque-box, requirement-driven. No dependency on implementation design.
- Methodology: Category-Partition + BVA + Pairwise + Workload Testing.

## Feature Inventory
| # | Feature | Source (requirement) | Tier 1 | Tier 2 | Tier 3 |
|---|---------|---------------------|:------:|:------:|:------:|
| 1 | Dynamic Model Listing | ORIGINAL_REQUEST R2, PROJECT.md | 5 | 5 | ✓ |
| 2 | Routing of OpenAI Models | ORIGINAL_REQUEST R2/R3, PROJECT.md | 5 | 5 | ✓ |
| 3 | Routing of Gemini Models | ORIGINAL_REQUEST R2/R3, PROJECT.md | 5 | 5 | ✓ |
| 4 | Caching and Latency Checks | ORIGINAL_REQUEST R3, PROJECT.md | 5 | 5 | ✓ |

## Test Architecture
- Test runner: pytest
- Invocation: `uv run pytest tests/integration/test_dynamic_model_routing.py -v`
- Test case format: pytest async test cases using `httpx.ASGITransport` to call the FastAPI app.
- Directory layout: `tests/integration/test_dynamic_model_routing.py`

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Multi-turn developer conversation | Dynamic listing, Gemini routing, state preservation | High |
| 2 | Tool calling developer flow | Dynamic listing, OpenAI routing, tool calls | High |
| 3 | Vision prompt routing flow | OpenAI/Gemini routing, vision capability checking | High |
| 4 | Failover and queueing under rate limit | Routing of OpenAI/Gemini, key exhaustion | High |
| 5 | Full workspace start and chat session | Discovery, token count, trivial probe, and completion | High |

## Coverage Thresholds
- Tier 1: 5 per feature (20 total)
- Tier 2: 5 per feature (20 total)
- Tier 3: pairwise coverage of major feature interactions (4 total)
- Tier 4: 5 realistic application scenarios (5 total)
- Total tests: 49
```

2. Implement a comprehensive integration/E2E test suite in `c:\code\clasp\tests\integration\test_dynamic_model_routing.py`. The suite must contain exactly 49 tests (as mapped out in the 4 tiers). Use `pytest.mark.parametrize` and well-defined async test functions.
Since the dynamic model listing and routing are not yet fully implemented in the codebase (they are in milestone M3), the tests should target the FastAPI app routes `/v1/models` and `/v1/messages`. You can use `pytest.mark.xfail(reason="Feature pending implementation in M3")` or mock/stub overrides where appropriate to ensure the test suite is compilable, executes correctly, and runs to completion.
Make sure you mock the upstream HTTP requests so the tests do not make actual external network calls to OpenAI, Gemini, etc.

Here is the inventory of the 49 test cases to implement:
- **Tier 1: Feature Coverage (20 tests)**
  - Dynamic Model Listing:
    1. test_list_models_empty_providers (no providers enabled, returns Anthropic default models only)
    2. test_list_models_one_provider_enabled (returns Anthropic + enabled provider models)
    3. test_list_models_multiple_providers_enabled (returns Anthropic + all enabled provider models)
    4. test_list_models_contains_no_thinking_variants (contains both standard and no-thinking variant IDs)
    5. test_list_models_response_format (conforms to standard OpenAI/Anthropic spec structure)
  - OpenAI Routing:
    6. test_route_openai_non_stream (routes to OpenAI provider, returns non-stream JSON response)
    7. test_route_openai_stream (routes to OpenAI provider, returns SSE stream)
    8. test_route_openai_custom_headers (routes requests with custom headers properly)
    9. test_route_openai_custom_base_url (routes to custom base url for Ollama/LM Studio)
    10. test_route_openai_api_key_header (authenticates and routes with bearer key from settings)
  - Gemini Routing:
    11. test_route_gemini_non_stream (routes to Gemini provider, returns non-stream JSON response)
    12. test_route_gemini_stream (routes to Gemini provider, returns SSE stream)
    13. test_route_gemini_thinking_enabled (gemini model with thinking option)
    14. test_route_gemini_thinking_disabled (gemini model using the no-thinking ID variant)
    15. test_route_gemini_safety_block_handling (translates Gemini safety blocks into correct error structure)
  - Caching and Latency:
    16. test_cache_hit_prevents_upstream_call (returns cached response instantly)
    17. test_cache_miss_calls_upstream (calls upstream on first request)
    18. test_cache_ttl_expiration (cache expires and calls upstream again)
    19. test_cache_disabled_always_calls_upstream (respects cache.enabled=False setting)
    20. test_cache_key_generation (cache key includes model, messages, tools, system)

- **Tier 2: Boundary & Corner Cases (20 tests)**
  - Listing boundaries:
    21. test_list_models_missing_settings (handles missing settings gracefully)
    22. test_list_models_provider_in_chain_but_disabled (skips disabled providers)
    23. test_list_models_duplicate_configs (handles duplicated model refs or providers gracefully)
    24. test_list_models_invalid_provider_name (ignores unknown/invalid provider name in chain)
    25. test_list_models_empty_keys_for_paid_provider (skips paid provider if keys list is empty)
  - OpenAI routing boundaries:
    26. test_route_openai_missing_provider_api_key (returns 401/403 or descriptive error)
    27. test_route_openai_unsupported_provider_model (gracefully rejects request)
    28. test_route_openai_empty_request_messages (returns unprocessable entity)
    29. test_route_openai_extreme_system_prompt (verifies serialization under massive inputs)
    30. test_route_openai_upstream_429_handling (gracefully handles and reports upstream rate limit)
  - Gemini routing boundaries:
    31. test_route_gemini_missing_api_key (skips/rejects request with appropriate error)
    32. test_route_gemini_safety_block_empty_response (handles edge cases where response is empty due to safety)
    33. test_route_gemini_invalid_model_string (rejects invalid model strings)
    34. test_route_gemini_upstream_500_error (propagates or handles upstream server failures)
    35. test_route_gemini_special_characters_in_prompt (ensures encoding and serialization remain valid)
  - Caching boundaries:
    36. test_cache_db_read_failure_fallback (fallback gracefully if sqlite db is locked or fails)
    37. test_cache_huge_payload (handles caching of huge prompts and responses)
    38. test_cache_different_parameters (checks that temperature/max_tokens affect cache key)
    39. test_cache_empty_response (does not cache empty or error responses)
    40. test_cache_sqlite_path_expansion (expands ~ in sqlite path setting)

- **Tier 3: Cross-Feature Combinations (4 tests)**
  41. test_cross_model_switching_mid_session (sequential requests to different provider models)
  42. test_concurrent_requests_different_providers (parallel requests route to correct providers without state bleeding)
  43. test_cache_hit_one_model_miss_another (validates independent caching per model)
  44. test_token_counting_cross_provider (verifies token estimation works accurately across different provider request formats)

- **Tier 4: Real-World Scenarios (5 tests)**
  45. test_scenario_multiturn_dev_conversation (simulates a multi-turn coding help request)
  46. test_scenario_tool_calling_dev_flow (simulates developer tool usage loop)
  47. test_scenario_vision_prompt_routing (simulates image-based inspection then text query)
  48. test_scenario_failover_and_queueing (simulates provider rate limiting and failover)
  49. test_scenario_full_workspace_startup (simulates Claude Code startup handshake: models -> count_tokens -> trivial probe -> stream message)

3. Run the tests using pytest to verify they are discovered and execute:
   `uv run pytest tests/integration/test_dynamic_model_routing.py -v --tb=short`
Confirm they execute completely. Document which pass (e.g. those with mocks) and which are marked as xfail/skipped.

4. Write `c:\code\clasp\TEST_READY.md` containing the test runner command, coverage summary per tier, and a feature checklist matching the template in the orchestrator's workflow prompt.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.
