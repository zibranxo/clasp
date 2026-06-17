"""
clasp/ui/routes.py
Serve the static web UI.

Routes:
  GET /       → 302 redirect to /ui
  GET /ui     → index.html
  GET /ui/assets/app.js    → app.js
  GET /ui/assets/style.css → style.css

The static files live in clasp/ui/static/.  At import time we resolve the
directory relative to *this* file so the package works regardless of the
current working directory or how it was installed.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

# ---------------------------------------------------------------------------
# Locate the static assets directory
# ---------------------------------------------------------------------------

# Prefer importlib.resources so the package works from a wheel as well as an
# editable install.  Fall back to __file__-relative path for dev convenience.
try:
    # Python 3.9+ path — works for both editable installs and wheels.
    _pkg_files = importlib.resources.files("clasp.ui")
    STATIC_DIR: Path = Path(str(_pkg_files.joinpath("static")))  # type: ignore[arg-type]
except (AttributeError, TypeError, ModuleNotFoundError):
    STATIC_DIR = Path(__file__).parent / "static"

if not STATIC_DIR.is_dir():
    logger.warning(
        "UI static directory not found — web UI will be unavailable",
        static_dir=str(STATIC_DIR),
    )

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["ui"])


@router.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    """Redirect bare root to the UI index."""
    return RedirectResponse(url="/ui", status_code=302)


@router.get("/ui", include_in_schema=False)
async def serve_index() -> FileResponse:
    """Serve the single-page app entry point."""
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        logger.error("index.html not found", path=str(index))
        # Return a minimal fallback so the server doesn't 500.
        from fastapi.responses import HTMLResponse  # local import to keep top clean

        return HTMLResponse(  # type: ignore[return-value]
            content="<h1>CLASP UI not found</h1><p>Run <code>clasp server</code> from the repo root.</p>",
            status_code=503,
        )
    return FileResponse(index, media_type="text/html")


@router.get("/ui/assets/app.js", include_in_schema=False)
async def serve_app_js() -> FileResponse:
    """Serve the Alpine.js application bundle."""
    path = STATIC_DIR / "app.js"
    return FileResponse(path, media_type="application/javascript")


@router.get("/ui/assets/style.css", include_in_schema=False)
async def serve_style_css() -> FileResponse:
    """Serve the custom stylesheet."""
    path = STATIC_DIR / "style.css"
    return FileResponse(path, media_type="text/css")


# ---------------------------------------------------------------------------
# Mount helper (called by server.py)
# ---------------------------------------------------------------------------

def mount_static(app: "FastAPI") -> None:  # noqa: F821 — type-only forward ref
    """
    Mount /ui/assets as a StaticFiles directory so that any additional static
    assets (fonts, icons, etc.) added later are served automatically without
    needing explicit routes above.

    Call this *after* including the router so explicit routes take priority
    over the catch-all mount.
    """
    if STATIC_DIR.is_dir():
        app.mount(
            "/ui/assets",
            StaticFiles(directory=str(STATIC_DIR)),
            name="ui_assets",
        )
        logger.debug("Static files mounted", path=str(STATIC_DIR), mount="/ui/assets")
    else:
        logger.warning(
            "Skipping static mount — directory missing",
            static_dir=str(STATIC_DIR),
        )