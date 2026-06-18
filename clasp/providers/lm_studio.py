"""
clasp/providers/lm_studio.py

LMStudioProvider — a local LM Studio instance via its OpenAI-compatible
endpoint. Same pattern as ollama.py: no real API key needed, `base_url`
configurable for non-default host/port setups. See ollama.py's
docstring for the placeholder-key note re: KeyPool/registry.py.
"""

from __future__ import annotations

from clasp.providers.openai_transport import OpenAIChatTransport

LM_STUDIO_BASE_URL = "http://localhost:1234"


class LMStudioProvider(OpenAIChatTransport):
    def __init__(
        self,
        *,
        base_url: str = LM_STUDIO_BASE_URL,
        timeout_seconds: float = 60.0,
        send_stream_options: bool = True,
        extra_headers: dict[str, str] | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        super().__init__(
            "lm_studio",
            base_url,
            timeout_seconds=timeout_seconds,
            send_stream_options=send_stream_options,
            extra_headers=extra_headers,
            static_models=static_models,
            models_cache_ttl_seconds=models_cache_ttl_seconds,
        )