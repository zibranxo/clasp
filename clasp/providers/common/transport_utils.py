"""
clasp/providers/common/transport_utils.py

Shared helper functions used by both ``openai_transport.py`` and
``anthropic_transport.py``.

Extracted to eliminate code duplication — both transports need the same
crude token estimate (placeholder until the real ``token_counter.py``
integration lands in each transport's hot path) and the same RFC 9110
``Retry-After`` header parser for 429 handling.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def local_token_estimate(text: str) -> int:
    """
    Crude ~4-chars-per-token estimate, used as a placeholder until
    ``providers/common/token_counter.py`` is wired into each transport's
    hot path with a real tiktoken-based count.  Good enough to feed
    ``message_start``'s ``usage.input_tokens`` and
    ``ratelimit/bucket.py``'s pre-emptive TPM check; not good enough
    for billing-accuracy use cases.
    """
    return max(1, len(text) // 4)


def parse_retry_after(value: str | None) -> float | None:
    """
    Parse a ``Retry-After`` header value, which per RFC 9110 is either an
    integer number of seconds or an HTTP-date.  Returns seconds remaining
    (clamped to >= 0), or ``None`` if the header is absent/unparseable.
    """
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(delta, 0.0)
    except (TypeError, ValueError):
        return None
