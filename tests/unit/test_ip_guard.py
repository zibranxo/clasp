"""
tests/unit/test_ip_guard.py
===========================
Unit tests for clasp.utils.ip_guard.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import os

from clasp.utils.ip_guard import IPGuard, _is_loopback


def test_is_loopback_ipv4():
    """Test IPv4 loopback detection."""
    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("127.0.0.0") is True
    assert _is_loopback("127.255.255.255") is True
    assert _is_loopback("127.1.2.3") is True


def test_is_loopback_ipv4_non_loopback():
    """Test IPv4 non-loopback addresses."""
    assert _is_loopback("192.168.1.1") is False
    assert _is_loopback("10.0.0.1") is False
    assert _is_loopback("172.16.0.1") is False
    assert _is_loopback("1.2.3.4") is False
    assert _is_loopback("0.0.0.0") is False
    assert _is_loopback("255.255.255.255") is False


def test_is_loopback_ipv6():
    """Test IPv6 loopback detection."""
    assert _is_loopback("::1") is True


def test_is_loopback_ipv6_non_loopback():
    """Test IPv6 non-loopback addresses."""
    assert _is_loopback("::") is False
    assert _is_loopback("2001:db8::1") is False
    assert _is_loopback("::ffff:192.0.2.1") is False


def test_is_loopback_edge_cases():
    """Test edge cases for _is_loopback."""
    assert _is_loopback("") is False
    assert _is_loopback(None) is False  # type: ignore
    assert _is_loopback("localhost") is False
    assert _is_loopback("127.0.0.1:8080") is False  # Port included
    assert _is_loopback("127.0.0.1 ") is False  # Trailing space
    assert _is_loopback(" 127.0.0.1") is False  # Leading space


def test_ip_guard_initialization():
    """Test IPGuard can be instantiated."""
    guard = IPGuard()
    assert isinstance(guard, IPGuard)


def test_ip_guard_dispatch_non_internal_path():
    """Test that non-/internal paths pass through."""
    guard = IPGuard()

    # Mock request with non-internal path
    request = MagicMock()
    request.url.path = "/v1/messages"
    request.client.host = "192.168.1.100"  # Non-loopback

    call_next = AsyncMock()
    call_next.return_value = MagicMock()

    # This should call call_next since path is not /internal
    import asyncio

    result = asyncio.run(guard.dispatch(request, call_next))

    call_next.assert_called_once_with(request)
    assert result == call_next.return_value


def test_ip_guard_dispatch_internal_path_loopback_allowed():
    """Test that /internal paths from loopback are allowed."""
    guard = IPGuard()

    # Mock request with internal path from loopback
    request = MagicMock()
    request.url.path = "/internal/config"
    request.client.host = "127.0.0.1"  # Loopback

    call_next = AsyncMock()
    call_next.return_value = MagicMock()

    import asyncio

    result = asyncio.run(guard.dispatch(request, call_next))

    call_next.assert_called_once_with(request)
    assert result == call_next.return_value


def test_ip_guard_dispatch_internal_path_loopback_v6_allowed():
    """Test that /internal paths from IPv6 loopback are allowed."""
    guard = IPGuard()

    # Mock request with internal path from IPv6 loopback
    request = MagicMock()
    request.url.path = "/internal/status"
    request.client.host = "::1"  # IPv6 loopback

    call_next = AsyncMock()
    call_next.return_value = MagicMock()

    import asyncio

    result = asyncio.run(guard.dispatch(request, call_next))

    call_next.assert_called_once_with(request)
    assert result == call_next.return_value


def test_ip_guard_dispatch_internal_path_non_loopback_blocked():
    """Test that /internal paths from non-loopback are blocked."""
    guard = IPGuard()

    # Mock request with internal path from non-loopback
    request = MagicMock()
    request.url.path = "/internal/config"
    request.client.host = "192.168.1.100"  # Non-loopback

    call_next = AsyncMock()

    import asyncio

    result = asyncio.run(guard.dispatch(request, call_next))

    # Should NOT call call_next
    call_next.assert_not_called()

    # Should return a 403 response
    assert hasattr(result, "status_code")
    assert result.status_code == 403
    assert b"Forbidden" in result.body or b"forbidden" in result.body.lower()


def test_ip_guard_dispatch_internal_path_no_client():
    """Test /internal path with no client info (fail closed)."""
    guard = IPGuard()

    # Mock request with internal path but no client
    request = MagicMock()
    request.url.path = "/internal/config"
    request.client = None

    call_next = AsyncMock()

    import asyncio

    result = asyncio.run(guard.dispatch(request, call_next))

    # Should NOT call call_next (fail closed)
    call_next.assert_not_called()

    # Should return a 403 response
    assert hasattr(result, "status_code")
    assert result.status_code == 403


def test_ip_guard_dispatch_internal_path_empty_client_host():
    """Test /internal path with empty client host (fail closed)."""
    guard = IPGuard()

    # Mock request with internal path but empty client host
    request = MagicMock()
    request.url.path = "/internal/config"
    request.client.host = ""  # Empty host

    call_next = AsyncMock()

    import asyncio

    result = asyncio.run(guard.dispatch(request, call_next))

    # Should NOT call call_next (fail closed)
    call_next.assert_not_called()

    # Should return a 403 response
    assert hasattr(result, "status_code")
    assert result.status_code == 403


if __name__ == "__main__":
    test_is_loopback_ipv4()
    test_is_loopback_ipv4_non_loopback()
    test_is_loopback_ipv6()
    test_is_loopback_ipv6_non_loopback()
    test_is_loopback_edge_cases()
    test_ip_guard_initialization()
    test_ip_guard_dispatch_non_internal_path()
    test_ip_guard_dispatch_internal_path_loopback_allowed()
    test_ip_guard_dispatch_internal_path_loopback_v6_allowed()
    test_ip_guard_dispatch_internal_path_non_loopback_blocked()
    test_ip_guard_dispatch_internal_path_no_client()
    test_ip_guard_dispatch_internal_path_empty_client_host()
    print("All IP guard tests passed!")