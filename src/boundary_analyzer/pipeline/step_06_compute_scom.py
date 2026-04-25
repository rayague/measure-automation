from __future__ import annotations

from pathlib import Path

import pandas as pd

from boundary_analyzer.metrics.scom import compute_scom, save_scom_csv


def main() -> int:
    mapping_path = Path("data/interim/endpoint_table_map.csv")
    output_path = Path("data/processed/service_scom.csv")
    
    print(f"Reading mapping from: {mapping_path}")
    
    if not mapping_path.exists():
        print("Error: endpoint_table_map.csv not found. Run step 05 first.")
        return 1
    
    mapping_df = pd.read_csv(mapping_path)
    print(f"Loaded {len(mapping_df)} endpoint-table mappings")
    
    scom_df = compute_scom(mapping_df)
    
    print("\nSCOM Scores:")
    print(scom_df.to_string(index=False))
    
    save_scom_csv(scom_df, output_path)
    print(f"\nSaved to: {output_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
