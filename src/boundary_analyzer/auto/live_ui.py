"""live_ui.py — Real-time terminal dashboard for the MBA pipeline.

Provides :class:`MBALiveUI`, a context-manager-based Rich live display that
renders a continuously-updated, dark-themed dashboard as the automated
analysis pipeline runs.

Usage::

    with MBALiveUI("scenario3", ["svc-a", "svc-b"], 60, 5) as ui:
        ui.set_pipeline_step("discover", "success")
        ui.set_phase("SEED", 1, 6, 15.0)
        ui.update_endpoint("svc-a", "GET", "/orders", "success", 200, 8.1, 2)
        ui.add_log("GET /orders → 200 OK  8ms", level="success")
        ui.update_stats(50, 49, 1, 5, 5, 3, 5)
        ui.tick()
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rich import box
from rich.columns import Columns  # noqa: F401 – part of the specified API surface
from rich.console import Console, Group
from rich.layout import Layout  # noqa: F401 – part of the specified API surface
from rich.live import Live
from rich.panel import Panel

from boundary_analyzer import __version__ as _MBA_VERSION
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,  # noqa: F401 – available for caller extensions
    TaskID,  # noqa: F401 – re-exported for type-checking convenience
    TextColumn,
    TimeElapsedColumn,  # noqa: F401 – available for caller extensions
)
from rich.rule import Rule  # noqa: F401 – part of the specified API surface
from rich.table import Table
from rich.text import Text

# ══════════════════════════════════════════════════════════════════════════════
#  Colour palette
# ══════════════════════════════════════════════════════════════════════════════

CYAN = "bright_cyan"  # primary accent
GREEN = "bright_green"  # success
RED = "bright_red"  # failure / suspicious
AMBER = "yellow"  # warning / in-progress
MUTED = "dim white"  # secondary text
BOLD = "bold white"  # headers
PURPLE = "magenta"  # special states

# ══════════════════════════════════════════════════════════════════════════════
#  Pipeline step metadata
# ══════════════════════════════════════════════════════════════════════════════

PIPELINE_STEPS: list[str] = [
    "discover",
    "deploy",
    "traffic",
    "collect",
    "analyze",
    "cleanup",
]
PIPELINE_LABELS: dict[str, str] = {
    "discover": "DISCOVER",
    "deploy": "DEPLOY",
    "traffic": "TRAFFIC",
    "collect": "COLLECT",
    "analyze": "ANALYZE",
    "cleanup": "CLEANUP",
}
# status → (icon, rich colour token)
_STEP_ICONS: dict[str, tuple[str, str]] = {
    "pending": ("○", "dim"),
    "running": ("●", CYAN),
    "success": ("✔", GREEN),
    "failed": ("✘", RED),
    "warning": ("⚠", AMBER),
}

# ══════════════════════════════════════════════════════════════════════════════
#  Endpoint / log display constants
# ══════════════════════════════════════════════════════════════════════════════

METHOD_COLORS: dict[str, str] = {
    "GET": CYAN,
    "POST": GREEN,
    "PUT": AMBER,
    "PATCH": AMBER,
    "DELETE": RED,
}

# status → (icon, short-label, rich colour token)
_ENDPOINT_STATUS: dict[str, tuple[str, str, str]] = {
    "pending": ("⏳", "wait", MUTED),
    "probing": ("↻", "probe", AMBER),
    "success": ("✔", "ok", GREEN),
    "failed": ("✗", "fail", RED),
    "skipped": ("⊘", "skip", MUTED),
}
_DIM_STATUSES = frozenset({"pending", "skipped"})

LEVEL_COLORS: dict[str, str] = {
    "info": MUTED,
    "success": GREEN,
    "warning": AMBER,
    "error": RED,
    "phase": PURPLE,
}
LEVEL_TAGS: dict[str, str] = {
    "info": "INFO",
    "success": "OK",
    "warning": "WARN",
    "error": "ERR",
    "phase": "PHASE",
}

_MAX_LOG_ENTRIES = 5  # lines kept in the log panel
_PATH_MAX_LEN = 22  # chars before path is truncated with …
_DB_DOT_MAX = 3  # max filled dots in DB column indicator


# ══════════════════════════════════════════════════════════════════════════════
#  Internal state dataclasses
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class _EndpointRow:
    """Mutable state for one row in the endpoint table."""

    service_name: str
    method: str
    path: str
    status: str = "pending"
    http_code: int = 0
    response_ms: float = 0.0
    db_ops: int = 0


@dataclass
class _LogEntry:
    """A single timestamped log entry."""

    timestamp: str
    message: str
    level: str = "info"


@dataclass
class _Stats:
    """Traffic generation counters."""

    requests_sent: int = 0
    requests_ok: int = 0
    requests_failed: int = 0
    endpoints_tested: int = 0
    endpoints_ok: int = 0
    endpoints_with_db: int = 0
    total_endpoints: int = 0


@dataclass
class _PhaseState:
    """State of the currently executing pipeline phase."""

    name: str = ""
    num: int = 0
    total: int = 6
    duration: float = 0.0
    start_time: float = field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════════════════
#  MBALiveUI
# ══════════════════════════════════════════════════════════════════════════════


class MBALiveUI:
    """Real-time terminal dashboard for the MBA analysis pipeline.

    Use as a context manager::

        with MBALiveUI("myproject", ["svc1", "svc2"], 60, 5) as ui:
            ui.set_pipeline_step("discover", "success")
            ui.set_phase("READ", 2, 6, 30.0)
            ui.update_endpoint("svc1", "GET", "/orders", "success", 200, 12.3, 2)
            ui.add_log("[READ]  GET /orders → 200 OK  12ms", level="success")
            ui.update_stats(100, 98, 2, 5, 5, 3, 5)
            ui.tick()

    All public methods are **thread-safe**.  When called outside a ``with``
    context (i.e. ``self._live is None``) they silently do nothing — no crash.
    """

    def __init__(
        self,
        project_name: str,
        services: list[str],
        total_duration: int,
        workers: int,
        version: str = _MBA_VERSION,
    ) -> None:
        self._project_name = project_name
        self._services = services
        self._total_duration = total_duration
        self._workers = workers
        self._version = version

        # Threading
        self._lock = threading.Lock()

        # Rich objects
        self._console = Console()
        self._live: Live | None = None

        # Session timing
        self._start_time = time.time()
        self._tick_count = 0

        # ── Mutable UI state ───────────────────────────────────────────────
        self._pipeline: dict[str, str] = {s: "pending" for s in PIPELINE_STEPS}
        self._phase = _PhaseState()
        # Keyed by (service_name, method, path) so repeated calls update in-place
        self._endpoints: dict[tuple[str, str, str], _EndpointRow] = {}
        self._logs: deque[_LogEntry] = deque(maxlen=_MAX_LOG_ENTRIES)
        self._stats = _Stats()

    # ── Context manager ────────────────────────────────────────────────────────

    def __enter__(self) -> MBALiveUI:
        """Start the live display."""
        live = Live(
            self._build_renderable(),
            console=self._console,
            refresh_per_second=4,
            screen=False,
            transient=False,
        )
        self._live = live
        live.__enter__()
        return self

    def __exit__(self, *args: Any) -> None:
        """Stop the live display."""
        if self._live is not None:
            self._live.__exit__(*args)
            self._live = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def set_pipeline_step(self, step: str, status: str) -> None:
        """Mark a pipeline step with a new status.

        Args:
            step:   ``discover | deploy | traffic | collect | analyze | cleanup``
            status: ``pending | running | success | failed | warning``
        """
        if self._live is None:
            return
        with self._lock:
            if step in self._pipeline:
                self._pipeline[step] = status
            self._refresh()

    def set_phase(
        self,
        phase_name: str,
        phase_num: int,
        total_phases: int,
        phase_duration: float,
    ) -> None:
        """Transition to a new pipeline phase and reset the phase progress timer.

        Args:
            phase_name:     Human-readable name (e.g. ``"READ"``).
            phase_num:      1-based index of this phase.
            total_phases:   Total number of phases in the pipeline.
            phase_duration: Expected duration of this phase in seconds.
        """
        if self._live is None:
            return
        with self._lock:
            self._phase = _PhaseState(
                name=phase_name,
                num=phase_num,
                total=total_phases,
                duration=phase_duration,
                start_time=time.time(),
            )
            self._refresh()

    def update_endpoint(
        self,
        service_name: str,
        method: str,
        path: str,
        status: str = "pending",
        http_code: int = 0,
        response_ms: float = 0.0,
        db_ops: int = 0,
    ) -> None:
        """Add or update an endpoint row in the endpoint table.

        Args:
            service_name: Short service identifier.
            method:       HTTP method (GET, POST, PUT, PATCH, DELETE).
            path:         URL path of the endpoint.
            status:       ``pending | probing | success | failed | skipped``
            http_code:    HTTP response code (0 = not yet probed).
            response_ms:  Round-trip latency in milliseconds.
            db_ops:       Number of detected database operations.
        """
        if self._live is None:
            return
        with self._lock:
            key = (service_name, method.upper(), path)
            self._endpoints[key] = _EndpointRow(
                service_name=service_name,
                method=method.upper(),
                path=path,
                status=status,
                http_code=http_code,
                response_ms=response_ms,
                db_ops=db_ops,
            )
            self._refresh()

    def add_log(self, message: str, level: str = "info") -> None:
        """Append a timestamped entry to the log panel (last 5 shown).

        Args:
            message: Log text to display.
            level:   ``info | success | warning | error | phase``
        """
        if self._live is None:
            return
        with self._lock:
            self._logs.append(
                _LogEntry(
                    timestamp=datetime.now().strftime("%H:%M:%S"),
                    message=message,
                    level=level,
                )
            )
            self._refresh()

    def update_stats(
        self,
        requests_sent: int,
        requests_ok: int,
        requests_failed: int,
        endpoints_tested: int,
        endpoints_ok: int,
        endpoints_with_db: int,
        total_endpoints: int,
    ) -> None:
        """Replace the current traffic statistics snapshot.

        Args:
            requests_sent:     Total HTTP requests dispatched so far.
            requests_ok:       Requests with a 2xx response code.
            requests_failed:   Requests that errored or received non-2xx.
            endpoints_tested:  Number of distinct endpoints probed.
            endpoints_ok:      Endpoints that responded successfully.
            endpoints_with_db: Endpoints that triggered ≥1 DB operation.
            total_endpoints:   Total endpoints in the project.
        """
        if self._live is None:
            return
        with self._lock:
            self._stats = _Stats(
                requests_sent=requests_sent,
                requests_ok=requests_ok,
                requests_failed=requests_failed,
                endpoints_tested=endpoints_tested,
                endpoints_ok=endpoints_ok,
                endpoints_with_db=endpoints_with_db,
                total_endpoints=total_endpoints,
            )
            self._refresh()

    def tick(self) -> None:
        """Advance the animation tick counter and refresh the display.

        Call this periodically (e.g. every 0.25 s) from the orchestrator
        to keep elapsed-time readouts and spinner animations current.
        """
        if self._live is None:
            return
        with self._lock:
            self._tick_count += 1
            self._refresh()

    # ── Internal helper ────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        """Push a freshly-built renderable to the Live display.

        **Must** be called while holding ``self._lock``.
        """
        if self._live is not None:
            self._live.update(self._build_renderable())

    # ══════════════════════════════════════════════════════════════════════════
    #  Renderable builders
    # ══════════════════════════════════════════════════════════════════════════

    def _build_renderable(self) -> Group:
        """Assemble the full dashboard ``Group`` from the current state.

        Every panel is rebuilt from scratch on each call so the display always
        reflects the latest state without incremental diffing.
        """
        return Group(
            self._build_header(),
            self._build_pipeline_bar(),
            self._build_phase_bar(),
            self._build_main_section(),
            self._build_log_panel(),
        )

    # ── Header ─────────────────────────────────────────────────────────────────

    def _build_header(self) -> Panel:
        """Top panel: branding, version, wall-clock time, and session metadata."""
        elapsed = int(time.time() - self._start_time)
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Title row — left: brand name, right: version + clock
        title_left = Text()
        title_left.append("◈  MBA", style=f"bold {CYAN}")
        title_left.append(" — Microservice Boundary Analyzer", style=f"bold {BOLD}")

        title_right = Text()
        title_right.append(f"v{self._version}", style=f"bold {CYAN}")
        title_right.append("  ·  ", style=MUTED)
        title_right.append(timestamp, style=MUTED)

        title_row = Table.grid(expand=True)
        title_row.add_column(justify="left")
        title_row.add_column(justify="right")
        title_row.add_row(title_left, title_right)

        # Metadata row — project, service count, duration, workers, elapsed
        meta = Text()
        meta.append("  Project: ", style=MUTED)
        meta.append(self._project_name, style=f"bold {CYAN}")
        meta.append("   ·   Services: ", style=MUTED)
        meta.append(str(len(self._services)), style=f"bold {CYAN}")
        meta.append("   ·   Duration: ", style=MUTED)
        meta.append(f"{self._total_duration}s", style=f"bold {CYAN}")
        meta.append("   ·   Workers: ", style=MUTED)
        meta.append(str(self._workers), style=f"bold {CYAN}")
        meta.append("   ·   Elapsed: ", style=MUTED)
        meta.append(f"{elapsed}s", style=f"bold {AMBER}")

        return Panel(
            Group(title_row, meta),
            box=box.HEAVY,
            border_style=CYAN,
            padding=(0, 1),
        )

    # ── Pipeline steps bar ─────────────────────────────────────────────────────

    def _build_pipeline_bar(self) -> Panel:
        """Horizontal row of pipeline step icons and labels."""
        row = Text()
        row.append("  ")

        for i, step in enumerate(PIPELINE_STEPS):
            status = self._pipeline.get(step, "pending")
            icon, color = _STEP_ICONS.get(status, ("○", "dim"))
            label = PIPELINE_LABELS[step]

            if status == "pending":
                row.append(f"{icon} {label}", style="dim")
            elif status == "running":
                # Alternating icons create a subtle "pulse" on each tick
                pulse = "◉" if (self._tick_count % 2 == 0) else "●"
                row.append(f"{pulse} {label}", style=f"bold {color}")
            else:
                row.append(f"{icon} {label}", style=f"bold {color}")

            if i < len(PIPELINE_STEPS) - 1:
                row.append("  ·  ", style="dim")

        return Panel(
            row,
            title=Text("PIPELINE", style=f"bold {CYAN}"),
            title_align="left",
            box=box.HEAVY,
            border_style=CYAN,
            padding=(0, 1),
        )

    # ── Phase progress bar ─────────────────────────────────────────────────────

    def _build_phase_bar(self) -> Panel:
        """Current phase name with a time-based ``rich.progress.Progress`` bar."""
        phase = self._phase

        if not phase.name:
            return Panel(
                Text("  Waiting for first phase…", style=MUTED),
                box=box.HEAVY,
                border_style=CYAN,
                padding=(0, 1),
            )

        elapsed = time.time() - phase.start_time
        pct = min(1.0, elapsed / phase.duration) if phase.duration > 0 else 0.0
        remaining = max(0.0, phase.duration - elapsed)

        # Progress is created with auto_refresh=False so it does NOT spawn its
        # own refresh thread that would conflict with the outer Live context.
        progress = Progress(
            TextColumn(f"  [dim white]PHASE {phase.num}/{phase.total}  \u2500  [/dim white][bold yellow]{phase.name}[/bold yellow]  \u2500  "),
            BarColumn(
                bar_width=22,
                style=f"dim {CYAN}",
                complete_style=CYAN,
                finished_style=GREEN,
            ),
            TextColumn("[bold yellow]{task.percentage:>3.0f}%[/bold yellow]"),
            TextColumn(f"  \u2500  [bright_green]{elapsed:.0f}s[/bright_green] [dim white]/ {phase.duration:.0f}s  ({remaining:.0f}s left)[/dim white]"),
            auto_refresh=False,
        )
        progress.add_task("", total=100, completed=pct * 100)

        return Panel(
            progress,
            box=box.HEAVY,
            border_style=CYAN,
            padding=(0, 1),
        )

    # ── Main two-column section ────────────────────────────────────────────────

    def _build_main_section(self) -> Table:
        """Split view: ENDPOINTS table (left 57 %) and COVERAGE stats (right 43 %)."""
        outer = Table(
            box=box.HEAVY,
            show_header=False,
            expand=True,
            padding=(0, 0),
            border_style=CYAN,
            show_edge=True,
        )
        outer.add_column(ratio=57, no_wrap=False)
        outer.add_column(ratio=43, no_wrap=False)
        outer.add_row(
            self._build_endpoint_table(),
            self._build_stats_grid(),
        )
        return outer

    # ── Endpoint table ─────────────────────────────────────────────────────────

    def _build_endpoint_table(self) -> Table:
        """Per-endpoint status table (``box.SIMPLE_HEAVY`` — header underline only)."""
        table = Table(
            box=box.SIMPLE_HEAVY,
            show_header=True,
            header_style=f"bold {CYAN}",
            expand=True,
            padding=(0, 1),
            title=Text("ENDPOINTS", style=f"bold {CYAN}"),
            title_justify="left",
            border_style=MUTED,
            show_edge=False,
        )
        table.add_column("Svc", max_width=9, no_wrap=True)
        table.add_column("Method", max_width=7, no_wrap=True)
        table.add_column("Path", max_width=24, no_wrap=True)
        table.add_column("Status", max_width=9, no_wrap=True)
        table.add_column("Code", max_width=5, no_wrap=True, justify="right")
        table.add_column("DB", max_width=5, no_wrap=True)
        table.add_column("ms", max_width=7, no_wrap=True, justify="right")

        if not self._endpoints:
            table.add_row(
                Text("—", style=MUTED),
                Text("—", style=MUTED),
                Text("No endpoints discovered yet", style=MUTED),
                Text("—", style=MUTED),
                Text("—", style=MUTED),
                Text("—", style=MUTED),
                Text("—", style=MUTED),
            )
            return table

        for row in self._endpoints.values():
            # ── Method (coloured by verb) ──────────────────────────────────
            mcolor = METHOD_COLORS.get(row.method, MUTED)
            method_text = Text(row.method, style=f"bold {mcolor}")

            # ── Path (truncated to _PATH_MAX_LEN) ─────────────────────────
            p = row.path
            path_text = Text(
                (p[: _PATH_MAX_LEN - 1] + "…") if len(p) > _PATH_MAX_LEN else p,
                style=MUTED,
            )

            # ── Status icon + label ────────────────────────────────────────
            icon, label, icolor = _ENDPOINT_STATUS.get(row.status, ("?", "?", MUTED))
            status_text = Text(
                f"{icon} {label}",
                style=MUTED if row.status in _DIM_STATUSES else f"bold {icolor}",
            )

            # ── HTTP response code (coloured by range) ─────────────────────
            if row.http_code:
                if 200 <= row.http_code < 300:
                    cstyle = f"bold {GREEN}"
                elif 300 <= row.http_code < 400:
                    cstyle = f"bold {CYAN}"
                elif 400 <= row.http_code < 500:
                    cstyle = f"bold {AMBER}"
                else:
                    cstyle = f"bold {RED}"
                code_text = Text(str(row.http_code), style=cstyle)
            else:
                code_text = Text("—", style=MUTED)

            # ── DB ops indicator (filled cyan ▪ / dim · dots) ──────────────
            n = min(row.db_ops, _DB_DOT_MAX)
            db_text = Text()
            if row.db_ops > 0:
                db_text.append("▪" * n, style=f"bold {CYAN}")
                db_text.append("·" * (_DB_DOT_MAX - n), style="dim")
            else:
                db_text.append("···", style="dim")

            # ── Latency ────────────────────────────────────────────────────
            ms_text = Text(f"{row.response_ms:.0f}", style=MUTED) if row.response_ms > 0 else Text("—", style=MUTED)

            table.add_row(
                Text(row.service_name[:9], style=MUTED),
                method_text,
                path_text,
                status_text,
                code_text,
                db_text,
                ms_text,
            )

        return table

    # ── Coverage / stats grid ──────────────────────────────────────────────────

    def _build_stats_grid(self) -> Table:
        """COVERAGE column: request counters, endpoint coverage, phase progress."""
        s = self._stats

        grid = Table.grid(padding=(0, 1), expand=False)
        grid.add_column(style=MUTED, min_width=18, no_wrap=True)
        grid.add_column(justify="left", no_wrap=True)

        # ── Section title ──
        grid.add_row("", "")
        grid.add_row(Text("COVERAGE", style=f"bold {CYAN}"), Text(""))
        grid.add_row("", "")

        # ── Request counters ──
        grid.add_row(
            Text("Requests sent", style=MUTED),
            Text(str(s.requests_sent), style=f"bold {BOLD}"),
        )

        ok_pct = (s.requests_ok / s.requests_sent * 100) if s.requests_sent else 0.0
        ok_text = Text()
        ok_text.append(str(s.requests_ok), style=f"bold {GREEN}")
        if s.requests_sent:
            ok_text.append(f"  ({ok_pct:.1f}%)", style=MUTED)
        grid.add_row(Text("Succeeded", style=MUTED), ok_text)

        fail_pct = (s.requests_failed / s.requests_sent * 100) if s.requests_sent else 0.0
        fail_text = Text()
        if s.requests_failed > 0:
            fail_text.append(str(s.requests_failed), style=f"bold {RED}")
            fail_text.append(f"  ({fail_pct:.1f}%)", style=MUTED)
        else:
            fail_text.append("0", style=f"bold {GREEN}")
        grid.add_row(Text("Failed", style=MUTED), fail_text)

        grid.add_row("", "")

        # ── Endpoint coverage ──
        ep_text = Text()
        ep_text.append(str(s.endpoints_tested), style=f"bold {CYAN}")
        ep_text.append(" / ", style=MUTED)
        ep_text.append(str(s.total_endpoints), style=MUTED)
        grid.add_row(Text("Endpoints tested", style=MUTED), ep_text)

        # DB coverage with coloured dot indicators  ●●●○○
        db_hit = s.endpoints_with_db
        db_total = s.total_endpoints
        db_text = Text()
        db_text.append(str(db_hit), style=f"bold {CYAN}")
        db_text.append(f"/{db_total}  ", style=MUTED)
        db_text.append("●" * db_hit, style=f"bold {CYAN}")
        db_text.append("○" * max(0, db_total - db_hit), style=MUTED)
        grid.add_row(Text("DB ops triggered", style=MUTED), db_text)

        grid.add_row("", "")

        # ── Phase info ──
        grid.add_row(
            Text("Current phase", style=MUTED),
            Text(self._phase.name or "—", style=f"bold {AMBER}"),
        )

        if self._phase.name and self._phase.duration > 0:
            elapsed = time.time() - self._phase.start_time
            pct = min(1.0, elapsed / self._phase.duration)
            pct_int = int(pct * 100)
            bar_w = 8
            filled = int(pct * bar_w)
            mini = Text()
            mini.append("█" * filled, style=CYAN)
            mini.append("░" * (bar_w - filled), style="dim")
            mini.append(f"  {pct_int}%", style=f"bold {AMBER}")
            grid.add_row(Text("Phase progress", style=MUTED), mini)

        return grid

    # ── Log panel ──────────────────────────────────────────────────────────────

    def _build_log_panel(self) -> Panel:
        """Bottom panel showing the last ``_MAX_LOG_ENTRIES`` log entries."""
        log_grid = Table.grid(padding=(0, 1))
        log_grid.add_column(style=MUTED, no_wrap=True, min_width=8)  # HH:MM:SS
        log_grid.add_column(no_wrap=True, min_width=8)  # [TAG]
        log_grid.add_column()  # message

        if not self._logs:
            log_grid.add_row(
                Text("—", style=MUTED),
                Text("", style=MUTED),
                Text("No log entries yet…", style=MUTED),
            )
        else:
            for entry in self._logs:
                color = LEVEL_COLORS.get(entry.level, MUTED)
                tag = LEVEL_TAGS.get(entry.level, entry.level.upper())
                log_grid.add_row(
                    Text(entry.timestamp, style=MUTED),
                    Text(f"[{tag}]", style=f"bold {color}"),
                    Text(
                        entry.message,
                        style=color if entry.level != "info" else MUTED,
                    ),
                )

        return Panel(
            log_grid,
            title=Text("LOG", style=f"bold {CYAN}"),
            title_align="left",
            box=box.HEAVY,
            border_style=CYAN,
            padding=(0, 1),
        )
