from __future__ import annotations

import logging

import pandas as pd

from boundary_analyzer.detection.db_table_extractor import extract_db_operations, save_db_operations_csv
from boundary_analyzer.settings_loader import get_data_dir

logger = logging.getLogger(__name__)


def main() -> int:
    base_dir = get_data_dir()
    spans_path = base_dir / "interim" / "spans.csv"
    output_path = base_dir / "interim" / "db_operations.csv"

    logger.info("Reading spans from: %s", spans_path)

    if not spans_path.exists():
        logger.error("Error: spans.csv not found. Run step 02 first.")
        return 1

    spans_df = pd.read_csv(spans_path)
    logger.info("Loaded %d spans", len(spans_df))

    db_ops_df = extract_db_operations(spans_df)
    logger.info("Found %d DB operations", len(db_ops_df))

    if not db_ops_df.empty:
        logger.info("DB systems: %s", db_ops_df["db_system"].unique().tolist())
        all_tables = set()
        for tables_str in db_ops_df["tables"]:
            if tables_str:
                all_tables.update(tables_str.split(","))
        logger.info("Tables found: %s", sorted(all_tables))

    save_db_operations_csv(db_ops_df, output_path)
    logger.info("Saved to: %s", output_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
