"""
Loopback-only IP guard middleware for CLASP.

Mounts as an ASGI middleware on the FastAPI app.  Any request whose path
starts with ``/internal`` is rejected with ``403 Forbidden`` unless it
arrives from the loopback interface (``127.x.x.x`` or ``::1``).

All other paths (proxy routes, ``/health``, static UI assets) pass through
regardless of source IP — the server is intentionally bound to ``127.0.0.1``
anyway, but this middleware provides defence-in-depth and explicit semantics.

Usage (in clasp/server.py)
--------------------------
    from clasp.utils.ip_guard import IPGuard
    app.add_middleware(IPGuard)
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from loguru import logger

_PROTECTED_PREFIX = "/internal"

# IPv4 loopback block: 127.0.0.0/8
_IPV4_LOOPBACK_PREFIX = "127."
# IPv6 loopback
_IPV6_LOOPBACK = "::1"


class IPGuard(BaseHTTPMiddleware):
    """Block non-loopback clients from ``/internal/*`` endpoints.

    Starlette's ``BaseHTTPMiddleware`` gives us a clean ``dispatch`` hook
    without having to deal with raw ASGI scope dicts.  The client IP is read
    from ``request.client.host`` which Starlette populates from the ASGI
    ``scope["client"]`` tuple set by uvicorn.
    """

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if request.url.path.startswith(_PROTECTED_PREFIX):
            client_host = (request.client.host if request.client else None) or ""
            if not _is_loopback(client_host):
                logger.warning(
                    "IPGuard: blocked {} {} from non-loopback address {}",
                    request.method,
                    request.url.path,
                    client_host,
                )
                return Response(
                    content='{"detail":"Forbidden: loopback access only"}',
                    status_code=403,
                    media_type="application/json",
                )

        return await call_next(request)


def _is_loopback(host: str) -> bool:
    """Return True when *host* is a loopback address.

    Handles:
      - IPv4 loopback range 127.0.0.0/8 (``127.x.x.x``)
      - IPv6 loopback ``::1``
      - Empty string / missing host → treated as *not* loopback (fail-closed)
    """
    if not host:
        return False
    return host.startswith(_IPV4_LOOPBACK_PREFIX) or host == _IPV6_LOOPBACK
