"""
PID file management for CLASP.

Manages ``~/.clasp/clasp.pid``.  The file contains only the integer PID of the
running server process as plain text.

Public API
----------
write_pid(pid_path)   — write os.getpid() to *pid_path*.
read_pid(pid_path)    — return the stored PID int, or None if the file is absent.
is_running(pid_path)  — (bool, pid|None): True when a live process owns that PID.
delete_pid(pid_path)  — remove the file (silent if already gone).

Stale-file handling: if the file exists but the PID is dead (crashed server),
``is_running`` removes the stale file and returns ``(False, None)``.
A ``PermissionError`` from ``os.kill(pid, 0)`` means the process exists but
belongs to another user; we treat that as running.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

DEFAULT_PID_PATH: Path = Path.home() / ".clasp" / "clasp.pid"


def write_pid(pid_path: Path = DEFAULT_PID_PATH) -> None:
    """Write the current process's PID to *pid_path*.

    Creates parent directories if they do not exist.  Overwrites any existing
    content (e.g. from a previous crashed run).
    """
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    pid_path.write_text(str(pid), encoding="utf-8")
    logger.debug("PID {} written to {}", pid, pid_path)


def read_pid(pid_path: Path = DEFAULT_PID_PATH) -> int | None:
    """Return the PID stored in *pid_path*, or ``None`` if the file is absent.

    Returns ``None`` (not an exception) when the file does not exist so callers
    can use a simple ``if pid := read_pid(): ...`` pattern.
    """
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        logger.warning("PID file {} contains non-integer content; ignoring.", pid_path)
        return None


def is_running(pid_path: Path = DEFAULT_PID_PATH) -> tuple[bool, int | None]:
    """Check whether a CLASP server process is currently alive.

    Returns
    -------
    (True, pid)
        The file exists *and* the recorded PID belongs to a live process.
    (False, None)
        Either the file is absent, or the PID is stale (process is dead).
        Stale files are removed automatically.

    Edge case: if ``os.kill`` raises ``PermissionError`` the process exists
    (owned by another user); we return ``(True, None)`` — the server is running
    even if we cannot signal it.
    """
    if not pid_path.exists():
        return False, None

    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)  # signal 0 = existence check only; raises if dead
        return True, pid
    except ValueError:
        logger.warning("Stale PID file {} has non-integer content; removing.", pid_path)
        pid_path.unlink(missing_ok=True)
        return False, None
    except ProcessLookupError:
        # PID in file is dead — server crashed without cleanup.
        logger.debug("Stale PID file found at {}; removing.", pid_path)
        pid_path.unlink(missing_ok=True)
        return False, None
    except PermissionError:
        # Process exists but belongs to another user.
        return True, None


def delete_pid(pid_path: Path = DEFAULT_PID_PATH) -> None:
    """Delete the PID file.

    Called on clean server shutdown.  Silent if the file is already gone
    (e.g. a concurrent ``clasp stop`` already removed it).
    """
    pid_path.unlink(missing_ok=True)
    logger.debug("PID file {} removed.", pid_path)
