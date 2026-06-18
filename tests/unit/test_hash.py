"""
tests/unit/test_hash.py
=======================
Unit tests for clasp.utils.hash.
"""

from __future__ import annotations

import sys
import os
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from clasp.utils.hash import hash_request, _canonicalise