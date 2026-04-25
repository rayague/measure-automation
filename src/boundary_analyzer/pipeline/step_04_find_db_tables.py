from __future__ import annotations

from pathlib import Path

import pandas as pd

from boundary_analyzer.detection.db_table_extractor import extract_db_operations, save_db_operations_csv


def main() -> int:
    spans_path = Path("data/interim/spans.csv")
    output_path = Path("data/interim/db_operations.csv")
    
    print(f"Reading spans from: {spans_path}")
    
    if not spans_path.exists():
        print("Error: spans.csv not found. Run step 02 first.")
        return 1
    
    spans_df = pd.read_csv(spans_path)
    print(f"Loaded {len(spans_df)} spans")
    
    db_ops_df = extract_db_operations(spans_df)
    print(f"Found {len(db_ops_df)} DB operations")
    
    if not db_ops_df.empty:
        print(f"DB systems: {db_ops_df['db_system'].unique().tolist()}")
        all_tables = set()
        for tables_str in db_ops_df["tables"]:
            if tables_str:
                all_tables.update(tables_str.split(","))
        print(f"Tables found: {sorted(all_tables)}")
    
    save_db_operations_csv(db_ops_df, output_path)
    print(f"Saved to: {output_path}")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
