"""
tests/unit/test_imports.py
==========================
Unit tests to verify all modules can be imported.
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def test_import_all_modules():
    """Test that all Sprint 1 modules can be imported without errors."""
    modules_to_test = [
        # Utils
        "clasp.utils.logger",
        "clasp.utils.pid",
        "clasp.utils.hash",
        "clasp.utils.ip_guard",

        # Config
        "clasp.config.provider_catalog",
        "clasp.config.settings",
        "clasp.config.writer",
        "clasp.config.watcher",

        # Providers
        "clasp.providers.base",
        "clasp.providers.registry",
        "clasp.providers.common.message_converter",
        "clasp.providers.common.sse_builder",
        "clasp.providers.common.token_counter",
        "clasp.providers.common.error_mapper",
        "clasp.providers.openai_transport",
        "clasp.providers.anthropic_transport",
        "clasp.providers.nvidia_nim",

        # API
        "clasp.api.optimize",
        "clasp.api.detect",
        "clasp.api.service",
        "clasp.api.proxy_routes",

        # UI
        "clasp.ui.routes",

        # Internal
        "clasp.internal.routes",

        # Server
        "clasp.server",

        # CLI
        "clasp.cli.main",
        "clasp.cli.cmd_server",
        "clasp.cli.cmd_claude",
    ]

    failed_imports = []

    for module_name in modules_to_test:
        try:
            __import__(module_name)
        except Exception as e:
            failed_imports.append((module_name, str(e)))

    if failed_imports:
        error_msg = "Failed to import the following modules:\n"
        for module_name, error in failed_imports:
            error_msg += f"  {module_name}: {error}\n"
        raise AssertionError(error_msg)

    # If we get here, all imports succeeded
    assert True


def test_import_submodules():
    """Test importing __init__.py files to ensure packages are properly structured."""
    # Only test packages that actually have __init__.py files
    init_modules = [
        "clasp",
        "clasp.utils",
        "clasp.config",
        "clasp.providers",
        "clasp.providers.common",
        "clasp.api",
        "clasp.ui",
        "clasp.internal",
        "clasp.cli",
    ]

    failed_imports = []

    for module_name in init_modules:
        try:
            __import__(module_name + ".__init__")
        except Exception as e:
            failed_imports.append((module_name, str(e)))

    if failed_imports:
        error_msg = "Failed to import the following __init__.py modules:\n"
        for module_name, error in failed_imports:
            error_msg += f"  {module_name}: {error}\n"
        raise AssertionError(error_msg)

    # If we get here, all imports succeeded
    assert True


if __name__ == "__main__":
    test_import_all_modules()
    test_import_submodules()
    print("All import tests passed!")