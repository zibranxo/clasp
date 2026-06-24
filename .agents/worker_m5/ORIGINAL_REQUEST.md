## 2026-06-24T05:55:25Z
You are a worker subagent implementing Milestone 5: Agent Optimizations & Headless Bots in the CLASP codebase.
Your working directory is `c:\code\clasp\.agents\worker_m5`.

Please perform the following tasks:

1. Update `clasp/config/settings.py` to add settings fields for agent optimizations:
   - `fast_prefix_detection: bool = Field(default=True, validation_alias="FAST_PREFIX_DETECTION")`
   - `enable_network_probe_mock: bool = Field(default=True, validation_alias="ENABLE_NETWORK_PROBE_MOCK")`
   - `enable_title_generation_skip: bool = Field(default=True, validation_alias="ENABLE_TITLE_GENERATION_SKIP")`
   - `enable_suggestion_mode_skip: bool = Field(default=True, validation_alias="ENABLE_SUGGESTION_MODE_SKIP")`
   - `enable_filepath_extraction_mock: bool = Field(default=True, validation_alias="ENABLE_FILEPATH_EXTRACTION_MOCK")`

2. Implement the client-side agent optimization mocks:
   - Create `clasp/api/command_utils.py`, `clasp/api/detection.py`, and `clasp/api/optimization_handlers.py`.
   - IMPORTANT: Adapt the request detection functions in `detection.py` and optimization handlers in `optimization_handlers.py` to accept a raw `dict` request body (instead of the Pydantic MessagesRequest model) because CLASP handles requests as dicts.
   - Adjust imports to reference the `clasp` namespace correctly.
   - Integrate the optimization handlers inside `clasp/api/service.py` (specifically in `dispatch_stream` and `dispatch`). If `try_optimizations(request, settings)` returns a response, return/stream that response immediately, bypassing any selector routing or upstream calls.
   - Note: Since `try_optimizations` returns a model-like structure or dict, ensure you format it properly (yielding appropriate SSE events in `dispatch_stream` and returning the dict in `dispatch`).

3. Port the headless session remote bots:
   - Port all modules from `free-claude-code-main/cli/managed/` to `clasp/cli/managed/`.
   - Port all modules from `free-claude-code-main/messaging/` to `clasp/messaging/`.
   - Port `free-claude-code-main/cli/process_registry.py` to `clasp/cli/process_registry.py`.
   - Adjust all imports in these files to use the `clasp` namespace.
   - Ensure the Whisper voice transcription in `clasp/messaging/transcription.py` has lazy import checks for librosa/transformers so the code compiles and tests pass even if these optional dependencies are not installed in the workspace.

4. Port the unit tests:
   - Port `free-claude-code-main/tests/api/test_optimization_handlers.py` and `free-claude-code-main/tests/api/test_routes_optimizations.py` to `tests/unit/test_optimization_handlers.py` and `tests/unit/test_routes_optimizations.py`. Adapt them to target your dict-based interface.
   - Port all unit tests under `free-claude-code-main/tests/messaging/` (e.g. `test_messaging.py`, `test_transcription.py`, etc.) to `tests/unit/messaging/` (or `tests/unit/`).

5. Run all tests to verify correctness:
   - Run `uv run pytest tests/unit -v --tb=short`
   - Run `uv run pytest tests/integration -v --tb=short`
   Ensure all tests compile and pass.
