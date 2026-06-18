from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.modules.pop("clasp", None)
sys.path.insert(0, str(ROOT))

import types

sys.modules.setdefault(
    "loguru",
    types.SimpleNamespace(
        logger=types.SimpleNamespace(debug=lambda *a, **k: None, warning=lambda *a, **k: None)
    ),
)

from clasp.providers.nvidia_nim import (
    DEFAULT_KIMI_THINKING_BUDGET_TOKENS,
    NVIDIA_NIM_BASE_URL,
    NvidiaProvider,
)


def test_defaults() -> None:
    p = NvidiaProvider()
    assert p.name == "nvidia_nim"
    assert p.base_url == NVIDIA_NIM_BASE_URL
    assert p.kimi_thinking_budget_tokens == DEFAULT_KIMI_THINKING_BUDGET_TOKENS


def test_merge_system_resolver_uses_configured_model_list() -> None:
    p = NvidiaProvider(merge_system_models=["model-a", "model-b"])
    assert p._merge_system_for_model("model-a") is True
    assert p._merge_system_for_model("other") is False


def test_apply_provider_quirks_noop_for_non_kimi() -> None:
    p = NvidiaProvider()
    payload = {"x": 1}
    req = {"thinking": {"type": "enabled", "budget_tokens": 123}}
    out = p._apply_provider_quirks(dict(payload), request=req, model="meta/llama-3.1")
    assert out == payload


def test_apply_provider_quirks_adds_extra_body_for_kimi_with_enabled_thinking() -> None:
    p = NvidiaProvider()
    payload = {"x": 1}
    req = {"thinking": {"type": "enabled", "budget_tokens": 1234}}
    out = p._apply_provider_quirks(dict(payload), request=req, model="moonshotai/kimi-k2")
    assert out["extra_body"]["thinking"]["type"] == "enabled"
    assert out["extra_body"]["thinking"]["budget_tokens"] == 1234


def test_apply_provider_quirks_uses_default_budget_when_missing() -> None:
    p = NvidiaProvider(kimi_thinking_budget_tokens=777)
    out = p._apply_provider_quirks({}, request={"thinking": {"type": "enabled"}}, model="kimi-k2-thinking")
    assert out["extra_body"]["thinking"]["budget_tokens"] == 777


def test_apply_provider_quirks_noop_when_thinking_not_enabled() -> None:
    p = NvidiaProvider()
    out = p._apply_provider_quirks({}, request={"thinking": {"type": "disabled"}}, model="kimi-k2")
    assert "extra_body" not in out
