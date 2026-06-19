from __future__ import annotations

import concurrent.futures
import json
import logging
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from boundary_analyzer.auto.deploy import (
    DeploymentResult,
    cleanup_docker_compose,
    cleanup_services,
    deploy_docker_compose,
    deploy_services,
    docker_available,
    find_otel_dockerfiles,
    start_jaeger,
    stop_jaeger,
)
from boundary_analyzer.auto.discover import discover_project
from boundary_analyzer.auto.errors import AnalysisError, ErrorCode, unexpected
from boundary_analyzer.auto.instrumentation_marker import (
    InstrumentationMarker,
    MarkerArtifact,
    check_stale_instrumentation,
    cleanup_instrumentation,
    cleanup_orphans,
    read_marker,
    write_marker,
)
from boundary_analyzer.auto.models import (
    AnalysisReport,
    Endpoint,
    ProjectInfo,
    StepResult,
    TrafficResult,
)
from boundary_analyzer.auto.traffic import (
    TrafficConfig,
    discover_endpoints_ast,
    discover_endpoints_openapi,
    generate_traffic,
)
from boundary_analyzer.llm.instrumentation import generate_instrumentation
from boundary_analyzer.pipeline.run_pipeline import run_pipeline

"""Orchestrator for fully automatic microservice boundary analysis.

Handles the full lifecycle: discover, deploy, traffic generation, trace
collection, SCOM computation, and cleanup.
"""

logger = logging.getLogger(__name__)
_console = Console()


@dataclass
class FullConfig:
    """Configuration for a full automatic analysis run."""

    project_dir: Path = Path(".")
    duration: int = 60
    workers: int = 5
    jaeger_port: int = 16686
    otlp_port: int = 4318
    no_clean: bool = False
    llm: bool = False
    verbose: bool = False
    skip_no_db: bool = True
    exclude_services: list[str] | None = None
    lookback_minutes: int = 10
    reset_jaeger: bool = False


def _uses_docker_compose(project: ProjectInfo) -> bool:
    return any(s.deployment == "docker-compose" for s in project.services)


def _export_jaeger_traces(
    output_dir: Path,
    service_filter: list[str] | None = None,
    jaeger_port: int = 16686,
    max_traces: int = 100,
    lookback_minutes: int = 10,
    start_time: float | None = None,
) -> int:
    base_url = f"http://127.0.0.1:{jaeger_port}"
    services_url = f"{base_url}/api/services"

    try:
        resp = requests.get(services_url, timeout=10)
        resp.raise_for_status()
        services = resp.json().get("data") or []
    except requests.RequestException as e:
        raise AnalysisError(
            code=ErrorCode.JAEGER_UNREACHABLE,
            _override_detail=str(e),
            recoverable=True,
        )

    all_jaeger_services = list(services)
    if service_filter:
        service_filter_lower = {s.lower() for s in service_filter}
        matched = []
        unmatched = []
        for s in services:
            if s in service_filter or s.lower() in service_filter_lower:
                matched.append(s)
            else:
                unmatched.append(s)
        if unmatched:
            logger.warning(
                "Service name mismatch between discovery and Jaeger: "
                "discovered=%s, not found in Jaeger=%s. "
                "Check that OTEL_SERVICE_NAME env var matches the service name in docker-compose.yml.",
                service_filter,
                unmatched,
            )
        services = matched

    if not services:
        raise AnalysisError(
            code=ErrorCode.NO_TRACES,
            _override_detail=f"No services found in Jaeger matching {service_filter}. "
            f"Available in Jaeger: {all_jaeger_services}. "
            "Check OTEL_SERVICE_NAME env var matches docker-compose service names.",
            recoverable=True,
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    total_traces = 0

    for svc in services:
        params = {"service": svc, "limit": str(max_traces), "lookback": f"{lookback_minutes}m"}
        try:
            resp = requests.get(f"{base_url}/api/traces", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise AnalysisError(
                code=ErrorCode.NO_TRACES,
                scope=svc,
                _override_detail=str(e),
                recoverable=True,
            )

        traces = data.get("data", [])
        if start_time is not None:
            # Convert start_time (seconds) to microseconds, subtracting 10 seconds safety margin
            start_time_us = int((start_time - 10.0) * 1000000)
            filtered_traces = []
            for t in traces:
                spans = t.get("spans") or []
                if any(s.get("startTime", 0) >= start_time_us for s in spans):
                    filtered_traces.append(t)
            traces = filtered_traces
            data["data"] = traces

        if not traces:
            continue

        safe_name = svc.replace("/", "_").replace("\\", "_").replace(" ", "_")
        out_file = output_dir / f"jaeger_traces_{safe_name}.json"
        payload = {
            "export_meta": {
                "jaeger_base_url": base_url,
                "service": svc,
                "export_unix_time": int(time.time()),
            },
            "jaeger_response": data,
        }
        with out_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        total_traces += len(traces)

    return total_traces


def _discover_endpoints_for_service(
    service_port: int,
    project_info: ProjectInfo,
    service_info: Any,
    config: FullConfig,
) -> tuple[list[Endpoint], str]:
    traffic_cfg = TrafficConfig(
        duration=config.duration,
        workers=config.workers,
    )

    endpoints = discover_endpoints_openapi("127.0.0.1", service_port, traffic_cfg)
    source = "OpenAPI"

    if not endpoints:
        service_dir = project_info.root_dir
        if service_info.entry_points:
            ep_path = Path(service_info.entry_points[0].path)
            if ep_path.is_absolute():
                service_dir = ep_path.parent
            else:
                service_dir = project_info.root_dir / ep_path.parent
        endpoints = discover_endpoints_ast(service_info, service_dir)
        source = "AST"

    return endpoints, source


def _build_endpoint_map(
    project_info: ProjectInfo,
    deployment: DeploymentResult,
    config: FullConfig,
) -> dict[str, tuple[list[Endpoint], str]]:
    endpoint_map: dict[str, tuple[list[Endpoint], str]] = {}

    for ds in deployment.ready_services:
        port = ds.port or ds.service.port or 0
        if port <= 0:
            continue
        eps, source = _discover_endpoints_for_service(port, project_info, ds.service, config)
        endpoint_map[ds.service.name] = (eps, source)

    return endpoint_map


def _generate_traffic_for_all(
    deployment: DeploymentResult,
    endpoint_map: dict[str, tuple[list[Endpoint], str]],
    config: FullConfig,
) -> dict[str, TrafficResult]:
    results: dict[str, TrafficResult] = {}

    for ds in deployment.ready_services:
        eps, source = endpoint_map.get(ds.service.name, ([], "none"))  # type: ignore[arg-type]
        traffic_cfg = TrafficConfig(
            duration=config.duration,
            workers=config.workers,
        )

        try:
            result = generate_traffic(ds.service, traffic_cfg, endpoints=eps)
            results[ds.service.name] = result
        except AnalysisError as e:
            results[ds.service.name] = TrafficResult(
                total_requests=0,
                errors=[e],
            )

    return results


def _build_scom_table(scom_df: Any) -> Table | None:
    if scom_df is None or (hasattr(scom_df, "empty") and scom_df.empty):
        return None

    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
    table.add_column("Service", width=20)
    table.add_column("Endpoints", justify="right")
    table.add_column("Tables", justify="right")
    table.add_column("SCOM (unweighted)", justify="right")
    table.add_column("SCOM (weighted)", justify="right")

    for _, row in scom_df.iterrows():
        name = str(row.get("service_name", ""))
        ep_count = str(row.get("endpoints_count", "?"))
        tbl_count = str(row.get("tables_count", "?"))
        score = row.get("scom_score", "/")
        method = str(row.get("method", ""))

        if pd.isna(score) or score == "/" or (isinstance(score, float) and score < 0):
            score_str = "  /  "
        else:
            score_str = f"{float(score):.4f}" if isinstance(score, (int, float)) else str(score)

        unweighted = score_str if method == "unweighted" or not method else score_str
        weighted = score_str if method == "weighted" else "  -  "

        suspicious = isinstance(score, (int, float)) and not pd.isna(score) and float(score) < 0.3
        style = "red" if suspicious else "white"
        table.add_row(name, ep_count, tbl_count, unweighted, weighted, style=style)

    return table


_STEP_ICONS = {"v": "✔", "*": "●", "!": "⚠", "X": "✘"}
_STEP_STYLES = {"v": "green", "*": "cyan", "!": "yellow", "X": "red"}


def _print_step(icon: str, message: str) -> None:
    style = _STEP_STYLES.get(icon, "white")
    display = _STEP_ICONS.get(icon, icon) if icon in _STEP_ICONS else icon
    _console.print(f"  [{style}]{display}[/] {message}")


def _status_ctx(message: str):
    return _console.status(f"[cyan]{message}[/]")


def _print_final_report(report: AnalysisReport) -> None:
    if report.all_success and not report.has_any_warnings:
        _console.print()
        _console.print("  [bold green]MBA full completed successfully.[/]")
        if report.report_path:
            _console.print(f"  Report: [cyan]{report.report_path}[/]")
        return

    _console.print()
    panel = Panel(
        Text("MBA Full - Complete Report", style="bold white"),
        border_style="cyan",
        padding=(0, 2),
    )
    _console.print(panel)

    project = report.project
    _console.print()
    detail_table = Table(box=None, padding=(0, 2))
    detail_table.add_column(style="bold")
    detail_table.add_column()
    detail_table.add_row("Project ", str(project.root_dir))
    detail_table.add_row("Language", f"{project.language.capitalize()} ({project.framework})")
    detail_table.add_row("Services", str(len(project.services)))
    _console.print(detail_table)

    _console.print()
    _console.print("  [bold]Steps:[/]")
    for step_name in ["discover", "deploy", "traffic", "collect", "analyze", "cleanup"]:
        s = report.step(step_name)
        if s is None:
            _console.print(f"    - [dim]{step_name.capitalize()} skipped[/]")
            continue
        icon_display = _STEP_ICONS.get(s.status_icon, s.status_icon)
        icon_style = _STEP_STYLES.get(s.status_icon, "white")
        msg = s.message or step_name.capitalize()
        _console.print(f"  [{icon_style}]{icon_display}[/]  [bold]{step_name.capitalize()}[/] {msg}")

    all_errors = report.all_errors()
    all_warnings = report.all_warnings()

    if all_errors:
        _console.print()
        _console.print("  [bold red]Errors:[/]")
        for err in all_errors:
            _console.print(f"  [red][{err.code_str}][/] {err.message}")
            if err.fix:
                _console.print(f"    [yellow]Fix:[/] {err.fix}")
            _console.print()

    if all_warnings:
        _console.print()
        _console.print("  [bold yellow]Warnings:[/]")
        for warn in all_warnings:
            _console.print(f"  [yellow][{warn.code_str}][/] {warn.message}")
            if warn.scope:
                _console.print(f"    Scope: {warn.scope}")
            _console.print()

    if report.scom_results:
        _console.print()
        _console.print("  [bold]SCOM Results:[/]")
        df = report.scom_results.get("scom_df")
        if df is not None:
            t = _build_scom_table(df)
            if t is not None:
                _console.print(t)

    if report.report_path:
        _console.print(f"  Report: [cyan]{report.report_path}[/]")

    _console.print()
    _console.print(f"  Duration: [bold]{report.total_duration_seconds:.1f}[/]s")


def _collect_marker_artifacts(project: ProjectInfo, backup_artifacts: list[MarkerArtifact]) -> list[MarkerArtifact]:
    """Collect all artifacts generated during instrumentation + deploy."""
    artifacts = list(backup_artifacts)

    override_path = project.root_dir / ".mba-compose-override.yml"
    if override_path.exists():
        artifacts.append(
            MarkerArtifact(
                type="compose_override",
                path=".mba-compose-override.yml",
            )
        )

    for otel_df in find_otel_dockerfiles(project.root_dir):
        try:
            df_rel = otel_df.relative_to(project.root_dir).as_posix()
            artifacts.append(
                MarkerArtifact(
                    type="dockerfile_override",
                    path=df_rel,
                )
            )
        except ValueError:
            artifacts.append(
                MarkerArtifact(
                    type="dockerfile_override",
                    path=str(otel_df),
                )
            )

    return artifacts


def _llm_instrument_services(project: ProjectInfo, config: FullConfig) -> list[MarkerArtifact]:
    """Use LLM to generate OTel instrumentation code for each Python service.

    Tries OpenRouter first (if API key set), then local Ollama.
    Falls back to existing Dockerfile patching if all LLM options fail
    or return invalid code.

    Returns a list of backup artifacts created for the instrumentation marker.
    """
    import os

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if api_key:
        _print_step("*", "OpenRouter API key detected — will fall back to local Ollama if needed")
    else:
        _print_step("*", "No OpenRouter API key — trying local Ollama only")

    backup_artifacts: list[MarkerArtifact] = []

    for svc in project.services:
        if svc.language != "python":
            continue

        _print_step("*", f"Instrumenting {svc.name} via LLM...")

        svc_root = svc.entry_points[0].path.parent if svc.entry_points else project.root_dir
        jaeger_host = "env" if _uses_docker_compose(project) else "127.0.0.1"
        try:
            code = generate_instrumentation(
                project_path=svc_root,
                jaeger_host=jaeger_host,
                jaeger_port=4318,
            )
        except Exception as e:
            logger.warning("LLM instrumentation failed for %s: %s", svc.name, e)
            _print_step("!", f"LLM exception for {svc.name}: {e} — using static template")
            continue

        if code is None:
            _print_step("!", f"All LLM options failed for {svc.name} — using static template")
            _print_step("*", "Tip: Install Ollama (ollama.com) and pull qwen2.5-coder for local LLM support")
            continue

        try:
            compile(code, f"{svc.name}_otel.py", "exec")
        except SyntaxError as e:
            _print_step("!", f"LLM code invalid for {svc.name}: {e} — using static template")
            continue

        entry = svc.entry_points[0] if svc.entry_points else None
        if entry is None:
            _print_step("!", f"No entry point for {svc.name} — cannot apply")
            continue

        entry_path = entry.path
        if not entry_path.exists():
            _print_step("!", f"Entry point not found: {entry_path}")
            continue

        import shutil

        backup_path = entry_path.with_suffix(entry_path.suffix + ".mba_bak")
        if backup_path.exists():
            # Create numbered backup to avoid overwriting previous backup
            counter = 1
            while True:
                numbered = entry_path.with_suffix(entry_path.suffix + f".mba_bak.{counter}")
                if not numbered.exists():
                    backup_path = numbered
                    break
                counter += 1
        shutil.copy2(entry_path, backup_path)

        entry_path.write_text(code, encoding="utf-8")
        _print_step("v", f"{svc.name} → instrumented via LLM")

        try:
            orig_rel = entry_path.relative_to(project.root_dir).as_posix()
            bak_rel = backup_path.relative_to(project.root_dir).as_posix()
            backup_artifacts.append(
                MarkerArtifact(
                    type="backup",
                    original=orig_rel,
                    backup=bak_rel,
                )
            )
        except ValueError:
            pass

    return backup_artifacts


def _ensure_docker() -> None:
    """Proactively check Docker availability with visible feedback.

    Raises AnalysisError if Docker is not available after the timeout.
    """
    if docker_available():
        return

    _print_step("!", "Docker daemon not responding yet — waiting up to 60 s...")
    deadline = time.time() + 60
    attempts = 0
    while time.time() < deadline:
        time.sleep(5)
        attempts += 1
        if docker_available():
            _print_step("v", f"Docker daemon ready after ~{attempts * 5}s")
            return

    raise AnalysisError(
        code=ErrorCode.DOCKER_DAEMON_DOWN,
        _override_detail=f"Docker daemon not ready after ~{attempts * 5}s.",
        recoverable=True,
    )


def _wait_for_jaeger_traces(
    jaeger_port: int = 16686,
    service_names: list[str] | None = None,
    poll_interval: int = 2,
    timeout: int = 30,
) -> None:
    """Wait for Jaeger to have traces for at least one service, with timeout.

    Replaces a hardcoded ``time.sleep(5)`` with an adaptive poll so that
    fast services aren't delayed unnecessarily while slow ones still get
    enough time to flush.
    """
    import requests as _requests

    deadline = time.time() + timeout
    waited = 0
    while time.time() < deadline:
        for svc in service_names or []:
            try:
                resp = _requests.get(
                    f"http://127.0.0.1:{jaeger_port}/api/traces",
                    params={"service": svc, "limit": "1", "lookback": "5m"},
                    timeout=5,
                )
                if resp.status_code == 200:
                    traces = resp.json().get("data", [])
                    if traces:
                        _print_step("v", f"Traces available for {svc} after ~{waited}s")
                        return
            except _requests.RequestException:
                pass
        time.sleep(poll_interval)
        waited += poll_interval
    logger.warning(
        "No Jaeger traces found after %ds — proceeding anyway. Increase --lookback or verify OTEL export.",
        timeout,
    )


def run_full_analysis(config: FullConfig) -> AnalysisReport:
    """Run the complete automatic analysis pipeline from discovery through cleanup.

    Args:
        config: Configuration for the analysis run.

    Returns:
        AnalysisReport detailing success/failure of each step.
    """
    report = AnalysisReport(
        project=ProjectInfo(services=[], root_dir=config.project_dir),
    )
    start_time = time.time()
    deployment: DeploymentResult | None = None

    if cleanup_orphans(config.project_dir):
        _print_step("v", "Cleaned up orphan artifacts from a previous version")

    if check_stale_instrumentation(config.project_dir):
        _print_step("v", "Cleaned up stale instrumentation from a previous version")

    try:
        _print_step("*", "Discovering project...")
        project = discover_project(config.project_dir)
        report.project = project
        _print_step("v", f"Detected {len(project.services)} services: {[s.name for s in project.services]}")
        report.steps["discover"] = StepResult(
            success=True,
            step_name="discover",
            message=f"Detected {len(project.services)} service(s)",
            data=project,
        )

    except AnalysisError as e:
        report.steps["discover"] = StepResult(
            success=False,
            step_name="discover",
            message=str(e.message),
            errors=[e],
        )
        report.total_duration_seconds = time.time() - start_time
        _print_final_report(report)
        return report
    except Exception as e:
        logger.exception("Unexpected error during discovery: %s", e)
        err = unexpected("discover", e)
        report.steps["discover"] = StepResult(
            success=False,
            step_name="discover",
            message="Unexpected error during discovery",
            errors=[err],
        )
        report.total_duration_seconds = time.time() - start_time
        _print_final_report(report)
        return report

    # ── LLM Instrumentation Step (optional) ─────────────────────────────
    backup_artifacts: list[MarkerArtifact] = []
    if config.llm:
        backup_artifacts = _llm_instrument_services(project, config)

    if _uses_docker_compose(project):
        _ensure_docker()
        _print_step("*", "Cleaning up previous Docker Compose project...")
        cleanup_docker_compose(project)
        if config.reset_jaeger:
            _reset_jaeger_container(config.jaeger_port, config.otlp_port)
        try:
            _print_step("*", "Deploying via Docker Compose...")
            deployment = deploy_docker_compose(
                project=project,
                jaeger_port=config.jaeger_port,
                otlp_port=config.otlp_port,
            )
            jaeger_port = config.jaeger_port

            ready_count = len(deployment.ready_services)
            total_count = len(deployment.services)
            if ready_count == 0:
                raise AnalysisError(
                    code=ErrorCode.ALL_SERVICES_FAILED,
                    _override_detail=f"0/{total_count} services started.",
                    recoverable=False,
                )

            report.steps["deploy"] = StepResult(
                success=True,
                step_name="deploy",
                message=f"{ready_count}/{total_count} services ready",
                data=deployment,
            )
            _print_step("v", f"{ready_count}/{total_count} services ready")

            artifacts = _collect_marker_artifacts(project, backup_artifacts)
            if artifacts:
                write_marker(
                    project.root_dir,
                    InstrumentationMarker(artifacts=artifacts),
                )
        except AnalysisError as e:
            report.steps["deploy"] = StepResult(
                success=False,
                step_name="deploy",
                message=str(e.message),
                errors=[e],
            )
            report.total_duration_seconds = time.time() - start_time
            _try_cleanup(report, project, deployment)
            _print_final_report(report)
            return report
    else:
        _ensure_docker()
        if config.reset_jaeger:
            _reset_jaeger_container(config.jaeger_port, config.otlp_port)
        try:
            _print_step("*", "Starting Jaeger...")
            jaeger_port = start_jaeger(
                jaeger_port=config.jaeger_port,
                otlp_port=config.otlp_port,
            )
            report.steps["deploy"] = StepResult(success=True, step_name="deploy")
            _print_step("v", f"Jaeger started on port {jaeger_port}")
        except AnalysisError as e:
            report.steps["deploy"] = StepResult(
                success=False,
                step_name="deploy",
                message=str(e.message),
                errors=[e],
            )
            report.total_duration_seconds = time.time() - start_time
            _print_final_report(report)
            return report

        try:
            _print_step("*", "Deploying services...")
            deployment = deploy_services(
                project=project,
                otlp_endpoint=f"http://localhost:{config.otlp_port}",
            )

            ready_count = len(deployment.ready_services)
            total_count = len(deployment.services)
            if ready_count == 0:
                raise AnalysisError(
                    code=ErrorCode.ALL_SERVICES_FAILED,
                    _override_detail=f"0/{total_count} services started.",
                    recoverable=False,
                )

            report.steps["deploy"] = StepResult(
                success=True,
                step_name="deploy",
                message=f"{ready_count}/{total_count} services ready",
                data=deployment,
            )
            _print_step("v", f"{ready_count}/{total_count} services ready")

            artifacts = _collect_marker_artifacts(project, backup_artifacts)
            if artifacts:
                write_marker(
                    project.root_dir,
                    InstrumentationMarker(artifacts=artifacts),
                )
        except AnalysisError as e:
            report.steps["deploy"] = StepResult(
                success=False,
                step_name="deploy",
                message=str(e.message),
                errors=[e],
            )
            report.total_duration_seconds = time.time() - start_time
            _try_cleanup(report, project, deployment)
            _print_final_report(report)
            return report

    try:
        _print_step("*", "Discovering endpoints...")
        endpoint_map = _build_endpoint_map(project, deployment, config)
        total_eps = sum(len(eps) for eps, _ in endpoint_map.values())
        _print_step("v", f"Discovered {total_eps} endpoints across {len(endpoint_map)} service(s)")
    except AnalysisError as e:
        report.steps["traffic"] = StepResult(
            success=False,
            step_name="traffic",
            message=str(e.message),
            errors=[e],
        )
        _try_cleanup(report, project, deployment)
        _print_final_report(report)
        return report

    try:
        _print_step("*", "Generating traffic...")
        traffic_results = _generate_traffic_for_all(deployment, endpoint_map, config)

        total_req = sum(r.total_requests for r in traffic_results.values())
        total_ok = sum(r.successful_requests for r in traffic_results.values())
        total_fail = sum(r.failed_requests for r in traffic_results.values())
        any_none_succeeded = any(r.none_succeeded for r in traffic_results.values())

        traffic_step = StepResult(
            success=not any_none_succeeded,
            step_name="traffic",
            message=f"{total_req} requests ({total_ok} ok, {total_fail} failed)",
            data=traffic_results,
        )

        ok_endpoints = sum(r.endpoints_ok for r in traffic_results.values())
        tested_endpoints = sum(r.endpoints_tested for r in traffic_results.values())

        if any_none_succeeded and tested_endpoints > 0:
            traffic_step.success = False
            traffic_step.errors.append(
                AnalysisError(
                    code=ErrorCode.ALL_ENDPOINTS_FAILED,
                    _override_detail=f"0/{tested_endpoints} endpoints succeeded across all services.",
                    recoverable=False,
                )
            )
            report.steps["traffic"] = traffic_step
            _try_cleanup(report, project, deployment)
            _print_final_report(report)
            return report

        if ok_endpoints < tested_endpoints and ok_endpoints > 0:
            traffic_step.warnings.append(
                AnalysisError(
                    code=ErrorCode.PARTIAL_ENDPOINTS_FAILED,
                    scope=f"{ok_endpoints}/{tested_endpoints} endpoints ok",
                    _override_detail="Some endpoints failed during traffic generation.",
                    recoverable=True,
                )
            )

        report.steps["traffic"] = traffic_step
        icon = "v" if traffic_step.success else "!"
        _print_step(icon, f"{total_req} requests ({total_ok} ok)")
    except AnalysisError as e:
        report.steps["traffic"] = StepResult(
            success=False,
            step_name="traffic",
            message=str(e.message),
            errors=[e],
        )
        _try_cleanup(report, project, deployment)
        _print_final_report(report)
        return report

    try:
        # Wait for Jaeger to flush pending spans — poll until traces appear or timeout
        _wait_for_jaeger_traces(
            config.jaeger_port,
            service_names=[s.name for s in project.services],
            poll_interval=2,
            timeout=30,
        )
        _print_step("*", "Collecting traces from Jaeger...")
        traces_dir = Path(tempfile.mkdtemp(prefix="mba_traces_"))
        service_names = [s.name for s in project.services]
        trace_count = _export_jaeger_traces(
            traces_dir,
            service_filter=service_names,
            jaeger_port=config.jaeger_port,
            lookback_minutes=config.lookback_minutes,
            start_time=start_time,
        )
        if trace_count == 0:
            raise AnalysisError(
                code=ErrorCode.NO_TRACES,
                _override_detail=f"Exported 0 traces for services: {', '.join(service_names)}",
                recoverable=True,
            )
        report.steps["collect"] = StepResult(
            success=True,
            step_name="collect",
            message=f"{trace_count} traces exported",
            data=traces_dir,
        )
        _print_step("v", f"{trace_count} traces exported")
    except AnalysisError as e:
        report.steps["collect"] = StepResult(
            success=False,
            step_name="collect",
            message=str(e.message),
            errors=[e],
        )
        _try_cleanup(report, project, deployment)
        _print_final_report(report)
        return report

    try:
        _print_step("*", "Running SCOM pipeline...")
        output_dir = Path(tempfile.mkdtemp(prefix="mba_scom_"))

        pipeline_timeout = 600
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                run_pipeline,
                traces=traces_dir,
                output_dir=output_dir,
                scom_method="weighted",
                threshold_method="percentile",
                threshold_percentile=25.0,
                fixed_threshold=0.5,
                exclude_services=config.exclude_services,
                exclude_health_routes=True,
                exclude_http_client_spans=True,
                exclude_unknown_endpoint=True,
                skip_no_db_services=config.skip_no_db,
            )
            rc = future.result(timeout=pipeline_timeout)

        scom_csv = output_dir / "processed" / "service_scom.csv"
        rank_csv = output_dir / "processed" / "service_rank.csv"
        mapping_csv = output_dir / "interim" / "endpoint_table_map.csv"
        report_path = output_dir / "report.md"

        scom_df = pd.read_csv(scom_csv) if scom_csv.exists() else pd.DataFrame()
        rank_df = pd.read_csv(rank_csv) if rank_csv.exists() else pd.DataFrame()
        mapping_df = pd.read_csv(mapping_csv) if mapping_csv.exists() else pd.DataFrame()

        report.scom_results = {"scom_df": scom_df, "rank_df": rank_df, "mapping_df": mapping_df}
        report.report_path = report_path

        analyze_success = rc == 0 and not scom_df.empty
        report.steps["analyze"] = StepResult(
            success=analyze_success,
            step_name="analyze",
            message=f"SCOM computed for {len(scom_df)} service(s)" if not scom_df.empty else "No SCOM data",
            data={"rc": rc},
        )
        _print_step("v", f"SCOM computed for {len(scom_df)} service(s)")
    except Exception as e:
        logger.exception("Pipeline failed during analyze step: %s", e)
        report.steps["analyze"] = StepResult(
            success=False,
            step_name="analyze",
            message="Pipeline failed",
            errors=[unexpected("analyze", e)],
        )
    finally:
        # Clean up traces temp directory
        if traces_dir and traces_dir.exists():
            try:
                shutil.rmtree(traces_dir)
            except OSError as rm_err:
                logger.warning("Failed to clean up temp dir %s: %s", traces_dir, rm_err)

        # Clean up output temp directory only if SCOM analyze failed
        if not report.steps.get("analyze") or not report.steps["analyze"].success:
            if output_dir and output_dir.exists():
                try:
                    shutil.rmtree(output_dir)
                except OSError as rm_err:
                    logger.warning("Failed to clean up temp dir %s: %s", output_dir, rm_err)

    if config.no_clean and deployment:
        report.steps.setdefault(
            "cleanup",
            StepResult(
                success=True,
                step_name="cleanup",
                message="Skipped (--no-clean)",
            ),
        )
    else:
        _try_cleanup(report, project, deployment)

    if not report.steps.get("cleanup"):
        report.steps["cleanup"] = StepResult(
            success=True,
            step_name="cleanup",
            message="Done",
        )

    report.total_duration_seconds = time.time() - start_time
    _print_final_report(report)
    return report


def _try_cleanup(report: AnalysisReport, project: ProjectInfo, deployment: DeploymentResult | None) -> None:
    errors: list[AnalysisError] = []

    marker = read_marker(project.root_dir)

    try:
        if _uses_docker_compose(project):
            errors.extend(cleanup_docker_compose(project))
        else:
            if deployment:
                errors.extend(cleanup_services(deployment))
            try:
                stop_jaeger()
            except AnalysisError as e:
                errors.append(e)

        # Force-remove any leftover Jaeger container from a previous crash
        import subprocess

        try:
            subprocess.run(
                ["docker", "rm", "-f", "mba-jaeger"],
                capture_output=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    except KeyboardInterrupt:
        _print_step("!", "Cleanup interrupted by user")
        errors.append(
            AnalysisError(
                code=ErrorCode.PROCESS_KILL_FAILED,
                scope="cleanup",
                _override_detail="Interrupted by user during cleanup.",
            )
        )

    if marker:
        cleanup_instrumentation(project.root_dir, marker)

    cleanup_step = StepResult(
        success=len(errors) == 0,
        step_name="cleanup",
        message="Done with warnings" if errors else "Done",
        errors=errors,
    )
    report.steps["cleanup"] = cleanup_step


def _reset_jaeger_container(jaeger_port: int = 16686, otlp_port: int = 4318) -> None:
    """Stop and remove the existing Jaeger container, then start a fresh one.

    Ensures no old traces from previous runs pollute the current analysis.
    """
    import subprocess

    from boundary_analyzer.auto.deploy import start_jaeger

    container_name = "mba-jaeger"
    _print_step("*", f"Resetting Jaeger container ({container_name})...")
    zombie_ids = set()
    try:
        ps1 = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={container_name}", "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for cid in ps1.stdout.splitlines():
            if cid.strip():
                zombie_ids.add(cid.strip())

        ps2 = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"publish={jaeger_port}", "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for cid in ps2.stdout.splitlines():
            if cid.strip():
                zombie_ids.add(cid.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    if zombie_ids:
        try:
            subprocess.run(
                ["docker", "rm", "-f"] + list(zombie_ids),
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            pass

    start_jaeger(jaeger_port=jaeger_port, otlp_port=otlp_port)
