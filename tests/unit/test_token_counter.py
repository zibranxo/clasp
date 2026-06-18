from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.modules.pop("clasp", None)
sys.path.insert(0, str(ROOT))

from clasp.providers.common import token_counter as tc


class _FakeEncoding:
    def encode(self, text: str) -> list[str]:
        # deterministic, easy-to-assert tokenization for tests
        return [t for t in text.split(" ") if t]


def test_estimate_tokens_returns_zero_if_messages_not_list() -> None:
    assert tc.estimate_tokens({"not": "a-list"}) == 0


def test_estimate_tokens_counts_text_system_tools_and_images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tc, "_get_encoding", lambda: _FakeEncoding())

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hello world"},
                {"type": "image", "source": {"type": "url", "url": "https://x"}},
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "read", "input": {"path": "a.py"}}],
        },
    ]

    total = tc.estimate_tokens(
        messages,
        system="system prompt",
        tools=[{"name": "tool_a", "description": "desc", "input_schema": {"type": "object"}}],
    )

    # text is tokenized by spaces via _FakeEncoding; image adds fixed estimate.
    assert total > tc.IMAGE_TOKEN_ESTIMATE


def test_count_text_falls_back_to_char_heuristic_when_no_encoding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tc, "_get_encoding", lambda: None)
    assert tc._count_text("abcd") == 1
    assert tc._count_text("abcdefgh") == 2


def test_count_text_falls_back_when_encoding_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenEncoding:
        def encode(self, _text: str) -> list[int]:
            raise RuntimeError("boom")

    monkeypatch.setattr(tc, "_get_encoding", lambda: _BrokenEncoding())
    assert tc._count_text("abcdefgh") == 2


def test_estimate_request_tokens_extracts_expected_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tc, "_get_encoding", lambda: _FakeEncoding())

    body = {
        "messages": [{"role": "user", "content": "hello there"}],
        "system": "sys",
        "tools": [{"name": "x", "description": "y", "input_schema": {"type": "object"}}],
    }
    assert tc.estimate_request_tokens(body) >= 1
