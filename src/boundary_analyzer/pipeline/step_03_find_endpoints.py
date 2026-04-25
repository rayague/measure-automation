from __future__ import annotations

from pathlib import Path

import pandas as pd

from boundary_analyzer.detection.endpoint_extractor import extract_endpoints, save_endpoints_csv


def main() -> int:
    spans_path = Path("data/interim/spans.csv")
    output_path = Path("data/interim/endpoints.csv")
    
    print(f"Reading spans from: {spans_path}")
    
    if not spans_path.exists():
        print("Error: spans.csv not found. Run step 02 first.")
        return 1
    
    spans_df = pd.read_csv(spans_path)
    print(f"Loaded {len(spans_df)} spans")
    
    endpoints_df = extract_endpoints(spans_df)
    print(f"Found {len(endpoints_df)} endpoint spans")
    
    if not endpoints_df.empty:
        print(f"Endpoints: {endpoints_df['endpoint_key'].unique().tolist()}")
    
    save_endpoints_csv(endpoints_df, output_path)
    print(f"Saved to: {output_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
