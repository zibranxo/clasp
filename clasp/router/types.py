"""
clasp/router/types.py

Shared lightweight types used by the Sprint-3 routing/queueing pipeline
(router/selector.py, queue/absorber.py, queue/manager.py).

Why a separate config carrier instead of clasp.config.settings.Settings?
--------------------------------------------------------------------------
`Settings` (clasp/config/settings.py) is a full pydantic-settings model that
loads from YAML + env vars — exactly right for the real server process, but
it pulls in pydantic/pydantic-settings/ruamel.yaml as hard dependencies.

The Sprint-3 routing core (selector / absorber / queue manager) only needs a
handful of fields: the provider chain order, which providers are enabled,
optional by-type routing overrides, and the max queue wait. `SelectorConfig`
captures exactly that as a plain dataclass with zero external dependencies,
which keeps this module testable in isolation and keeps the pydantic
boundary at the edges of the system (proxy_routes.py / service.py) rather
than threaded through the core dispatch logic.

A future integration step (plan.md §20 step 42, "Update api/service.py to
use the selector") is where a `SelectorConfig.from_settings(settings)`
adapter would translate the real `Settings` object into this shape for
production use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clasp.api.detect import RequestType, classify_priority


# ---------------------------------------------------------------------------
# AnthropicRequest
# ---------------------------------------------------------------------------

@dataclass
class AnthropicRequest:
    """
    Thin wrapper around a raw Anthropic Messages API request body, enriched
    with the classification fields the router/queue layer needs:
    `type`, `priority`, `estimated_tokens`, `needs_tools`, `needs_vision`.
    """

    body: dict[str, Any]
    type: RequestType
    priority: int
    estimated_tokens: int = 0
    needs_tools: bool = False
    needs_vision: bool = False

    @property
    def model(self) -> str:
        """Return the model ID from the request body."""
        return self.body.get("model", "")

    def model_dump(self, *args, **kwargs) -> dict[str, Any]:
        """Return the request body dictionary (duck-typing Pydantic model)."""
        return self.body

    @classmethod
    def from_body(cls, body: dict[str, Any]) -> "AnthropicRequest":
        """Classify *body* via clasp.api.detect and wrap it."""
        from clasp.api.detect import _estimate_tokens  # noqa: PLC0415

        req_type = detect_request_type(body)
        return cls(
            body=body,
            type=req_type,
            priority=classify_priority(req_type),
            estimated_tokens=_estimate_tokens(body),
            needs_tools=bool(body.get("tools")),
            needs_vision=req_type == RequestType.VISION,
        )


def detect_request_type(body: dict[str, Any]) -> RequestType:
    """Local indirection so this module only needs `detect()` lazily."""
    from clasp.api.detect import detect  # noqa: PLC0415

    return detect(body)


# ---------------------------------------------------------------------------
# SelectorConfig — minimal, dependency-free routing configuration
# ---------------------------------------------------------------------------

@dataclass
class ProviderEnableConfig:
    """Per-provider enable flag, mirroring the relevant slice of the real
    `clasp.config.settings.ProviderConfig` without the pydantic dependency."""

    enabled: bool = True


@dataclass
class SelectorConfig:
    """
    Minimal routing configuration consumed by `router.selector.select()`,
    `queue.manager.QueueManager.drain_task()`, and `queue.absorber`.

    `by_type` maps a lowercase `RequestType.value` (e.g. "think",
    "long_context", "background", "vision") to a `"provider/model-slug"`
    override string. Only the provider portion (before the first "/") is
    used by the simplified Sprint-3 selector; the model-slug portion will
    matter once `router/model_map.py` lands.
    """

    provider_chain: list[str]
    providers: dict[str, ProviderEnableConfig] = field(default_factory=dict)
    by_type: dict[str, str] = field(default_factory=dict)
    max_queue_wait_seconds: float = 180.0