"""
clasp/utils/hash.py
Deterministic SHA-256 request fingerprint used as the response-cache key.

Plan §4 spec:
  Included fields: model, messages, system, temperature, top_p, top_k,
                   max_tokens, tools, tool_choice
  Excluded:        stream, metadata, request IDs, user, internal fields

  Returns a 64-character lowercase hex string.

Design decisions
----------------
* Only the semantically-significant fields are hashed. Excluded fields
  (stream, metadata) do not affect model output, so streaming and
  non-streaming calls with otherwise identical content share one cache entry.

* Field order is canonicalised (sorted keys at every nesting level) before
  serialisation so that two dicts with the same logical content but different
  key insertion order produce the same hash.

* messages is a list of dicts; each dict is sorted by key. Content blocks
  inside messages are also sorted so that image/tool payloads canonicalise.

* tools is sorted by tool name so that { tools: [B, A] } and
  { tools: [A, B] } hash identically — the order Claude Code sends tools
  is non-deterministic across sessions.

* All values are JSON-serialised with sort_keys=True, separators=(',', ':')
  (no whitespace) for maximum compactness and reproducibility across
  Python versions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Fields that affect model output — included in the hash.
_INCLUDED_FIELDS = (
    "model",
    "messages",
    "system",
    "temperature",
    "top_p",
    "top_k",
    "max_tokens",
    "tools",
    "tool_choice",
)

# Sentinel — marks a field as absent (distinct from None/false/0).
_ABSENT = object()


def hash_request(payload: dict[str, Any]) -> str:
    """
    Return a 64-char hex SHA-256 digest of the cache-relevant fields in
    *payload*.

    Parameters
    ----------
    payload:
        Raw Anthropic Messages API request body (dict).

    Returns
    -------
    str
        64-character lowercase hex SHA-256.
    """
    canonical = _extract_canonical(payload)
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=_json_default)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_canonical(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Build an ordered dict of only the included fields, with every nested
    structure recursively canonicalised.
    """
    out: dict[str, Any] = {}
    for field in _INCLUDED_FIELDS:
        value = payload.get(field, _ABSENT)
        if value is _ABSENT:
            continue  # omit absent keys so they don't affect the hash
        out[field] = _canonicalise(value)
    return out


def _canonicalise(value: Any) -> Any:
    """
    Recursively normalise *value* for deterministic serialisation.

    * dicts  → sorted by key
    * lists  → each element canonicalised, tools list sorted by tool name
    * str/int/float/bool/None → as-is
    """
    if isinstance(value, dict):
        return {k: _canonicalise(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_canonicalise(item) for item in value]
    return value


def _sort_tools(tools: list[Any]) -> list[Any]:
    """
    Sort a tools list by the tool's ``name`` field so insertion order doesn't
    affect the hash.  Non-dict entries sort to the end.
    """
    def _key(t: Any) -> str:
        if isinstance(t, dict):
            return str(t.get("name", ""))
        return ""
    return sorted((_canonicalise(t) for t in tools), key=_key)


# Override: apply tool-name sort specifically for the "tools" field.
_ORIGINAL_EXTRACT = _extract_canonical


def _extract_canonical(payload: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    out: dict[str, Any] = {}
    for field in _INCLUDED_FIELDS:
        value = payload.get(field, _ABSENT)
        if value is _ABSENT:
            continue
        if field == "tools" and isinstance(value, list):
            out[field] = _sort_tools(value)
        else:
            out[field] = _canonicalise(value)
    return out


def _json_default(obj: Any) -> Any:
    """Fallback JSON serialiser for non-standard types."""
    try:
        return str(obj)
    except Exception:
        return None