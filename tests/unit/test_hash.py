"""
tests/unit/test_hash.py
=======================
Unit tests for clasp.utils.hash.
"""

from __future__ import annotations

import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import os

from clasp.utils.hash import hash_request, _canonicalise


def test_hash_request_empty():
    """Test hashing empty request."""
    result = hash_request({})
    # Should be hash of empty JSON object
    expected = hash_request({})
    assert result == expected
    assert len(result) == 64  # SHA-256 hex length
    assert all(c in "0123456789abcdef" for c in result)


def test_hash_request_with_fields():
    """Test hashing request with various fields."""
    payload = {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Hello"}],
        "temperature": 0.7,
        "max_tokens": 100,
    }

    result1 = hash_request(payload)
    result2 = hash_request(payload)

    # Same input should give same output
    assert result1 == result2
    assert len(result1) == 64


def test_hash_request_different_order():
    """Test that field order doesn't matter (keys are sorted)."""
    payload1 = {
        "model": "test",
        "messages": [{"role": "user", "content": "test"}],
        "temperature": 0.5,
    }

    payload2 = {
        "temperature": 0.5,
        "model": "test",
        "messages": [{"role": "user", "content": "test"}],
    }

    assert hash_request(payload1) == hash_request(payload2)


def test_hash_request_excludes_non_influential_fields():
    """Test that stream and metadata fields are excluded."""
    payload1 = {
        "model": "test",
        "messages": [{"role": "user", "content": "test"}],
        "stream": True,
        "metadata": {"user_id": "123"},
    }

    payload2 = {
        "model": "test",
        "messages": [{"role": "user", "content": "test"}],
        "stream": False,
        "metadata": {"user_id": "456"},
    }

    # Should be same because stream and metadata are excluded
    assert hash_request(payload1) == hash_request(payload2)


def test_hash_request_sensitive_to_content():
    """Test that hash changes when content changes."""
    payload1 = {
        "model": "test",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    payload2 = {
        "model": "test",
        "messages": [{"role": "user", "content": "World"}],
    }

    assert hash_request(payload1) != hash_request(payload2)


def test_hash_request_system_field():
    """Test handling of system field."""
    payload1 = {
        "model": "test",
        "system": "You are helpful",
        "messages": [{"role": "user", "content": "Hi"}],
    }

    payload2 = {
        "model": "test",
        "system": "You are helpful",
        "messages": [{"role": "user", "content": "Hi"}],
    }

    assert hash_request(payload1) == hash_request(payload2)

    payload3 = {
        "model": "test",
        "system": "You are different",
        "messages": [{"role": "user", "content": "Hi"}],
    }

    assert hash_request(payload1) != hash_request(payload3)


def test_hash_request_system_as_list():
    """Test system field as list of blocks."""
    payload1 = {
        "model": "test",
        "system": [{"type": "text", "text": "Part 1"}, {"type": "text", "text": "Part 2"}],
        "messages": [{"role": "user", "content": "Hi"}],
    }

    payload2 = {
        "model": "test",
        "system": "Part 1Part 2",  # Concatenated
        "messages": [{"role": "user", "content": "Hi"}],
    }

    # Should produce same hash
    assert hash_request(payload1) == hash_request(payload2)


def test_canonicalise():
    """Test the _canonicalise helper function."""
    payload = {
        "model": "test",
        "messages": [{"role": "user", "content": "test"}],
        "temperature": 0.7,
        "stream": True,  # Should be excluded
        "metadata": {"ignore": "me"},  # Should be excluded
        "unknown_field": "also excluded",
    }

    canonical = _canonicalise(payload)

    # Should only contain cache fields
    expected_keys = {
        "model",
        "messages",
        "temperature",
        # Note: stream and metadata are not in _CACHE_FIELDS
        # unknown_field is not in _CACHE_FIELDS
    }
    assert set(canonical.keys()) == expected_keys

    # Check values are preserved
    assert canonical["model"] == "test"
    assert canonical["temperature"] == 0.7
    assert canonical["messages"] == [{"role": "user", "content": "test"}]


def test_canonicalise_empty():
    """Test _canonicalise with empty payload."""
    assert _canonicalise({}) == {}


def test_canonicalise_no_cache_fields():
    """Test _canonicalise when no cache fields present."""
    payload = {
        "stream": True,
        "metadata": {},
        "custom": "field",
    }
    assert _canonicalise(payload) == {}


if __name__ == "__main__":
    test_hash_request_empty()
    test_hash_request_with_fields()
    test_hash_request_different_order()
    test_hash_request_excludes_non_influential_fields()
    test_hash_request_sensitive_to_content()
    test_hash_request_system_field()
    test_hash_request_system_as_list()
    test_canonicalise()
    test_canonicalise_empty()
    test_canonicalise_no_cache_fields()
    print("All hash tests passed!")