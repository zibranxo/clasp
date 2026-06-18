from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT_PARENT = ROOT.parent

# Ensure imports resolve to this repo's `clasp/` package, not a shadowing
# top-level package discovered from the parent directory.
sys.modules.pop("clasp", None)
sys.path = [p for p in sys.path if Path(p).resolve() != PROJECT_PARENT.resolve()]
sys.path.insert(0, str(ROOT))

try:
    import loguru  # noqa: F401
except ModuleNotFoundError:
    class _DummyLogger:
        def debug(self, *args, **kwargs):
            return None

        def info(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

        def add(self, *args, **kwargs):
            return 1

        def remove(self, *args, **kwargs):
            return None

        def complete(self):
            return None

    sys.modules["loguru"] = types.SimpleNamespace(logger=_DummyLogger())
