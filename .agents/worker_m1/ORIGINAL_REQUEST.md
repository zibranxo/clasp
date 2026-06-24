# Worker Milestone 1 Task Request

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Objective
Implement Milestone 1: Models & Transport Alignment.

Specifically:
1. Update `GET /v1/models` in `clasp` (routed in `clasp/api/proxy_routes.py` and implemented via `answer_models()` in `clasp/api/optimize.py`) so that it exposes all available non-Anthropic models from configured providers dynamically.
2. In `answer_models()`, fetch these models and return them in two formats:
   - `anthropic/{provider_name}/{model_id}`
   - `claude-3-freecc-no-thinking/{provider_name}/{model_id}` (which signals the client to skip thinking/reasoning blocks).
3. Update `clasp/config/provider_catalog.py` and `clasp/providers/registry.py` to change the transport types for OpenRouter, Ollama, and LM Studio from `openai_chat` to `anthropic_messages`.
4. Ensure the proxy request path (`POST /v1/messages` and `clasp/api/service.py`) handles requests with these prefixed model IDs, splits/decodes them (using the existing `decode_gateway_model_id` function), and correctly routes the request to the correct provider.
5. Make sure all unit and integration tests continue to build and run. If needed, write new unit tests to verify the models listing and prefix routing.

## 2026-06-23T15:42:10Z
You are a worker tasked with executing the Milestone 1 requirements in `c:\code\clasp\.agents\worker_m1\ORIGINAL_REQUEST.md`.
Please read that file, implement the requirements, and verify the changes by running unit and integration tests.
Ensure you write/maintain a `progress.md` file in `c:\code\clasp\.agents\worker_m1\progress.md` with your status, last visited timestamp, and current step.
Once done, write `handoff.md` and message the parent agent (c3d26509-90be-4ebf-9f0c-824b9c1d8712) with your final status and verification results.
