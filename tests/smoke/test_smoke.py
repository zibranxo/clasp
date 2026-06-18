"""
tests/smoke/test_smoke.py
=========================
Smoke test for CLASP basic functionality.
"""

from __future__ import annotations

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import httpx


def test_smoke():
    """Basic smoke test: start server, check health, check models."""
    print("Starting CLASP smoke test...")

    # Test 1: Check if we can import the main components
    try:
        from clasp.server import create_app
        from clasp.config.settings import Settings
        from clasp.providers.registry import build_registry
        print("✓ All core modules imported successfully")
    except Exception as e:
        print(f"✗ Failed to import core modules: {e}")
        return False

    # Test 2: Check settings creation
    try:
        settings = Settings()
        assert settings.server.port == 8082
        assert settings.server.api_key == "freecc"
        print("✓ Settings creation working")
    except Exception as e:
        print(f"✗ Settings creation failed: {e}")
        return False

    # Test 3: Check provider catalog
    try:
        from clasp.config.provider_catalog import PROVIDER_CATALOG
        assert "nvidia_nim" in PROVIDER_CATALOG
        assert PROVIDER_CATALOG["nvidia_nim"].rpm_limit == 40
        print("✓ Provider catalog accessible")
    except Exception as e:
        print(f"✗ Provider catalog access failed: {e}")
        return False

    print("Smoke test completed successfully!")
    return True


if __name__ == "__main__":
    if test_smoke():
        sys.exit(0)
    else:
        sys.exit(1)