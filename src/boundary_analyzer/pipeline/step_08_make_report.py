from __future__ import annotations

from pathlib import Path

import yaml

from boundary_analyzer.reporting.report_builder import generate_report


def main() -> int:
    rank_path = Path("data/processed/service_rank.csv")
    suspicious_path = Path("data/processed/suspicious_services.csv")
    output_path = Path("reports/latest/report.md")
    
    print(f"Reading ranking from: {rank_path}")
    
    if not rank_path.exists():
        print("Error: service_rank.csv not found. Run step 07 first.")
        return 1
    
    # Load settings for threshold
    settings_path = Path("config/settings.yaml")
    if settings_path.exists():
        with settings_path.open("r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        threshold = float(settings.get("scom_threshold", 0.5))
    else:
        threshold = 0.5
    
    print(f"Using SCOM threshold: {threshold}")
    
    generate_report(rank_path, suspicious_path, output_path, threshold)
    
    print(f"Report saved to: {output_path}")
    print(f"\nOpen the report in your browser or Markdown viewer.")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
