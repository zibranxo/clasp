"""
clasp/providers/cerebras.py

CerebrasProvider — Cerebras's OpenAI-compatible endpoint. No quirks are
called out anywhere in plan.md for this one (unlike gemini/groq/
openrouter), so this is exactly the thin wrapper nvidia_nim.py's pattern
reduces to when there's nothing provider-specific to override — just the
catalog's base_url, with every OpenAIChatTransport default left as-is.
"""

from __future__ import annotations

from clasp.providers.openai_transport import OpenAIChatTransport

CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"


class CerebrasProvider(OpenAIChatTransport):
    def __init__(
        self,
        *,
        base_url: str = CEREBRAS_BASE_URL,
        timeout_seconds: float = 60.0,
        send_stream_options: bool = True,
        extra_headers: dict[str, str] | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        super().__init__(
            "cerebras",
            base_url,
            timeout_seconds=timeout_seconds,
            send_stream_options=send_stream_options,
            extra_headers=extra_headers,
            static_models=static_models,
            models_cache_ttl_seconds=models_cache_ttl_seconds,
        )