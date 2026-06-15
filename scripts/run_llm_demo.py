from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime
from pathlib import Path


def _backup_report(report_path: Path) -> None:
    if report_path.exists():
        backup = report_path.with_suffix(f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}.md")
        shutil.copy2(report_path, backup)
        print(f"  Backup saved: {backup}")


def _generate_base_report(rank_path: Path, suspicious_path: Path, output_path: Path, threshold: float) -> bool:
    from boundary_analyzer.reporting.report_builder import generate_report
    try:
        generate_report(rank_path, suspicious_path, output_path, threshold)
        print(f"  Report written to: {output_path}")
        return True
    except FileNotFoundError as e:
        print(f"  Error: {e}")
        return False


def _append_llm_analysis(report_path: Path, base_dir: Path) -> bool:
    mapping_path = base_dir / "interim" / "endpoint_table_map.csv"
    rank_path = base_dir / "processed" / "service_rank.csv"

    if not rank_path.exists() or not mapping_path.exists():
        print("  Skipping AI analysis: input files not found.")
        return False

    from boundary_analyzer.llm.analysis import generate_narrative_analysis

    has_key = bool(os.environ.get("OPENROUTER_API_KEY", "").strip())
    if not has_key:
        print("  OPENROUTER_API_KEY not set. Using local analysis (no LLM).")
        print("  To enable AI: `$env:OPENROUTER_API_KEY = \"your-key-here\"`")
    else:
        print("  Generating AI-powered narrative analysis...")
        print("  (this may take a moment due to API fallback retries)")

    analysis = generate_narrative_analysis(
        rank_path=rank_path,
        mapping_path=mapping_path,
        data_dir=base_dir,
    )

    if analysis is None:
        print("  Error: AI analysis returned no result.")
        if not has_key:
            print("  Tip: The local fallback requires pandas. Install with: pip install pandas")
        return False

    try:
        with report_path.open("a", encoding="utf-8") as f:
            f.write("\n\n---\n\n## AI-Powered Analysis\n\n")
            f.write(analysis)
            f.write("\n")
        print("  AI analysis appended to report.")
        return True
    except OSError as e:
        print(f"  Warning: could not write AI analysis to report: {e}")
        return False


def _launch_dashboard(data_dir: Path) -> int:
    host = os.environ.get("BOUNDARY_ANALYZER_DASH_HOST", "127.0.0.1")
    port = int(os.environ.get("BOUNDARY_ANALYZER_DASH_PORT", "8050"))

    from boundary_analyzer.dashboard.app import main as dashboard_main
    print(f"\n  ** Launching dashboard at http://{host}:{port}")
    print(f"  ** Data source: {data_dir.resolve()}")
    print("  ** Close the terminal to stop.\n")
    return dashboard_main(data_dir=data_dir)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate LLM-powered analysis + launch dashboard in one command."
    )
    parser.add_argument(
        "--data-dir", "-d",
        default="_audit_out3",
        help="Data directory with processed/ and interim/ folders (default: _audit_out3)",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8050,
        help="Dashboard port (default: 8050)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Dashboard host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="SCOM threshold override (default: auto-detect from service_rank.csv)",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM analysis (report + dashboard only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate report and LLM analysis, but do NOT launch the dashboard.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        parser.error(f"Data directory not found: {data_dir}")

    rank_path = data_dir / "processed" / "service_rank.csv"
    suspicious_path = data_dir / "processed" / "suspicious_services.csv"
    if not rank_path.exists():
        parser.error(f"service_rank.csv not found in {data_dir / 'processed'}")

    # Detect threshold from data
    import pandas as pd
    rank_df = pd.read_csv(rank_path)
    threshold = args.threshold
    if threshold is None:
        if "threshold_value" in rank_df.columns:
            threshold = float(rank_df["threshold_value"].iloc[0])
        else:
            threshold = 0.5
    print(f"Using SCOM threshold: {threshold}")

    # Prepare report directory
    report_dir = Path("reports") / "latest"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "report.md"

    # Backup existing report
    _backup_report(report_path)

    # Generate base report
    if not _generate_base_report(rank_path, suspicious_path, report_path, threshold):
        return 1

    # Append LLM analysis
    if not args.no_llm:
        _append_llm_analysis(report_path, data_dir)

    # Launch dashboard (unless dry-run)
    if args.dry_run:
        print("\n  Dry-run complete. Skipping dashboard launch.\n")
        return 0

    os.environ["BOUNDARY_ANALYZER_DASH_HOST"] = str(args.host)
    os.environ["BOUNDARY_ANALYZER_DASH_PORT"] = str(int(args.port))
    return _launch_dashboard(data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
