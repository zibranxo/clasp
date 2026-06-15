"""
Canonical request hasher for CLASP response-cache keys.

``hash_request(payload)`` takes the decoded JSON body of an Anthropic
``/v1/messages`` (or compatible) request and returns a deterministic
hex-encoded SHA-256 digest suitable for use as a cache key.

Canonicalisation rules
----------------------
Only fields that affect the response are included:
  - ``model``
  - ``messages``   (full list; order is significant)
  - ``system``     (if present)
  - ``temperature`` (if present)
  - ``top_p``      (if present)
  - ``top_k``      (if present)
  - ``max_tokens`` (if present)
  - ``tools``      (if present; order matters for tool-use)
  - ``tool_choice`` (if present)

Fields that do NOT affect the response (e.g. ``stream``, ``metadata``,
client-side ``x-request-id``) are excluded so that streaming and
non-streaming requests for the same content share a cache entry.

The canonical form is the JSON serialisation of the extracted fields
sorted by key, encoded as UTF-8, then SHA-256 hashed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Keys that influence the model's output and must be included in the cache key.
_CACHE_FIELDS: frozenset[str] = frozenset(
    [
        "model",
        "messages",
        "system",
        "temperature",
        "top_p",
        "top_k",
        "max_tokens",
        "tools",
        "tool_choice",
    ]
)


def hash_request(payload: dict[str, Any]) -> str:
    """Return a hex SHA-256 digest for *payload*.

    Parameters
    ----------
    payload:
        Decoded JSON body from an Anthropic ``/v1/messages`` request (or any
        OpenAI-compatible body that has been normalised to Anthropic format
        before reaching the cache layer).

    Returns
    -------
    str
        64-character lowercase hex digest, stable across process restarts.
    """
    canonical = _canonicalise(payload)
    serialised = json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


def _canonicalise(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract only the fields that affect the response."""
    return {k: payload[k] for k in _CACHE_FIELDS if k in payload}
