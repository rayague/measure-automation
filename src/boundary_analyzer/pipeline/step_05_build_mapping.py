from __future__ import annotations

from pathlib import Path

import pandas as pd

from boundary_analyzer.detection.mapping_builder import build_endpoint_table_mapping, save_endpoint_table_map_csv


def main() -> int:
    spans_path = Path("data/interim/spans.csv")
    endpoints_path = Path("data/interim/endpoints.csv")
    db_ops_path = Path("data/interim/db_operations.csv")
    output_path = Path("data/interim/endpoint_table_map.csv")
    
    # Check inputs exist
    for path in [spans_path, endpoints_path, db_ops_path]:
        if not path.exists():
            print(f"Error: {path.name} not found. Run previous steps first.")
            return 1
    
    print("Reading input files...")
    spans_df = pd.read_csv(spans_path)
    endpoints_df = pd.read_csv(endpoints_path)
    db_ops_df = pd.read_csv(db_ops_path)
    
    print(f"Loaded {len(spans_df)} spans, {len(endpoints_df)} endpoints, {len(db_ops_df)} DB operations")
    
    # Build mapping
    mapping_df = build_endpoint_table_mapping(spans_df, endpoints_df, db_ops_df)
    
    print(f"Built {len(mapping_df)} endpoint-table mappings")
    
    if not mapping_df.empty:
        print("\nMapping preview:")
        print(mapping_df.head(10).to_string(index=False))
        
        # Show summary by service
        print("\nBy service:")
        for service in mapping_df["service_name"].unique():
            service_df = mapping_df[mapping_df["service_name"] == service]
            endpoints = service_df["endpoint_key"].nunique()
            tables = service_df["table"].nunique()
            print(f"  {service}: {endpoints} endpoints, {tables} tables")
    
    save_endpoint_table_map_csv(mapping_df, output_path)
    print(f"\nSaved to: {output_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
