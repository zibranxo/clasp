from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.modules.pop("clasp", None)
sys.path.insert(0, str(ROOT))

from clasp.providers.common.error_mapper import (
    ErrorType,
    build_anthropic_error,
    classify_error,
    is_retryable,
    to_anthropic_error_type,
)


def test_classify_content_filter_even_on_http_200() -> None:
    body = {"choices": [{"finish_reason": "content_filter"}]}
    assert classify_error(200, body) == ErrorType.CONTENT_FILTER


def test_classify_none_status_as_timeout() -> None:
    assert classify_error(None, None) == ErrorType.TIMEOUT


def test_classify_body_type_overrides_status_guess() -> None:
    body = {"error": {"type": "rate_limit_error"}}
    assert classify_error(400, body) == ErrorType.RATE_LIMIT


def test_classify_gemini_resource_exhausted() -> None:
    body = {"error": {"status": "RESOURCE_EXHAUSTED"}}
    assert classify_error(503, body) == ErrorType.RATE_LIMIT


def test_classify_fallbacks_for_status_ranges() -> None:
    assert classify_error(502, {}) == ErrorType.SERVER_ERROR
    assert classify_error(599, {}) == ErrorType.SERVER_ERROR
    assert classify_error(418, {}) == ErrorType.UNKNOWN


def test_retryable_matrix() -> None:
    assert is_retryable(ErrorType.RATE_LIMIT) is True
    assert is_retryable(ErrorType.TIMEOUT) is True
    assert is_retryable(ErrorType.CONTENT_FILTER) is False
    assert is_retryable(ErrorType.INVALID_REQUEST) is False


def test_anthropic_mapping_and_error_body_shape() -> None:
    assert to_anthropic_error_type(ErrorType.PERMISSION_ERROR) == "permission_error"
    payload = build_anthropic_error(ErrorType.CONTENT_FILTER, "blocked")
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["message"] == "blocked"
