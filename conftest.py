"""
Root conftest.py — adds the project root to sys.path so that `clasp.*`
imports work correctly in all test modules without requiring the package
to be installed in editable mode first.

This runs before any test collection, so it replaces the per-file
`sys.path.insert(0, ...)` hacks that existed in older test files.
"""
import os
import sys

# Add the project root (parent of tests/) to sys.path so
# `import clasp.*` resolves to c:/code/clasp/clasp/
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
