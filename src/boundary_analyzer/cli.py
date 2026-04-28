from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


def _run_pipeline(skip_collect: bool) -> int:
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

    return 0


def _run_dashboard(data_dir: Path | None = None, host: str = "127.0.0.1", port: int = 8050) -> int:
    from boundary_analyzer.dashboard.app import main as dashboard_main

    # Pass configuration via env to avoid coupling CLI to Dash internals
    import os
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
) -> int:
    from boundary_analyzer.auto_setup import setup_instrumentation

    cmd = [sys.executable, str(Path(setup_instrumentation.__file__))]
    cmd += ["--project-path", project_path]

    if framework:
        cmd += ["--framework", framework]
    if service_name:
        cmd += ["--service-name", service_name]
    if jaeger_host:
        cmd += ["--jaeger-host", jaeger_host]
    if no_jaeger:
        cmd += ["--no-jaeger"]
    if no_install:
        cmd += ["--no-install"]
    if traces_output:
        cmd += ["--traces-output", traces_output]
    if trace_limit:
        cmd += ["--trace-limit", str(int(trace_limit))]

    return subprocess.run(cmd).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="boundary-analyzer",
        description="Boundary Analyzer CLI",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run",
        help="Run the full analysis pipeline (steps 01-08)",
    )
    run_parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="Skip Step 01 (trace collection) and reuse existing traces from settings.yaml output_dir.",
    )
    run_parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the dashboard after the pipeline finishes.",
    )
    run_parser.add_argument(
        "--data-dir",
        default="data",
        help="Base directory containing interim/ and processed/ folders for the dashboard (default: data).",
    )
    run_parser.add_argument(
        "--dash-host",
        default="127.0.0.1",
        help="Dashboard host bind (default: 127.0.0.1). Use 0.0.0.0 to expose on LAN.",
    )
    run_parser.add_argument(
        "--dash-port",
        type=int,
        default=8050,
        help="Dashboard port (default: 8050).",
    )
    run_parser.add_argument(
        "--settings",
        default="config/settings.yaml",
        help="Path to settings.yaml (applies to all pipeline steps).",
    )

    dash_parser = subparsers.add_parser(
        "dashboard",
        help="Launch the dashboard (requires pipeline outputs in data/)",
    )
    dash_parser.add_argument(
        "--data-dir",
        default="data",
        help="Base directory containing interim/ and processed/ folders (default: data).",
    )
    dash_parser.add_argument(
        "--dash-host",
        default="127.0.0.1",
        help="Dashboard host bind (default: 127.0.0.1). Use 0.0.0.0 to expose on LAN.",
    )
    dash_parser.add_argument(
        "--dash-port",
        type=int,
        default=8050,
        help="Dashboard port (default: 8050).",
    )

    setup_parser = subparsers.add_parser(
        "setup",
        help="Auto-setup OpenTelemetry + Jaeger (optional), collect traces, and run analysis for a target project",
    )
    setup_parser.add_argument(
        "--project-path",
        required=True,
        help="Path to the microservice project to instrument",
    )
    setup_parser.add_argument(
        "--framework",
        default="",
        help="Force a specific framework (default: auto-detect)",
    )
    setup_parser.add_argument(
        "--service-name",
        default="",
        help="Service name as it should appear in Jaeger (default: folder name)",
    )
    setup_parser.add_argument(
        "--jaeger-host",
        default="localhost",
        help="Host where Jaeger is running (default: localhost)",
    )
    setup_parser.add_argument(
        "--no-jaeger",
        action="store_true",
        help="Skip starting Jaeger (use if it is already running)",
    )
    setup_parser.add_argument(
        "--no-install",
        action="store_true",
        help="Skip package installation (use if already installed)",
    )
    setup_parser.add_argument(
        "--traces-output",
        default="",
        help="Where to save collected traces JSON (default: ./traces/ inside the target project)",
    )
    setup_parser.add_argument(
        "--trace-limit",
        type=int,
        default=500,
        help="Maximum number of traces to collect (default: 500)",
    )
    setup_parser.add_argument(
        "--dashboard",
        action="store_true",
        help="Launch the dashboard after setup+analysis (loads from <project-path>/scom_report).",
    )

    args = parser.parse_args(argv)

    if args.command == "run":
        settings_path = Path(args.settings)
        if not settings_path.exists():
            parser.error(f"settings file not found: {settings_path}")

        # Make settings path visible to all pipeline steps
        import os
        os.environ["BOUNDARY_ANALYZER_SETTINGS"] = str(settings_path)

        rc = _run_pipeline(skip_collect=bool(args.skip_collect))
        if rc != 0:
            return rc

        if args.dashboard:
            return _run_dashboard(
                data_dir=Path(str(args.data_dir)),
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
        )

        if rc != 0:
            return rc

        if args.dashboard:
            return _run_dashboard(data_dir=Path(str(args.project_path)) / "scom_report")

        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
