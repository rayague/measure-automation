from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError

logger = logging.getLogger(__name__)

_FORMAT_LABELS: dict[str, str] = {
    "jaeger": "Jaeger JSON",
    "zipkin": "Zipkin JSON",
    "otlp": "OpenTelemetry OTLP JSON",
    "locust": "Locust CSV (load-test statistics)",
    "nginx": "nginx / Apache access log",
    "w3c": "W3C Extended Log Format",
    "generic_sql": "Application log with HTTP + SQL patterns",
    "json_lines": "JSON Lines (structured log)",
}


def _generate_markdown_report(
    rank_df: pd.DataFrame,
    suspicious_df: pd.DataFrame,
    threshold: float,
) -> str:
    """Generate Markdown report from DataFrames."""

    total_services = len(rank_df)
    suspicious_services = len(suspicious_df)

    report = []

    # Use the threshold passed by the caller (already resolved in run_llm_demo.py)
    threshold_used = float(threshold)
    threshold_method = None
    if not rank_df.empty and "threshold_method" in rank_df.columns:
        threshold_method = str(rank_df["threshold_method"].iloc[0])

    # Header
    report.append("# Microservice Boundary Analysis Report\n")
    report.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    if threshold_method:
        report.append(f"**Threshold Method:** {threshold_method}\n")
    report.append(f"**SCOM Threshold Used:** {threshold_used}\n")

    scom_methods = []
    if "method" in rank_df.columns:
        scom_methods = sorted({str(m) for m in rank_df["method"].dropna().unique().tolist()})
    if scom_methods:
        report.append(f"**SCOM Method:** {', '.join(scom_methods)}\n")
    report.append("---\n")

    # Summary
    report.append("## Summary\n")
    report.append(f"- **Total Services:** {total_services}\n")
    report.append(f"- **Suspicious Services (SCOM < {threshold_used}):** {suspicious_services}\n")
    report.append(f"- **Safe Services (SCOM >= {threshold_used}):** {total_services - suspicious_services}\n")
    report.append("\n")

    # Suspicious services
    if not suspicious_df.empty:
        report.append("## Suspicious Services\n")
        report.append("These services have low cohesion. They may have a boundary problem.\n")
        report.append("\n")
        report.append("| Rank | Service | SCOM | Endpoints | Tables |\n")
        report.append("|------|---------|------|-----------|--------|\n")
        for _, row in suspicious_df.iterrows():
            report.append(f"| {row['rank']} | {row['service_name']} | {row['scom_score']:.4f} | {row['endpoints_count']} | {row['tables_count']} |\n")
        report.append("\n")

        report.append("### Why they are suspicious (simple English)\n")
        report.append("\n")
        for _, row in suspicious_df.iterrows():
            service_name = str(row.get("service_name", ""))
            scom_score = float(row.get("scom_score", 0.0))
            endpoints_count = int(row.get("endpoints_count", 0))
            tables_count = int(row.get("tables_count", 0))

            report.append(f"- **{service_name}**\n")
            report.append(f"  - SCOM is {scom_score:.4f}. This is below the threshold {threshold_used}.\n")
            report.append(f"  - This service has {endpoints_count} endpoints and {tables_count} tables/collections.\n")
            report.append("  - Low cohesion can mean the service does many different things.\n")
        report.append("\n")
    else:
        report.append("## Suspicious Services\n")
        report.append("No suspicious services found. All services have good cohesion.\n")
        report.append("\n")

    # All services ranking
    report.append("## Full Service Ranking\n")
    report.append("Services ranked by SCOM score (lowest first).\n")
    report.append("\n")

    has_weighted_col = "scom_score_weighted" in rank_df.columns
    if has_weighted_col:
        # Reproduce Table I / Table II format from the ICSA26 paper:
        # both unweighted (primary, Section III-C formula) and weighted (Section IV-B extension)
        report.append("| Rank | Service | SCOM (unweighted) | SCOM (weighted) | Endpoints | Tables | Suspicious |\n")
        report.append("|------|---------|-------------------|-----------------|-----------|--------|------------|\n")
        for _, row in rank_df.iterrows():
            suspicious_mark = "Yes" if row["is_suspicious"] else "No"
            w_val = row["scom_score_weighted"]
            w_str = f"{float(w_val):.4f}" if str(w_val) not in ("", "nan", "None") else "/"
            report.append(
                f"| {row['rank']} | {row['service_name']} | {row['scom_score']:.4f} "
                f"| {w_str} | {row['endpoints_count']} | {row['tables_count']} | {suspicious_mark} |\n"
            )
    else:
        report.append("| Rank | Service | SCOM | Endpoints | Tables | Suspicious |\n")
        report.append("|------|---------|------|-----------|--------|------------|\n")
        for _, row in rank_df.iterrows():
            suspicious_mark = "Yes" if row["is_suspicious"] else "No"
            report.append(
                f"| {row['rank']} | {row['service_name']} | {row['scom_score']:.4f} | {row['endpoints_count']} | {row['tables_count']} | {suspicious_mark} |\n"
            )
    report.append("\n")

    # Notes
    report.append("## Notes\n")
    if scom_methods:
        report.append(f"- SCOM (Service Cohesion Measure) method: {', '.join(scom_methods)}.\n")
    else:
        report.append("- SCOM (Service Cohesion Measure) method is recorded in service_scom.csv.\n")
    report.append("- A service is suspicious if its SCOM score is below the threshold.\n")
    report.append("- Low cohesion may indicate that the service boundary is not optimal.\n")
    report.append("\n")

    return "".join(report)


def _ingestion_section(summary_path: Path) -> str:
    """Build the ## Data Sources Markdown section from an ingestion summary JSON."""
    try:
        summary: dict[str, Any] = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    totals: dict[str, Any] = summary.get("totals", {})
    sources: list[dict[str, Any]] = summary.get("sources", [])
    errors: list[dict[str, Any]] = summary.get("errors", [])

    lines: list[str] = []
    lines.append("## Data Sources\n")
    lines.append(f"- **Files parsed:** {totals.get('files_parsed', '?')} / {totals.get('files_seen', '?')}\n")
    lines.append(f"- **Total spans:** {totals.get('total_spans', '?')}\n")
    lines.append(f"- **HTTP spans:** {totals.get('http_spans', '?')}\n")
    lines.append(f"- **DB spans:** {totals.get('db_spans', '?')}\n")

    corr = totals.get("correlated_db_spans", 0)
    db = totals.get("db_spans", 0)
    if db:
        pct = f"{corr / db * 100:.0f}%" if db else "0%"
        lines.append(f"- **DB spans with HTTP parent (correlated):** {corr} / {db} ({pct})\n")
    else:
        lines.append("- **DB spans:** none — SCOM uses heuristic path-based table inference\n")

    services = totals.get("services", [])
    if services:
        lines.append(f"- **Services detected:** {', '.join(services)}\n")
    lines.append("\n")

    if sources:
        lines.append("### Per-file breakdown\n")
        lines.append("| File | Format | Confidence | DB info | Correlated |\n")
        lines.append("|------|--------|------------|---------|------------|\n")
        for src in sources:
            fname = Path(str(src.get("source", ""))).name
            fmt = _FORMAT_LABELS.get(str(src.get("format", "")), str(src.get("format", "?")))
            conf = f"{float(src.get('confidence', 0)) * 100:.0f}%"
            has_db = "✔" if src.get("has_db_info") else "✗"
            has_corr = "✔" if src.get("has_trace_correlation") else "✗"
            lines.append(f"| `{fname}` | {fmt} | {conf} | {has_db} | {has_corr} |\n")
        lines.append("\n")

        for src in sources:
            for w in src.get("warnings", []):
                lines.append(f"> ⚠ `{Path(str(src.get('source', ''))).name}`: {w}\n")

    if errors:
        lines.append("### Parse errors\n")
        for err in errors:
            lines.append(f"- `{Path(str(err.get('source', ''))).name}`: {err.get('error', '?')}\n")
        lines.append("\n")

    return "".join(lines)


def generate_report(
    rank_path: Path,
    suspicious_path: Path,
    output_path: Path,
    threshold: float = 0.5,
    ingestion_summary_path: Path | None = None,
) -> None:
    """Generate and save the Markdown report."""

    if not rank_path.exists():
        raise FileNotFoundError(f"Rank file not found: {rank_path}")

    rank_df = pd.read_csv(rank_path)

    suspicious_df = pd.DataFrame()
    if suspicious_path.exists() and suspicious_path.stat().st_size > 0:
        try:
            suspicious_df = pd.read_csv(suspicious_path)
        except EmptyDataError:
            suspicious_df = pd.DataFrame()

    report_content = _generate_markdown_report(rank_df, suspicious_df, threshold)

    # Append data-source provenance section when ingestion metadata is available
    if ingestion_summary_path and ingestion_summary_path.exists():
        report_content += "\n" + _ingestion_section(ingestion_summary_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        f.write(report_content)
