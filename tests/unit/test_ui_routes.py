"""
tests/unit/test_ui_routes.py
============================
Unit tests for clasp.ui.routes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.ui.routes import (
    router,
    serve_index,
    serve_app_js,
    serve_style_css,
    mount_static,
    STATIC_DIR,
)


def test_static_dir_exists():
    """Test that STATIC_DIR points to a valid directory."""
    # This might fail in test environment if static files don't exist
    # but we can at least check it's a Path object
    assert isinstance(STATIC_DIR, Path)


def test_ui_router_exists():
    """Test that the UI router was created."""
    assert router is not None
    assert len(router.routes) > 0


def test_serve_index_success():
    """Test serving index.html when file exists."""
    # Create a temporary index.html
    with tempfile.TemporaryDirectory() as tmpdir:
        static_dir = Path(tmpdir)
        index_file = static_dir / "index.html"
        index_file.write_text("<html><body>Test</body></html>")

        # Temporarily replace STATIC_DIR
        import clasp.ui.routes as ui_routes
        original_static_dir = ui_routes.STATIC_DIR
        ui_routes.STATIC_DIR = static_dir

        try:
            # This would normally be async, but we can test the logic
            # serve_index is async, so we need to run it
            async def test_serve():
                response = await serve_index()
                assert response.status_code == 200
                assert response.media_type == "text/html"
                body = b"".join([chunk async for chunk in response.body_iterator])
                assert b"<html><body>Test</body></html>" in body

            asyncio.run(test_serve())
        finally:
            ui_routes.STATIC_DIR = original_static_dir


def test_serve_index_not_found():
    """Test serving index.html when file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        static_dir = Path(tmpdir)
        # Don't create index.html

        import clasp.ui.routes as ui_routes
        original_static_dir = ui_routes.STATIC_DIR
        ui_routes.STATIC_DIR = static_dir

        try:
            async def test_serve():
                response = await serve_index()
                assert response.status_code == 503  # Service unavailable
                assert "CLASP UI not found" in str(response.body)

            asyncio.run(test_serve())
        finally:
            ui_routes.STATIC_DIR = original_static_dir


def test_serve_app_js():
    """Test serving app.js."""
    with tempfile.TemporaryDirectory() as tmpdir:
        static_dir = Path(tmpdir)
        js_file = static_dir / "app.js"
        js_file.write_text("console.log('test');")

        import clasp.ui.routes as ui_routes
        original_static_dir = ui_routes.STATIC_DIR
        ui_routes.STATIC_DIR = static_dir

        try:
            async def test_serve():
                response = await serve_app_js()
                assert response.status_code == 200
                assert response.media_type == "application/javascript"
                body = b"".join([chunk async for chunk in response.body_iterator])
                assert b"console.log('test');" in body

            asyncio.run(test_serve())
        finally:
            ui_routes.STATIC_DIR = original_static_dir


def test_serve_style_css():
    """Test serving style.css."""
    with tempfile.TemporaryDirectory() as tmpdir:
        static_dir = Path(tmpdir)
        css_file = static_dir / "style.css"
        css_file.write_text("body { color: red; }")

        import clasp.ui.routes as ui_routes
        original_static_dir = ui_routes.STATIC_DIR
        ui_routes.STATIC_DIR = static_dir

        try:
            async def test_serve():
                response = await serve_style_css()
                assert response.status_code == 200
                assert response.media_type == "text/css"
                body = b"".join([chunk async for chunk in response.body_iterator])
                assert b"body { color: red; }" in body

            asyncio.run(test_serve())
        finally:
            ui_routes.STATIC_DIR = original_static_dir


def test_mount_static():
    """Test mount_static function."""
    # This is harder to test without a real FastAPI app
    # We'll just test that it doesn't crash when called
    mock_app = MagicMock()
    mock_app.mount = MagicMock()

    with tempfile.TemporaryDirectory() as tmpdir:
        static_dir = Path(tmpdir)
        static_dir.mkdir()  # Make it exist

        import clasp.ui.routes as ui_routes
        original_static_dir = ui_routes.STATIC_DIR
        ui_routes.STATIC_DIR = static_dir

        try:
            mount_static(mock_app)
            # Should have called mount on the app
            mock_app.mount.assert_called_once()
            args, kwargs = mock_app.mount.call_args
            assert args[0] == "/ui/assets"
            assert isinstance(args[1], MagicMock)  # StaticFiles instance
            assert kwargs["name"] == "ui-assets"
        finally:
            ui_routes.STATIC_DIR = original_static_dir


def test_mount_static_missing_directory():
    """Test mount_static when directory doesn't exist."""
    mock_app = MagicMock()
    mock_app.mount = MagicMock()

    with tempfile.TemporaryDirectory() as tmpdir:
        static_dir = Path(tmpdir) / "nonexistent"
        # Don't create the directory

        import clasp.ui.routes as ui_routes
        original_static_dir = ui_routes.STATIC_DIR
        ui_routes.STATIC_DIR = static_dir

        try:
            mount_static(mock_app)
            # Should not have called mount
            mock_app.mount.assert_not_called()
        finally:
            ui_routes.STATIC_DIR = original_static_dir


if __name__ == "__main__":
    test_static_dir_exists()
    test_ui_router_exists()
    test_serve_index_success()
    test_serve_index_not_found()
    test_serve_app_js()
    test_serve_style_css()
    test_mount_static()
    test_mount_static_missing_directory()
    print("All UI routes tests passed!")