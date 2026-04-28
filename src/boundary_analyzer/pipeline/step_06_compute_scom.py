from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from boundary_analyzer.metrics.scom_ultimate import (
    compute_paper_scom,
    compute_simple_scom,
    compute_weighted_scom,
    save_scom_csv,
)


def main() -> int:
    mapping_path = Path("data/interim/endpoint_table_map.csv")
    endpoints_path = Path("data/interim/endpoints.csv")
    output_path = Path("data/processed/service_scom.csv")
    
    print(f"Reading mapping from: {mapping_path}")
    
    if not mapping_path.exists():
        print("Error: endpoint_table_map.csv not found. Run step 05 first.")
        return 1
    
    # Load settings for SCOM method
    settings_path = Path("config/settings.yaml")
    if settings_path.exists():
        with settings_path.open("r", encoding="utf-8") as f:
            settings = yaml.safe_load(f)
        scom_method = settings.get("scom_method", "weighted")
        table_weighting = settings.get("table_weighting", True)
        endpoint_weighting = settings.get("endpoint_weighting", True)
    else:
        scom_method = "weighted"
        table_weighting = True
        endpoint_weighting = True
    
    mapping_df = pd.read_csv(mapping_path)
    print(f"Loaded {len(mapping_df)} endpoint-table mappings")

    endpoints_df = None
    if endpoints_path.exists():
        endpoints_df = pd.read_csv(endpoints_path)
        print(f"Loaded {len(endpoints_df)} endpoint spans (for endpoint coverage)")
    else:
        print("Warning: endpoints.csv not found; endpoints without DB ops may be missed.")
    
    print(f"\nSCOM method: {scom_method}")
    if scom_method == "weighted":
        print(f"  Table weighting: {table_weighting}")
        print(f"  Endpoint weighting: {endpoint_weighting}")
    
    # Compute SCOM based on method
    if scom_method == "paper":
        scom_df = compute_paper_scom(mapping_df, endpoints_df=endpoints_df)
    elif scom_method == "weighted":
        scom_df = compute_weighted_scom(
            mapping_df,
            use_table_weighting=table_weighting,
            use_endpoint_weighting=endpoint_weighting,
            endpoints_df=endpoints_df,
        )
    else:
        scom_df = compute_simple_scom(mapping_df, endpoints_df=endpoints_df)
    
    print("\nSCOM Scores:")
    print(scom_df.to_string(index=False))
    
    save_scom_csv(scom_df, output_path)
    print(f"\nSaved to: {output_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
