from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from boundary_analyzer.metrics.cohesion_rules import get_threshold, is_suspicious


def main() -> int:
    scom_path = Path("data/processed/service_scom.csv")
    rank_path = Path("data/processed/service_rank.csv")
    suspicious_path = Path("data/processed/suspicious_services.csv")
    
    print(f"Reading SCOM scores from: {scom_path}")
    
    if not scom_path.exists():
        print("Error: service_scom.csv not found. Run step 06 first.")
        return 1
    
    # Load settings for threshold
    settings_path = Path("config/settings.yaml")
    if settings_path.exists():
        with settings_path.open("r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
    else:
        settings = None
    
    threshold = get_threshold(settings)
    print(f"SCOM threshold for suspicious services: {threshold}")
    
    scom_df = pd.read_csv(scom_path)
    
    # Sort by SCOM (lowest first)
    rank_df = scom_df.sort_values("scom_score", ascending=True).reset_index(drop=True)
    rank_df["rank"] = range(1, len(rank_df) + 1)
    
    # Flag suspicious services
    rank_df["is_suspicious"] = rank_df["scom_score"].apply(
        lambda x: is_suspicious(x, threshold)
    )
    
    # Reorder columns
    rank_df = rank_df[["rank", "service_name", "scom_score", "endpoints_count", "tables_count", "is_suspicious"]]
    
    # Save full ranking
    rank_path.parent.mkdir(parents=True, exist_ok=True)
    rank_df.to_csv(rank_path, index=False)
    print(f"Saved ranking to: {rank_path}")
    
    # Save suspicious services only
    suspicious_df = rank_df[rank_df["is_suspicious"] == True].copy()
    suspicious_df.to_csv(suspicious_path, index=False)
    
    print("\nService Ranking:")
    print(rank_df.to_string(index=False))
    
    print(f"\nSuspicious services (SCOM < {threshold}): {len(suspicious_df)}")
    if not suspicious_df.empty:
        print(suspicious_df[["rank", "service_name", "scom_score"]].to_string(index=False))
    
    print(f"\nSaved suspicious services to: {suspicious_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
