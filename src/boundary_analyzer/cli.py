from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from rich.console import Console

# Windows cp1252 workaround: ensure stdout/stderr can encode Unicode chars
# used by rich (bullet ●, checkmark ✔, cross ✘).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_console = Console()

"""Command-line interface for the Microservice Boundary Analyzer (MBA).

Defines the ``mba`` CLI with subcommands: ``run``, ``dashboard``, ``setup``,
``teastore``, and ``full``.
"""

logging.basicConfig(
    level=logging.WARNING,
    format="[%(levelname)s] %(message)s",
    stream=sys.stderr,
    force=True,
)
logger = logging.getLogger("boundary_analyzer")


def _validate_port(val: str) -> int:
    """Validate and parse a port number (1-65535)."""
    ival = int(val)
    if ival < 1 or ival > 65535:
        raise argparse.ArgumentTypeError(f"Port must be 1-65535, got {ival}")
    return ival


def _validate_threshold(val: str) -> float:
    """Validate and parse a threshold value (0.0-1.0)."""
    fval = float(val)
    if fval < 0.0 or fval > 1.0:
        raise argparse.ArgumentTypeError(f"Threshold must be 0.0-1.0, got {fval}")
    return fval


def _validate_duration(val: str) -> int:
    """Validate and parse a duration in seconds (>= 1)."""
    ival = int(val)
    if ival < 1:
        raise argparse.ArgumentTypeError(f"Duration must be >= 1 second, got {ival}")
    return ival


def _validate_positive_int(val: str) -> int:
    """Validate and parse a positive integer (>= 1)."""
    ival = int(val)
    if ival < 1:
        raise argparse.ArgumentTypeError(f"Value must be >= 1, got {ival}")
    return ival


def _run_pipeline(skip_collect: bool) -> int:
    """Run the SCOM pipeline from step 2 (or step 1 if not skipped)."""
    from boundary_analyzer.pipeline import (
        step_01_collect_traces,
        step_02_read_traces,
        step_03_find_endpoints,
        step_04_find_db_tables,
        step_05_build_mapping,
        step_06_compute_scom,
        step_07_rank_and_flag,
        step_08_make_report,
    )

    if not skip_collect:
        rc = step_01_collect_traces.main()
        if rc != 0:
            return rc

    for step in [
        step_02_read_traces,
        step_03_find_endpoints,
        step_04_find_db_tables,
        step_05_build_mapping,
        step_06_compute_scom,
        step_07_rank_and_flag,
        step_08_make_report,
    ]:
        rc = step.main()
        if rc != 0:
            return rc

    _console.print("\n  [bold green]Pipeline complete.[/]")
    return 0


def _run_dashboard(data_dir: Path | None = None, host: str = "127.0.0.1", port: int = 8050) -> int:
    """Start the SCOM results web dashboard."""
    from boundary_analyzer.dashboard.app import main as dashboard_main

    # Pass configuration via env to avoid coupling CLI to Dash internals
    os.environ["BOUNDARY_ANALYZER_DASH_HOST"] = str(host)
    os.environ["BOUNDARY_ANALYZER_DASH_PORT"] = str(int(port))

    return dashboard_main(data_dir=data_dir)


def _run_setup(
    project_path: str,
    framework: str,
    service_name: str,
    jaeger_host: str,
    no_jaeger: bool,
    no_install: bool,
    traces_output: str,
    trace_limit: int,
    llm: bool = False,
) -> int:
    """Run the auto-setup command: add OpenTelemetry to a project and collect traces."""
    from boundary_analyzer.auto_setup.setup_instrumentation import main as setup_main

    argv = ["--project-path", project_path]

    if framework:
        argv += ["--framework", framework]
    if service_name:
        argv += ["--service-name", service_name]
    if jaeger_host:
        argv += ["--jaeger-host", jaeger_host]
    if no_jaeger:
        argv += ["--no-jaeger"]
    if no_install:
        argv += ["--no-install"]
    if traces_output:
        argv += ["--traces-output", traces_output]
    if trace_limit:
        argv += ["--trace-limit", str(int(trace_limit))]
    if llm:
        argv += ["--llm"]

    try:
        setup_main(argv)
        return 0
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        return code
    except Exception as e:
        logger.error("Setup error: %s", e)
        if os.environ.get("MBA_DEBUG"):
            logger.exception("Debug traceback")
        return 1


def _add_dash_args(parser: argparse.ArgumentParser, group_name: str = "Dashboard options") -> None:
    """Add dashboard-related CLI arguments (data-dir, dash-host, dash-port) to a parser."""
    group = parser.add_argument_group(group_name)
    group.add_argument(
        "--data-dir",
        default="data",
        help="Folder with pipeline results for the dashboard (default: data).",
    )
    group.add_argument(
        "--dash-host",
        default="127.0.0.1",
        help="Dashboard web address (default: 127.0.0.1). Use 0.0.0.0 for all devices.",
    )
    group.add_argument(
        "--dash-port",
        type=_validate_port,
        default=8050,
        help="Dashboard web port (default: 8050).",
    )


def _cmd_runs_compare(args: argparse.Namespace) -> int:
    """Compare SCOM scores across runs."""
    from boundary_analyzer.auto.run_registry import list_runs, load_run_meta

    all_runs = list_runs()
    run_ids = args.run_ids or []

    if not run_ids:
        if len(all_runs) < 2:
            _console.print("  [yellow]Need at least 2 runs to compare.[/]")
            return 1
        run_ids = [all_runs[0]["id"], all_runs[1]["id"]]

    metas = []
    for rid in run_ids:
        m = load_run_meta(rid)
        if m is None:
            _console.print(f"  [red]Run not found: {rid}[/]")
            return 1
        metas.append(m)

    # Build service × run SCOM matrix
    svc_scoms: dict[str, dict[str, float]] = {}
    all_svcs: set[str] = set()
    for meta in metas:
        for s in meta.get("scom_results", []):
            name = s.get("Service") if s.get("Service") is not None else s.get("service", "?")
            scom_val = float(s.get("SCOM") if s.get("SCOM") is not None else s.get("scom", 0.0))
            svc_scoms.setdefault(name, {})[meta["id"]] = scom_val
            all_svcs.add(name)

    if not all_svcs:
        _console.print("  [yellow]No SCOM data in the selected runs.[/]")
        return 1

    sorted_svcs = sorted(all_svcs)

    if args.json:
        out = []
        for svc in sorted_svcs:
            entry = {"service": svc}
            for meta in metas:
                entry[meta["id"]] = svc_scoms.get(svc, {}).get(meta["id"], None)
            out.append(entry)
        _console.print(json.dumps(out, indent=2, default=str))
        return 0

    from rich.table import Table

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Service", style="cyan")
    labels = []
    for meta in metas:
        ts = meta.get("timestamp", "?")[:10]
        label = f"{ts}\n{meta['id'][:15]}..."
        labels.append(label)
        table.add_column(label, justify="right")

    if len(metas) >= 2:
        table.add_column("Δ", justify="right", style="yellow")

    for svc in sorted_svcs:
        scores = [svc_scoms.get(svc, {}).get(m["id"]) for m in metas]
        row = [svc] + [f"{s:.4f}" if s is not None else "—" for s in scores]
        if len(scores) >= 2:
            a, b = scores[0], scores[1]
            if a is not None and b is not None:
                delta = b - a
                sign = "+" if delta > 0 else ""
                row.append(f"{sign}{delta:.4f}")
            else:
                row.append("—")
        table.add_row(*row)

    _console.print()
    _console.print("  [bold]SCOM Comparison[/]")
    _console.print(table)
    _console.print(f"\n  [dim]Runs compared:[/] {'  |  '.join(m.get('id', '?') for m in metas)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``mba`` CLI. Parses args, dispatches to subcommands."""
    try:
        return _main(argv)
    except Exception as e:
        _console.print(f"\n  [red]Unexpected error:[/] {e}")
        if os.environ.get("MBA_DEBUG"):
            import traceback

            _console.print(f"  [dim]{traceback.format_exc()}[/]")
        logger.critical("Unexpected error: %s", e, exc_info=True)
        return 1


def _main(argv: list[str] | None = None) -> int:
    """Internal CLI dispatch: build argument parsers and route to the correct handler."""
    from boundary_analyzer import __version__

    _console.print(f"[bold cyan]MBA[/] [dim]v{__version__}[/] [dim]- Microservice Boundary Analyzer[/]\n", highlight=False)

    parser = argparse.ArgumentParser(
        prog="mba",
        description=(
            "MBA - Microservice Boundary Analyzer\n"
            "\n"
            "Analyze your microservices from Jaeger traces.\n"
            "Find services with low SCOM score.\n"
            "SCOM = Service COhesion Metric (0=bad, 1=perfect)."
        ),
        epilog=(
            "Examples:\n"
            "  mba run                              Run the full pipeline\n"
            "  mba run --skip-collect               Use traces you already have\n"
            "  mba run --skip-no-db-services        Skip services with no database\n"
            "  mba dashboard                        Open the web dashboard\n"
            "  mba dashboard --dash-port 9000       Dashboard on port 9000\n"
            "  mba setup --project-path ./my-app    Add OpenTelemetry to your app\n"
            "  mba teastore                         Deploy TeaStore and analyze\n"
            "\n"
            "Documentation: https://github.com/rayague/measure-automation"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── run subcommand ──────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        "run",
        help="Run the full SCOM pipeline (traces -> score -> report)",
        description=(
            "Run the full SCOM analysis pipeline.\n"
            "\n"
            "Steps:\n"
            "  1. Collect traces from Jaeger\n"
            "  2. Read and parse all traces\n"
            "  3. Find HTTP endpoints\n"
            "  4. Find database tables in traces\n"
            "  5. Build endpoint -> table mapping\n"
            "  6. Compute SCOM score for each service\n"
            "  7. Rank services and flag suspicious ones\n"
            "  8. Generate a report"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_group = run_parser.add_argument_group("Pipeline options")
    run_group.add_argument(
        "--skip-collect",
        action="store_true",
        help="Do not collect new traces. Use traces you already have.",
    )
    run_group.add_argument(
        "--output-dir",
        default="",
        help="Save traces to a different folder (default: from settings.yaml).",
    )
    run_group.add_argument(
        "--no-clean",
        action="store_true",
        help="Keep old files from previous runs. Default: clean before start.",
    )
    run_group.add_argument(
        "--new-dir",
        default="",
        help="Run in a new folder: data/runs/<name>/ (keeps each run separate).",
    )
    run_group.add_argument(
        "--skip-no-db-services",
        action="store_true",
        help="Skip services that have no database tables.",
    )

    dash_group = run_parser.add_argument_group("Dashboard options")
    dash_group.add_argument(
        "--dashboard",
        action="store_true",
        help="Open the web dashboard after the pipeline finishes.",
    )
    _add_dash_args(run_parser, group_name="Dashboard options")

    settings_group = run_parser.add_argument_group("Settings")
    settings_group.add_argument(
        "--settings",
        default="config/settings.yaml",
        help="Path to your settings file (default: config/settings.yaml).",
    )
    settings_group.add_argument(
        "--llm",
        action="store_true",
        help="Use AI to write the report. Needs OPENROUTER_API_KEY in your environment.",
    )

    # ── dashboard subcommand ─────────────────────────────────────────
    dash_parser = subparsers.add_parser(
        "dashboard",
        help="Open the web dashboard to see SCOM results",
        description=(
            "Open the web dashboard to explore SCOM results.\nThe dashboard shows scores, rankings, and service details.\n\nUse --run to view a specific historical run."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    dash_parser.add_argument(
        "--run",
        default="",
        help="Run ID to display (default: most recent run). Use 'mba runs list' to see available runs.",
    )
    _add_dash_args(dash_parser)

    # ── setup subcommand ─────────────────────────────────────────────
    setup_parser = subparsers.add_parser(
        "setup",
        help="Add OpenTelemetry to your Python app and run the pipeline",
        description=(
            "Add OpenTelemetry to your Python microservice.\n"
            "Then collect traces from Jaeger and run the SCOM pipeline.\n"
            "\n"
            "Supported frameworks:\n"
            "  FastAPI, Flask, Django, Starlette, Tornado"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    setup_proj = setup_parser.add_argument_group("Project")
    setup_proj.add_argument(
        "--project-path",
        required=True,
        help="Path to your microservice project.",
    )
    setup_proj.add_argument(
        "--framework",
        default="",
        help=(
            "Force a framework (default: auto-detect). Choices: flask, fastapi, django, "
            "djangorest, starlette, tornado, laravel, express, nextjs, nestjs."
        ),
    )
    setup_proj.add_argument(
        "--service-name",
        default="",
        help="Name for your service in Jaeger (default: folder name).",
    )
    setup_proj.add_argument(
        "--traces-output",
        default="",
        help="Where to save traces (default: ./traces/ in your project).",
    )
    setup_proj.add_argument(
        "--trace-limit",
        type=_validate_positive_int,
        default=500,
        help="Max number of traces to collect (default: 500).",
    )

    setup_jaeger = setup_parser.add_argument_group("Jaeger")
    setup_jaeger.add_argument(
        "--jaeger-host",
        default="localhost",
        help="Jaeger server address (default: localhost).",
    )
    setup_jaeger.add_argument(
        "--no-jaeger",
        action="store_true",
        help="Do not start Jaeger (use if Jaeger is already running).",
    )

    setup_extra = setup_parser.add_argument_group("Extra")
    setup_extra.add_argument(
        "--no-install",
        action="store_true",
        help="Do not install Python packages (use if already installed).",
    )
    setup_extra.add_argument(
        "--dashboard",
        action="store_true",
        help="Open the dashboard when finished.",
    )
    setup_extra.add_argument(
        "--llm",
        action="store_true",
        help="Use AI to write instrumentation code. Needs OPENROUTER_API_KEY.",
    )

    # ── teastore subcommand ───────────────────────────────────────────
    teastore_parser = subparsers.add_parser(
        "teastore",
        help="Deploy TeaStore (Java) with OTel and run SCOM",
        description=(
            "Deploy the TeaStore benchmark application with OpenTelemetry.\n"
            "Generate traffic, export traces, and run SCOM analysis.\n"
            "\n"
            "TeaStore has 6 Java services. Only persistence-service has a database.\n"
            "Use --no-skip-no-db to also analyze services with no database."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    teastore_run = teastore_parser.add_argument_group("Run options")
    teastore_run.add_argument("--output", default="data/teastore_run", help="Save folder for traces and results (default: data/teastore_run).")
    teastore_run.add_argument("--duration", type=_validate_duration, default=60, help="How many seconds to generate traffic (default: 60).")
    teastore_run.add_argument(
        "--wait",
        type=_validate_positive_int,
        default=900,
        help="Max seconds to wait for TeaStore to start (default: 900).",
    )
    teastore_run.add_argument("--download-only", action="store_true", help="Only download the OTel agent. Do not start anything.")

    teastore_scom = teastore_parser.add_argument_group("SCOM analysis")
    teastore_scom.add_argument("--threshold", type=_validate_threshold, default=0.5, help="SCOM threshold for suspicious flag (default: 0.5).")
    teastore_scom.add_argument("--no-skip-no-db", action="store_false", dest="skip_no_db", help="Also analyze services with no database.")
    teastore_scom.add_argument("--skip-pipeline", action="store_true", help="Only export traces from Jaeger. Do not run SCOM.")

    teastore_docker = teastore_parser.add_argument_group("Docker")
    teastore_docker.add_argument("--no-cleanup", action="store_false", dest="cleanup", help="Keep Docker containers running after finish.")
    teastore_docker.add_argument("--jaeger-ui", action="store_true", help="Open Jaeger web UI. Keeps containers running.")
    teastore_docker.add_argument("--prune", action="store_true", help="Remove leftover teastore containers/networks before starting.")

    # ── analyze subcommand ───────────────────────────────────────────
    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Run SCOM analysis on an existing traces file (skip collection/deployment)",
        description=(
            "Analyze existing traces from a file and run the SCOM pipeline.\n"
            "\n"
            "This command skips the trace collection and deployment steps.\n"
            "It reads traces from a file, then runs:\n"
            "  1. Read and parse all traces\n"
            "  2. Find HTTP endpoints\n"
            "  3. Find database tables in traces\n"
            "  4. Build endpoint -> table mapping\n"
            "  5. Compute SCOM score for each service\n"
            "  6. Rank services and flag suspicious ones\n"
            "  7. Generate a report"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyze_parser.add_argument(
        "traffic_file",
        help="Path to a JSON traces file (Jaeger export format).",
    )
    analyze_out = analyze_parser.add_argument_group("Output options")
    analyze_out.add_argument(
        "--output-dir",
        default="data/analysis",
        help="Save pipeline results to this folder (default: data/analysis).",
    )
    analyze_out.add_argument(
        "--dashboard",
        action="store_true",
        help="Open the web dashboard after analysis finishes.",
    )
    _add_dash_args(analyze_parser, group_name="Dashboard options")

    analyze_settings = analyze_parser.add_argument_group("Settings")
    analyze_settings.add_argument(
        "--threshold",
        type=_validate_threshold,
        default=0.5,
        help="SCOM threshold for suspicious flag (default: 0.5).",
    )
    analyze_settings.add_argument(
        "--skip-no-db-services",
        action="store_true",
        help="Skip services that have no database tables.",
    )
    analyze_settings.add_argument(
        "--language",
        default="",
        help="Force language for trace parsing (python, java, node, etc.). Auto-detected if omitted.",
    )
    analyze_settings.add_argument(
        "--llm",
        action="store_true",
        help="Use AI to write the report. Needs OPENROUTER_API_KEY in your environment.",
    )

    # ── runs subcommand ──────────────────────────────────────────────
    runs_parser = subparsers.add_parser(
        "runs",
        help="Manage saved analysis runs",
        description=(
            "List, show, or manage historical analysis runs.\n"
            "\n"
            "Each run is saved to data/runs/ with a timestamp and project name.\n"
            "You can view past SCOM results without re-running the analysis."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    runs_sub = runs_parser.add_subparsers(dest="runs_command", required=True)

    runs_list = runs_sub.add_parser("list", help="List all saved runs")
    runs_list.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON lines.",
    )

    runs_show = runs_sub.add_parser("show", help="Show details for a specific run")
    runs_show.add_argument("run_id", help="Run ID (prefix also works)")
    runs_show.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON.",
    )

    runs_compare = runs_sub.add_parser("compare", help="Compare SCOM scores across runs")
    runs_compare.add_argument("run_ids", nargs="*", help="Run IDs to compare (default: last 2 runs)")
    runs_compare.add_argument("--json", action="store_true", help="Output as JSON.")

    runs_delete = runs_sub.add_parser("delete", help="Delete a specific run")
    runs_delete.add_argument("run_id", nargs="?", help="Run ID to delete")
    runs_delete.add_argument("--all", action="store_true", help="Delete ALL runs")
    runs_delete.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")

    # ── ingest subcommand ─────────────────────────────────────────────
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Analyze any log file (Jaeger, Zipkin, OTLP, nginx, Locust, app logs…)",
        description=(
            "Universal log file ingestion.\n\n"
            "Accepts any log format and runs the full SCOM pipeline:\n"
            "  jaeger      Jaeger JSON export (default for .json files)\n"
            "  zipkin      Zipkin v2 JSON\n"
            "  otlp        OpenTelemetry OTLP JSON\n"
            "  locust      Locust CSV request statistics\n"
            "  nginx       nginx / Apache combined access log\n"
            "  w3c         W3C Extended Log Format (IIS)\n"
            "  generic_sql Application logs with HTTP + SQL (Django, SQLAlchemy, etc.)\n"
            "  json_lines  JSON Lines (one structured record per line)\n"
            "  raw_text    Guaranteed fallback for any other text file — never fails\n"
            "\n"
            "The format is auto-detected from file content. Use --format to override.\n"
            "Any non-empty file can be ingested: if no structured format matches, the\n"
            "raw_text fallback still turns every line into an analyzable span."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ingest_parser.add_argument("log_file", help="Path to the log / trace file to analyze")
    ingest_opts = ingest_parser.add_argument_group("Ingestion options")
    ingest_opts.add_argument(
        "--format",
        default="",
        dest="log_format",
        choices=["auto", "jaeger", "zipkin", "otlp", "locust", "nginx", "w3c", "generic_sql", "json_lines", "raw_text"],
        help="Force a specific format (default: auto-detect). 'raw_text' forces the unstructured-text fallback.",
    )
    ingest_opts.add_argument(
        "--service-name",
        default="",
        help="Override the service name embedded in the log file.",
    )
    ingest_opts.add_argument(
        "--encoding",
        default="utf-8",
        help="Text encoding of the log file (default: utf-8).",
    )
    ingest_out = ingest_parser.add_argument_group("Output options")
    ingest_out.add_argument("--output-dir", default="data/ingest", help="Output directory (default: data/ingest).")
    ingest_out.add_argument("--threshold", type=_validate_threshold, default=0.5, help="SCOM threshold (default: 0.5).")
    ingest_out.add_argument("--dashboard", action="store_true", help="Open the dashboard after analysis.")
    _add_dash_args(ingest_parser)

    # ── benchmark subcommand ──────────────────────────────────────────
    bench_parser = subparsers.add_parser(
        "benchmark",
        help="Run SCOM analysis on a known microservice benchmark",
        description=(
            "Run SCOM analysis on a well-known microservice benchmark application.\n\n"
            "Available benchmarks:\n"
            "  teastore   TeaStore (6 Java services, e-commerce)\n"
            "  hotel      DeathStarBench — Hotel Reservation (5 services, Go/Python)\n"
            "  boutique   Google Online Boutique (11 polyglot microservices)\n"
            "  sockshop   Weaveworks Sock Shop (8 services)\n"
            "\n"
            "Run without a name to list all benchmarks with setup instructions."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bench_parser.add_argument(
        "benchmark_name",
        nargs="?",
        default="",
        choices=["", "teastore", "hotel", "boutique", "sockshop"],
        help="Benchmark to run (omit to list all).",
    )
    bench_run = bench_parser.add_argument_group("Run options")
    bench_run.add_argument("--output", default="data/benchmark", help="Output directory.")
    bench_run.add_argument("--duration", type=_validate_duration, default=60, help="Traffic duration in seconds (default: 60).")
    bench_run.add_argument("--workers", type=_validate_positive_int, default=5, help="Traffic workers (default: 5).")
    bench_run.add_argument("--no-cleanup", action="store_false", dest="cleanup", help="Keep containers running after analysis.")
    bench_run.add_argument("--jaeger-ui", action="store_true", help="Open Jaeger UI after run.")
    bench_run.add_argument("--dashboard", action="store_true", help="Open MBA dashboard after run.")
    bench_run.add_argument("--wait", type=_validate_positive_int, default=900, help="Max seconds to wait for services to start (default: 900 — TeaStore's 6 JVMs are slow to warm up).")
    bench_run.add_argument("--threshold", type=_validate_threshold, default=0.5, help="SCOM threshold.")

    # ── full subcommand ─────────────────────────────────────────────
    full_parser = subparsers.add_parser(
        "full",
        help="Fully automatic analysis: detect, instrument, deploy, traffic, collect, compute SCOM",
        description=(
            "Fully automatic microservice boundary analysis.\n"
            "\n"
            "This command runs the entire pipeline automatically:\n"
            "  1. DISCOVER  - Detect language, framework, entry points\n"
            "  2. DEPLOY    - Start Jaeger, instrument and start services\n"
            "  3. TRAFFIC   - Auto-discover endpoints and generate traffic\n"
            "  4. COLLECT   - Export traces from Jaeger\n"
            "  5. ANALYZE   - Run SCOM pipeline and generate report\n"
            "  6. CLEANUP   - Stop services and clean up"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    full_parser.add_argument(
        "project_dir",
        nargs="?",
        default=".",
        help="Path to the project to analyze (default: current directory).",
    )
    full_group = full_parser.add_argument_group("Traffic options")
    full_group.add_argument(
        "--duration",
        type=_validate_duration,
        default=60,
        help="How many seconds to generate traffic (default: 60).",
    )
    full_group.add_argument(
        "--workers",
        type=_validate_positive_int,
        default=5,
        help="Number of concurrent traffic workers (default: 5).",
    )

    full_jaeger = full_parser.add_argument_group("Jaeger options")
    full_jaeger.add_argument(
        "--jaeger-port",
        type=_validate_port,
        default=16686,
        help="Jaeger UI port (default: 16686).",
    )
    full_jaeger.add_argument(
        "--otlp-port",
        type=_validate_port,
        default=4318,
        help="OTLP HTTP port (default: 4318).",
    )

    full_settings = full_parser.add_argument_group("Settings")
    full_settings.add_argument(
        "--language",
        default="",
        help="Force project language (python, java, node, etc.). Auto-detected if omitted.",
    )
    full_settings.add_argument(
        "--no-clean",
        action="store_true",
        help="Keep services and Jaeger running after analysis.",
    )
    full_settings.add_argument(
        "--llm",
        action="store_true",
        help="Use AI for smarter endpoint discovery and payload generation.",
    )
    full_settings.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output including payloads and responses.",
    )
    full_settings.add_argument(
        "--exclude-services",
        nargs="*",
        default=None,
        help="Service names to exclude from analysis (e.g. gateway).",
    )
    full_settings.add_argument(
        "--lookback",
        type=int,
        default=10,
        help="Jaeger trace lookback in minutes (default: 10).",
    )
    full_settings.add_argument(
        "--reset-jaeger",
        action="store_true",
        help="Stop and remove existing Jaeger container before starting fresh (avoids trace pollution from previous runs).",
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        settings_path = Path(args.settings)
        if not settings_path.exists():
            parser.error(f"settings file not found: {settings_path}")

        # Make settings path visible to all pipeline steps
        import os

        os.environ["BOUNDARY_ANALYZER_SETTINGS"] = str(settings_path)

        if str(args.new_dir).strip():
            run_root = Path("data") / "runs" / str(args.new_dir).strip()
            os.environ["BOUNDARY_ANALYZER_DATA_DIR"] = str(run_root)
            os.environ["BOUNDARY_ANALYZER_REPORTS_DIR"] = str(run_root / "reports")
            os.environ["BOUNDARY_ANALYZER_OUTPUT_DIR"] = str(run_root / "raw" / "traces")
        else:
            if str(args.output_dir).strip():
                os.environ["BOUNDARY_ANALYZER_OUTPUT_DIR"] = str(args.output_dir).strip()

            if str(args.data_dir).strip() and str(args.data_dir).strip() != "data":
                os.environ["BOUNDARY_ANALYZER_DATA_DIR"] = str(args.data_dir).strip()

        # ── Automatic cleanup of old data ─────────────────────────────────
        # By default, clean old data before each run to prevent stale results.
        # Use --no-clean to explicitly preserve old data.
        cleaned_parts: list[str] = []
        if not bool(args.no_clean):
            from boundary_analyzer.settings_loader import clean_data_dirs

            # If skipping trace collection, preserve traces but clean computed data
            should_clean_traces = not bool(args.skip_collect)
            deleted = clean_data_dirs(
                clean_traces=should_clean_traces,
                clean_interim=True,
                clean_processed=True,
            )

            if deleted["traces"] > 0:
                cleaned_parts.append(f"{deleted['traces']} trace files")
            if deleted["interim"] > 0:
                cleaned_parts.append(f"{deleted['interim']} interim files")
            if deleted["processed"] > 0:
                cleaned_parts.append(f"{deleted['processed']} processed files")

            if cleaned_parts:
                _console.print(f"  [dim]Cleaned old data:[/] {', '.join(cleaned_parts)}")
            else:
                _console.print("  [dim]No old data to clean.[/]")

        # Pass flags to pipeline steps via environment
        if args.llm:
            os.environ["BOUNDARY_ANALYZER_LLM_ENABLED"] = "1"
        if args.skip_no_db_services:
            os.environ["BOUNDARY_ANALYZER_SKIP_NO_DB_SERVICES"] = "1"

        rc = _run_pipeline(skip_collect=bool(args.skip_collect))
        if rc != 0:
            return rc

        if args.dashboard:
            dash_dir = Path(os.environ.get("BOUNDARY_ANALYZER_DATA_DIR", str(args.data_dir)))
            return _run_dashboard(
                data_dir=dash_dir,
                host=str(args.dash_host),
                port=int(args.dash_port),
            )

        return 0

    if args.command == "dashboard":
        dashboard_data_dir = Path(str(args.data_dir))

        run_id = str(args.run).strip() if str(args.run).strip() else ""
        if run_id:
            from boundary_analyzer.auto.run_registry import get_run_path

            run_path = get_run_path(run_id)
            if run_path:
                dashboard_data_dir = run_path
                _console.print(f"  [dim]Showing run:[/] [cyan]{run_id}[/]")
            else:
                _console.print(f"  [yellow]Run '{run_id}' not found — using {dashboard_data_dir}[/]")

        return _run_dashboard(
            data_dir=dashboard_data_dir,
            host=str(args.dash_host),
            port=int(args.dash_port),
        )

    if args.command == "setup":
        rc = _run_setup(
            project_path=str(args.project_path),
            framework=str(args.framework),
            service_name=str(args.service_name),
            jaeger_host=str(args.jaeger_host),
            no_jaeger=bool(args.no_jaeger),
            no_install=bool(args.no_install),
            traces_output=str(args.traces_output),
            trace_limit=int(args.trace_limit),
            llm=bool(args.llm),
        )

        if rc != 0:
            return rc

        if args.dashboard:
            return _run_dashboard(data_dir=Path(str(args.project_path)) / "scom_report")

        return 0

    if args.command == "teastore":
        from boundary_analyzer.auto.teastore_runner import run_teastore

        return run_teastore(
            output=args.output,
            duration=args.duration,
            wait=args.wait,
            threshold=args.threshold,
            skip_no_db=args.skip_no_db,
            cleanup=args.cleanup,
            skip_pipeline=args.skip_pipeline,
            jaeger_ui=args.jaeger_ui,
            download_only=args.download_only,
            prune=getattr(args, "prune", False),
        )

    if args.command == "analyze":
        traffic_path = Path(str(args.traffic_file))
        if not traffic_path.exists():
            parser.error(f"Traffic file not found: {traffic_path}")

        output_dir = Path(str(args.output_dir))

        if bool(args.llm):
            os.environ["BOUNDARY_ANALYZER_LLM_ENABLED"] = "1"
        if bool(args.skip_no_db_services):
            os.environ["BOUNDARY_ANALYZER_SKIP_NO_DB_SERVICES"] = "1"
        if str(args.language).strip():
            os.environ["BOUNDARY_ANALYZER_LANGUAGE"] = str(args.language).strip()

        from boundary_analyzer.auto.models import (
            AnalysisReport,
            ProjectInfo,
            ServiceInfo,
            StepResult,
        )
        from boundary_analyzer.auto.run_registry import save_run
        from boundary_analyzer.pipeline.run_pipeline import run_pipeline

        rc = run_pipeline(
            traces=traffic_path,
            output_dir=output_dir,
            scom_method="weighted",
            threshold_method="fixed",
            fixed_threshold=float(args.threshold),
            exclude_health_routes=True,
            exclude_http_client_spans=True,
            exclude_unknown_endpoint=True,
            skip_no_db_services=bool(args.skip_no_db_services),
        )
        if rc != 0:
            return rc

        _console.print(f"  Results saved to: [cyan]{output_dir.resolve()}[/]")

        # Build a lightweight AnalysisReport for the run registry
        try:
            project_dir = Path.cwd()
            project = ProjectInfo(services=[], root_dir=project_dir)
            report = AnalysisReport(project=project)
            report.total_duration_seconds = 0.0
            report.report_path = output_dir / "report.md"
            report.steps["analyze"] = StepResult(
                success=True,
                step_name="analyze",
                message="SCOM pipeline complete",
            )

            scom_csv = output_dir / "processed" / "service_scom.csv"
            rank_csv = output_dir / "processed" / "service_rank.csv"
            sus_csv = output_dir / "processed" / "suspicious_services.csv"

            if scom_csv.exists():
                import pandas as pd

                scom_df = pd.read_csv(scom_csv)
                report.scom_results["scom_df"] = scom_df
                # Populate services from SCOM CSV so meta.json is meaningful
                services = []
                for _, row in scom_df.iterrows():
                    svc_name = str(row.get("service_name", ""))
                    if svc_name:
                        services.append(
                            ServiceInfo(
                                name=svc_name,
                                language="",
                                framework="",
                                entry_points=[],
                                deployment="analyze",
                            )
                        )
                if services:
                    report.project.services = services
            if rank_csv.exists():
                import pandas as pd

                report.scom_results["rank_df"] = pd.read_csv(rank_csv)
            if sus_csv.exists():
                import pandas as pd

                report.scom_results["suspicious_df"] = pd.read_csv(sus_csv)

            mapping_csv = output_dir / "interim" / "endpoint_table_map.csv"
            if mapping_csv.exists():
                import pandas as pd

                report.scom_results["mapping_df"] = pd.read_csv(mapping_csv)

            saved_run = save_run(report)
            saved_run_id = saved_run.id
        except Exception as e:
            saved_run_id = None
            logger.warning("Failed to save run: %s", e)

        if args.dashboard:
            return _run_dashboard(
                data_dir=output_dir,
                host=str(args.dash_host),
                port=int(args.dash_port),
            )

        if saved_run_id:
            _console.print(f"  [dim]View with:[/] [cyan]mba dashboard --run {saved_run_id}[/]")

        return 0

    if args.command == "runs":
        from boundary_analyzer.auto.run_registry import (
            delete_run,
            list_runs,
            load_run_meta,
        )

        if args.runs_command == "list":
            runs = list_runs()
            if not runs:
                _console.print("  [yellow]No saved runs found.[/]")
                return 0
            if args.json:
                for r in runs:
                    _console.print(json.dumps(r, default=str))
                return 0
            _console.print(f"  [bold]Saved runs[/] [dim]({len(runs)} total)[/]\n")
            for i, r in enumerate(runs, 1):
                label = "  [bold cyan]last →[/]" if i == 1 else "    "
                ts = r.get("timestamp", "?")[:19].replace("T", " ")
                proj = r.get("project_name", "?")
                svcs = len(r.get("services", []))
                ok = "✔" if r.get("all_success") else "⚠"
                _console.print(f"{label} [dim]{ts}[/] [cyan]{r['id']}[/] {ok} [white]{proj}[/] ([dim]{svcs} services[/])")
            _console.print("\n  [dim]Use[/] [cyan]mba runs show <id>[/] [dim]for details.[/]")
            return 0

        if args.runs_command == "show":
            meta = load_run_meta(args.run_id)
            if not meta:
                _console.print(f"  [red]Run not found: {args.run_id}[/]")
                return 1
            if args.json:
                _console.print(json.dumps(meta, default=str, indent=2))
                return 0
            ts = meta.get("timestamp", "?")[:19].replace("T", " ")
            proj = meta.get("project_name", "?")
            lang = meta.get("language", "?")
            dur = meta.get("duration_seconds", 0)
            svcs = meta.get("services", [])
            scoms = meta.get("scom_results", [])
            endpoints = meta.get("endpoints_total", 0)
            tables = meta.get("tables_total", 0)
            traffic_req = meta.get("traffic_requests", 0)
            traffic_ok = meta.get("traffic_ok", 0)
            status = "✔ Success" if meta.get("all_success") else "⚠ Issues"

            _console.print(f"  [bold]Run:[/] [cyan]{meta['id']}[/]")
            _console.print(f"  [bold]Date:[/] {ts}  [bold]Project:[/] {proj}  [bold]Language:[/] {lang}")
            _console.print(f"  [bold]Duration:[/] {dur:.1f}s  [bold]Status:[/] {status}")
            _console.print(f"  [bold]Endpoints:[/] {endpoints}  [bold]Tables:[/] {tables}")
            _console.print(f"  [bold]Traffic:[/] {traffic_req} req ({traffic_ok} ok, {traffic_req - traffic_ok} failed)")
            _console.print(f"  [bold]Services:[/] {len(svcs)}\n")

            if scoms:
                from rich.table import Table

                from boundary_analyzer._utils import classify_scom

                table = Table(show_header=True, header_style="bold cyan")
                table.add_column("Service")
                table.add_column("Endpoints")
                table.add_column("Tables")
                table.add_column("SCOM")
                table.add_column("Cohésion")
                table.add_column("Status")
                for s in scoms:
                    name = s.get("Service") if s.get("Service") is not None else (s.get("service") or "?")
                    ep = s.get("Endpoints") if s.get("Endpoints") is not None else (s.get("endpoints") or "?")
                    tbl = s.get("Tables") if s.get("Tables") is not None else (s.get("tables") or s.get("Tables/Collections") or "?")
                    scom_val = s.get("SCOM") if s.get("SCOM") is not None else (s.get("scom") if s.get("scom") is not None else "?")
                    susp = s.get("is_suspicious") if s.get("is_suspicious") is not None else (s.get("Suspicious") or "")
                    raw_scom = s.get("SCOM") if s.get("SCOM") is not None else s.get("scom")
                    coh = classify_scom(raw_scom)
                    label = "⚠" if susp else "✔"
                    table.add_row(str(name), str(ep), str(tbl), str(scom_val), coh, label)
                _console.print(table)

            report_path = meta.get("report_path", "")
            if report_path and Path(report_path).exists():
                _console.print(f"\n  [dim]Report:[/] {report_path}")
            return 0

        if args.runs_command == "compare":
            return _cmd_runs_compare(args)

        if args.runs_command == "delete":
            if args.all:
                from boundary_analyzer.auto.run_registry import list_runs
                all_runs = list_runs(data_root_guess)
                if not all_runs:
                    _console.print("  [yellow]No runs to delete.[/]")
                    return 0
                if not args.yes:
                    _console.print(f"  [red]Are you sure you want to delete ALL {len(all_runs)} runs?[/]")
                    _console.print("  [dim]Pass --yes to skip confirmation.[/]")
                    return 1
                for r in all_runs:
                    delete_run(r["id"])
                _console.print(f"  [red]Deleted {len(all_runs)} runs.[/]")
                return 0
            if not args.run_id:
                _console.print("  [red]Specify a run_id or use --all[/]")
                return 1
            if not args.yes:
                _console.print(f"  [red]Are you sure you want to delete run '{args.run_id}'?[/]")
                _console.print("  [dim]Pass --yes to skip confirmation.[/]")
                return 1
            if delete_run(args.run_id):
                _console.print(f"  [red]Deleted run: {args.run_id}[/]")
                return 0
            _console.print(f"  [red]Run not found: {args.run_id}[/]")
            return 1

        return 0

    if args.command == "full":
        from boundary_analyzer.auto import run_full_analysis
        from boundary_analyzer.auto.orchestrator import FullConfig
        from boundary_analyzer.auto.run_registry import save_run

        if str(args.language).strip():
            os.environ["BOUNDARY_ANALYZER_LANGUAGE"] = str(args.language).strip()

        config = FullConfig(
            project_dir=Path(str(args.project_dir)),
            duration=int(args.duration),
            workers=int(args.workers),
            jaeger_port=int(args.jaeger_port),
            otlp_port=int(args.otlp_port),
            no_clean=bool(args.no_clean),
            llm=bool(args.llm),
            verbose=bool(args.verbose),
            exclude_services=args.exclude_services,
            lookback_minutes=int(args.lookback),
            reset_jaeger=bool(args.reset_jaeger),
        )
        try:
            report = run_full_analysis(config)
        except KeyboardInterrupt:
            _console.print("\n  [red]✘ Interrupted by user[/]")
            return 130

        try:
            meta = save_run(report)
            _console.print(f"  Saved run: [cyan]mba runs show {meta.id}[/]")
            if report.report_path and report.report_path.parent.exists():
                if "mba_scom_" in report.report_path.parent.name:
                    try:
                        import shutil

                        shutil.rmtree(report.report_path.parent)
                    except OSError as e:
                        logger.warning("Failed to clean up temp report dir: %s", e)
        except Exception as e:
            logger.warning("Failed to save run: %s", e)

        return 0 if report.all_success else 1

    if args.command == "ingest":
        return _cmd_ingest(args)

    if args.command == "benchmark":
        return _cmd_benchmark(args)

    parser.error(f"Unknown command: {args.command}")


# ───────────────────────────────────────────────────────────────────────────────
# mba ingest handler
# ───────────────────────────────────────────────────────────────────────────────


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Handler for ``mba ingest <log_file>``.

    Auto-detects the log format, runs the SCOM pipeline, and optionally
    opens the dashboard.
    """
    import os

    import pandas as pd
    from rich.table import Table

    from boundary_analyzer.auto.models import (
        AnalysisReport,
        ProjectInfo,
        ServiceInfo,
        StepResult,
    )
    from boundary_analyzer.auto.run_registry import save_run
    from boundary_analyzer.parsing.log_ingestion import detect_format, ingest_log_file
    from boundary_analyzer.pipeline.run_pipeline import run_pipeline

    log_path = Path(str(args.log_file))
    if not log_path.exists():
        _console.print(f"  [red]✗ File not found:[/] {log_path}")
        return 1

    output_dir = Path(str(args.output_dir))
    format_hint = str(getattr(args, "log_format", "") or "").strip()
    if format_hint == "auto":
        format_hint = ""
    svc_name = str(getattr(args, "service_name", "") or "").strip()
    encoding = str(getattr(args, "encoding", "utf-8") or "utf-8").strip()

    # ── Format detection + quick preview ───────────────────────────────
    fmt_id, conf = detect_format(log_path, encoding=encoding)
    effective_fmt = format_hint or fmt_id
    _console.print(f"  [dim]File:[/]    [cyan]{log_path}[/]")
    _console.print(f"  [dim]Format:[/]  [cyan]{effective_fmt}[/]  [dim](confidence {conf:.0%})[/]")

    # ── Ingest ─────────────────────────────────────────────────────────────
    try:
        result = ingest_log_file(log_path, service_name=svc_name, format_hint=effective_fmt, encoding=encoding)
    except Exception as exc:
        _console.print(f"  [red]✗ Ingestion failed:[/] {exc}")
        return 1

    for w in result.warnings:
        _console.print(f"  [yellow]⚠[/] {w}")

    # ── Ingestion stats table ────────────────────────────────────────────
    st = result.stats
    tbl = Table(show_header=True, header_style="bold cyan", box=None)
    tbl.add_column("Metric", style="dim")
    tbl.add_column("Value", style="cyan")
    tbl.add_row("Total spans", str(st.get("total_spans", 0)))
    tbl.add_row("HTTP spans", str(st.get("http_spans", 0)))
    tbl.add_row("DB spans", str(st.get("db_spans", 0)))
    tbl.add_row("Services", ", ".join(st.get("services", [])) or "?")
    tbl.add_row("Unique traces", str(st.get("unique_traces", 0)))
    tbl.add_row("DB info", "✔ yes" if result.has_db_info else "✗ no (heuristic mode)")
    tbl.add_row("Correlated", "✔ yes" if result.has_trace_correlation else "✗ no")
    _console.print(tbl)
    _console.print()

    if not result.has_db_info:
        _console.print("  [yellow]⚠[/]  No DB operations found — SCOM will use path-based table heuristics. Results will be labelled as estimated.")

    if result.spans_df.empty:
        _console.print("  [red]✗ No spans extracted — cannot run SCOM pipeline.[/]")
        return 1

    # ── Run pipeline ─────────────────────────────────────────────────────────
    _console.print(f"  [●] Running SCOM pipeline → [cyan]{output_dir}[/]")
    try:
        rc = run_pipeline(
            traces=log_path,
            output_dir=output_dir,
            scom_method="weighted",
            threshold_method="fixed",
            fixed_threshold=float(args.threshold),
            exclude_health_routes=True,
            exclude_http_client_spans=True,
            exclude_unknown_endpoint=True,
            service_name=result.service_name_used or svc_name,
            format_hint=effective_fmt,
            encoding=encoding,
        )
    except Exception as exc:
        _console.print(f"  [red]✗ Pipeline error:[/] {exc}")
        return 1

    if rc != 0:
        return rc

    # ── Show SCOM results ───────────────────────────────────────────────────
    rank_csv = output_dir / "processed" / "service_rank.csv"
    if rank_csv.exists():
        from boundary_analyzer._utils import classify_scom

        rank_df = pd.read_csv(rank_csv)
        rtbl = Table(show_header=True, header_style="bold cyan", box=None)
        rtbl.add_column("#", style="dim")
        rtbl.add_column("Service")
        rtbl.add_column("SCOM")
        rtbl.add_column("Cohesion")
        rtbl.add_column("Endpoints")
        rtbl.add_column("Tables")
        rtbl.add_column("Status")
        for _, row in rank_df.iterrows():
            is_susp = bool(row.get("is_suspicious", False))
            status_str = "[red]⚠ suspect[/]" if is_susp else "[green]✔ healthy[/]"
            scom_val = float(row.get("scom_score", 0))
            rtbl.add_row(
                str(int(row.get("rank", 0))),
                str(row.get("service_name", "?")),
                f"[cyan]{scom_val:.4f}[/]",
                classify_scom(scom_val),
                str(row.get("endpoints_count", "?")),
                str(row.get("tables_count", "?")),
                status_str,
            )
        _console.print()
        _console.print(rtbl)
        _console.print()

    # ── Save run registry ─────────────────────────────────────────────────
    saved_run_id = None
    try:
        project = ProjectInfo(services=[], root_dir=log_path.parent)
        report = AnalysisReport(project=project)
        report.total_duration_seconds = 0.0
        report.report_path = output_dir / "report.md"
        report.steps["analyze"] = StepResult(success=True, step_name="analyze", message="SCOM pipeline complete (ingest mode)")
        scom_csv = output_dir / "processed" / "service_scom.csv"
        if scom_csv.exists():
            scom_df = pd.read_csv(scom_csv)
            report.scom_results["scom_df"] = scom_df
            svcs = []
            for _, row in scom_df.iterrows():
                nm = str(row.get("service_name", ""))
                if nm:
                    svcs.append(ServiceInfo(name=nm, language="", framework="", entry_points=[], deployment="ingest"))
            report.project.services = svcs
        if rank_csv.exists():
            report.scom_results["rank_df"] = pd.read_csv(rank_csv)
        mapping_csv = output_dir / "interim" / "endpoint_table_map.csv"
        if mapping_csv.exists():
            report.scom_results["mapping_df"] = pd.read_csv(mapping_csv)
        saved_meta = save_run(report)
        saved_run_id = saved_meta.id
    except Exception as e:
        logger.warning("Failed to save run: %s", e)

    _console.print(f"  [✔] Results saved to [cyan]{output_dir.resolve()}[/]")
    if saved_run_id:
        _console.print(f"  [dim]View with:[/] [cyan]mba dashboard --run {saved_run_id}[/]")

    if getattr(args, "dashboard", False):
        return _run_dashboard(
            data_dir=output_dir,
            host=str(args.dash_host),
            port=int(args.dash_port),
        )

    return 0


# ────────────────────────────────────────────────────────────────────────────────
# mba benchmark handler
# ────────────────────────────────────────────────────────────────────────────────

_BENCHMARKS: dict[str, dict] = {
    "teastore": {
        "label": "TeaStore",
        "services": 6,
        "language": "Java",
        "description": "E-commerce microservices benchmark (6 Java services, shared MySQL).",
        "source": "https://github.com/DescartesResearch/TeaStore",
        "native": True,
        "setup": ["Run: mba teastore --duration 120"],
    },
    "hotel": {
        "label": "Hotel Reservation (DeathStarBench)",
        "services": 5,
        "language": "Go / Python",
        "description": "Hotel booking system from CMU DeathStarBench (5 Go services + MongoDB).",
        "source": "https://github.com/delimitrou/DeathStarBench",
        "native": False,
        "setup": [
            "git clone https://github.com/delimitrou/DeathStarBench.git",
            "cd DeathStarBench/hotelReservation",
            "docker compose up -d",
            "# Generate traffic (e.g. with wrk or locust), then export Jaeger traces",
            "mba ingest ./traces.json  (or mba full . if OTel is already configured)",
        ],
    },
    "boutique": {
        "label": "Online Boutique (Google)",
        "services": 11,
        "language": "Go / Python / Node / Java / C# / Ruby",
        "description": "Google's polyglot microservices demo (11 services, gRPC + Redis + PostgreSQL).",
        "source": "https://github.com/GoogleCloudPlatform/microservices-demo",
        "native": False,
        "setup": [
            "git clone https://github.com/GoogleCloudPlatform/microservices-demo.git",
            "cd microservices-demo",
            "# Docker Compose path: see docs/development-guide.md",
            "docker compose up -d",
            "mba full .  (or mba ingest <traces.json> after exporting from Jaeger)",
        ],
    },
    "sockshop": {
        "label": "Sock Shop (Weaveworks)",
        "services": 8,
        "language": "Go / Node / Java / Python",
        "description": "Classic Weaveworks microservices demo (8 services, MongoDB + MySQL).",
        "source": "https://github.com/microservices-demo/microservices-demo",
        "native": False,
        "setup": [
            "git clone https://github.com/microservices-demo/microservices-demo.git",
            "cd microservices-demo/deploy/docker-compose",
            "docker compose up -d",
            "mba full .  (or mba ingest <traces.json>)",
        ],
    },
}


def _cmd_benchmark(args: argparse.Namespace) -> int:
    """Handler for ``mba benchmark [name]``."""
    import subprocess
    import sys

    from rich.table import Table

    name = str(getattr(args, "benchmark_name", "") or "").strip()

    if not name:
        # List all benchmarks
        _console.print()
        _console.print("  [bold cyan]Available benchmarks[/]\n")
        tbl = Table(show_header=True, header_style="bold cyan", box=None)
        tbl.add_column("Name", style="cyan")
        tbl.add_column("Label")
        tbl.add_column("Services")
        tbl.add_column("Language")
        tbl.add_column("Built-in")
        for key, meta in _BENCHMARKS.items():
            tbl.add_row(
                key,
                meta["label"],
                str(meta["services"]),
                meta["language"],
                "[green]✔ mba benchmark " + key + "[/]" if meta["native"] else "[dim]manual setup[/]",
            )
        _console.print(tbl)
        _console.print()
        _console.print("  Run [cyan]mba benchmark <name>[/] for setup instructions.")
        _console.print("  Run [cyan]mba benchmark teastore[/] to start the built-in TeaStore analysis.")
        return 0

    meta = _BENCHMARKS.get(name, {})
    if not meta:
        _console.print(f"  [red]✗ Unknown benchmark:[/] {name!r}")
        return 1

    _console.print()
    _console.print(f"  [bold]{meta['label']}[/]  —  {meta['description']}")
    _console.print(f"  [dim]Source:[/] {meta['source']}")
    _console.print()

    if meta.get("native") and name == "teastore":
        from boundary_analyzer.auto.teastore_runner import run_teastore

        return run_teastore(
            output=args.output,
            duration=args.duration,
            wait=args.wait,
            threshold=args.threshold,
            skip_no_db=True,
            cleanup=args.cleanup,
            skip_pipeline=False,
            jaeger_ui=args.jaeger_ui,
            download_only=False,
            prune=getattr(args, "prune", False),
        )

    # Not natively automated — print step-by-step setup guide
    _console.print(f"  [yellow]⚠[/]  [bold]{name}[/] requires manual setup steps:\n")
    for i, step in enumerate(meta.get("setup", []), 1):
        if step.startswith("#"):
            _console.print(f"     [dim]{step}[/]")
        elif step.startswith("mba "):
            _console.print(f"  {i}. [cyan]{step}[/]")
        else:
            _console.print(f"  {i}. [dim]$[/] [white]{step}[/]")
    _console.print()
    _console.print(f"  [dim]After generating traffic, run[/] [cyan]mba ingest <traces.json>[/] [dim]to compute SCOM.[/]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
