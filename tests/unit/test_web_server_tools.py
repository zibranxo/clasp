from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

import clasp.api.web_tools.constants as web_tool_constants
from clasp.api.web_tools import egress as web_egress
from clasp.api.web_tools.egress import (
    WebFetchEgressPolicy,
    WebFetchEgressViolation,
    enforce_web_fetch_egress,
)
from clasp.api.web_tools.outbound import (
    _drain_response_body_capped,
    _read_response_body_capped,
    _run_web_fetch,
)
from clasp.api.web_tools.request import is_web_server_tool_request
from clasp.api.web_tools.streaming import stream_web_server_tool_response

_STRICT_EGRESS = WebFetchEgressPolicy(
    allow_private_network_targets=False,
    allowed_schemes=frozenset({"http", "https"}),
)


# ---------------------------------------------------------------------------
# Stream contract helpers (ported from free-claude-code-main)
# ---------------------------------------------------------------------------

from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class SSEEvent:
    event: str
    data: dict[str, Any]
    raw: str


def parse_sse_text(text: str) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    current_event = ""
    data_parts: list[str] = []
    raw_parts: list[str] = []

    for line in text.splitlines():
        stripped = line.rstrip("\r\n")
        if stripped == "":
            if current_event or data_parts:
                data_text = "\n".join(data_parts)
                try:
                    parsed = json.loads(data_text) if data_text else {}
                    data = parsed if isinstance(parsed, dict) else {"value": parsed}
                except Exception:
                    data = {"raw": data_text}
                events.append(SSEEvent(current_event, data, "\n".join(raw_parts)))
            current_event = ""
            data_parts = []
            raw_parts = []
            continue
        raw_parts.append(stripped)
        if stripped.startswith("event:"):
            current_event = stripped.split(":", 1)[1].strip()
        elif stripped.startswith("data:"):
            data_parts.append(stripped.split(":", 1)[1].strip())

    if current_event or data_parts:
        data_text = "\n".join(data_parts)
        try:
            parsed = json.loads(data_text) if data_text else {}
            data = parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            data = {"raw": data_text}
        events.append(SSEEvent(current_event, data, "\n".join(raw_parts)))
    return events


import json

_NO_DELTA_BLOCK_KINDS = frozenset(
    {
        "server_tool_use",
        "web_search_tool_result",
        "web_fetch_tool_result",
        "text_eager",
        "redacted_thinking",
    }
)

_ALLOWED_BLOCK_START_TYPES = frozenset(
    {
        "text",
        "thinking",
        "tool_use",
        "redacted_thinking",
        "server_tool_use",
        "web_search_tool_result",
        "web_fetch_tool_result",
    }
)


def assert_anthropic_stream_contract(events: list[SSEEvent], *, allow_error: bool = False) -> None:
    assert events, "stream produced no SSE events"
    event_names = [event.event for event in events]
    assert "message_start" in event_names, event_names
    assert event_names[-1] == "message_stop", event_names

    open_blocks: dict[int, str] = {}
    seen_blocks: set[int] = set()
    for event in events:
        if event.event == "error" and not allow_error:
            raise AssertionError(f"unexpected SSE error event: {event.data}")

        if event.event == "content_block_start":
            index = event.data.get("index")
            assert isinstance(index, int)
            block = event.data.get("content_block", {})
            assert isinstance(block, dict)
            block_type = str(block.get("type", ""))
            assert block_type in _ALLOWED_BLOCK_START_TYPES, event.data
            assert index not in open_blocks
            assert index not in seen_blocks
            if block_type == "text" and str(block.get("text", "")).strip():
                storage = "text_eager"
            else:
                storage = block_type
            open_blocks[index] = storage
            seen_blocks.add(index)
            continue

        if event.event == "content_block_delta":
            index = event.data.get("index")
            assert isinstance(index, int)
            assert index in open_blocks
            kind = open_blocks[index]
            assert kind not in _NO_DELTA_BLOCK_KINDS
            delta = event.data.get("delta", {})
            assert isinstance(delta, dict)
            delta_type = str(delta.get("type", ""))
            expected = {
                "text": "text_delta",
                "tool_use": "input_json_delta",
            }[kind]
            assert delta_type == expected
            continue

        if event.event == "content_block_stop":
            index = event.data.get("index")
            assert isinstance(index, int)
            assert index in open_blocks
            open_blocks.pop(index)

    assert not open_blocks
    assert seen_blocks


def text_content(events: list[SSEEvent]) -> str:
    parts: list[str] = []
    for event in events:
        if event.event == "content_block_start":
            block = event.data.get("content_block", {})
            if isinstance(block, dict) and block.get("type") == "text":
                eager = str(block.get("text", ""))
                if eager:
                    parts.append(eager)
        delta = event.data.get("delta", {})
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            parts.append(str(delta.get("text", "")))
    return "".join(parts)


def parse_cli_event(event: Any) -> list[dict]:
    if not isinstance(event, dict):
        return []
    etype = event.get("type")
    if etype == "content_block_delta":
        delta = event.get("delta", {})
        if isinstance(delta, dict) and delta.get("type") == "text_delta":
            return [
                {
                    "type": "text_delta",
                    "index": event.get("index", -1),
                    "text": delta.get("text", ""),
                }
            ]
    return []


# ---------------------------------------------------------------------------
# Ported Tests
# ---------------------------------------------------------------------------

def test_web_server_tool_not_detected_when_tool_only_listed():
    """Listing web_search without forcing it must not skip the upstream provider."""
    request = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "search"}],
        "tools": [{"name": "web_search", "type": "web_search_20250305"}],
    }

    assert not is_web_server_tool_request(request)


def test_web_server_tool_detected_when_tool_choice_forces_it():
    request = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "search"}],
        "tools": [{"name": "web_search", "type": "web_search_20250305"}],
        "tool_choice": {"type": "tool", "name": "web_search"},
    }

    assert is_web_server_tool_request(request)


def test_web_server_tool_not_detected_when_forced_name_missing_from_tools():
    request = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"name": "other", "type": "function"}],
        "tool_choice": {"type": "tool", "name": "web_search"},
    }

    assert not is_web_server_tool_request(request)


@pytest.mark.parametrize("provider_id", ["nvidia_nim", "groq"])
@pytest.mark.asyncio
async def test_service_rejects_forced_server_tool_on_openai_when_disabled(provider_id: str):
    """OpenAI Chat upstreams cannot run forced server tools without the local handler."""
    from clasp.api.service import dispatch_stream
    from clasp.config.settings import Settings

    mock_settings = Settings(enable_web_server_tools=False)

    mock_provider = MagicMock()
    mock_provider.provider_name = provider_id
    async def mock_select(*args, **kwargs):
        return mock_provider, "fake_key", 0

    request = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": "Perform a web search for the query: DeepSeek V4 model release 2026",
            }
        ],
        "tools": [{"name": "web_search", "type": "web_search_20250305"}],
        "tool_choice": {"type": "tool", "name": "web_search"},
    }

    with patch("clasp.api.service.get_settings", return_value=mock_settings), \
         patch("clasp.api.service.get_cache", return_value=None):
        
        gen = dispatch_stream(request, select_fn=mock_select)
        with pytest.raises(HTTPException) as excinfo:
            async for _ in gen:
                pass
        
        assert excinfo.value.status_code == 400
        assert "ENABLE_WEB_SERVER_TOOLS" in excinfo.value.detail["error"]["message"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://192.168.1.1/",
        "http://10.0.0.1/",
        "http://[::1]/",
        "http://localhost/foo",
        "http://mybox.local/",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
def test_enforce_web_fetch_egress_blocks_internal_or_disallowed(url: str):
    with pytest.raises(WebFetchEgressViolation):
        enforce_web_fetch_egress(url, _STRICT_EGRESS)


def test_enforce_web_fetch_egress_allows_global_literal_ip():
    enforce_web_fetch_egress("http://8.8.8.8/", _STRICT_EGRESS)


def test_enforce_web_fetch_egress_skips_private_checks_when_opted_in():
    enforce_web_fetch_egress(
        "http://127.0.0.1/",
        WebFetchEgressPolicy(
            allow_private_network_targets=True,
            allowed_schemes=frozenset({"http", "https"}),
        ),
    )


def _httpx_response(
    status: int,
    *,
    url: str = "http://8.8.8.8/",
    location: str | None = None,
    body: bytes = b"hello world",
) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.url = httpx.URL(url)
    hdrs: dict[str, str] = {}
    if location is not None:
        hdrs["location"] = location
    r.headers = httpx.Headers(hdrs)
    r.encoding = "utf-8"
    r.raise_for_status = MagicMock()
    
    async def aiter_bytes(chunk_size=None):
        yield body
        
    r.aiter_bytes = aiter_bytes
    return r


def _httpx_client_patch(
    *responses: MagicMock,
) -> tuple[MagicMock, MagicMock]:
    queue = list(responses)
    n = 0

    async def get_side(*_a: Any, **_k: Any) -> Any:
        nonlocal n
        resp = queue[n] if n < len(queue) else queue[-1]
        n += 1
        return resp

    client = MagicMock()
    client.get = AsyncMock(side_effect=get_side)

    client_cm = MagicMock()
    client_cm.__aenter__ = AsyncMock(return_value=client)
    client_cm.__aexit__ = AsyncMock(return_value=None)
    return client_cm, client


def test_enforce_web_fetch_egress_documents_connect_time_pinning():
    assert enforce_web_fetch_egress.__doc__ and "resolved addresses" in (
        enforce_web_fetch_egress.__doc__ or ""
    )
    assert (
        web_egress.get_validated_stream_addrinfos_for_egress.__doc__
        and "pinning"
        in (web_egress.get_validated_stream_addrinfos_for_egress.__doc__ or "")
    )
    assert "DNS-pinned" in (_run_web_fetch.__doc__ or "")


@pytest.mark.asyncio
async def test_run_web_fetch_follows_redirect_when_each_hop_is_allowed():
    res_redirect = _httpx_response(
        302, url="http://8.8.8.8/start", location="/final", body=b""
    )
    res_ok = _httpx_response(200, url="http://8.8.8.8/final", body=b"hello world")
    client_cm, client = _httpx_client_patch(res_redirect, res_ok)
    with patch("clasp.api.web_tools.outbound.httpx.AsyncClient", return_value=client_cm):
        out = await _run_web_fetch("http://8.8.8.8/start", _STRICT_EGRESS)

    assert out["data"] == "hello world"
    assert client.get.call_count == 2


@pytest.mark.asyncio
async def test_run_web_fetch_truncates_large_body_to_byte_cap(monkeypatch):
    huge = b"x" * 5000
    res_ok = _httpx_response(200, url="http://8.8.8.8/big", body=huge)
    client_cm, _ = _httpx_client_patch(res_ok)
    monkeypatch.setattr(web_tool_constants, "_MAX_WEB_FETCH_RESPONSE_BYTES", 100)
    with patch("clasp.api.web_tools.outbound.httpx.AsyncClient", return_value=client_cm):
        out = await _run_web_fetch("http://8.8.8.8/big", _STRICT_EGRESS)

    assert len(out["data"]) <= 100
    assert out["data"] == "x" * 100


@pytest.mark.asyncio
async def test_run_web_fetch_redirect_to_blocked_host_raises():
    res_redirect = _httpx_response(
        302,
        url="http://8.8.8.8/start",
        location="http://127.0.0.1/secret",
        body=b"",
    )
    client_cm, client = _httpx_client_patch(res_redirect)
    with (
        patch("clasp.api.web_tools.outbound.httpx.AsyncClient", return_value=client_cm),
        pytest.raises(WebFetchEgressViolation),
    ):
        await _run_web_fetch("http://8.8.8.8/start", _STRICT_EGRESS)

    client.get.assert_called_once()


@pytest.mark.asyncio
async def test_run_web_fetch_redirect_without_location_raises():
    res_bad = _httpx_response(302, url="http://8.8.8.8/here", body=b"")
    client_cm, _ = _httpx_client_patch(res_bad)
    with (
        patch("clasp.api.web_tools.outbound.httpx.AsyncClient", return_value=client_cm),
        pytest.raises(WebFetchEgressViolation, match="missing Location"),
    ):
        await _run_web_fetch("http://8.8.8.8/here", _STRICT_EGRESS)


@pytest.mark.asyncio
async def test_run_web_fetch_excess_redirects_raises():
    res1 = _httpx_response(302, url="http://8.8.8.8/a", location="/b", body=b"")
    res2 = _httpx_response(302, url="http://8.8.8.8/b", location="/c", body=b"")
    client_cm, _ = _httpx_client_patch(res1, res2)
    with (
        patch("clasp.api.web_tools.constants._MAX_WEB_FETCH_REDIRECTS", 1),
        patch("clasp.api.web_tools.outbound.httpx.AsyncClient", return_value=client_cm),
        pytest.raises(WebFetchEgressViolation, match="exceeded maximum redirects"),
    ):
        await _run_web_fetch("http://8.8.8.8/a", _STRICT_EGRESS)


@pytest.mark.asyncio
async def test_streams_web_search_server_tool_result():
    async def fake_search(query: str) -> list[dict[str, str]]:
        assert query == "DeepSeek V4 model release 2026"
        return [{"title": "DeepSeek V4 Released", "url": "https://example.com/v4"}]

    request = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Perform a web search for the query: DeepSeek V4 model release 2026"
                ),
            }
        ],
        "tools": [{"name": "web_search", "type": "web_search_20250305"}],
        "tool_choice": {"type": "tool", "name": "web_search"},
    }

    with patch("clasp.api.web_tools.outbound._run_web_search", fake_search):
        raw = "".join(
            [
                event
                async for event in stream_web_server_tool_response(
                    request, input_tokens=42, web_fetch_egress=_STRICT_EGRESS
                )
            ]
        )
    events = parse_sse_text(raw)
    assert_anthropic_stream_contract(events)
    starts = [e for e in events if e.event == "content_block_start"]
    assert starts[0].data["content_block"]["type"] == "server_tool_use"
    assert starts[0].data["content_block"]["name"] == "web_search"
    tool_use_id = starts[0].data["content_block"]["id"]
    assert starts[1].data["content_block"]["type"] == "web_search_tool_result"
    assert starts[1].data["content_block"]["tool_use_id"] == tool_use_id
    assert starts[1].data["content_block"]["content"][0]["url"] == (
        "https://example.com/v4"
    )
    text_deltas = [
        e
        for e in events
        if e.event == "content_block_delta"
        and e.data.get("delta", {}).get("type") == "text_delta"
    ]
    assert text_deltas, "summary must be streamed as text_delta"
    assert "example.com" in text_content(events)
    cli_text: list[str] = []
    for ev in events:
        cli_text.extend(
            str(p.get("text", ""))
            for p in parse_cli_event(ev.data)
            if p.get("type") == "text_delta"
        )
    assert "example.com" in "".join(cli_text)
    deltas = [e for e in events if e.event == "message_delta"]
    assert deltas[-1].data["usage"]["server_tool_use"] == {"web_search_requests": 1}


@pytest.mark.asyncio
async def test_forced_web_fetch_ignores_stale_url_from_prior_user_turns():
    """Only the latest user message supplies the URL (not earlier transcript text)."""
    target = "https://new-only.example.com/page"

    async def fake_fetch(url: str, _egress: WebFetchEgressPolicy) -> dict[str, str]:
        assert url == target
        return {
            "url": url,
            "title": "T",
            "media_type": "text/plain",
            "data": "x",
        }

    request = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": "Earlier turn https://stale.com/old-article ignore this",
            },
            {"role": "assistant", "content": "ok"},
            {
                "role": "user",
                "content": f"Please fetch {target} for the summary",
            },
        ],
        "tools": [{"name": "web_fetch", "type": "web_fetch_20250910"}],
        "tool_choice": {"type": "tool", "name": "web_fetch"},
    }

    with patch("clasp.api.web_tools.outbound._run_web_fetch", fake_fetch):
        raw = "".join(
            [
                event
                async for event in stream_web_server_tool_response(
                    request, input_tokens=1, web_fetch_egress=_STRICT_EGRESS
                )
            ]
        )
    assert target in raw


@pytest.mark.asyncio
async def test_streams_web_fetch_server_tool_result():
    async def fake_fetch(url: str, _egress: WebFetchEgressPolicy) -> dict[str, str]:
        assert url == "https://example.com/article"
        return {
            "url": url,
            "title": "Example Article",
            "media_type": "text/plain",
            "data": "Article body",
        }

    request = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "Fetch https://example.com/article please"}
        ],
        "tools": [{"name": "web_fetch", "type": "web_fetch_20250910"}],
        "tool_choice": {"type": "tool", "name": "web_fetch"},
    }

    with patch("clasp.api.web_tools.outbound._run_web_fetch", fake_fetch):
        raw = "".join(
            [
                event
                async for event in stream_web_server_tool_response(
                    request, input_tokens=42, web_fetch_egress=_STRICT_EGRESS
                )
            ]
        )
    events = parse_sse_text(raw)
    assert_anthropic_stream_contract(events)
    starts = [e for e in events if e.event == "content_block_start"]
    assert starts[0].data["content_block"]["type"] == "server_tool_use"
    tool_use_id = starts[0].data["content_block"]["id"]
    assert starts[1].data["content_block"]["type"] == "web_fetch_tool_result"
    assert starts[1].data["content_block"]["tool_use_id"] == tool_use_id
    assert starts[1].data["content_block"]["content"]["content"]["title"] == (
        "Example Article"
    )
    assert any(
        e.event == "content_block_delta"
        and e.data.get("delta", {}).get("type") == "text_delta"
        for e in events
    )
    assert "Article body" in text_content(events)
    cli_text: list[str] = []
    for ev in events:
        cli_text.extend(
            str(p.get("text", ""))
            for p in parse_cli_event(ev.data)
            if p.get("type") == "text_delta"
        )
    assert "Article body" in "".join(cli_text)
    deltas = [e for e in events if e.event == "message_delta"]
    assert deltas[-1].data["usage"]["server_tool_use"] == {"web_fetch_requests": 1}


@pytest.mark.asyncio
async def test_streams_web_fetch_error_summary_generic_by_default():
    secret = "sensitive-upstream-token"

    async def boom(_url: str, _egress: WebFetchEgressPolicy) -> dict[str, str]:
        raise ValueError(secret)

    request = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [
            {
                "role": "user",
                "content": "Fetch https://example.com/sensitive-path?x=1 please",
            }
        ],
        "tools": [{"name": "web_fetch", "type": "web_fetch_20250910"}],
        "tool_choice": {"type": "tool", "name": "web_fetch"},
    }

    with patch("clasp.api.web_tools.outbound.logger.warning") as log_warn:
        with patch("clasp.api.web_tools.outbound._run_web_fetch", boom):
            raw = "".join(
                [
                    event
                    async for event in stream_web_server_tool_response(
                        request,
                        input_tokens=1,
                        web_fetch_egress=_STRICT_EGRESS,
                        verbose_client_errors=False,
                    )
                ]
            )

    assert secret not in raw
    assert "ValueError" not in raw
    assert "Web tool request failed." in raw
    err_events = parse_sse_text(raw)
    assert_anthropic_stream_contract(err_events)
    assert any(
        e.event == "content_block_delta"
        and e.data.get("delta", {}).get("type") == "text_delta"
        for e in err_events
    )
    cli_err_text: list[str] = []
    for ev in err_events:
        cli_err_text.extend(
            str(p.get("text", ""))
            for p in parse_cli_event(ev.data)
            if p.get("type") == "text_delta"
        )
    assert "Web tool request failed." in "".join(cli_err_text)
    log_blob = " ".join(str(a) for c in log_warn.call_args_list for a in c.args)
    assert secret not in log_blob
    assert "example.com" in log_blob
    assert "/sensitive-path" not in log_blob


@pytest.mark.asyncio
async def test_streams_web_fetch_error_summary_verbose_includes_exception_class():
    async def boom(_url: str, _egress: WebFetchEgressPolicy) -> dict[str, str]:
        raise OSError(5, "oops")

    request = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 100,
        "messages": [
            {"role": "user", "content": "Fetch https://example.com/x"}
        ],
        "tools": [{"name": "web_fetch", "type": "web_fetch_20250910"}],
        "tool_choice": {"type": "tool", "name": "web_fetch"},
    }

    with patch("clasp.api.web_tools.outbound._run_web_fetch", boom):
        raw = "".join(
            [
                event
                async for event in stream_web_server_tool_response(
                    request,
                    input_tokens=1,
                    web_fetch_egress=_STRICT_EGRESS,
                    verbose_client_errors=True,
                )
            ]
        )
    assert "OSError" in raw


@pytest.mark.asyncio
async def test_read_response_body_capped_truncates_single_oversized_chunk():
    cap = 500

    async def aiter_bytes(chunk_size=None):
        yield b"z" * (cap * 20)

    response = MagicMock()
    response.aiter_bytes = aiter_bytes

    out = await _read_response_body_capped(response, cap)
    assert len(out) == cap
    assert out == b"z" * cap


@pytest.mark.asyncio
async def test_drain_response_body_capped_stops_after_first_chunk_when_oversized():
    cap = 300
    chunk_calls = {"n": 0}

    async def aiter_bytes(chunk_size=None):
        chunk_calls["n"] += 1
        yield b"y" * (cap * 10)

    response = MagicMock()
    response.aiter_bytes = aiter_bytes

    await _drain_response_body_capped(response, cap)
    assert chunk_calls["n"] == 1


@pytest.mark.parametrize("provider_id", ["nvidia_nim", "groq"])
@pytest.mark.asyncio
async def test_service_rejects_listed_server_tools_on_openai_chat(provider_id: str):
    from clasp.api.service import dispatch_stream
    from clasp.config.settings import Settings

    mock_settings = Settings(enable_web_server_tools=True)

    mock_provider = MagicMock()
    mock_provider.provider_name = provider_id
    async def mock_select(*args, **kwargs):
        return mock_provider, "fake_key", 0

    request = {
        "model": "m",
        "max_tokens": 20,
        "messages": [{"role": "user", "content": "q"}],
        "tools": [{"name": "web_search", "type": "web_search_20250305"}],
    }

    with patch("clasp.api.service.get_settings", return_value=mock_settings), \
         patch("clasp.api.service.get_cache", return_value=None):
        
        gen = dispatch_stream(request, select_fn=mock_select)
        with pytest.raises(HTTPException) as excinfo:
            async for _ in gen:
                pass
        
        assert excinfo.value.status_code == 400
        assert "OpenAI Chat upstreams" in excinfo.value.detail["error"]["message"]


@pytest.mark.parametrize("provider_id", ["ollama", "fireworks"])
@pytest.mark.asyncio
async def test_listed_server_tools_routed_on_anthropic_messages_providers(provider_id: str):
    """Native Anthropic transports may receive listed server tool definitions."""
    from clasp.api.service import dispatch_stream
    from clasp.config.settings import Settings

    mock_settings = Settings(enable_web_server_tools=False)

    mock_provider = MagicMock()
    mock_provider.provider_name = provider_id
    
    async def fake_stream(*args, **kwargs):
        yield b"event: message_start\ndata: {}\n\n"
        yield b"event: message_stop\ndata: {}\n\n"
    
    mock_provider.stream = fake_stream
    
    async def mock_select(*args, **kwargs):
        return mock_provider, "fake_key", 0

    request = {
        "model": "m",
        "max_tokens": 20,
        "messages": [{"role": "user", "content": "q"}],
        "tools": [{"name": "web_search", "type": "web_search_20250305"}],
    }

    with patch("clasp.api.service.get_settings", return_value=mock_settings), \
         patch("clasp.api.service.get_cache", return_value=None):
        
        gen = dispatch_stream(request, select_fn=mock_select)
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
            
        assert len(chunks) == 2


@pytest.mark.parametrize("provider_id", ["ollama", "fireworks"])
@pytest.mark.asyncio
async def test_forced_server_tools_routed_on_anthropic_messages_providers_when_local_disabled(provider_id: str):
    """Native Anthropic transports may receive forced server tools when local tools are off."""
    from clasp.api.service import dispatch_stream
    from clasp.config.settings import Settings

    mock_settings = Settings(enable_web_server_tools=False)

    mock_provider = MagicMock()
    mock_provider.provider_name = provider_id
    
    async def fake_stream(*args, **kwargs):
        yield b"event: message_start\ndata: {}\n\n"
        yield b"event: message_stop\ndata: {}\n\n"
    
    mock_provider.stream = fake_stream
    
    async def mock_select(*args, **kwargs):
        return mock_provider, "fake_key", 0

    request = {
        "model": "m",
        "max_tokens": 20,
        "messages": [{"role": "user", "content": "q"}],
        "tools": [{"name": "web_search", "type": "web_search_20250305"}],
        "tool_choice": {"type": "tool", "name": "web_search"},
    }

    with patch("clasp.api.service.get_settings", return_value=mock_settings), \
         patch("clasp.api.service.get_cache", return_value=None):
        
        gen = dispatch_stream(request, select_fn=mock_select)
        chunks = []
        async for chunk in gen:
            chunks.append(chunk)
            
        assert len(chunks) == 2
