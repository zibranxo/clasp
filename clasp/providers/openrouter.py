"""
clasp/providers/openrouter.py

OpenRouterProvider — OpenRouter's OpenAI-compatible endpoint.

Two quirks from plan.md:

1. HTTP-Referer and X-Title headers — OpenRouter uses these for app
   attribution; defaulted here but overridable via `extra_headers`.

2. Free-tier model slugs must end in `:free`, or the request may incur
   charges. plan.md's given code frames this as a startup-time check
   (`log.warning(...)` once, not per-request) — but nothing in this
   codebase ties a specific model string to a provider before request
   time (model selection happens per-call, via `stream()`'s `model`
   argument; `router/model_map.py`, which would know the configured
   routing table up front, doesn't exist yet). `_apply_provider_quirks()`
   is the closest available hook, called on every `stream()` call, so
   this warns the first time a given model string is seen and stays
   silent for every repeat after that — "warn once" in spirit, just
   triggered by first use rather than by process start.
"""

from __future__ import annotations

from loguru import logger

from clasp.providers.openai_transport import OpenAIChatTransport

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_HTTP_REFERER = "http://localhost:8082"
DEFAULT_X_TITLE = "CLASP"


class OpenRouterProvider(OpenAIChatTransport):
    def __init__(
        self,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        timeout_seconds: float = 60.0,
        send_stream_options: bool = True,
        http_referer: str = DEFAULT_HTTP_REFERER,
        x_title: str = DEFAULT_X_TITLE,
        extra_headers: dict[str, str] | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        headers = {"HTTP-Referer": http_referer, "X-Title": x_title}
        headers.update(extra_headers or {})
        self._warned_models: set[str] = set()

        super().__init__(
            "openrouter",
            base_url,
            timeout_seconds=timeout_seconds,
            send_stream_options=send_stream_options,
            extra_headers=headers,
            static_models=static_models,
            models_cache_ttl_seconds=models_cache_ttl_seconds,
        )

    def _apply_provider_quirks(self, payload: dict, *, request: dict, model: str) -> dict:
        self._maybe_warn_non_free_model(model)
        return payload

    def _maybe_warn_non_free_model(self, model: str) -> None:
        if model in self._warned_models:
            return
        self._warned_models.add(model)
        if not model.endswith(":free"):
            logger.warning(
                f"openrouter: model {model!r} may incur charges — "
                f"append ':free' to the model slug for the free tier"
            )