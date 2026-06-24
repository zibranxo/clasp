from __future__ import annotations

import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.modules.pop("clasp", None)
sys.path.insert(0, str(ROOT))

from clasp.providers.deepseek import DeepSeekProvider
from clasp.providers.anthropic_transport import AnthropicMessagesTransport


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def json(self) -> dict:
        return self._json_data


class _FakeClient:
    def __init__(self, *, get_response: _FakeResponse | None = None) -> None:
        self._get_response = get_response or _FakeResponse(json_data={"data": []})
        self.last_url = ""
        self.last_headers = {}

    async def get(self, url: str, headers: dict) -> _FakeResponse:
        self.last_url = url
        self.last_headers = headers
        return self._get_response


def test_deepseek_provider_inheritance() -> None:
    p = DeepSeekProvider("deepseek", "https://api.deepseek.com/anthropic")
    assert isinstance(p, AnthropicMessagesTransport)


def test_sanitize_request_preserves_text_and_valid_tool_results() -> None:
    p = DeepSeekProvider("deepseek", "https://api.deepseek.com/anthropic")
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_result", "tool_use_id": "tool-1", "content": [{"type": "text", "text": "success"}]},
                ],
            }
        ]
    }
    sanitized = p._sanitize_request(request)
    assert sanitized["messages"][0]["content"] == [
        {"type": "text", "text": "hello"},
        {"type": "tool_result", "tool_use_id": "tool-1", "content": [{"type": "text", "text": "success"}]},
    ]


def test_sanitize_request_strips_top_level_image_and_document() -> None:
    p = DeepSeekProvider("deepseek", "https://api.deepseek.com/anthropic")
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "abc"}},
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "def"}},
                ],
            }
        ]
    }
    sanitized = p._sanitize_request(request)
    assert sanitized["messages"][0]["content"] == [
        {"type": "text", "text": "hello"},
    ]


def test_sanitize_request_strips_nested_image_and_document_in_tool_result() -> None:
    p = DeepSeekProvider("deepseek", "https://api.deepseek.com/anthropic")
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": [
                            {"type": "text", "text": "partial success"},
                            {"type": "image", "source": {}},
                        ],
                    },
                ],
            }
        ]
    }
    sanitized = p._sanitize_request(request)
    assert sanitized["messages"][0]["content"] == [
        {"type": "text", "text": "hello"},
        {"type": "tool_result", "tool_use_id": "tool-1", "content": [{"type": "text", "text": "partial success"}]},
    ]


def test_sanitize_request_strips_empty_tool_result() -> None:
    p = DeepSeekProvider("deepseek", "https://api.deepseek.com/anthropic")
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "tool_result", "tool_use_id": "tool-empty-list", "content": []},
                    {"type": "tool_result", "tool_use_id": "tool-empty-str", "content": ""},
                    {"type": "tool_result", "tool_use_id": "tool-none", "content": None},
                ],
            }
        ]
    }
    sanitized = p._sanitize_request(request)
    assert sanitized["messages"][0]["content"] == [
        {"type": "text", "text": "hello"},
    ]


def test_sanitize_request_strips_tool_result_that_becomes_empty() -> None:
    p = DeepSeekProvider("deepseek", "https://api.deepseek.com/anthropic")
    request = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": [
                            {"type": "image", "source": {}},
                        ],
                    },
                ],
            }
        ]
    }
    sanitized = p._sanitize_request(request)
    assert sanitized["messages"][0]["content"] == [
        {"type": "text", "text": "hello"},
    ]


@pytest.mark.asyncio
async def test_deepseek_list_models_endpoints() -> None:
    p = DeepSeekProvider("deepseek", "https://api.deepseek.com/anthropic")
    fake = _FakeClient(get_response=_FakeResponse(json_data={"data": [{"id": "deepseek-chat"}]}))
    p.client = fake

    models = await p.list_models(api_key="ds-key")

    assert models == ["deepseek-chat"]
    assert fake.last_url == "https://api.deepseek.com/models"
    assert fake.last_headers == {"Authorization": "Bearer ds-key"}
