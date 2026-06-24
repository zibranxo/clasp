# Changes Log

## 1. Gateway Prefix Case-Insensitivity
- **Modified File**: `clasp/router/model_map.py`
- **Details**: Changed `decode_gateway_model_id` to convert the prefix partition to lowercase (`prefix_lower = prefix.lower()`) before performing comparisons with `"anthropic"` and `"claude-3-freecc-no-thinking"`. This ensures case-insensitivity when client or proxy requests prefixed gateway model IDs.
- **Unit Tests**: Added `test_decode_gateway_model_id_case_insensitivity` to `tests/unit/test_model_map.py` to assert that variations like `Anthropic`, `CLAUDE-3-FREECC-NO-THINKING`, and `aNtHrOpIc` are decoded correctly.

## 2. Refactor Integration Test Mocks
- **Modified File**: `tests/integration/test_dynamic_model_routing.py`
- **Details**: Refactored the `app_with_mocks` fixture:
  - Removed the monkeypatch of the `clasp.api.proxy_routes.answer_models` function.
  - Added a mock of `clasp.providers.registry.get_model_lists` which queries the dynamic settings and returns mock lists for enabled providers.
  - Monkeypatched both the global registry's `get_model_lists` and the local test module's `get_settings` reference to point to `mock_settings`, ensuring consistent access to the same setting instance under test.
  - Configured `gemini` and `nvidia_nim` to have dummy keys in the default `mock_settings` object so that paid providers pass the key check in `mock_get_model_lists()`.
  - Updated all `/v1/models` route assertions in `test_dynamic_model_routing.py` to expect the prefix-encoded model IDs (`anthropic/{provider}/{model}`) returned by the real `answer_models` implementation, instead of bare IDs.
