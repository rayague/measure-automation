from __future__ import annotations

from pathlib import Path

from boundary_analyzer.reporting.report_builder import generate_report
from boundary_analyzer.settings_loader import get_data_dir, get_reports_dir, get_llm_enabled, load_settings


def _append_llm_analysis(report_path: Path, base_dir: Path) -> None:
    """Generate and append LLM-powered narrative analysis to the report."""
    mapping_path = base_dir / "interim" / "endpoint_table_map.csv"
    rank_path = base_dir / "processed" / "service_rank.csv"

    if not rank_path.exists() or not mapping_path.exists():
        print("  Skipping LLM analysis: input files not found.")
        return

    from boundary_analyzer.llm.analysis import generate_narrative_analysis

    print("  Generating AI-powered narrative analysis...")
    analysis = generate_narrative_analysis(
        rank_path=rank_path,
        mapping_path=mapping_path,
        data_dir=base_dir,
    )

    if analysis is None:
        print("  Warning: LLM analysis returned no result (check OPENROUTER_API_KEY).")
        return

    # Append analysis to the report
    try:
        with report_path.open("a", encoding="utf-8") as f:
            f.write("\n\n---\n\n## AI-Powered Analysis\n\n")
            f.write(analysis)
            f.write("\n")
        print("  AI analysis appended to report.")
    except OSError as e:
        print(f"  Warning: could not write LLM analysis to report: {e}")


def main() -> int:
    base_dir = get_data_dir()
    reports_dir = get_reports_dir()
    rank_path = base_dir / "processed" / "service_rank.csv"
    suspicious_path = base_dir / "processed" / "suspicious_services.csv"
    output_path = reports_dir / "latest" / "report.md"
    
    print(f"Reading ranking from: {rank_path}")
    
    if not rank_path.exists():
        print("Error: service_rank.csv not found. Run step 07 first.")
        return 1
    
    # Load settings for threshold
    settings = load_settings()
    threshold = float(settings.get("scom_threshold", 0.5))
    
    print(f"Using SCOM threshold from settings as fallback: {threshold}")
    print("Note: if service_rank.csv contains a computed threshold, the report will use it.")
    
    generate_report(rank_path, suspicious_path, output_path, threshold)
    
    print(f"Report saved to: {output_path}")

    # LLM-powered narrative analysis (optional)
    if get_llm_enabled(settings):
        print("\nLLM analysis enabled. Running AI-powered narrative analysis...")
        _append_llm_analysis(output_path, base_dir)
    else:
        print("\nLLM analysis disabled. Set llm.enabled=true in settings.yaml and")
        print("set OPENROUTER_API_KEY environment variable to enable AI analysis.")
    
    print("\nOpen the report in your browser or Markdown viewer.")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
