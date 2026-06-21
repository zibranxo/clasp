"""
tests/unit/test_server.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.server import create_app, lifespan
from fastapi import FastAPI

def test_create_app():
    app = create_app()
    assert isinstance(app, FastAPI)

def test_lifespan_exists():
    assert callable(lifespan)