"""
clasp/tui/live_panel.py

Rich Live layout for `clasp server --live`.

Polls GET /internal/status every 2 s and renders a full-terminal panel showing:
  • System health bar   (status, uptime, active requests)
  • Provider grid       (per-provider name/status/key RPM bars / P50 latency)
  • Queue row           (depth, interactive, background, drain state)
  • Daily totals        (req, tokens, cache hits, absorbed 429s, failovers)
  • Latency sparklines  (last 60 req per provider — stored locally)

Usage (called from cmd_server.py when --live is passed):
    from clasp.tui.live_panel import LivePanel
    LivePanel(port=settings.server.port).run_sync()
    # or from an async context:
    await LivePanel(port=settings.server.port).run()
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

import httpx
from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# ── constants ────────────────────────────────────────────────────────────────

REFRESH_INTERVAL = 2.0          # seconds between status polls
SPARKLINE_HISTORY = 60          # how many latency samples to keep per provider
SPARKLINE_WIDTH = 10            # characters wide in the display
SPARK_CHARS = " ▁▂▃▄▅▆▇█"

# status → (color, label)
STATUS_STYLE: dict[str, tuple[str, str]] = {
    "HEALTHY":      ("green",  "● OK"),
    "SOFT_LIMIT":   ("yellow", "⚠ SOFT"),
    "COOLING_DOWN": ("dark_orange", "♻ COOL"),
    "CIRCUIT_OPEN": ("red",    "✗ OPEN"),
    "OFF":          ("bright_black", "— OFF"),
    "UNKNOWN":      ("bright_black", "? UNK"),
}

# ── helpers ──────────────────────────────────────────────────────────────────

def _sparkline(samples: deque[float]) -> str:
    """Convert a deque of float latency values (ms) into a Unicode sparkline."""
    vals = list(samples)
    if not vals:
        return " " * SPARKLINE_WIDTH
    lo, hi = min(vals), max(vals)
    span = hi - lo or 1.0
    tail = vals[-SPARKLINE_WIDTH:]
    chars = [SPARK_CHARS[int((v - lo) / span * (len(SPARK_CHARS) - 1))] for v in tail]
    # left-pad to SPARKLINE_WIDTH
    return ("".join(chars)).rjust(SPARKLINE_WIDTH)


def _rpm_bar(used: int, total: int, width: int = 12) -> Text:
    """Filled/empty block progress bar with colour based on fill ratio."""
    if total == 0:
        return Text("─" * width, style="bright_black")
    ratio = min(used / total, 1.0)
    filled = int(ratio * width)
    empty = width - filled
    bar_str = "█" * filled + "░" * empty
    if ratio >= 0.80:
        color = "red"
    elif ratio >= 0.60:
        color = "yellow"
    else:
        color = "green"
    return Text(bar_str, style=color)


def _fmt_uptime(seconds: int | float | None) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m}m" if h else f"{m}m"


def _fmt_k(n: int | None) -> str:
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}k"
    return str(n)


def _status_text(status: str) -> Text:
    color, label = STATUS_STYLE.get(status.upper(), STATUS_STYLE["UNKNOWN"])
    return Text(label, style=color)


# ── main class ───────────────────────────────────────────────────────────────

class LivePanel:
    """
    Renders a self-refreshing Rich Live panel showing CLASP system status.

    Instantiate and call `run_sync()` (blocking) or `await run()` (async).
    """

    def __init__(
        self,
        port: int = 8082,
        host: str = "127.0.0.1",
        refresh: float = REFRESH_INTERVAL,
    ) -> None:
        self.base_url = f"http://{host}:{port}"
        self.refresh = refresh
        self.console = Console()

        # Rolling latency history keyed by provider name
        self._latency: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=SPARKLINE_HISTORY)
        )
        self._last_data: dict[str, Any] = {}
        self._error_msg: str | None = None

    # ── data fetch ───────────────────────────────────────────────────────────

    async def _fetch(self, client: httpx.AsyncClient) -> dict[str, Any] | None:
        try:
            r = await client.get(f"{self.base_url}/internal/status", timeout=3.0)
            r.raise_for_status()
            data = r.json()
            # Feed latency history from provider p50 values
            for pname, pdata in (data.get("providers") or {}).items():
                p50 = pdata.get("p50_latency_ms")
                if p50 is not None:
                    self._latency[pname].append(float(p50))
            self._error_msg = None
            return data
        except httpx.ConnectError:
            self._error_msg = "Cannot connect to CLASP server — is it running?"
        except httpx.HTTPStatusError as e:
            self._error_msg = f"HTTP {e.response.status_code} from /internal/status"
        except Exception as e:
            self._error_msg = f"Error: {e}"
        return None

    # ── renderable builders ──────────────────────────────────────────────────

    def _build_header(self, data: dict[str, Any]) -> Panel:
        """Top status bar: health dot, uptime, active requests."""
        status = data.get("status", "unknown").upper()
        color, label = STATUS_STYLE.get(status, STATUS_STYLE["UNKNOWN"])
        uptime = _fmt_uptime(data.get("uptime_seconds"))
        active = data.get("active_requests", 0)

        t = Text()
        t.append(f" {label} ", style=f"bold {color}")
        t.append(" │ ", style="bright_black")
        t.append(f"Uptime: {uptime}", style="white")
        t.append("  │  ", style="bright_black")
        t.append(f"Active: {active} request(s)", style="cyan")
        return Panel(t, style="bright_black", padding=(0, 1))

    def _build_provider_grid(self, providers: dict[str, Any]) -> Table:
        """2-column grid of provider cards."""
        grid = Table.grid(padding=(0, 2))
        grid.add_column()
        grid.add_column()

        items = list(providers.items())
        # Pair them up for 2-column layout
        for i in range(0, len(items), 2):
            left = self._provider_card(items[i][0], items[i][1])
            right = (
                self._provider_card(items[i + 1][0], items[i + 1][1])
                if i + 1 < len(items)
                else Text("")
            )
            grid.add_row(left, right)
        return grid

    def _provider_card(self, name: str, pdata: dict[str, Any]) -> Panel:
        """Single provider card: name, status, key bars, P50."""
        display_name = name.replace("_", " ").title()
        status = pdata.get("status", "UNKNOWN")
        status_t = _status_text(status)

        t = Text()
        t.append(f"{display_name}  ", style="bold white")
        t.append_text(status_t)
        t.append("\n")

        keys = pdata.get("keys") or []
        for k in keys:
            kidx = k.get("index", 0)
            kstatus = k.get("status", "UNKNOWN")
            rpm_used = k.get("rpm_used", 0)
            rpm_limit = k.get("rpm_limit", 1)
            recovery = k.get("recovery_in")

            t.append(f"  Key{kidx + 1} ", style="bright_black")
            if kstatus == "COOLING_DOWN" and recovery is not None:
                t.append(f"⚠ cool {int(recovery)}s", style="dark_orange")
            else:
                t.append_text(_rpm_bar(rpm_used, rpm_limit))
                t.append(f"  {rpm_used}/{rpm_limit}", style="bright_black")
            t.append("\n")

        # Daily token bar if limit exists
        daily_limit = pdata.get("daily_token_limit")
        tokens_today = pdata.get("tokens_today", 0)
        if daily_limit:
            t.append("  Tokens: ", style="bright_black")
            t.append(f"{_fmt_k(tokens_today)}/{_fmt_k(daily_limit)} daily\n", style="blue")

        p50 = pdata.get("p50_latency_ms")
        if p50 is not None:
            t.append(f"  P50: {p50/1000:.2f}s", style="bright_black")

        border_color = {
            "HEALTHY": "green",
            "SOFT_LIMIT": "yellow",
            "COOLING_DOWN": "dark_orange",
            "CIRCUIT_OPEN": "red",
        }.get(status.upper(), "bright_black")

        return Panel(t, border_style=border_color, padding=(0, 1))

    def _build_queue_row(self, data: dict[str, Any]) -> Text:
        t = Text()
        t.append(" Queue: ", style="bold white")
        t.append(str(data.get("queue_depth", 0)), style="cyan")
        t.append("  │  Interactive: ", style="bright_black")
        t.append(str(data.get("queue_interactive", 0)), style="cyan")
        t.append("  │  Background: ", style="bright_black")
        t.append(str(data.get("queue_background", 0)), style="cyan")
        t.append("  │  Drain: ", style="bright_black")
        drain = "running" if data.get("queue_depth", 0) > 0 else "—"
        t.append(drain, style="green" if drain != "—" else "bright_black")
        return t

    def _build_totals_row(self, data: dict[str, Any]) -> Text:
        t = Text()
        t.append(" Today: ", style="bold white")
        t.append(f"{_fmt_k(data.get('requests_today', 0))} req", style="white")
        t.append("  •  ", style="bright_black")
        t.append(f"{_fmt_k(data.get('tokens_today', 0))} tokens", style="white")
        t.append("  •  ", style="bright_black")
        t.append(f"{data.get('cache_hits_today', 0)} cache hits", style="blue")
        t.append("\n")
        t.append(" 429s absorbed: ", style="bright_black")
        t.append(str(data.get("absorbed_429s_today", 0)), style="green")
        t.append("  •  Failovers: ", style="bright_black")
        t.append(str(data.get("failovers_today", 0)), style="yellow")
        t.append("  •  Errors surfaced: ", style="bright_black")
        errors = sum(
            p.get("errors_today", 0)
            for p in (data.get("providers") or {}).values()
        )
        t.append(str(errors), style="red" if errors else "bright_black")
        return t

    def _build_sparklines(self) -> Text:
        if not self._latency:
            return Text("")
        t = Text()
        t.append(" Latency (last 60 req)\n", style="bold bright_black")
        for pname, samples in self._latency.items():
            if not samples:
                continue
            display = pname.replace("_", " ").upper()[:8].ljust(8)
            spark = _sparkline(samples)
            p50 = sum(samples) / len(samples) if samples else 0
            t.append(f"  {display}  ", style="bright_black")
            t.append(spark, style="cyan")
            t.append(f"  P50:{p50/1000:.2f}s\n", style="bright_black")
        return t

    def _build_error_panel(self) -> Panel:
        t = Text(f" ✗ {self._error_msg}", style="red")
        t.append("\n   Waiting for server...", style="bright_black")
        return Panel(t, title="[red]CLASP — not connected[/red]", border_style="red")

    # ── composited renderable ────────────────────────────────────────────────

    def _render(self, data: dict[str, Any] | None) -> Table:
        """Build the full renderable from the latest status data."""
        root = Table.grid(expand=True)
        root.add_column()

        if data is None:
            root.add_row(self._build_error_panel())
            return root

        # ① header bar
        root.add_row(self._build_header(data))

        # ② provider grid (only if providers present)
        providers = data.get("providers") or {}
        if providers:
            root.add_row(self._build_provider_grid(providers))
        else:
            root.add_row(Text(" No providers configured.", style="bright_black"))

        # ③ queue row
        root.add_row(
            Panel(self._build_queue_row(data), style="bright_black", padding=(0, 0))
        )

        # ④ daily totals
        root.add_row(
            Panel(self._build_totals_row(data), style="bright_black", padding=(0, 0))
        )

        # ⑤ latency sparklines
        sparks = self._build_sparklines()
        if sparks.plain:
            root.add_row(Panel(sparks, style="bright_black", padding=(0, 0)))

        return root

    # ── run loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Async run loop. Blocks until Ctrl+C."""
        async with httpx.AsyncClient() as client:
            with Live(
                self._render(None),
                console=self.console,
                refresh_per_second=4,
                screen=False,
            ) as live:
                while True:
                    data = await self._fetch(client)
                    if data:
                        self._last_data = data
                    live.update(self._render(data or self._last_data or None))
                    await asyncio.sleep(self.refresh)

    def run_sync(self) -> None:
        """Synchronous entry point. Runs the async loop until Ctrl+C."""
        try:
            asyncio.run(self.run())
        except KeyboardInterrupt:
            self.console.print("\n[bright_black]Live panel stopped.[/bright_black]")