from __future__ import annotations

from pathlib import Path

import pandas as pd

from boundary_analyzer.metrics.scom import (
    compute_scom,
    save_scom_csv,
)
from boundary_analyzer.settings_loader import get_data_dir, load_settings


def main() -> int:
    base_dir = get_data_dir()
    mapping_path = base_dir / "interim" / "endpoint_table_map.csv"
    endpoints_path = base_dir / "interim" / "endpoints.csv"
    output_path = base_dir / "processed" / "service_scom.csv"
    
    print(f"Reading mapping from: {mapping_path}")
    
    if not mapping_path.exists():
        print("Error: endpoint_table_map.csv not found. Run step 05 first.")
        return 1
    
    # Load settings for SCOM weighting
    settings = load_settings()
    endpoint_weighting = settings.get("endpoint_weighting", True)
    
    mapping_df = pd.read_csv(mapping_path)
    print(f"Loaded {len(mapping_df)} endpoint-table mappings")

    endpoints_df = None
    if endpoints_path.exists():
        endpoints_df = pd.read_csv(endpoints_path)
        print(f"Loaded {len(endpoints_df)} endpoint spans (for endpoint coverage)")
    else:
        print("Warning: endpoints.csv not found; endpoints without DB ops may be missed.")
    
    print(f"\nSCOM endpoint weighting: {endpoint_weighting}")
    
    # Compute SCOM faithfully according to the paper
    scom_df = compute_scom(
        mapping_df=mapping_df,
        endpoints_df=endpoints_df,
        use_endpoint_weighting=endpoint_weighting,
    )
    
    print("\nSCOM Scores:")
    if scom_df.empty:
        print("  (No services to score - no DB operations found)")
        print("  Tip: Check if database traces are being captured in Jaeger.")
    else:
        print(scom_df.to_string(index=False))
    
    save_scom_csv(scom_df, output_path)
    print(f"\nSaved to: {output_path}")
    
    # Warn if empty but allow pipeline to continue (step_07 will handle it)
    if scom_df.empty:
        print("\nWarning: SCOM DataFrame is empty. Pipeline will attempt to continue.")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
