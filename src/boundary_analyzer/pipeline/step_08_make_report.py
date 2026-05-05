from __future__ import annotations

from pathlib import Path

from boundary_analyzer.reporting.report_builder import generate_report
from boundary_analyzer.settings_loader import get_data_dir, get_reports_dir, load_settings


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
    print("\nOpen the report in your browser or Markdown viewer.")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
