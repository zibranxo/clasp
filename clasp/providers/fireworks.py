"""
clasp/providers/fireworks.py

FireworksProvider — Fireworks AI's Anthropic-Messages-compatible
endpoint. Unlike every other provider in this file group, this subclasses
`AnthropicMessagesTransport`, not `OpenAIChatTransport` — Fireworks
speaks `/v1/messages` natively (no content-block translation step at
all), per the catalog's `transport: "anthropic_messages"` and
`base_url` already pointing at the full messages endpoint rather than a
root to append a path onto.
"""

from __future__ import annotations

from clasp.providers.anthropic_transport import AnthropicMessagesTransport

FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1/messages"


class FireworksProvider(AnthropicMessagesTransport):
    def __init__(
        self,
        *,
        base_url: str = FIREWORKS_BASE_URL,
        timeout_seconds: float = 60.0,
        extra_headers: dict[str, str] | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        super().__init__(
            "fireworks",
            base_url,
            timeout_seconds=timeout_seconds,
            extra_headers=extra_headers,
            static_models=static_models,
            models_cache_ttl_seconds=models_cache_ttl_seconds,
        )