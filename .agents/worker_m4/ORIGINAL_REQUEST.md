## 2026-06-24T05:38:58Z

You are a worker subagent implementing Milestone 4: Local Web Server Tools in the CLASP codebase.
Your working directory is `c:\code\clasp\.agents\worker_m4`.

Please perform the following tasks:

1. Update `clasp/config/settings.py` to add settings fields for local web tools:
   - `enable_web_server_tools: bool = Field(default=False, validation_alias="ENABLE_WEB_SERVER_TOOLS")`
   - `web_fetch_allowed_schemes: str = Field(default="http,https", validation_alias="WEB_FETCH_ALLOWED_SCHEMES")`
   - `web_fetch_allow_private_networks: bool = Field(default=False, validation_alias="WEB_FETCH_ALLOW_PRIVATE_NETWORKS")`
   And implement the helper method `web_fetch_allowed_scheme_set(self) -> frozenset[str]` returning the set of lowercased allowed schemes.

2. Port the web tools modules from `c:\code\clasp\free-claude-code-main\api\web_tools\` to `c:\code\clasp\clasp\api\web_tools\`. 
   - You need to create `clasp/api/web_tools/__init__.py`, `constants.py`, `egress.py`, `outbound.py`, `parsers.py`, `request.py`, and `streaming.py`.
   - IMPORTANT: Adapt the request extraction/helper functions in `request.py` (e.g., `is_web_server_tool_request`, `forced_tool_turn_text`, `forced_server_tool_name`, `has_tool_named`, `is_anthropic_server_tool_definition`, `has_listed_anthropic_server_tools`, and `openai_chat_upstream_server_tool_error`) to accept a raw `dict` request body (instead of Pydantic models) since CLASP passes the request body as a dictionary.
   - Adjust all imports in the ported files to reference `clasp` namespace instead of `api` or `core` from `free-claude-code-main`.

3. Integrate local web tools interception in the main request flow:
   - In `clasp/api/service.py` (or `clasp/api/proxy_routes.py`), intercept the incoming message request. If `settings.enable_web_server_tools` is enabled and `is_web_server_tool_request(body)` is true, short-circuit normal provider routing and instead stream the local tool execution results using `stream_web_server_tool_response`.
   - Ensure you raise appropriate validation errors or return error payloads if an OpenAI-compatible provider is selected but web tools are disabled/unforced, using the checks in `openai_chat_upstream_server_tool_error`.

4. Port the unit tests from `c:\code\clasp\free-claude-code-main\tests\api\test_web_server_tools.py` to `c:\code\clasp\tests\unit/test_web_server_tools.py`. Adapt them to target your new dictionary-based interface and CLASP-specific imports.

5. Run all tests to verify correctness:
   - Run `uv run pytest tests/unit -v --tb=short`
   - Run `uv run pytest tests/integration -v --tb=short`
   Ensure all tests compile and pass.

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Regularly update your `progress.md` with timestamps and task statuses (using ID comments) during execution. Once complete, write a `handoff.md` report in your working directory and notify me.
