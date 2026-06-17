from __future__ import annotations

import argparse
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
        description=("Open the web dashboard to explore SCOM results.\nThe dashboard shows scores, rankings, and service details."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        help="Force a framework (default: auto-detect). Choices: fastapi, flask, django.",
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
        default=300,
        help="Max seconds to wait for TeaStore to start (default: 300).",
    )
    teastore_run.add_argument("--download-only", action="store_true", help="Only download the OTel agent. Do not start anything.")

    teastore_scom = teastore_parser.add_argument_group("SCOM analysis")
    teastore_scom.add_argument("--threshold", type=_validate_threshold, default=0.5, help="SCOM threshold for suspicious flag (default: 0.5).")
    teastore_scom.add_argument("--no-skip-no-db", action="store_false", dest="skip_no_db", help="Also analyze services with no database.")
    teastore_scom.add_argument("--skip-pipeline", action="store_true", help="Only export traces from Jaeger. Do not run SCOM.")

    teastore_docker = teastore_parser.add_argument_group("Docker")
    teastore_docker.add_argument("--no-cleanup", action="store_false", dest="cleanup", help="Keep Docker containers running after finish.")
    teastore_docker.add_argument("--jaeger-ui", action="store_true", help="Open Jaeger web UI. Keeps containers running.")

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
        return _run_dashboard(
            data_dir=Path(str(args.data_dir)),
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
        import subprocess
        import sys
        from pathlib import Path as _Path

        _script = _Path(__file__).resolve().parents[2] / "scripts" / "teastore" / "deploy_and_trace.py"
        _cmd = [sys.executable, str(_script)]
        _cmd += ["--output", str(args.output)]
        _cmd += ["--duration", str(args.duration)]
        _cmd += ["--wait", str(args.wait)]
        _cmd += ["--threshold", str(args.threshold)]
        if not args.skip_no_db:
            _cmd += ["--no-skip-no-db"]
        if not args.cleanup:
            _cmd += ["--no-cleanup"]
        if args.skip_pipeline:
            _cmd += ["--skip-pipeline"]
        if args.jaeger_ui:
            _cmd += ["--jaeger-ui"]
        if args.download_only:
            _cmd += ["--download-only"]
        return subprocess.call(_cmd)

    if args.command == "full":
        from boundary_analyzer.auto import run_full_analysis
        from boundary_analyzer.auto.orchestrator import FullConfig

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
        )
        try:
            report = run_full_analysis(config)
        except KeyboardInterrupt:
            print("\n  ✘ Interrupted by user")
            return 130
        return 0 if report.all_success else 1

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
