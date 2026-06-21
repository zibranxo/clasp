"""
clasp/providers/nvidia_nim.py

NvidiaNIMProvider — the first concrete provider, and the simplest one:
NIM speaks plain OpenAI-compatible Chat Completions, so this is a thin
`OpenAIChatTransport` subclass that just supplies NIM's `base_url` and
its two documented model-specific quirks (plan.md §19 "NVIDIA NIM Model
Quirks" and "NVIDIA NIM system role"):

  1. kimi-k2 / kimi-k2-thinking models want extended-thinking config
     passed as `extra_body.thinking` instead of wherever a standard
     OpenAI-compatible field would otherwise go.
  2. Some NIM models reject the `system` role outright and need the
     system prompt merged into the first user message instead (the
     `merge_system` mechanism already built into OpenAIChatTransport).
     plan.md defers *which* models need this to `router/capability.py`
     (not yet built) or real-world testing — no specific model names are
     given, so this ships with an empty default set rather than guessing.

A third quirk plan.md calls out — "some NIM models return
`'role': 'assistant'` in delta frames instead of omitting it" — needs no
code here: `sse_builder.py`'s `SSEBuilder` only ever reads
`delta.get("content")` and `delta.get("tool_calls")`, so a redundant
`delta["role"]` key is already silently ignored.
"""

from __future__ import annotations

from typing import Iterable

from clasp.providers.openai_transport import OpenAIChatTransport

#: Default base URL from the provider catalog (plan.md §6).
NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

#: Default extended-thinking budget for kimi-k2 models when the original
#: Anthropic request enables thinking but doesn't specify budget_tokens.
DEFAULT_KIMI_THINKING_BUDGET_TOKENS = 8000


class NvidiaNimProvider(OpenAIChatTransport):
    def __init__(
        self,
        *,
        base_url: str = NVIDIA_NIM_BASE_URL,
        timeout_seconds: float = 60.0,
        merge_system_models: Iterable[str] | None = None,
        kimi_thinking_budget_tokens: int = DEFAULT_KIMI_THINKING_BUDGET_TOKENS,
        send_stream_options: bool = True,
        extra_headers: dict[str, str] | None = None,
        static_models: list[str] | None = None,
        models_cache_ttl_seconds: float = 300.0,
    ) -> None:
        """
        Args:
            merge_system_models: Model slugs known to reject the
                `system` role and need it merged into the first user
                message instead. Empty by default — plan.md explicitly
                defers naming specific models to `router/capability.py`
                or real-world testing rather than guessing here. Populate
                this (or wire up `router/capability.py` once it exists)
                as you discover which NIM models actually need it.
            kimi_thinking_budget_tokens: Fallback `budget_tokens` for
                kimi-k2 models when the original Anthropic request has
                `thinking.type == "enabled"` but no `budget_tokens` of
                its own.
            static_models: Pass a list to skip the upstream `/models`
                call entirely (e.g. for offline/test use). Left as
                `None` by default so `list_models()` genuinely calls
                NIM's `/models` endpoint, which build.nvidia.com does
                support.
        """
        self._merge_system_models: frozenset[str] = frozenset(merge_system_models or ())
        self.kimi_thinking_budget_tokens = kimi_thinking_budget_tokens

        super().__init__(
            "nvidia_nim",
            base_url,
            timeout_seconds=timeout_seconds,
            merge_system_resolver=self._merge_system_for_model,
            send_stream_options=send_stream_options,
            extra_headers=extra_headers,
            static_models=static_models,
            models_cache_ttl_seconds=models_cache_ttl_seconds,
        )

    # ------------------------------------------------------------------ #
    # merge_system resolution
    # ------------------------------------------------------------------ #

    def _merge_system_for_model(self, model: str) -> bool:
        return model in self._merge_system_models

    # ------------------------------------------------------------------ #
    # kimi-k2 extended-thinking quirk
    # ------------------------------------------------------------------ #

    def _apply_provider_quirks(self, payload: dict, *, request: dict, model: str) -> dict:
        """
        plan.md §19: `moonshotai/kimi-k2` and `kimi-k2-thinking` want
        extended thinking passed as `extra_body.thinking` rather than a
        standard field. Only applies when the original Anthropic request
        actually enabled thinking (`request["thinking"]["type"] ==
        "enabled"`) — absent that, kimi models behave like any other
        OpenAI-compatible chat model and this is a no-op.
        """
        if "kimi" not in model.lower():
            return payload

        thinking_cfg = request.get("thinking")
        if not isinstance(thinking_cfg, dict) or thinking_cfg.get("type") != "enabled":
            return payload

        budget_tokens = thinking_cfg.get("budget_tokens", self.kimi_thinking_budget_tokens)
        payload["extra_body"] = {"thinking": {"type": "enabled", "budget_tokens": budget_tokens}}
        return payload


# Export the class for external use
__all__ = ["NvidiaNimProvider"]