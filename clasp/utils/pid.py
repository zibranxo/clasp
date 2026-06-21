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
import sys
from pathlib import Path

from loguru import logger

DEFAULT_PID_PATH: Path = Path.home() / ".clasp" / "clasp.pid"


def _pid_exists(pid: int) -> bool | None:
    """Return True if *pid* is a live process, False if it is dead.

    Returns None if the answer is uncertain (e.g. permission denied —
    the process exists but is owned by another user).

    Uses Win32 ``OpenProcess`` on Windows to avoid the CPython bug where
    ``os.kill(pid, 0)`` can raise a ``SystemError`` instead of a normal
    Python exception for certain dead-process states (WinError 87).
    """
    if sys.platform == "win32":
        import ctypes
        import ctypes.wintypes

        SYNCHRONIZE = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle == 0:
            err = ctypes.windll.kernel32.GetLastError()
            # ERROR_INVALID_PARAMETER (87) or ERROR_NOT_FOUND — process is gone.
            # ERROR_ACCESS_DENIED (5) — process exists, we just can't open it.
            if err == 5:  # ERROR_ACCESS_DENIED
                return None  # uncertain — treat as alive
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return None  # process exists but owned by another user


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

    Edge case: if we cannot determine ownership (PermissionError / access
    denied) we assume the process is running and return ``(True, None)``.
    """
    if not pid_path.exists():
        return False, None

    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except ValueError:
        logger.warning("Stale PID file {} has non-integer content; removing.", pid_path)
        pid_path.unlink(missing_ok=True)
        return False, None

    alive = _pid_exists(pid)
    if alive is True:
        return True, pid
    if alive is None:
        # Process exists but owned by another user — treat as running.
        return True, None
    # alive is False — stale PID file.
    logger.debug("Stale PID file found at {}; removing.", pid_path)
    pid_path.unlink(missing_ok=True)
    return False, None


def delete_pid(pid_path: Path = DEFAULT_PID_PATH) -> None:
    """Delete the PID file.

    Called on clean server shutdown.  Silent if the file is already gone
    (e.g. a concurrent ``clasp stop`` already removed it).
    """
    pid_path.unlink(missing_ok=True)
    logger.debug("PID file {} removed.", pid_path)
