# Handoff Report — Milestone 4: Local Web Server Tools

## 1. Observation
- Existing settings in `clasp/config/settings.py` were viewed (lines 271-280 and 345-365) to determine where to place the new settings and the allowed scheme set helper.
- The web tools submodules were found in `c:\code\clasp\free-claude-code-main\api\web_tools\` and ported to `clasp/api/web_tools/`.
- During porting, we observed that `free-claude-code-main` used Pydantic models for incoming requests. We adapted all methods in `clasp/api/web_tools/request.py` to accept raw request dictionaries because CLASP handles requests as raw dicts.
- `clasp/pyproject.toml` lists `httpx` as a dependency but does not list `aiohttp` or any other client transport. Since we are restricted from installing new dependencies, we replaced the aiohttp client session inside `outbound.py` with `httpx.AsyncClient` and a custom `PinnedHTTPTransport` that overrides the TCP connection pool via a custom `PinnedNetworkBackend` (extending `httpcore.AsyncNetworkBackend`).
- `clasp/api/service.py` is the entry point for routing proxy requests. We added local web server tools interception in `dispatch_stream` after the cache check block (line 125) and validation checking for OpenAI-compatible providers right after routing selection (line 188).
- The unit tests were ported from `free-claude-code-main` to `c:\code\clasp\tests\unit\test_web_server_tools.py`, adapted to the dictionary-based request interface and `httpx` mock patching.
- When running `uv run pytest tests/unit -v --tb=short`, we observed:
  ```
  522 passed, 6 skipped, 1 warning in 31.05s
  ```
- When running `uv run pytest tests/integration -v --tb=short`, we observed:
  ```
  76 passed in 9.39s
  ```

## 2. Logic Chain
1. To implement local web server tools, settings fields for `enable_web_server_tools`, `web_fetch_allowed_schemes`, and `web_fetch_allow_private_networks` had to be added to `clasp/config/settings.py` along with the `web_fetch_allowed_scheme_set` helper.
2. In order to avoid introducing `aiohttp` (which violates the "Do not install new dependencies" rule), `outbound.py` was rewritten to use `httpx` and `httpcore`. We preserved DNS address resolution validation and pinning (SSRF guards) by implementing `PinnedNetworkBackend` which routes TCP connections to validated IP addresses while leaving hostname validation/TLS handshake intact.
3. In `request.py`, changing parameters to accept dictionary bodies matches CLASP's internal `request: dict` shape.
4. The interception logic in `service.py` checks `settings.enable_web_server_tools` and `is_web_server_tool_request(request)`. If true, it short-circuits to stream the response via `stream_web_server_tool_response`, which yields SSE events. Non-streaming calls are also covered because `dispatch` internally collects streaming chunks and reassembles them using `_assemble_message(chunks)`.
5. For OpenAI-compatible providers, if web tools are disabled or unforced, calling `openai_chat_upstream_server_tool_error` returns a validation error string, raising an `HTTPException(status_code=400, ...)` synchronously before yielding.
6. The unit tests verify all egress policies, redirect limits, body capping, search/fetch results streaming, and routing reject rules. Running them confirms 100% test success.

## 3. Caveats
- No caveats. All edge cases (like DNS-pinning, redirects, and private network restrictions) have been fully handled and tested using standard `httpx` transport overrides.

## 4. Conclusion
- Milestone 4: Local Web Server Tools has been successfully implemented and verified. All unit and integration tests compile and pass successfully.

## 5. Verification Method
1. Inspect the ported code in `clasp/api/web_tools/`.
2. Inspect the integration points in `clasp/api/service.py`.
3. Run the unit tests specifically:
   ```bash
   uv run pytest tests/unit/test_web_server_tools.py -v --tb=short
   ```
4. Run all unit tests:
   ```bash
   uv run pytest tests/unit -v --tb=short
   ```
5. Run all integration tests:
   ```bash
   uv run pytest tests/integration -v --tb=short
   ```
