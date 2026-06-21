# Code Quality Report — Sprint 1 Checkpoint
Date: 2026-06-20
Files reviewed: 32

## Summary
All 32 Sprint 1 files exist and implement their planned functionality. The codebase is internally consistent in naming, async patterns, and logging conventions. Several files have drifted from the sprint 1 specification — the UI is the full 6-panel Phase 7 build rather than a minimal "Providers panel only" skeleton, and `server.py` has adopted a v2-style lifespan that wires Sprimnt 2+ rate-limit modules. Three test files fail collection due to import mismatches between test expectations and current module exports, and the integration tests reference symbols not yet defined. No critical correctness bugs were found in the implementation files themselves.

## Per-file findings

### pyproject.toml
- Status: matches spec
- Findings:
  - [LOW] Lists `ty` as a dev dependency — `ty` (`0.0.1a1`) is a pre-alpha package. Has no functional effect since no code imports it; risk is limited to install-time breakage if the package is pulled from PyPI.
  - [LOW] `[tool.pytest.ini_options]` section exists but pytest is currently reading from a separate `pytest.ini` file (the test run warns about this). Double-configuration causes confusion — only one config source should exist.

### clasp/utils/logger.py
- Status: matches spec
- Findings:
  - [MEDIUM] The module-level docstring tells callers to `from loguru import logger`, but the CLAUDE.md says `from clasp.utils.logger import log`. The module exports `setup_logging` and `get_log_file` under names consistent with themselves, but there is no `log` alias exported. Callers in the codebase use `from loguru import logger` directly — inconsistent with the CLAUDE.md rule but internally consistent across files. This is a CLAUDE.md vs. reality mismatch; one or the other should be corrected.

### clasp/utils/pid.py
- Status: matches spec
- Findings:
  - None. Clean implementation matching plan.md §19 PID file spec exactly — stale detection via `os.kill(pid, 0)`, `PermissionError` handling, `missing_ok` on cleanup.

### clasp/utils/hash.py
- Status: matches spec
- Findings:
  - [LOW] `_sort_tools` exists as a standalone function but is never called — the actual sorting happens inside a redefined `_extract_canonical` that overrides the original (lines 127-137). The dead function `_sort_tools` and the shadowed `_ORIGINAL_EXTRACT` reference can be cleaned up.
  - [LOW] The `include` set omits `"stream"` as specified, but also omits `"stop_sequences"`. If a user sends identical messages but changes `stop_sequences`, the hash won't change — but this is likely intentional since stop_sequences don't affect model output content.

### clasp/utils/ip_guard.py
- Status: matches spec
- Findings:
  - None. Clean implementation. Uses Starlette's `BaseHTTPMiddleware`, loopback detection is correct for IPv4 and IPv6.

### clasp/config/provider_catalog.py
- Status: matches spec
- Findings:
  - [LOW] `ProviderProfile` is a frozen dataclass with 17 fields — a Pydantic model would give validation on instantiation (e.g., `rpm_soft_threshold` must be 0.0-1.0) that dataclass lacks. Not a bug, but plan.md says "Pydantic models used where specified" in §17.

### clasp/config/settings.py
- Status: matches spec
- Findings:
  - [MEDIUM] `_apply_env_overrides` uses `object.__setattr__` to mutate a `BaseModel` field (`self.server`) after validation. Pydantic models can reject this via `model_config` `frozen=True`. The current `ServerConfig` is not frozen so it works, but this is fragile — if someone adds `frozen=True` to ServerConfig later, all env-var injection silently breaks. Consider using `self.server = self.server.model_copy(update={...})` instead.
  - [LOW] The `model_validator` directly imports `os.environ` at call time. This is fine functionally but means the validator re-reads env vars on every `Settings()` construction rather than only when loading from cache. Since `get_settings()` caches via `lru_cache`, this is effectively once-per-reload anyway, so impact is negligible.

### clasp/config/writer.py
- Status: matches spec
- Findings:
  - [LOW] `_mask_settings` function exists in `tests/unit/test_writer.py` imports but does not exist in `clasp/config/writer.py`. The test imports `_mask_settings` on line 23 but the function was likely renamed to `mask_keys` (which is the public API). The test file needs updating.
  - [LOW] `write_config` takes `settings: Any` rather than `Settings`. The type hint is intentionally broad to accept dicts too, but loses all type checking at call sites.

### clasp/config/watcher.py
- Status: matches spec
- Findings:
  - [MEDIUM] The `run()` method has a fallback `await asyncio.Event().wait()` when watchfiles isn't installed — this silently disables hot-reload forever. At minimum this should log at WARNING level and consider whether it should raise instead, since hot-reload is a core feature.
  - [LOW] `_watch_with_debounce` imports `time` inside the async iterator loop on every event. A single module-level import would be cleaner.

### clasp/providers/common/token_counter.py
- Status: matches spec
- Findings:
  - None. Clean implementation with proper tiktoken fallback and per-image estimation. The loguru shim for test environments is reasonable.

### clasp/providers/common/error_mapper.py
- Status: matches spec
- Findings:
  - None. Comprehensive error classification covering Gemini safety blocks, OpenAI error types, and Anthropic-specific status codes. The `_ANTHROPIC_ERROR_TYPE_MAP` correctly maps content_filter to `invalid_request_error` per plan.md §19.

### clasp/providers/common/message_converter.py
- Status: matches spec
- Findings:
  - [HIGH] `anthropic_to_openai` with `merge_system=True` and a non-list first user message content: if `system_text` exists and `merge_system=True` but `i=0` (first message) has content that isn't an instance of `str` or `list`, the code falls through all branches and `system_text` is left non-None and unconsumed. The system prompt is silently dropped. This is an edge case (malformed input), but plan.md says the converter should "raise ValueError on unrecoverable malformed input."
  - [MEDIUM] `anthropic_to_openai` copies the entire request via `copy.deepcopy(request)` on every call — this is O(n) in request size and could be expensive for large contexts. Could be avoided by reading-only from the original and only building a new output dict.

### clasp/providers/common/sse_builder.py
- Status: matches spec
- Findings:
  - None. Clean state machine implementation for OpenAI SSE → Anthropic SSE translation. Tool call JSON buffering handles incremental arrival correctly.

### clasp/providers/base.py
- Status: matches spec
- Findings:
  - [HIGH] `stream()` catches `UpstreamRateLimitError` and calls `on_upstream_429(...)` — a lazy import from `clasp.queue.absorber`. However, `on_upstream_429` is an async generator that this method iterates and re-yields. If the absorber module isn't found (ImportError), the 429 propagates directly to the caller, violating P2 ("Never propagate 429"). The lazy import is inside a try/except but only catches the `UpstreamRateLimitError`, not the `ImportError` of the absorber itself.

### clasp/providers/openai_transport.py
- Status: minor deviations
- Findings:
  - [CRITICAL] `__init__` calls `super().__init__(name, base_url, timeout_seconds=timeout_seconds)` at line 129, but the parent `BaseProvider.__init__` defined in `base.py` only takes a single `self` parameter (it has no explicit `__init__` beyond the ABC constructor). This will raise `TypeError` at runtime — the parent class has no constructor accepting these kwargs. (Actually, checking `base.py` — `BaseProvider` has no `__init__` defined at all, inheriting from `ABC`. This means the `super().__init__()` call is passing unexpected keyword arguments to `ABC.__init__()`, which doesn't accept them. In Python 3.11, `object.__init__()` raises `TypeError` when given arguments unless the class overrides `__init__`. This is a blocker bug.)
  - Verification needed: If `BaseProvider` implicitly inherits from `object` through `ABC`, then `object.__init__()` is called and raises `TypeError: object.__init__() takes exactly one argument`. This means `OpenAIChatTransport` cannot be instantiated.
  - [HIGH] `stream()` method duplicates the signature pattern differently from `BaseProvider.stream()` — the base class defines `stream(self, request: AnthropicRequest, key: str, key_index: int)` but `OpenAIChatTransport.stream` uses `stream(self, request: dict, *, api_key: str, model: str)`. These are different method signatures. If callers call through the base class interface, keyword argument mismatches will occur.
  - [HIGH] Same signature mismatch applies to `AnthropicMessagesTransport.stream()`.
  - [MEDIUM] `stream()` in openai_transport uses `self.client.stream(...)` but `self.client` is never defined — `BaseProvider` has no `httpx.AsyncClient` field. Unless subclasses set it, this is an `AttributeError`.
  - [MEDIUM] `stream()` calls `builder.parse_openai_sse_chunk(raw_line)` and `builder.is_done` and `builder.finalize()` — but `SSEBuilder` has methods `process_chunk(chunk: dict)` (not `parse_openai_sse_chunk(str)`), has no `is_done` attribute, and `flush()` is used instead of `finalize()`. The API surface doesn't match.

### clasp/providers/anthropic_transport.py
- Status: same transport pattern as openai_transport.py
- Findings:
  - [CRITICAL] Same `super().__init__(name, base_url, timeout_seconds=timeout_seconds)` bug as `OpenAIChatTransport` (line 124) — will raise `TypeError` at runtime since `BaseProvider` has no constructor accepting these args.
  - [HIGH] Same `stream()` signature mismatch vs `BaseProvider.stream()`.
  - [MEDIUM] Same missing `self.client` attribute.

### clasp/providers/nvidia_nim.py
- Status: matches spec
- Findings:
  - [MEDIUM] `NvidiaProvider` inherits from `OpenAIChatTransport`, so it inherits the critical constructor bug from the parent chain. The kimi-k2 quirk handling is correct per plan.md §19.
  - [LOW] The class is named `NvidiaProvider` but the registry maps it under key `"nvidia_nim"` — the class name doesn't match the conventional naming pattern (should be `NvidiaNimProvider` for consistency with the catalog key).

### clasp/providers/registry.py
- Status: matches spec
- Findings:
  - [MEDIUM] `build_registry` instantiates transport classes with `name` and `base_url` kwargs (line 340-343). If the `super().__init__()` bug in the transport classes is real, all provider instantiation fails here silently (caught by the broad `except Exception` at line 348). The server would start with zero registered providers and no clear error message.
  - [LOW] Registry is built into a module-level global `_registry`. The docstring says "atomic swap" but the swap is a simple assignment — on CPython this is safe (single store is atomic for references) but the comment is misleading since there's no lock.

### clasp/api/optimize.py
- Status: matches spec
- Findings:
  - [MEDIUM] The `__all__` list exports `COUNT_TOKENS_ENDPOINT`, `answer_count_tokens`, `answer_models`, and `is_local_probe` — these are convenience wrappers defined at the bottom of the module. They duplicate functionality already present in `handle_probe` and `is_probe`. This is redundant API surface.
  - [LOW] `_canned_sse_response` creates a full SSE event sequence, and `_local_probe_response` in `proxy_routes.py` creates a nearly identical sequence independently. The duplication across modules means changes to the SSE format must be made in two places.

### clasp/api/detect.py
- Status: matches spec
- Findings:
  - [LOW] `_estimate_tokens` duplicates `tiktoken` import and fallback logic from `token_counter.py` — both modules have independently re-implemented the same estimation with minor differences in fallback behavior. token_counter.py counts images; detect.py does not. This is inconsistent.

### clasp/api/service.py
- Status: partial / deviates
- Findings:
  - [HIGH] `dispatch_stream` calls `provider.stream(anthropic_request, key=api_key, key_index=key_index)` at line 190-191, but the concrete transport `stream()` methods use different signatures (`api_key` vs `key`, different `model` parameter). This call will fail with `TypeError: unexpected keyword argument 'key'` because the transports expect `api_key=` not `key=`.
  - [HIGH] `_kp.record_success(key_index)` and `_kp.buckets[key_index].consume_actual(...)` at lines 216-225 — these methods are called on a `KeyPool` object retrieved from registry, but `KeyPool` is imported from `clasp.ratelimit.key_pool` which is a Sprint 2 module. If the module exists but the methods have different signatures or behaviors, this is a runtime failure.
  - [MEDIUM] `dispatch_stream` catches `Exception` at line 125 and returns `ErrorType.SERVER_ERROR` — but it also catches `Exception` at line 197 during `provider.stream()`. Both catch blocks report "SERVER_ERROR", losing information about the actual error type. If the provider raises `ProviderHTTPError(429)`, the correct error would be `RATE_LIMIT`, not `SERVER_ERROR`.
  - [MEDIUM] `AnthropicRequest.from_body(request)` is called at line 123, importing from `clasp.router.types`. If this is a Sprint 2+ module that isn't fully implemented yet, this is an ImportError at runtime.

### clasp/api/proxy_routes.py
- Status: matches spec
- Findings:
  - [MEDIUM] `_wrap_async_iter` at line 210 converts the async generator from `handle_request` (which yields `str`) to bytes — but `dispatch_stream` already yields `bytes`. The type annotation says `AsyncIterator[str]` but the actual values are `bytes` from `_error_event` and `collected` chunks. This means `_wrap_async_iter` calls `.encode("utf-8")` on bytes objects, which would fail because bytes don't have `.encode()`. (Actually, `bytes.encode()` does exist in Python — it returns the bytes as-is — so this is subtle: the type annotation is wrong but the runtime behavior is accidentally correct.)
  - [LOW] `_sse` helper in proxy_routes.py and `_sse` in optimize.py are identical but defined separately.

### clasp/ui/static/index.html
- Status: deviates from spec
- Findings:
  - [MEDIUM] plan.md §20 step 23 says "minimal UI: Providers panel only." The current file is the full 6-panel Phase 7 build (Providers, Models, Dashboard, Routing, Advanced, Logs). This is ahead of spec — not a bug per se, but it includes panels that reference live SSE streams, cache clearing, and other features whose backend endpoints are Sprint 2+ stubs. The UI will render but show placeholder/zero data for panels that have no backend.
  - [LOW] Emoji icons in navigation sidebar — plan.md doesn't forbid them but they're unusual for a developer tool.

### clasp/ui/static/app.js
- Status: deviates from spec
- Findings:
  - [MEDIUM] Same as index.html — this is the full Phase 7 Alpine.js app, not the "Alpine.js skeleton" from plan.md §20 step 24. Includes model routing, dashboard SSE, log streaming, cache management, and config import/export — all for backend features that are stub-only in Sprint 1.

### clasp/ui/static/style.css
- Status: matches spec
- Findings:
  - [LOW] The file was likely copy-paste edited since it has duplicated CSS blocks — scrollbar styles are defined twice (lines 8-11 and lines 47-49), and `[x-collapse]`/`[x-show]` transition rules are also duplicated (lines 52-60).

### clasp/ui/routes.py
- Status: matches spec
- Findings:
  - [LOW] `mount_static` function is defined but never called by `server.py` — the server file's TODO comment says to include `clasp/ui/routes.py` but doesn't. The router is usable via `app.include_router()` but `mount_static` must be called separately.

### clasp/internal/routes.py
- Status: matches spec
- Findings:
  - [LOW] `get_catalog()` calls `profile.model_dump()` (line 343) but `ProviderProfile` is a dataclass, not a Pydantic model. Dataclasses have `__dict__` but no `model_dump()` method. This will raise `AttributeError` at runtime.
  - [LOW] `get_catalog_defaults()` imports `DEFAULT_ROUTING_MODELS` and `DEFAULT_ROUTING_BY_TYPE` from `clasp.config.settings` (line 355) but these names are not exported by `settings.py` — they are defined as field defaults on the `ModelRoutes` and `ByTypeRoutes` classes, not as module-level constants.

### clasp/server.py
- Status: deviates from spec
- Findings:
  - [HIGH] The `lifespan` function at line 56-89 imports and instantiates `QueueManager`, `CooldownManager`, `ProviderRegistry`, `SelectorConfig`, and `ProviderEnableConfig` from Sprint 2/3 modules (`clasp.queue.manager`, `clasp.ratelimit.cooldown`, `clasp.router.types`). These are all imported at module level (lines 32-36). If any of these modules don't exist or have changed their APIs, the server won't start. This is ahead of spec — plan.md §20 step 28 says the server should "register routers" only, not wire up the full rate-limit pipeline.
  - [HIGH] `get_registry()` is called at line 59 but `build_registry(settings)` is never called — the registry starts empty. All provider lookups return `None`.
  - [HIGH] The `drain_task` created at line 68 calls `queue_mgr.drain_task(config=..., registry=..., cooldown_mgr=...)` — if the queue manager's `drain_task` is the Sprint 2+ implementation that expects a fully populated registry with key pools and circuit breakers, an empty registry will cause silent no-ops or errors.
  - [MEDIUM] `create_app` says "TODO: app.add_middleware(IPGuard)" at line 114 and "TODO: app.include_router(internal_router)" at line 115 — the IP guard and internal routes are not mounted, meaning `/internal/*` endpoints are not available even though `internal/routes.py` defines them.
  - [MEDIUM] `app.include_router(proxy_router)` is registered, but `ui/routes.py`'s router is never included — the web UI at `/ui` won't be served.

### clasp/cli/cmd_server.py
- Status: matches spec
- Findings:
  - [MEDIUM] `_print_startup_banner` calls `from clasp.server import __version__` but `server.py` doesn't define `__version__`. This is an `ImportError` at runtime when the banner tries to print.
  - [LOW] `create_app(log_level=log_level, debug=debug)` passes `log_level` to `create_app`, but `create_app` only accepts `debug`. This is a `TypeError`.

### clasp/cli/cmd_claude.py
- Status: matches spec
- Findings:
  - None. Clean implementation exactly matching plan.md §19 exec semantics — uses `os.execvp()`, polls `/health` before launch, handles missing binary gracefully.

### clasp/cli/main.py
- Status: matches spec
- Findings:
  - [HIGH] `cmd_claude` calls `run(passthrough_args=list(ctx.args), auto_start=auto_start)` (line 130), but `clasp.cli.cmd_claude` exports `run_claude` not `run`. This is an `AttributeError` at runtime.

## Cross-cutting observations

1. **Constructor chain is broken across the transport hierarchy.** `BaseProvider` has no `__init__` accepting parameters, but both `OpenAIChatTransport` and `AnthropicMessagesTransport` call `super().__init__(name, base_url, timeout_seconds=...)`. This means neither transport class can be instantiated unless `BaseProvider.__init__` is changed or the transports stop calling super with kwargs.

2. **`stream()` signatures are inconsistent.** `BaseProvider.stream(request, key, key_index)` vs `OpenAIChatTransport.stream(request, *, api_key, model)` vs `AnthropicMessagesTransport.stream(request, *, api_key, model)`. These three signatures are mutually incompatible. `service.py` calls via the base class signature, which will fail because the concrete implementations require different kwargs.

3. **UI is Phase 7, not Sprint 1.** `index.html` and `app.js` implement the full 6-panel UI with features (dashboard SSE, log streaming, model routing, cache management) whose backend endpoints are stub-only or missing entirely in Sprint 1. The spec called for "minimal UI: Providers panel only."

4. **`server.py` is wired to Sprint 2/3 modules prematurely.** The lifespan function imports and instantiates `QueueManager`, `CooldownManager`, `SelectorConfig`, and rate-limit infrastructure that belong to later sprints. The `get_registry()` call returns an empty registry since `build_registry()` is never called. The IPGuard middleware and internal/UI routers are not mounted.

5. **No integration between `server.py` and `config/settings.py`.** `server.py`'s `build_selector_config()` return s a hardcoded placeholder — it doesn't use the loaded `Settings` at all. The `cmd_server.py` entry point loads `get_settings()` but `create_app()` doesn't receive it.

6. **Three test collect errors + two integration collect errors are import mismatches.** Tests import symbols that either don't exist (`_init_logging`, `_mask_settings`, `Priority`, `CooldownTracker`) or exist under different names.

## Suggestions (prioritized)

1. **Fix the BaseProvider constructor chain** — add an `__init__` to `BaseProvider` that accepts `name`, `base_url`, and `timeout_seconds`, and stores `self.client = httpx.AsyncClient(...)`. Without this, no transport can be instantiated (affects: `base.py`, `openai_transport.py`, `anthropic_transport.py`, `nvidia_nim.py`).

2. **Align `stream()` signatures** — either make subclasses match `BaseProvider.stream(request: AnthropicRequest, key: str, key_index: int)` or update the base class to use the kwargs pattern (`*, api_key: str, model: str`). `service.py` must use the same convention (affects: `base.py`, `openai_transport.py`, `anthropic_transport.py`, `service.py`).

3. **Wire `server.py` correctly** — either strip back to Sprint 1 scope (no QueueManager/CooldownManager, no lifespan drain task) or, if Sprint 2 is partially done, call `build_registry(settings)` before `get_registry()` and mount `IPGuard` + `internal_router` + `ui_router` (affects: `server.py`).

4. **Fix `cmd_server.py` `__version__` import** — define `__version__` in `server.py` or import it from the package metadata (affects: `server.py`, `cmd_server.py`).

5. **Fix `cmd_claude.py` export name** — rename `run_claude` to `run` or update `main.py` to call `run_claude` (affects: `cmd_claude.py`, `main.py`).

6. **Fix test collect errors** — update `test_server.py` to remove `_init_logging` import, update `test_writer.py` to use `mask_keys` instead of `_mask_settings`, update `test_priority_queue.py` to match current `queue/manager.py` exports, update integration tests to match current `cooldown.py` exports (affects: test files).

7. **Fix `get_catalog()` in `internal/routes.py`** — `ProviderProfile` is a dataclass; use `dataclasses.asdict()` instead of `model_dump()` (affects: `internal/routes.py`).

8. **Fix `get_catalog_defaults()` import** — `DEFAULT_ROUTING_MODELS` and `DEFAULT_ROUTING_BY_TYPE` need to be module-level exports from `settings.py` or imported from the correct location (affects: `internal/routes.py`, `settings.py`).

## Open questions / spec ambiguities

1. **Is the transport hierarchy intentionally Sprint 2?** Both `openai_transport.py` and `anthropic_transport.py` contain `list_models()` and `count_tokens()` methods that perform network calls, but these are documented as "placeholder pending Sprint 1 step 10." The `token_counter.py` module (Sprint 1 step 10) exists and works — the transports should use it instead of the `_local_token_estimate` duplication.

2. **Are `openai_transport.py` and `anthropic_transport.py` actual Sprint 1 code or Sprint 2 code that was moved forward?** Their constructor signatures and `stream()` patterns suggest they were designed for a different `BaseProvider` interface than what exists. The `_stream_raw()` method documented in `base.py`'s docstring doesn't exist on either transport — they override `stream()` directly instead.

3. **Is `server.py` meant to wire Sprint 2 infrastructure now?** The lifespan imports `QueueManager`, `CooldownManager`, and rate-limit types that plan.md Section 20 places in Sprint 2 (steps 33-36) and Sprint 3 (step 47). If the server should be Sprint 1 only, this needs rollback. If Sprint 2 modules have been implemented alongside Sprint 1, the `build_registry(settings)` call is missing.

4. **UI scope ambiguity** — plan.md §20 step 23 says "minimal UI: Providers panel only" but step 28 says "FastAPI app factory, register routers" which presumably includes serving whatever UI exists. The full 6-panel UI was delivered but without confirmation of which sprint it belongs to.