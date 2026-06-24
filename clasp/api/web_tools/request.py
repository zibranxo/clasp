"""Detect forced Anthropic web server tool requests."""

from __future__ import annotations

from typing import Any


def request_text(request: dict) -> str:
    """Join all user/assistant message content into one string for tool input parsing."""
    from clasp.api.web_tools.parsers import content_text

    messages = request.get("messages") or []
    return "\n".join(content_text(message.get("content")) for message in messages if isinstance(message, dict))


def forced_tool_turn_text(request: dict) -> str:
    """Text for parsing forced server-tool inputs: latest user turn only (avoids stale history)."""
    messages = request.get("messages") or []
    if not messages:
        return ""

    from clasp.api.web_tools.parsers import content_text

    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return content_text(message.get("content"))
    return ""


def forced_server_tool_name(request: dict) -> str | None:
    """Return web_search or web_fetch only when tool_choice forces that server tool."""
    tc = request.get("tool_choice")
    if not isinstance(tc, dict):
        return None
    if tc.get("type") != "tool":
        return None
    name = tc.get("name")
    if name in {"web_search", "web_fetch"}:
        return str(name)
    return None


def has_tool_named(request: dict, name: str) -> bool:
    tools = request.get("tools") or []
    for tool in tools:
        if isinstance(tool, dict):
            if tool.get("name") == name:
                return True
        else:
            if getattr(tool, "name", None) == name:
                return True
    return False


def is_web_server_tool_request(request: dict) -> bool:
    """True when the client forces a web server tool via tool_choice (not merely listed)."""
    forced = forced_server_tool_name(request)
    if forced is None:
        return False
    return has_tool_named(request, forced)


def is_anthropic_server_tool_definition(tool: dict | Any) -> bool:
    """Whether ``tool`` refers to an Anthropic server tool (web_search / web_fetch family)."""
    if isinstance(tool, dict):
        name = (tool.get("name") or "").strip()
        typ = tool.get("type")
    else:
        name = (getattr(tool, "name", None) or "").strip()
        typ = getattr(tool, "type", None)

    if name in ("web_search", "web_fetch"):
        return True
    if isinstance(typ, str):
        return typ.startswith("web_search") or typ.startswith("web_fetch")
    return False


def has_listed_anthropic_server_tools(request: dict) -> bool:
    """True when tools include web_search / web_fetch-style entries (listed, forced or not)."""
    tools = request.get("tools") or []
    return any(is_anthropic_server_tool_definition(t) for t in tools)


def openai_chat_upstream_server_tool_error(
    request: dict, *, web_tools_enabled: bool
) -> str | None:
    """Return a user-facing error when OpenAI Chat upstream cannot satisfy server-tool semantics."""
    forced = forced_server_tool_name(request)
    if forced and not web_tools_enabled:
        return (
            f"tool_choice forces Anthropic server tool {forced!r}, but local web server tools are "
            "disabled (ENABLE_WEB_SERVER_TOOLS=false). Enable them or use a native Anthropic "
            "Messages transport (e.g. open_router, ollama, lmstudio)."
        )
    if not forced and has_listed_anthropic_server_tools(request):
        return (
            "OpenAI Chat upstreams cannot use listed Anthropic server tools "
            "(web_search / web_fetch) without the local web server tool handler. Use a native "
            "Anthropic transport, set ENABLE_WEB_SERVER_TOOLS=true and force the tool with "
            "tool_choice, or remove these tools from the request."
        )
    return None
