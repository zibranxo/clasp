"""Request detection utilities for API optimizations.

Detects quota checks, title generation, prefix detection, safety classifier,
suggestion mode, and filepath extraction requests to enable targeted handling.
"""

from typing import Any


def get_field(obj: Any, key: str, default: Any = None) -> Any:
    """Get an attribute/key from a Pydantic model, lightweight object, or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def get_block_attr(block: Any, attr: str, default: Any = None) -> Any:
    """Get an attribute from a Pydantic model, lightweight object, or dict."""
    return get_field(block, attr, default)


def extract_text_from_content(content: Any) -> str:
    """Extract concatenated text from message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            text = get_block_attr(block, "text", "")
            if isinstance(text, str) and text:
                parts.append(text)
        return "".join(parts)
    return ""


def is_quota_check_request(request_data: dict[str, Any]) -> bool:
    """Check if this is a quota probe request.

    Quota checks are typically simple requests with max_tokens=1
    and a single message containing the word "quota".
    """
    max_tokens = get_field(request_data, "max_tokens")
    messages = get_field(request_data, "messages", [])
    if (
        max_tokens == 1
        and len(messages) == 1
        and get_field(messages[0], "role") == "user"
    ):
        text = extract_text_from_content(get_field(messages[0], "content"))
        if "quota" in text.lower():
            return True
    return False


def is_title_generation_request(request_data: dict[str, Any]) -> bool:
    """Check if this is a conversation title generation request.

    Title generation requests are detected by a system prompt containing
    title extraction instructions, no tools, and a single user message.
    """
    system = get_field(request_data, "system")
    tools = get_field(request_data, "tools")
    if not system or tools:
        return False
    system_text = extract_text_from_content(system).lower()
    if "title" not in system_text:
        return False
    return "sentence-case title" in system_text or (
        "return json" in system_text
        and "field" in system_text
        and ("coding session" in system_text or "this session" in system_text)
    )


def is_prefix_detection_request(request_data: dict[str, Any]) -> tuple[bool, str]:
    """Check if this is a fast prefix detection request.

    Prefix detection requests contain a policy_spec block and
    a Command: section for extracting shell command prefixes.

    Returns:
        Tuple of (is_prefix_request, command_string)
    """
    messages = get_field(request_data, "messages", [])
    if len(messages) != 1 or get_field(messages[0], "role") != "user":
        return False, ""

    content = extract_text_from_content(get_field(messages[0], "content"))

    if "<policy_spec>" in content and "Command:" in content:
        try:
            cmd_start = content.rfind("Command:") + len("Command:")
            return True, content[cmd_start:].strip()
        except TypeError:
            return False, ""

    return False, ""


def is_safety_classifier_request(request_data: dict[str, Any]) -> bool:
    """Return whether this is Claude Code's auto-mode safety classifier prompt."""
    tools = get_field(request_data, "tools")
    if tools:
        return False

    system = get_field(request_data, "system")
    system_text = (
        extract_text_from_content(system) if system else ""
    )
    messages = get_field(request_data, "messages", [])
    messages_text = "".join(
        extract_text_from_content(get_field(message, "content")) for message in messages
    )
    combined = f"{system_text}\n{messages_text}"
    has_verdict_instruction = "yes</block>" in combined or "no</block>" in combined
    return "<transcript>" in combined and has_verdict_instruction


def is_suggestion_mode_request(request_data: dict[str, Any]) -> bool:
    """Check if this is a suggestion mode request.

    Suggestion mode requests contain "[SUGGESTION MODE:" in the user's message,
    used for auto-suggesting what the user might type next.
    """
    messages = get_field(request_data, "messages", [])
    for msg in messages:
        if get_field(msg, "role") == "user":
            text = extract_text_from_content(get_field(msg, "content"))
            if "[SUGGESTION MODE:" in text:
                return True
    return False


def is_filepath_extraction_request(
    request_data: dict[str, Any],
) -> tuple[bool, str, str]:
    """Check if this is a filepath extraction request.

    Filepath extraction requests have a single user message with
    "Command:" and "Output:" sections, asking to extract file paths
    from command output.

    Returns:
        Tuple of (is_filepath_request, command, output)
    """
    messages = get_field(request_data, "messages", [])
    if len(messages) != 1 or get_field(messages[0], "role") != "user":
        return False, "", ""
    tools = get_field(request_data, "tools")
    if tools:
        return False, "", ""

    content = extract_text_from_content(get_field(messages[0], "content"))

    if "Command:" not in content or "Output:" not in content:
        return False, "", ""

    # Match if user content OR system block indicates filepath extraction
    user_has_filepaths = (
        "filepaths" in content.lower() or "<filepaths>" in content.lower()
    )
    system = get_field(request_data, "system")
    system_text = (
        extract_text_from_content(system) if system else ""
    )
    system_has_extract = (
        "extract any file paths" in system_text.lower()
        or "file paths that this command" in system_text.lower()
    )
    if not user_has_filepaths and not system_has_extract:
        return False, "", ""

    cmd_start = content.find("Command:") + len("Command:")
    output_marker = content.find("Output:", cmd_start)
    if output_marker == -1:
        return False, "", ""

    command = content[cmd_start:output_marker].strip()
    output = content[output_marker + len("Output:") :].strip()

    for marker in ["<", "\n\n"]:
        if marker in output:
            output = output.split(marker)[0].strip()

    return True, command, output
