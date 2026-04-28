from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd


def _generate_markdown_report(
    rank_df: pd.DataFrame,
    suspicious_df: pd.DataFrame,
    threshold: float,
) -> str:
    """Generate Markdown report from DataFrames."""
    
    total_services = len(rank_df)
    suspicious_services = len(suspicious_df)
    
    report = []
    
    # Header
    report.append("# Microservice Boundary Analysis Report\n")
    report.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    report.append(f"**SCOM Threshold:** {threshold}\n")

    scom_methods = []
    if "method" in rank_df.columns:
        scom_methods = sorted({str(m) for m in rank_df["method"].dropna().unique().tolist()})
    if scom_methods:
        report.append(f"**SCOM Method:** {', '.join(scom_methods)}\n")
    report.append("---\n")
    
    # Summary
    report.append("## Summary\n")
    report.append(f"- **Total Services:** {total_services}\n")
    report.append(f"- **Suspicious Services (SCOM < {threshold}):** {suspicious_services}\n")
    report.append(f"- **Safe Services (SCOM >= {threshold}):** {total_services - suspicious_services}\n")
    report.append("\n")
    
    # Suspicious services
    if not suspicious_df.empty:
        report.append("## Suspicious Services\n")
        report.append("These services have low cohesion and may have problematic boundaries.\n")
        report.append("\n")
        report.append("| Rank | Service | SCOM | Endpoints | Tables |\n")
        report.append("|------|---------|------|-----------|--------|\n")
        for _, row in suspicious_df.iterrows():
            report.append(f"| {row['rank']} | {row['service_name']} | {row['scom_score']:.4f} | {row['endpoints_count']} | {row['tables_count']} |\n")
        report.append("\n")
    else:
        report.append("## Suspicious Services\n")
        report.append("No suspicious services found. All services have good cohesion.\n")
        report.append("\n")
    
    # All services ranking
    report.append("## Full Service Ranking\n")
    report.append("Services ranked by SCOM score (lowest first).\n")
    report.append("\n")
    report.append("| Rank | Service | SCOM | Endpoints | Tables | Suspicious |\n")
    report.append("|------|---------|------|-----------|--------|------------|\n")
    for _, row in rank_df.iterrows():
        suspicious_mark = "Yes" if row["is_suspicious"] else "No"
        report.append(f"| {row['rank']} | {row['service_name']} | {row['scom_score']:.4f} | {row['endpoints_count']} | {row['tables_count']} | {suspicious_mark} |\n")
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


def generate_report(
    rank_path: Path,
    suspicious_path: Path,
    output_path: Path,
    threshold: float = 0.5,
) -> None:
    """Generate and save the Markdown report."""
    
    if not rank_path.exists():
        raise FileNotFoundError(f"Rank file not found: {rank_path}")
    
    rank_df = pd.read_csv(rank_path)
    
    suspicious_df = pd.DataFrame()
    if suspicious_path.exists():
        suspicious_df = pd.read_csv(suspicious_path)
    
    report_content = _generate_markdown_report(rank_df, suspicious_df, threshold)
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        f.write(report_content)
