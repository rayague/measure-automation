from __future__ import annotations

from pathlib import Path

import pandas as pd

from boundary_analyzer.metrics.threshold_ultimate import apply_threshold
from boundary_analyzer.settings_loader import get_data_dir, load_settings


def main() -> int:
    base_dir = get_data_dir()
    scom_path = base_dir / "processed" / "service_scom.csv"
    rank_output_path = base_dir / "processed" / "service_rank.csv"
    suspicious_output_path = base_dir / "processed" / "suspicious_services.csv"
    
    print(f"Reading SCOM scores from: {scom_path}")

    if not scom_path.exists():
        print("Error: service_scom.csv not found. Run step 06 first.")
        return 1

    # Check if file is empty (no data or just headers)
    if scom_path.stat().st_size == 0:
        print("Error: service_scom.csv is empty (0 bytes). No SCOM data to process.")
        print("This usually means no DB operations were found in traces.")
        print("Tip: Check if database traces are appearing in Jaeger.")
        return 3

    # Load settings for threshold method
    settings = load_settings()
    threshold_method = settings.get("threshold_method", "percentile")
    threshold_percentile = settings.get("threshold_percentile", 25.0)
    threshold_zscore = settings.get("threshold_zscore", -1.5)
    fixed_threshold = settings.get("scom_threshold", 0.5)

    # Try to read CSV, handle empty/malformed case
    try:
        scom_df = pd.read_csv(scom_path)
    except pd.errors.EmptyDataError:
        print("Error: service_scom.csv has no columns (EmptyDataError).")
        print("This happens when no services could be scored (no DB operations found).")
        print("Tip: Check if database traces are appearing in Jaeger.")
        return 3

    print(f"Loaded {len(scom_df)} services")

    if scom_df.empty:
        print("Error: No services found in service_scom.csv.")
        print("This usually means no traces/spans were collected (Jaeger returned 0 traces).")
        print("Tip: send some traffic to your service, increase lookback_minutes/limit_traces, then re-run.")
        return 2
    
    # Add rank (sorted by SCOM, lowest first)
    scom_df = scom_df.sort_values("scom_score").reset_index(drop=True)
    scom_df["rank"] = scom_df.index + 1
    
    # Apply statistical threshold
    print(f"\nThreshold method: {threshold_method}")
    if threshold_method == "percentile":
        print(f"  Percentile: {threshold_percentile}%")
    elif threshold_method == "zscore":
        print(f"  Z-score cutoff: {threshold_zscore}")
    elif threshold_method == "fixed":
        print(f"  Fixed threshold: {fixed_threshold}")
    
    scom_df = apply_threshold(
        scom_df,
        threshold_method=threshold_method,
        threshold_percentile=threshold_percentile,
        threshold_zscore=threshold_zscore,
        fixed_threshold=fixed_threshold,
    )
    
    threshold_value = scom_df["threshold_value"].iloc[0]
    suspicious_count = len(scom_df[scom_df["is_suspicious"] == True])
    print(f"\nComputed threshold: {threshold_value:.4f}")
    print(f"Suspicious services: {suspicious_count}")
    
    # Save ranking
    rank_output_path.parent.mkdir(parents=True, exist_ok=True)
    scom_df.to_csv(rank_output_path, index=False)
    print(f"Saved ranking to: {rank_output_path}")
    
    # Save suspicious services only
    suspicious_df = scom_df[scom_df["is_suspicious"] == True].copy()
    suspicious_df.to_csv(suspicious_output_path, index=False)
    print(f"Saved suspicious services to: {suspicious_output_path}")
    
    print(f"\nSuspicious services (SCOM < {threshold_value:.4f}): {len(suspicious_df)}")
    if not suspicious_df.empty:
        print(suspicious_df[["rank", "service_name", "scom_score"]].to_string(index=False))
    
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
