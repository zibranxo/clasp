"""
clasp/providers/together.py

TogetherProvider — together.ai's OpenAI-compatible endpoint. No quirks
called out in plan.md for this one — same thin-wrapper pattern as
cerebras.py.
"""

from __future__ import annotations

from clasp.providers.openai_transport import OpenAIChatTransport

TOGETHER_BASE_URL = "https://api.together.xyz/v1"


class TogetherProvider(OpenAIChatTransport):
    def __init__(
        self,
        *,
        base_url: str = TOGETHER_BASE_URL,
        timeout_seconds: float = 60.0,
        send_stream_options: bool = True,
        extra_headers: dict[str, str] | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        super().__init__(
            "together",
            base_url,
            timeout_seconds=timeout_seconds,
            send_stream_options=send_stream_options,
            extra_headers=extra_headers,
            static_models=static_models,
            models_cache_ttl_seconds=models_cache_ttl_seconds,
        )