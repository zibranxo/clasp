# Worker Milestone 2 Task Request

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Objective
Implement Milestone 2: Upstream Provider Adapters.

Specifically:
1. Add the following 8 new provider profiles to `PROVIDER_CATALOG` in `clasp/config/provider_catalog.py`:
   - `mistral_codestral` (transport: `openai_chat`, tier: `free`)
   - `deepseek` (transport: `anthropic_messages`, tier: `free_credits`)
   - `kimi` (transport: `anthropic_messages`, tier: `free`)
   - `llamacpp` (transport: `anthropic_messages`, tier: `local`)
   - `opencode` (transport: `openai_chat`, tier: `free`)
   - `opencode_go` (transport: `openai_chat`, tier: `free`)
   - `wafer` (transport: `anthropic_messages`, tier: `free`)
   - `zai` (transport: `anthropic_messages`, tier: `free_credits`)
   Specify their appropriate endpoints, limits (e.g. RPM, TPM), Display Names, capability flags (`supports_tools`, `supports_vision`, `supports_thinking`), and tier information based on FCC's catalog.

2. In `clasp/providers/registry.py`, add mapping entries inside `PROVIDER_CLASS_MAP` for these new providers.
   - Most new providers can map to either `OpenAIChatTransport` or `AnthropicMessagesTransport` depending on their transport type.
   - For `deepseek`, map it to a new custom `DeepSeekProvider` class.

3. Create the `DeepSeekProvider` class in `clasp/providers/deepseek.py` inheriting from `AnthropicMessagesTransport`.
   - Override `_stream_raw` to preprocess and sanitize the request: strip unsupported attachment blocks (`image`, `document`, and empty `tool_result` content) before forwarding.
   - Also, deepseek's `/models` endpoint is hosted at the OpenAI-style endpoint, not the native `/anthropic` path. Ensure `list_models` gets model lists by making a GET request to the base URL copy-with `/models` and using `Authorization: Bearer <key>`.

4. Register the new providers in `_DEFAULT_PROVIDERS` and `_DEFAULT_PROVIDER_CHAIN` in `clasp/config/settings.py` so they are available in default configurations.

5. Verify all changes by running the unit tests and integration tests. Add new unit tests for the DeepSeek attachment stripping functionality.

## 2026-06-23T15:50:01Z
<USER_REQUEST>
You are a worker tasked with executing the Milestone 2 requirements in `c:\code\clasp\.agents\worker_m2\ORIGINAL_REQUEST.md`.
Please read that file, implement the requirements, and verify the changes by running unit and integration tests.
Ensure you write/maintain a `progress.md` file in `c:\code\clasp\.agents\worker_m2\progress.md` with your status, last visited timestamp, and current step.
Once done, write `handoff.md` and message the parent agent (c3d26509-90be-4ebf-9f0c-824b9c1d8712) with your final status and verification results.
</USER_REQUEST>
