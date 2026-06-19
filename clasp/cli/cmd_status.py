"""
clasp/cli/cmd_status.py

`clasp status [--json]`

One-line proxy status designed for tmux status bar integration.

Normal output:
  [CLASP] NIM(28/40 rpm)→Gemini | Queue:0 | Keys:3/4 healthy | 1.2k req today

JSON output (--json):
  {"status":"healthy","active_provider":"nvidia_nim","rpm_used":28,"rpm_limit":40,
   "queue_depth":0,"healthy_keys":3,"total_keys":4,"requests_today":1240}

If server not running:
  [CLASP] not running — start with: clasp server
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import typer

# ── helpers ──────────────────────────────────────────────────────────────────

_PID_PATH = Path.home() / ".clasp" / "clasp.pid"
_DEFAULT_PORT = 8082


def _read_port() -> int:
    """Try to read the server port from config; fall back to default."""
    try:
        from clasp.config.settings import get_settings  # type: ignore[import]
        return get_settings().server.port
    except Exception:
        pass
    return _DEFAULT_PORT


def _server_running() -> bool:
    """Return True if the PID file exists and the process is alive."""
    if not _PID_PATH.exists():
        return False
    try:
        import os
        pid = int(_PID_PATH.read_text().strip())
        os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError, OSError):
        return False


def _fmt_k(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _active_provider_info(data: dict) -> tuple[str, str]:
    """
    Returns (active_label, next_label) where label is a short display name.
    Walks provider_chain order in the status data.
    """
    providers = data.get("providers") or {}
    # Derive chain order: try explicit active_provider first, then walk by health
    chain = list(providers.keys())  # ordered as returned by the server

    active_name: str | None = data.get("active_provider")
    healthy_names = [
        n for n, p in providers.items()
        if p.get("status", "").upper() == "HEALTHY"
    ]

    if active_name and active_name not in healthy_names:
        active_name = None

    if not active_name and healthy_names:
        active_name = healthy_names[0]

    # Next healthy after active
    next_name: str | None = None
    if active_name:
        try:
            idx = chain.index(active_name)
            remaining = chain[idx + 1:]
        except ValueError:
            remaining = chain
        next_name = next((n for n in remaining if n in healthy_names), None)

    def _short(name: str | None) -> str:
        if not name:
            return "—"
        # Short alias map for common providers
        aliases = {
            "nvidia_nim": "NIM",
            "gemini":     "Gemini",
            "cerebras":   "Cerebras",
            "groq":       "Groq",
            "fireworks":  "FW",
            "openrouter": "OR",
            "mistral":    "Mistral",
            "together":   "Together",
            "ollama":     "Ollama",
            "lm_studio":  "LMStudio",
        }
        return aliases.get(name, name[:8])

    return _short(active_name), _short(next_name)


def _rpm_summary(data: dict, active_provider: str | None) -> tuple[int, int]:
    """Return (rpm_used, rpm_limit) for the active provider's healthiest key."""
    providers = data.get("providers") or {}
    if not active_provider:
        return 0, 0
    pdata = providers.get(active_provider, {})
    keys = pdata.get("keys") or []
    for k in keys:
        if k.get("status", "").upper() == "HEALTHY":
            return k.get("rpm_used", 0), k.get("rpm_limit", 1)
    return 0, 0


def _key_health(data: dict) -> tuple[int, int]:
    """Returns (healthy_keys, total_keys) across all providers."""
    healthy = 0
    total = 0
    for pdata in (data.get("providers") or {}).values():
        for k in (pdata.get("keys") or []):
            total += 1
            if k.get("status", "").upper() == "HEALTHY":
                healthy += 1
    return healthy, total


# ── command ──────────────────────────────────────────────────────────────────

def status_cmd(as_json: bool = typer.Option(False, "--json", help="Output machine-readable JSON")) -> None:
    """Print one-line proxy status. Designed for tmux status bar integration."""

    if not _server_running():
        if as_json:
            typer.echo(json.dumps({"status": "not_running"}))
        else:
            typer.echo("[CLASP] not running — start with: clasp server")
        raise typer.Exit(1)

    port = _read_port()
    base_url = f"http://127.0.0.1:{port}"

    try:
        with httpx.Client(timeout=3.0) as client:
            r = client.get(f"{base_url}/internal/status")
            r.raise_for_status()
            data = r.json()
    except httpx.ConnectError:
        if as_json:
            typer.echo(json.dumps({"status": "not_running"}))
        else:
            typer.echo("[CLASP] not running — start with: clasp server")
        raise typer.Exit(1)
    except Exception as e:
        if as_json:
            typer.echo(json.dumps({"status": "error", "detail": str(e)}))
        else:
            typer.echo(f"[CLASP] error: {e}")
        raise typer.Exit(2)

    # ── normal text output ──────────────────────────────────────────────────
    providers = data.get("providers") or {}

    # Find active provider name for rpm lookup
    active_label, next_label = _active_provider_info(data)

    active_name = data.get("active_provider")
    if not active_name:
        healthy = [
            n for n, p in providers.items()
            if p.get("status", "").upper() == "HEALTHY"
        ]
        active_name = healthy[0] if healthy else None

    rpm_used, rpm_limit = _rpm_summary(data, active_name)
    healthy_keys, total_keys = _key_health(data)
    queue_depth = data.get("queue_depth", 0)
    requests_today = data.get("requests_today", 0)

    if as_json:
        typer.echo(json.dumps({
            "status":          data.get("status", "unknown"),
            "active_provider": active_name,
            "rpm_used":        rpm_used,
            "rpm_limit":       rpm_limit,
            "queue_depth":     queue_depth,
            "healthy_keys":    healthy_keys,
            "total_keys":      total_keys,
            "requests_today":  requests_today,
        }))
        return

    # Compose the arrow segment only when both sides are known
    if next_label and next_label != "—":
        provider_part = f"{active_label}({rpm_used}/{rpm_limit} rpm)→{next_label}"
    else:
        provider_part = f"{active_label}({rpm_used}/{rpm_limit} rpm)"

    line = (
        f"[CLASP] {provider_part}"
        f" | Queue:{queue_depth}"
        f" | Keys:{healthy_keys}/{total_keys} healthy"
        f" | {_fmt_k(requests_today)} req today"
    )
    typer.echo(line)