"""
clasp/providers/ollama.py

OllamaProvider — a local Ollama instance via its OpenAI-compatible
endpoint. No real API key needed; `base_url` is configurable since
Ollama can run on any host/port the user set it up on, not just the
catalog's localhost default.

Note for whoever wires up `provider_keys` (config/settings.py,
registry.py): `KeyPool`/`registry.py`'s `initialize()` currently skips
any provider with an empty key list (`if not keys: continue`), so a
local provider needing zero real credentials still needs at least one
placeholder entry in its key list — e.g. `["local"]` — to actually get
instantiated. `OpenAIChatTransport._build_headers()` already tolerates a
falsy/placeholder `api_key` fine (it just skips adding an Authorization
header), so the placeholder value itself is never sent anywhere.
"""

from __future__ import annotations

from clasp.providers.openai_transport import OpenAIChatTransport

OLLAMA_BASE_URL = "http://localhost:11434"


class OllamaProvider(OpenAIChatTransport):
    def __init__(
        self,
        *,
        base_url: str = OLLAMA_BASE_URL,
        timeout_seconds: float = 60.0,
        send_stream_options: bool = True,
        extra_headers: dict[str, str] | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        super().__init__(
            "ollama",
            base_url,
            timeout_seconds=timeout_seconds,
            send_stream_options=send_stream_options,
            extra_headers=extra_headers,
            static_models=static_models,
            models_cache_ttl_seconds=models_cache_ttl_seconds,
        )